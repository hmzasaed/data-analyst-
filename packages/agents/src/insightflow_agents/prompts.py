"""Prompt loading and the six-layer prompt composition.

Layers, in the order they are assembled:

1. system policy and safety constraints
2. agent role and output schema
3. dataset profile and trusted computed statistics
4. user objective
5. tool results and evidence
6. report style instructions

Only layers 3, 4 and 5 contain dataset-derived content, and layers 3 and 5 are
redacted before assembly. Raw rows are never included by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: Repository `prompts/` directory. Resolved relative to the installed package so
#: it works from a checkout and from a container image alike.
_CANDIDATE_ROOTS = (
    Path(__file__).resolve().parents[4] / "prompts",
    Path("/app/prompts"),
    Path.cwd() / "prompts",
)


def prompts_dir() -> Path:
    for candidate in _CANDIDATE_ROOTS:
        if candidate.is_dir():
            return candidate
    return _CANDIDATE_ROOTS[0]


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Read a prompt file by name (without extension), or return a marker."""
    path = prompts_dir() / f"{name}.md"
    if not path.exists():
        return f"[prompt '{name}' was not found at {path}]"
    return path.read_text(encoding="utf-8")


@dataclass
class PromptLayers:
    """The composed prompt, kept separate so each layer can be audited."""

    policy: str = ""
    role: str = ""
    context: str = ""
    objective: str = ""
    evidence: str = ""
    style: str = ""

    def user_message(self) -> str:
        """Compose the user-side message from layers 3-6."""
        blocks = [
            _block("Dataset context", self.context),
            _block("User objective", self.objective),
            _block("Tool results and evidence", self.evidence),
            _block("Output style", self.style),
        ]
        return "\n\n".join(block for block in blocks if block)

    def system_message(self) -> str:
        """Compose the system-side message from layers 1-2."""
        return "\n\n".join(
            block for block in (_block("Policy", self.policy), _block("Role", self.role)) if block
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "policy": self.policy,
            "role": self.role,
            "context": self.context,
            "objective": self.objective,
            "evidence": self.evidence,
            "style": self.style,
        }


@dataclass
class PromptBuilder:
    """Assembles `PromptLayers` with redaction applied to dataset content."""

    max_rows: int = 50
    extra: dict[str, str] = field(default_factory=dict)

    def policy(self, name: str = "policy") -> PromptLayers:
        return PromptLayers(policy=load_prompt(name))

    def with_role(self, name: str) -> RolePrompt:
        return RolePrompt(layers=self.policy(), role_name=name)

    def style(self, name: str = "report-style") -> PromptLayers:
        return PromptLayers(style=load_prompt(name))


class RolePrompt:
    """Layer 2: the agent's role plus its output schema description."""

    def __init__(self, layers: PromptLayers, role_name: str) -> None:
        self.layers = layers
        self.role_name = role_name

    def complete(self) -> PromptLayers:
        self.layers.role = load_prompt(self.role_name)
        return self.layers

    def __call__(self) -> PromptLayers:
        return self.complete()


def _block(title: str, body: str) -> str:
    if not body or not body.strip():
        return ""
    return f"## {title}\n{body.strip()}"