"""Sandbox policy.

The policy is the single place that decides what generated code may do. It is
loaded from settings and can be tightened by a JSON file under `infra/policies`,
so tightening the sandbox does not require a code change.
"""

from __future__ import annotations

import json
from pathlib import Path

from insightflow_contracts import SandboxSettings, StrictModel
from pydantic import Field

#: Paths available inside the sandbox, relative to its working directory.
INPUT_DIR = "input"
OUTPUT_DIR = "output"

#: The dataset is mounted read-only under this name.
INPUT_DATA_NAME = "data.parquet"

#: Generated code must write this file for the runner to accept its output.
RESULT_NAME = "result.json"

#: Keys permitted in `result.json`.
RESULT_KEYS: frozenset[str] = frozenset({"metrics", "tables", "summary", "warnings"})


class SandboxPolicy(StrictModel):
    """Resource and filesystem policy for one execution."""

    timeout_seconds: int = Field(default=120, ge=5, le=1800)
    memory_limit_bytes: int = Field(default=2 * 1024**3, ge=64 * 1024**2)
    cpu_seconds: int = Field(default=120, ge=1, le=1800)
    max_processes: int = Field(default=64, ge=1, le=1024)
    max_output_bytes: int = Field(default=1_000_000, ge=1024)
    max_file_descriptors: int = Field(default=64, ge=8)
    allowed_imports: frozenset[str] = frozenset()
    allow_network: bool = False
    allow_generated_code: bool = True
    read_only_root: bool = True
    non_root_user: str = "10001:10001"
    container_runtime: str | None = None
    container_image: str = "insightflow/sandbox:1"
    stack_size_bytes: int = Field(default=8 * 1024 * 1024, ge=64 * 1024)

    @property
    def input_path(self) -> str:
        return f"{INPUT_DIR}/{INPUT_DATA_NAME}"

    @property
    def output_path(self) -> str:
        return f"{OUTPUT_DIR}/{RESULT_NAME}"

    def docker_args(self, *, host_input: Path, host_output: Path) -> list[str]:
        """Container flags implementing the policy.

        Network is disabled outright, capabilities are dropped, the root filesystem
        is read-only, and only two volumes are mounted: the dataset and harness
        read-only, and an output directory that can be written. The caller appends
        the command to run.
        """
        workdir = "/work"
        return [
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.max_processes),
            "--memory",
            str(self.memory_limit_bytes),
            "--memory-swap",
            str(self.memory_limit_bytes),
            "--cpus",
            "1.0",
            "--ulimit",
            f"nofile={self.max_file_descriptors}:{self.max_file_descriptors}",
            "--user",
            self.non_root_user,
            "--tmpfs",
            "/tmp:rw,size=64m,noexec,nosuid",  # noqa: S108
            "--workdir",
            workdir,
            "--mount",
            f"type=bind,src={host_input},dst={workdir}/{INPUT_DIR},readonly",
            "--mount",
            f"type=bind,src={host_output},dst={workdir}/{OUTPUT_DIR}",
            self.container_image,
        ]


def policy_from_settings(settings: SandboxSettings) -> SandboxPolicy:
    """Build a policy from application settings."""
    return SandboxPolicy(
        timeout_seconds=settings.timeout_seconds,
        memory_limit_bytes=settings.memory_limit_bytes,
        cpu_seconds=settings.cpu_seconds,
        max_processes=settings.max_processes,
        max_output_bytes=settings.max_output_bytes,
        max_file_descriptors=settings.max_file_descriptors,
        allowed_imports=frozenset(settings.allowed_imports),
        allow_network=settings.allow_network,
        allow_generated_code=settings.allow_generated_code,
        container_runtime=settings.container_runtime,
    )


def load_policy_overrides(path: str | Path) -> dict:
    """Read a JSON policy override file, if one exists."""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise ValueError("sandbox policy override must be a JSON object")  # noqa: TRY004
    return payload


def tighten(policy: SandboxPolicy, overrides: dict) -> SandboxPolicy:
    """Apply overrides that may only ever make the sandbox stricter."""
    hardened: dict = {}
    for key, value in overrides.items():
        current = getattr(policy, key, None)
        if key == "allowed_imports":
            hardened[key] = frozenset(set(current) & set(value))
        elif isinstance(current, bool):
            hardened[key] = bool(current) and bool(value)
        elif isinstance(current, int | float) and isinstance(value, int | float):
            hardened[key] = min(current, value)
        elif key == "container_runtime" and value is None:
            continue
        else:
            hardened[key] = value
    return policy.model_copy(update=hardened)
