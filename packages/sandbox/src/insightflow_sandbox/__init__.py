"""Restricted execution of generated analysis code.

Three layers, in order: static AST validation, a disposable runtime with resource
limits and no network, and a closed schema on what comes back. The disposable
runtime is the security boundary; the AST check is a cheap first filter.
"""

from insightflow_sandbox.policy import (
    INPUT_DATA_NAME,
    INPUT_DIR,
    OUTPUT_DIR,
    RESULT_KEYS,
    SandboxPolicy,
    load_policy_overrides,
    policy_from_settings,
    tighten,
)
from insightflow_sandbox.runner import (
    SandboxResult,
    SandboxRunner,
    SandboxWorkspace,
    validate_result_payload,
)
from insightflow_sandbox.validator import (
    FORBIDDEN_ATTRIBUTES,
    FORBIDDEN_MODULES,
    FORBIDDEN_NAMES,
    CodeValidator,
    ValidationReport,
)

__all__ = [
    "FORBIDDEN_ATTRIBUTES",
    "FORBIDDEN_MODULES",
    "FORBIDDEN_NAMES",
    "INPUT_DATA_NAME",
    "INPUT_DIR",
    "OUTPUT_DIR",
    "RESULT_KEYS",
    "CodeValidator",
    "SandboxPolicy",
    "SandboxResult",
    "SandboxRunner",
    "SandboxWorkspace",
    "ValidationReport",
    "load_policy_overrides",
    "policy_from_settings",
    "runner_from_settings",
    "tighten",
    "validate_result_payload",
]


def runner_from_settings(*, workdir=None) -> SandboxRunner:
    """Build a runner from application settings, applying any policy overrides."""
    from insightflow_contracts import get_settings

    settings = get_settings()
    policy = policy_from_settings(settings.sandbox)
    overrides = load_policy_overrides(settings.artifact_path("policies", "sandbox.json"))
    return SandboxRunner(tighten(policy, overrides), workdir=workdir)
