"""Redaction policy applied before any dataset-derived text reaches a model."""

from __future__ import annotations

import re

from pydantic import Field, PrivateAttr

from insightflow_contracts.profile import StrictModel


class RedactionRule(StrictModel):
    rule_id: str
    name: str
    pattern: str
    replacement: str = "[REDACTED]"
    applies_to_columns: list[str] = Field(default_factory=list)
    applies_to_pii_risk: list[str] = Field(default_factory=list)
    enabled: bool = True

    _compiled: re.Pattern[str] | None = PrivateAttr(default=None)

    def compiled(self) -> re.Pattern[str]:
        cached = self._compiled
        if cached is None:
            cached = re.compile(self.pattern, flags=re.IGNORECASE)
            self._compiled = cached
        return cached

    def scrub(self, text: str) -> str:
        if not self.enabled:
            return text
        return self.compiled().sub(self.replacement, text)


class RedactionPolicy(StrictModel):
    """Built-in rules plus a hard floor on what may be shown to an external model."""

    rules: list[RedactionRule] = Field(default_factory=list)
    mask_high_pii_columns: bool = True
    max_sample_values: int = Field(default=10, ge=0, le=100)
    redact_free_text_samples: bool = True

    def rules_for(self, *, column: str | None, pii_risk: str | None) -> list[RedactionRule]:
        """Rules in scope for a column and risk level.

        A rule that declares neither a column nor a risk level is unscoped and
        always applies - that is how a secret-shaped token rule is guaranteed to
        fire on free text.
        """
        matched: list[RedactionRule] = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            if not rule.applies_to_columns and not rule.applies_to_pii_risk:
                matched.append(rule)
                continue
            if rule.applies_to_columns and column in rule.applies_to_columns:
                matched.append(rule)
                continue
            if rule.applies_to_pii_risk and pii_risk in rule.applies_to_pii_risk:
                matched.append(rule)
        return matched

    def scrub(self, text: str, *, column: str | None = None, pii_risk: str | None = None) -> str:
        for rule in self.rules_for(column=column, pii_risk=pii_risk):
            text = rule.scrub(text)
        return text


def default_policy() -> RedactionPolicy:
    """Default deny-ish policy: mask high-risk columns and scrub obvious secrets."""
    return RedactionPolicy(
        rules=[
            RedactionRule(
                rule_id="email",
                name="Email address",
                pattern=r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
                applies_to_pii_risk=["high", "medium"],
            ),
            RedactionRule(
                rule_id="long_digits",
                name="Long digit runs (card / account like)",
                pattern=r"\b(?:\d[ -]?){11,19}\b",
                applies_to_pii_risk=["high"],
            ),
            RedactionRule(
                rule_id="ipv4",
                name="IPv4 address",
                pattern=r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
                applies_to_pii_risk=["high"],
            ),
            RedactionRule(
                rule_id="bearer",
                name="Bearer / API token",
                pattern=r"\b(?:bearer\s+[A-Za-z0-9._-]{8,}|sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,})\b",
            ),
        ],
        mask_high_pii_columns=True,
    )
