"""Static validation of generated Python.

This is a *defence in depth* layer, not the security boundary. It removes the
obvious escapes early and cheaply; the real boundary is the disposable container or
microVM described in `docs/threat-model.md`. Never treat passing AST validation as
evidence that code is safe to run on the host.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from insightflow_contracts import SandboxViolationError

#: Root modules that may never be imported, regardless of policy.
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "atexit",
        "asyncio",
        "bdb",
        "builtins",
        "code",
        "codeop",
        "ctypes",
        "curses",
        "dis",
        "faulthandler",
        "fcntl",
        "ftplib",
        "gc",
        "glob",
        "http",
        "imaplib",
        "importlib",
        "inspect",
        "marshal",
        "multiprocessing",
        "operator",
        "os",
        "pathlib",
        "pdb",
        "pickle",
        "platform",
        "plistlib",
        "pty",
        "requests",
        "resource",
        "runpy",
        "shelve",
        "shutil",
        "signal",
        "site",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "subprocess",
        "sys",
        "tempfile",
        "telnetlib",
        "traceback",
        "types",
        "urllib",
        "webbrowser",
        "xmlrpc",
    }
)

#: Callables that are never allowed, whatever the import allowlist says.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "input",
        "locals",
        "memoryview",
        "open",
        "quit",
        "setattr",
        "vars",
    }
)

#: Dunder and private attributes used for sandbox escapes.
FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__bases__",
        "__builtins__",
        "__class__",
        "__code__",
        "__dict__",
        "__closure__",
        "__func__",
        "__globals__",
        "__getattribute__",
        "__init_subclass__",
        "__loader__",
        "__mro__",
        "__reduce__",
        "__reduce_ex__",
        "__self__",
        "__subclasses__",
        "f_globals",
        "f_locals",
        "func_globals",
        "gi_frame",
        "gi_code",
        "cr_frame",
        "tb_frame",
    }
)

#: String literals that indicate an attempted escape rather than analysis.
SUSPICIOUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"__subclasses__", "walks the class hierarchy to reach builtins"),
    (r"__globals__", "reaches a function's global namespace"),
    (r"__builtins__", "reaches the builtins module"),
    (r"os\.system", "invokes a shell"),
    (r"subprocess\.run", "spawns a process"),
    (r"socket\.socket", "opens a network socket"),
    (r"shutil\.rmtree", "deletes files"),
    (r"importlib\.import_module", "imports at runtime"),
)

#: Hard ceiling on generated code size, before it is even parsed.
MAX_SOURCE_BYTES = 200_000
MAX_LINES = 5_000
MAX_AST_NODES = 60_000


@dataclass
class ValidationReport:
    """Result of static validation. `ok=False` means the code must not run."""

    ok: bool
    imports: set[str] = field(default_factory=set)
    functions: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def raise_if_invalid(self, source: str | None = None) -> None:
        if self.ok:
            return
        raise SandboxViolationError(
            "generated code failed static validation",
            violations=self.violations[:20],
            violation_count=len(self.violations),
            source_excerpt=(source or "")[:500] if source else None,
        )


class CodeValidator:
    """AST allowlist validator for generated analysis code."""

    def __init__(self, allowed_imports: frozenset[str] | set[str] | tuple[str, ...]) -> None:
        self.allowed_imports = frozenset(allowed_imports)

    def validate(self, source: str) -> ValidationReport:
        report = ValidationReport(ok=False)

        if not source or not source.strip():
            report.violations.append("source is empty")
            return report
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            report.violations.append(f"source exceeds {MAX_SOURCE_BYTES} bytes")
            return report
        if source.count("\n") > MAX_LINES:
            report.violations.append(f"source exceeds {MAX_LINES} lines")
            return report

        try:
            tree = ast.parse(source, filename="<generated>", mode="exec")
        except SyntaxError as exc:
            report.violations.append(f"syntax error on line {exc.lineno}: {exc.msg}")
            return report

        if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
            report.violations.append(f"source exceeds {MAX_AST_NODES} AST nodes")
            return report

        self._check_imports(tree, report)
        self._check_names(tree, report)
        self._check_attributes(tree, report)
        self._check_literal_patterns(source, report)
        self._check_suspicious_structures(tree, report)

        report.ok = not report.violations
        return report

    # ------------------------------------------------------------------ rules
    def _check_imports(self, tree: ast.Module, report: ValidationReport) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_module(alias.name, report)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    report.violations.append("relative imports are not allowed")
                    continue
                module = node.module or ""
                self._check_module(module, report)
                for alias in node.names:
                    full = f"{module}.{alias.name}" if module else alias.name
                    if full.split(".")[0] not in self.allowed_imports and alias.name != "*":
                        report.violations.append(
                            f"import of '{full}' is not in the allowlist "
                            f"({sorted(self.allowed_imports)})"
                        )

    def _check_module(self, module: str, report: ValidationReport) -> None:
        root = module.split(".", maxsplit=1)[0]
        if root in FORBIDDEN_MODULES:
            report.violations.append(f"import of '{module}' is forbidden")
            return
        if root not in self.allowed_imports:
            report.violations.append(
                f"import of '{module}' is not in the allowlist ({sorted(self.allowed_imports)})"
            )
        else:
            report.imports.add(root)

    def _check_names(self, tree: ast.Module, report: ValidationReport) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                report.violations.append(f"use of forbidden name '{node.id}'")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                    report.violations.append(f"call to forbidden function '{func.id}'")
                elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_NAMES:
                    report.violations.append(f"call to forbidden method '{func.attr}'")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                report.functions.append(node.name)
                if node.name.startswith("__") and node.name.endswith("__"):
                    report.violations.append(
                        f"definition of dunder function '{node.name}' is not allowed"
                    )
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("__") and node.attr.endswith("__"):
                    report.violations.append(f"access to dunder attribute '{node.attr}'")
                if node.attr in FORBIDDEN_ATTRIBUTES:
                    report.violations.append(f"access to escape attribute '{node.attr}'")

    def _check_attributes(self, tree: ast.Module, report: ValidationReport) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
                report.violations.append(f"access to escape attribute '{node.attr}'")

    def _check_literal_patterns(self, source: str, report: ValidationReport) -> None:
        import re

        for pattern, description in SUSPICIOUS_PATTERNS:
            if re.search(pattern, source):
                report.violations.append(
                    f"source mentions '{pattern}', which {description}"
                )

    def _check_suspicious_structures(self, tree: ast.Module, report: ValidationReport) -> None:
        """Reject constructs with no legitimate use in an analysis script."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                report.violations.append("global declarations are not allowed")
            elif isinstance(node, ast.Nonlocal):
                report.violations.append("nonlocal declarations are not allowed")
            elif isinstance(node, ast.With):
                for item in node.items:
                    if isinstance(item.context_expr, ast.Call):
                        func = item.context_expr.func
                        if isinstance(func, ast.Name) and func.id in {"open", "eval", "exec"}:
                            report.violations.append(
                                f"with-statement using '{func.id}' is not allowed"
                            )
            elif isinstance(node, ast.Try) and any(
                isinstance(handler.type, ast.Name) and handler.type.id == "BaseException"
                for handler in node.handlers
            ):
                report.violations.append(
                    "catching BaseException is not allowed; it can swallow kills"
                )
