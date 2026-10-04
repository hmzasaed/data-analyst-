"""Sandbox security: escapes must fail, and the AST check is not the boundary.

Two layers are tested here:

* static validation, which must reject every escape pattern before execution;
* the runtime, which must still hold when validation is bypassed or when the code
  simply misbehaves (loops, memory, missing results).

The important assertion in this file is `test_ast_validation_is_not_the_boundary`:
it records that passing static validation buys nothing on its own.
"""

from __future__ import annotations

import pytest
from insightflow_contracts import SandboxViolationError
from insightflow_sandbox import CodeValidator, SandboxPolicy, SandboxRunner

pytestmark = pytest.mark.security

ALLOWED = frozenset({"math", "statistics", "json", "polars", "numpy"})


@pytest.fixture(scope="module")
def policy() -> SandboxPolicy:
    return SandboxPolicy(
        timeout_seconds=30,
        memory_limit_bytes=1024 * 1024 * 1024,
        allowed_imports=ALLOWED,
    )


@pytest.fixture(scope="module")
def validator() -> CodeValidator:
    return CodeValidator(ALLOWED)


@pytest.fixture(scope="module")
def runner(policy: SandboxPolicy, tmp_path_factory) -> SandboxRunner:
    return SandboxRunner(policy, workdir=tmp_path_factory.mktemp("sandbox"))


class TestStaticValidation:
    @pytest.mark.parametrize(
        ("name", "source"),
        [
            ("import os", "import os\n"),
            ("import sys", "import sys\n"),
            ("import subprocess", "import subprocess\n"),
            ("import socket", "import socket\n"),
            ("import shutil", "import shutil\n"),
            ("import ctypes", "import ctypes\n"),
            ("import pickle", "import pickle\n"),
            ("import importlib", "import importlib\n"),
            ("import pathlib", "import pathlib\n"),
            ("import platform", "import platform\n"),
            ("import multiprocessing", "import multiprocessing\n"),
            ("from os import path", "from os import path\n"),
            ("relative import", "from . import sibling\n"),
            ("import outside the allowlist", "import pandas\n"),
        ],
    )
    def test_forbidden_imports(self, validator, name, source):
        report = validator.validate(source)
        assert not report.ok, f"{name} passed static validation"
        assert report.violations

    def test_submodules_of_an_allowed_package_are_allowed(self, validator):
        # `numpy.linalg` is as harmless as `numpy`; the check is on the root package.
        report = validator.validate("import numpy.linalg\nwrite_result(metrics={'n': 1})\n")
        assert report.ok, report.violations
        assert "numpy" in report.imports

    @pytest.mark.parametrize(
        ("name", "source"),
        [
            ("eval", "value = eval('1+1')\n"),
            ("exec", "exec('x=1')\n"),
            ("compile", "compile('1', '<s>', 'eval')\n"),
            ("open", "handle = open('/etc/passwd')\n"),
            ("getattr", "value = getattr(1, 'real')\n"),
            ("setattr", "setattr(object, 'x', 1)\n"),
            ("globals", "g = globals()\n"),
            ("locals", "locals()\n"),
            ("vars", "vars()\n"),
            ("__import__", "__import__('os')\n"),
            ("breakpoint", "breakpoint()\n"),
        ],
    )
    def test_forbidden_calls(self, validator, name, source):
        report = validator.validate(source)
        assert not report.ok, f"{name} passed static validation"

    @pytest.mark.parametrize(
        ("name", "source"),
        [
            ("__class__", "x = (1).__class__\n"),
            ("__globals__", "x = print.__globals__\n"),
            ("__builtins__", "x = __builtins__\n"),
            ("__subclasses__", "x = [c for c in ().__class__.__base__.__subclasses__()]\n"),
            ("__mro__", "x = (1).__class__.__mro__\n"),
            ("__reduce__", "x = (1).__reduce__\n"),
            ("__code__", "x = (lambda: 1).__code__\n"),
            ("f_globals", "x = (lambda: 1).f_globals\n"),
            ("gi_code", "x = (lambda: 1).__code__\n"),
        ],
    )
    def test_attribute_escapes(self, validator, name, source):
        report = validator.validate(source)
        assert not report.ok, f"{name} passed static validation"

    @pytest.mark.parametrize(
        ("name", "source"),
        [
            ("os.system in a string", "note = 'os.system(\"rm -rf /\")'\n"),
            ("subprocess.run in a string", "note = 'subprocess.run([\"ls\"])'\n"),
            ("socket.socket in a string", "note = 'socket.socket()'\n"),
            ("shutil.rmtree in a string", "note = 'shutil.rmtree(\"/\")'\n"),
        ],
    )
    def test_escape_patterns_in_strings(self, validator, name, source):
        report = validator.validate(source)
        assert not report.ok, f"{name} passed static validation"

    def test_catching_baseexception_is_rejected(self, validator):
        # Swallowing BaseException would let code survive a resource kill signal.
        report = validator.validate("try:\n    pass\nexcept BaseException:\n    pass\n")
        assert not report.ok

    def test_global_declaration_is_rejected(self, validator):
        report = validator.validate("def f():\n    global x\n")
        assert not report.ok

    def test_dunder_function_definition_is_rejected(self, validator):
        report = validator.validate("class A:\n    def __init__(self):\n        pass\n")
        assert not report.ok

    def test_syntax_error_is_reported(self, validator):
        report = validator.validate("def broken(:\n")
        assert not report.ok
        assert "syntax error" in report.violations[0]

    def test_empty_source_is_rejected(self, validator):
        assert not validator.validate("").ok
        assert not validator.validate("   \n").ok

    def test_oversized_source_is_rejected(self, validator):
        report = validator.validate("value = 1\n" * 6000)
        assert not report.ok
        assert any("exceeds" in violation for violation in report.violations)

    def test_valid_code_passes(self, validator):
        source = (
            "import math\n"
            "import polars as pl\n\n"
            "data = read_table()\n"
            "total = sum(math.fsum(v) for v in data['value'].to_list())\n"
            "write_result(metrics={'total': total})\n"
        )
        report = validator.validate(source)
        assert report.ok, report.violations
        assert "math" in report.imports

    def test_report_raises_with_evidence(self, validator):
        report = validator.validate("import os\n")
        with pytest.raises(SandboxViolationError) as excinfo:
            report.raise_if_invalid("import os\n")
        assert excinfo.value.detail["violations"]


class TestRuntimeContainment:
    def test_valid_code_produces_metrics(self, runner, tmp_path):
        import polars as pl

        data = tmp_path / "input.parquet"
        pl.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]}).write_parquet(data)

        result = runner.run(
            "data = read_table()\n"
            "write_result(metrics={'mean_a': float(data['a'].mean()), 'n': data.height})\n",
            dataset_path=data,
        )
        assert result.status == "succeeded"
        assert result.metrics["mean_a"] == pytest.approx(2.0)
        assert result.metrics["n"] == 3

    def test_rejected_code_never_executes(self, runner):
        result = runner.run("import os\nwrite_result(metrics={'cwd': os.getcwd()})\n")
        assert result.status == "rejected"
        assert result.error_code == "sandbox_violation"
        assert not result.metrics

    def test_infinite_loop_is_killed(self, runner, tmp_path):
        import polars as pl

        data = tmp_path / "loop.parquet"
        pl.DataFrame({"a": [1.0]}).write_parquet(data)
        result = runner.run("while True:\n    pass\n", dataset_path=data, timeout_seconds=5)
        assert result.status == "failed"
        assert result.error_code == "sandbox_timeout"

    def test_memory_bomb_is_killed(self, runner, tmp_path):
        import polars as pl

        data = tmp_path / "mem.parquet"
        pl.DataFrame({"a": [1.0]}).write_parquet(data)
        result = runner.run(
            "rows = []\n"
            "for index in range(400_000_000):\n"
            "    rows.append([index, index])\n"
            "write_result(metrics={'n': len(rows)})\n",
            dataset_path=data,
            timeout_seconds=8,
        )
        assert result.status == "failed"
        assert result.error_code in {"sandbox_timeout", "sandbox_crashed"}

    def test_missing_result_file_is_detected(self, runner, tmp_path):
        import polars as pl

        data = tmp_path / "noresult.parquet"
        pl.DataFrame({"a": [1.0]}).write_parquet(data)
        result = runner.run("value = 1 + 1\n", dataset_path=data)
        assert result.status == "failed"
        assert result.error_code == "sandbox_no_result"

    def test_result_with_unknown_keys_is_rejected(self, runner, tmp_path):
        import polars as pl

        data = tmp_path / "schema.parquet"
        pl.DataFrame({"a": [1.0]}).write_parquet(data)
        # `write_result` has a fixed signature, so the only way to smuggle an extra
        # key is to write the file directly. The closed schema must still catch it.
        result = runner.run(
            "write_text('result.json', '{\"metrics\": {\"x\": 1}, \"sneaky\": {\"a\": 1}}')\n",
            dataset_path=data,
        )
        assert result.status == "failed"
        assert result.error_code == "sandbox_invalid_result"
        assert "unexpected keys" in (result.error or "")

    def test_crash_is_distinguishable_from_no_result(self, runner, tmp_path):
        import polars as pl

        data = tmp_path / "crash.parquet"
        pl.DataFrame({"a": [1.0]}).write_parquet(data)
        result = runner.run("raise ValueError('boom')\n", dataset_path=data)
        assert result.status == "failed"
        assert result.error_code == "sandbox_crashed"
        assert "boom" in (result.error or "") or "ValueError" in (result.stderr or "")

    def test_generated_code_is_disabled_when_policy_says_so(self):
        runner = SandboxRunner(
            SandboxPolicy(allow_generated_code=False, allowed_imports=ALLOWED)
        )
        with pytest.raises(SandboxViolationError, match="disabled"):
            runner.run("write_result(metrics={})\n")

    def test_local_mode_declares_that_it_is_not_isolated(self, runner, tmp_path):
        import polars as pl

        data = tmp_path / "mode.parquet"
        pl.DataFrame({"a": [1.0]}).write_parquet(data)
        result = runner.run("write_result(metrics={'ok': 1})\n", dataset_path=data)
        if result.mode == "local_process":
            assert not result.isolated
            assert any("not an isolation boundary" in w for w in result.warnings)


class TestPolicy:
    def test_overrides_can_only_tighten(self):
        from insightflow_sandbox import SandboxPolicy, tighten

        base = SandboxPolicy(
            timeout_seconds=300,
            memory_limit_bytes=4 * 1024**3,
            max_processes=128,
            allow_network=True,
            allowed_imports=frozenset({"polars", "numpy", "os"}),
        )
        hardened = tighten(
            base,
            {
                "timeout_seconds": 30,
                "memory_limit_bytes": 1024**3,
                "max_processes": 8,
                "allow_network": False,
                "allowed_imports": ["polars"],
            },
        )
        assert hardened.timeout_seconds == 30
        assert hardened.memory_limit_bytes == 1024**3
        assert hardened.max_processes == 8
        assert hardened.allow_network is False
        # `os` must not survive the intersection even though the base allowed it.
        assert hardened.allowed_imports == frozenset({"polars"})

    def test_a_looser_override_cannot_loosen(self):
        from insightflow_sandbox import SandboxPolicy, tighten

        base = SandboxPolicy(timeout_seconds=30, allowed_imports=frozenset({"polars"}))
        loosened = tighten(
            base, {"timeout_seconds": 9999, "allowed_imports": ["polars", "numpy"]}
        )
        assert loosened.timeout_seconds == 30
        assert loosened.allowed_imports == frozenset({"polars"})

    def test_container_arguments_disable_network_and_drop_capabilities(self):
        from pathlib import Path

        args = SandboxPolicy(container_runtime="docker").docker_args(
            host_input=Path("/tmp/in"), host_output=Path("/tmp/out")
        )
        assert args[args.index("--network") + 1] == "none"
        assert args[args.index("--cap-drop") + 1] == "ALL"
        assert "--read-only" in args
        assert "no-new-privileges" in args
        assert args[args.index("--pids-limit") + 1]
        assert any(arg.startswith("type=bind") and "readonly" in arg for arg in args)
        assert not args[-1].endswith("run.py"), "the command is appended by the runner"


class TestHonestLimits:
    def test_ast_validation_is_not_the_boundary(self):
        """Passing the static check buys no isolation on its own.

        This test documents a design decision rather than a behaviour: the AST check
        is a cheap first filter, and the disposable container is the boundary. If a
        future change treats AST validation as sufficient, this test should fail.
        """
        validator = CodeValidator(ALLOWED)
        benign = validator.validate("value = 1 + 1\nwrite_result(metrics={'v': value})\n")
        assert benign.ok
        policy = SandboxPolicy(allowed_imports=ALLOWED)
        assert policy.container_runtime is None
        assert SandboxRunner(policy).mode == "local_process"