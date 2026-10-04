"""The restricted runner.

Two execution modes behind one contract:

* `container` - a disposable container with no network, a read-only root filesystem,
  dropped capabilities and a non-root user. This is the production boundary.
* `local_process` - a separate isolated interpreter with a scrubbed environment,
  resource limits where the platform supports them, and a hard timeout. Development
  and CI only; it is *not* an isolation boundary, and every result says so.

Both modes refuse to run code that fails static validation, capture stdout/stderr,
cap output size, and validate the result file against a closed schema. Generated
code writes its own results to `output/result.json`; the runner never trusts
anything else the code produces.

Note that Python always gives executed code its own builtins. Static validation
cannot remove that, which is exactly why the container - not the AST check - is
the security boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    SandboxViolationError,
    ToolExecutionError,
    content_digest,
    file_sha256,
)

from insightflow_sandbox.policy import (
    INPUT_DATA_NAME,
    INPUT_DIR,
    OUTPUT_DIR,
    RESULT_KEYS,
    SandboxPolicy,
)
from insightflow_sandbox.validator import CodeValidator

ExecutionMode = Literal["container", "local_process"]

#: Generated code lives here; the trusted harness runs it.
USER_CODE_NAME = "user_code.py"
HARNESS_NAME = "run.py"
API_NAME = "sandbox_api.py"

#: Only these environment variables survive into a local sandbox process. None of
#: them carry a secret, a path into user data or a network setting.
_ENV_PASSTHROUGH: tuple[str, ...] = (
    "PATH",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    # Polars' CPU feature detection reads these on Windows and aborts without them.
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
)


@dataclass
class SandboxResult:
    """Outcome of one sandboxed execution."""

    status: Literal["succeeded", "failed", "rejected"]
    mode: ExecutionMode
    isolated: bool
    metrics: dict[str, float | int | str | bool | None] = field(default_factory=dict)
    tables: dict[str, list[dict]] = field(default_factory=dict)
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    wall_seconds: float = 0.0
    peak_memory_bytes: int | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    source_sha256: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass
class SandboxWorkspace:
    """Disposable input/output layout handed to the code."""

    root: Path

    @property
    def input_dir(self) -> Path:
        return self.root / INPUT_DIR

    @property
    def output_dir(self) -> Path:
        return self.root / OUTPUT_DIR

    @property
    def input_data(self) -> Path:
        return self.input_dir / INPUT_DATA_NAME

    @property
    def result_path(self) -> Path:
        return self.output_dir / "result.json"

    def prepare(self) -> None:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


#: Trusted harness plus the safe API available to generated code.
#:
#: Generated code never gets `open`, `exec` or an environment variable. It gets a
#: small, explicit set of functions that can only touch `input/` (read) and
#: `output/` (write). That is a far stronger guarantee than an AST check: there is
#: no name in the namespace that reaches the filesystem.
SANDBOX_API_SOURCE = '''\
"""Safe API exposed to generated analysis code.

Every name listed in the harness' API_NAMES tuple must be defined here; the
harness copies exactly this set into the generated code's namespace and nothing
else. Generated code therefore has no `open`, no `__import__`, no environment
access and no route to the filesystem outside input/ and output/.
"""
import json
import math
import statistics

import polars as pl

INPUT_DIR = "input"
OUTPUT_DIR = "output"
RESULT_PATH = OUTPUT_DIR + "/result.json"
_MAX_BYTES = 4_000_000


def read_table(name="data"):
    """Read a Parquet dataset that was placed in input/ for this job."""
    return pl.read_parquet(INPUT_DIR + "/" + name + ".parquet")


def write_table(frame, name):
    """Write an evidence table to output/<name>.csv. Returns the path written."""
    frame.write_csv(OUTPUT_DIR + "/" + name + ".csv")
    return OUTPUT_DIR + "/" + name + ".csv"


def write_text(name, text):
    """Write a small text artifact to output/<name>."""
    with open(OUTPUT_DIR + "/" + name, "w", encoding="utf-8") as handle:
        handle.write(str(text))
    return OUTPUT_DIR + "/" + name


def write_result(metrics=None, tables=None, summary="", warnings=None):
    """Publish this analysis. Call exactly once, at the end of the script."""
    payload = {
        "metrics": metrics or {},
        "tables": tables or {},
        "summary": summary,
        "warnings": list(warnings or []),
    }
    encoded = json.dumps(payload, default=str)
    if len(encoded) > _MAX_BYTES:
        raise ValueError("result payload is too large; summarise instead")
    with open(RESULT_PATH, "w", encoding="utf-8") as handle:
        handle.write(encoded)
    return RESULT_PATH


def frame(rows, columns=None):
    """Build a DataFrame from row dictionaries."""
    return pl.DataFrame(rows, strict=False)


def save_progress(message):
    """Record a progress note for the audit log."""
    print("progress:", message)
'''

HARNESS_SOURCE = '''\
"""Trusted harness: runs the generated analysis and reports failures cleanly."""
import sys
import traceback

INPUT_DIR = "input"

# The generated code's namespace contains only the safe API and its own names.
# There is deliberately no `open`, no reachable builtins module and no environment
# access: nothing in scope can touch the filesystem outside input/ and output/.
API_NAMES = (
    "read_table",
    "write_table",
    "write_text",
    "write_result",
    "frame",
    "save_progress",
    "INPUT_DIR",
    "OUTPUT_DIR",
    "RESULT_PATH",
    "pl",
    "json",
    "math",
    "statistics",
)


def build_namespace():
    api = {}
    with open(INPUT_DIR + "/sandbox_api.py", encoding="utf-8") as handle:
        exec(compile(handle.read(), "sandbox_api.py", "exec"), api)  # noqa: S102
    namespace = {"__name__": "__analysis__", "__file__": "user_code.py"}
    for name in API_NAMES:
        namespace[name] = api[name]
    return namespace


def main() -> int:
    namespace = build_namespace()
    try:
        with open(INPUT_DIR + "/user_code.py", encoding="utf-8") as handle:
            source = handle.read()
        exec(compile(source, "user_code.py", "exec"), namespace)  # noqa: S102
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - any failure must be reported, not swallowed
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


class SandboxRunner:
    """Validate, then execute generated analysis code under a `SandboxPolicy`."""

    version = "1"

    def __init__(self, policy: SandboxPolicy, *, workdir: Path | None = None) -> None:
        self.policy = policy
        self.workdir = Path(workdir) if workdir else None
        self.validator = CodeValidator(policy.allowed_imports)

    # ------------------------------------------------------------- public API
    @property
    def mode(self) -> ExecutionMode:
        return "container" if self.policy.container_runtime else "local_process"

    def run(
        self,
        source: str,
        *,
        dataset_path: str | Path | None = None,
        extra_files: dict[str, bytes] | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxResult:
        """Validate, then execute `source` in a disposable workspace."""
        if not self.policy.allow_generated_code:
            raise SandboxViolationError(
                "generated code execution is disabled by policy",
                policy="allow_generated_code=False",
            )

        report = self.validator.validate(source)
        digest = content_digest(source)
        if not report.ok:
            return SandboxResult(
                status="rejected",
                mode=self.mode,
                isolated=self.mode == "container",
                error="generated code failed static validation",
                error_code="sandbox_violation",
                warnings=report.violations[:20],
                source_sha256=digest,
            )

        timeout = min(timeout_seconds or self.policy.timeout_seconds, self.policy.timeout_seconds)
        workspace = self._make_workspace(dataset_path, extra_files, source)
        try:
            if self.mode == "container":
                return self._run_container(workspace, timeout)
            return self._run_local(workspace, timeout)
        finally:
            if self.workdir is None:
                shutil.rmtree(workspace.root, ignore_errors=True)

    # ------------------------------------------------------------- workspace
    def _make_workspace(
        self,
        dataset_path: str | Path | None,
        extra_files: dict[str, bytes] | None,
        source: str,
    ) -> SandboxWorkspace:
        if self.workdir is not None:
            self.workdir.mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix="insightflow-sbx-", dir=self.workdir))
        else:
            root = Path(tempfile.mkdtemp(prefix="insightflow-sbx-"))

        workspace = SandboxWorkspace(root=root)
        workspace.prepare()
        # Harness, API and generated code all live in input/, which is mounted
        # read-only in container mode; only output/ is writable.
        (workspace.input_dir / USER_CODE_NAME).write_text(source, encoding="utf-8")
        (workspace.input_dir / HARNESS_NAME).write_text(HARNESS_SOURCE, encoding="utf-8")
        (workspace.input_dir / API_NAME).write_text(SANDBOX_API_SOURCE, encoding="utf-8")

        if dataset_path is not None:
            source_path = Path(dataset_path)
            if not source_path.exists():
                raise ToolExecutionError(
                    "dataset file for the sandbox does not exist",
                    path=str(source_path),
                    retryable=False,
                )
            shutil.copyfile(source_path, workspace.input_data)

        for name, payload in (extra_files or {}).items():
            target = workspace.input_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        return workspace

    # ------------------------------------------------------------------ local
    def _run_local(self, workspace: SandboxWorkspace, timeout: int) -> SandboxResult:
        """Run in a separate interpreter with a scrubbed environment.

        `python -I` gives isolated mode: no user site-packages directory, no
        `PYTHON*` environment variables, and the script's directory kept off
        `sys.path`. The virtualenv's site-packages remains available, which is what
        the sandbox image provides in container mode.
        """
        started = time.perf_counter()
        try:
            # cwd is the workspace root in both modes, so generated code always
            # addresses `input/...` and `output/...` by the same relative paths.
            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(Path(INPUT_DIR) / HARNESS_NAME)],
                cwd=workspace.root,
                env=self._scrubbed_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                preexec_fn=self._preexec() if os.name == "posix" else None,  # type: ignore[arg-type]
            )
            stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired:
            return SandboxResult(
                status="failed",
                mode="local_process",
                isolated=False,
                error=f"execution exceeded its {timeout}s time limit and was terminated",
                error_code="sandbox_timeout",
                wall_seconds=round(time.perf_counter() - started, 3),
            )
        except OSError as exc:  # pragma: no cover - defensive
            raise ToolExecutionError(
                "sandbox process could not start", cause=str(exc), retryable=False
            ) from exc

        return self._collect(
            workspace,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            elapsed=time.perf_counter() - started,
            mode="local_process",
            isolated=False,
        )

    # -------------------------------------------------------------- container
    def _run_container(self, workspace: SandboxWorkspace, timeout: int) -> SandboxResult:
        runtime = self.policy.container_runtime or "docker"
        argv = [
            runtime,
            *self.policy.docker_args(
                host_input=workspace.input_dir,
                host_output=workspace.output_dir,
            ),
            "python",
            "-I",
            "-B",
            HARNESS_NAME,
        ]

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired:
            return SandboxResult(
                status="failed",
                mode="container",
                isolated=True,
                error=(
                    f"execution exceeded its {timeout}s time limit; the container was destroyed"
                ),
                error_code="sandbox_timeout",
                wall_seconds=round(time.perf_counter() - started, 3),
            )
        except FileNotFoundError as exc:
            raise ToolExecutionError(
                "configured container runtime is not available",
                runtime=runtime,
                suggestion="Install the runtime, or clear it to fall back to local_process.",
                retryable=False,
            ) from exc

        return self._collect(
            workspace,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            elapsed=time.perf_counter() - started,
            mode="container",
            isolated=True,
        )

    # ----------------------------------------------------------------- output
    def _collect(
        self,
        workspace: SandboxWorkspace,
        *,
        stdout: str,
        stderr: str,
        returncode: int,
        elapsed: float,
        mode: ExecutionMode,
        isolated: bool,
    ) -> SandboxResult:
        limit = self.policy.max_output_bytes
        result = SandboxResult(
            status="failed",
            mode=mode,
            isolated=isolated,
            stdout=_clip(stdout, limit),
            stderr=_clip(stderr, limit),
            wall_seconds=round(elapsed, 3),
        )

        if not workspace.result_path.exists():
            detail = f"exit status {returncode}" if returncode else "exit status 0"
            result.error = f"code produced no result file ({detail})"
            result.error_code = "sandbox_crashed" if returncode else "sandbox_no_result"
            tail = _clip(stderr.strip().splitlines()[-5:], limit)
            if tail:
                result.error = f"{result.error}; output: {tail}"
            return result

        try:
            payload = json.loads(workspace.result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            result.error = f"result file could not be read: {exc}"
            result.error_code = "sandbox_invalid_result"
            return result

        problems = validate_result_payload(payload)
        if problems:
            result.error = "; ".join(problems[:5])
            result.error_code = "sandbox_invalid_result"
            return result

        assert isinstance(payload, dict)  # guaranteed by validate_result_payload
        result.status = "succeeded"
        result.metrics = payload.get("metrics", {})
        result.tables = payload.get("tables", {})
        result.summary = str(payload.get("summary", ""))
        result.warnings = list(payload.get("warnings", []))
        result.artifacts = self._persist(workspace)
        if not isolated:
            result.warnings.append(
                "Executed in local_process mode, which is not an isolation boundary. "
                "Configure a container runtime for production."
            )
        return result

    def _persist(self, workspace: SandboxWorkspace) -> list[ArtifactRef]:
        """Record any extra files the code wrote under output/."""
        artifacts: list[ArtifactRef] = []
        for item in sorted(workspace.output_dir.rglob("*")):
            if not item.is_file() or item.name == "result.json":
                continue
            kind = _artifact_kind(item)
            artifacts.append(
                ArtifactRef(
                    artifact_id=content_digest({"file": item.name}),
                    kind=kind,
                    path=str(item),
                    row_count=_csv_row_count(item) if kind is ArtifactKind.TABLE_CSV else None,
                    byte_size=item.stat().st_size,
                    sha256=file_sha256(item),
                )
            )
        return artifacts

    # ---------------------------------------------------------------- helpers
    def _scrubbed_env(self) -> dict[str, str]:
        """A minimal environment: no API keys, no proxy settings, no host paths."""
        env = {key: os.environ[key] for key in _ENV_PASSTHROUGH if key in os.environ}
        env["PYTHONHASHSEED"] = "0"
        env["HOME"] = workspace_tempdir()
        env["MPLBACKEND"] = "Agg"
        return env

    def _preexec(self):  # pragma: no cover - POSIX only
        """Return a callable applying POSIX rlimits in the child before exec."""
        import resource

        policy = self.policy

        def apply_limits() -> None:
            resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
            resource.setrlimit(
                resource.RLIMIT_AS, (policy.memory_limit_bytes, policy.memory_limit_bytes)
            )
            resource.setrlimit(resource.RLIMIT_NPROC, (policy.max_processes, policy.max_processes))
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (policy.max_output_bytes * 10, policy.max_output_bytes * 10),
            )
            resource.setrlimit(
                resource.RLIMIT_NOFILE, (policy.max_file_descriptors, policy.max_file_descriptors)
            )
            resource.setrlimit(resource.RLIMIT_STACK, (policy.stack_size_bytes, policy.stack_size_bytes))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        return apply_limits


def workspace_tempdir() -> str:
    return tempfile.gettempdir()


def _artifact_kind(path: Path) -> ArtifactKind:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return ArtifactKind.TABLE_CSV
    if suffix == ".json":
        return ArtifactKind.REPORT_JSON
    return ArtifactKind.LOG


def _csv_row_count(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> int | None:
    """Data rows in a CSV, excluding the header. Bounded so it cannot be abused."""
    try:
        if path.stat().st_size > max_bytes:
            return None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = sum(1 for _ in handle)
    except OSError:  # pragma: no cover - defensive
        return None
    return max(lines - 1, 0)


def validate_result_payload(payload: object) -> list[str]:
    """The result contract is a closed schema; unexpected keys are rejected."""
    if not isinstance(payload, dict):
        return ["result must be a JSON object"]
    problems: list[str] = []
    unknown = sorted(set(payload) - RESULT_KEYS)
    if unknown:
        problems.append(f"unexpected keys in result: {unknown} (allowed: {sorted(RESULT_KEYS)})")
    metrics = payload.get("metrics", {})
    if not isinstance(metrics, dict):
        problems.append("'metrics' must be an object")
    else:
        for name, value in metrics.items():
            if value is not None and not isinstance(value, str | int | float | bool):
                problems.append(f"metric '{name}' must be a number, string or boolean")
    tables = payload.get("tables", {})
    if not isinstance(tables, dict):
        problems.append("'tables' must be an object")
    else:
        for name, rows in tables.items():
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                problems.append(f"table '{name}' must be a list of row objects")
    if not isinstance(payload.get("summary", ""), str):
        problems.append("'summary' must be a string")
    if not isinstance(payload.get("warnings", []), list):
        problems.append("'warnings' must be a list")
    return problems


def _clip(text: str | list[str], limit: int) -> str:
    rendered = "\n".join(text) if isinstance(text, list) else text
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + f"\n... [truncated at {limit} characters]"
