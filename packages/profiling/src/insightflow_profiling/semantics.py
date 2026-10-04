"""Business-meaning inference and PII detection.

Semantic roles come from column names and value shapes only. They are hints for
the planner, never facts: a column named `customer_id` is a *likely* identifier,
and the profile says so rather than asserting it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import polars as pl
from insightflow_contracts import PiiRisk, SemanticRole

# --------------------------------------------------------------------------- names

_ID_PATTERNS = (
    r"_id$",
    r"^id_",
    r"_key$",
    r"_uuid$",
    r"_guid$",
    r"_pk$",
    r"_code$",
    r"_number$",
    r"_no$",
    r"^order_id$",
)

_DATE_PATTERNS = (
    r"_date$",
    r"_at$",
    r"_time$",
    r"_timestamp$",
    r"_on$",
    r"^date$",
    r"^day$",
    r"^month$",
    r"^year$",
    r"^quarter$",
    r"^week$",
)

# Name patterns are matched with `(^|_)` so a bare column called `revenue` is
# recognised just like `gross_revenue`.
_MEASURE_NAMES = (
    "amount", "price", "cost", "costs", "revenue", "sales", "count", "total", "qty",
    "quantity", "rate", "ratio", "pct", "percent", "score", "margin", "balance",
    "spend", "value", "fee", "profit", "units", "weight", "duration", "age", "volume",
)

_DIMENSION_NAMES = (
    "name", "type", "status", "category", "segment", "channel", "source", "brand",
    "product", "team", "tier", "grade", "class", "group", "owner", "rep", "level",
)

_MEASURE_PATTERNS = tuple(rf"(^|_){name}$" for name in _MEASURE_NAMES)
_DIMENSION_PATTERNS = tuple(rf"(^|_){name}$" for name in _DIMENSION_NAMES)

_GEO_PATTERNS = (
    r"region",
    r"country",
    r"city",
    r"state$",
    r"province",
    r"territory",
    r"zip",
    r"postal",
    r"latitude",
    r"longitude",
    r"lat$",
    r"lon$",
    r"lng$",
)

_FLAG_PATTERNS = (
    r"^is_",
    r"^has_",
    r"_flag$",
    r"_indicator$",
    r"^active$",
    r"^enabled$",
    r"^deleted$",
)

_TEXT_PATTERNS = (r"name$", r"comment", r"note", r"description", r"address", r"message", r"body")

# ---------------------------------------------------------------------- PII names

PII_NAME_SIGNALS: dict[str, PiiRisk] = {
    "email": PiiRisk.HIGH,
    "e_mail": PiiRisk.HIGH,
    "phone": PiiRisk.HIGH,
    "mobile": PiiRisk.HIGH,
    "telephone": PiiRisk.HIGH,
    "ssn": PiiRisk.HIGH,
    "social_security": PiiRisk.HIGH,
    "national_id": PiiRisk.HIGH,
    "passport": PiiRisk.HIGH,
    "iban": PiiRisk.HIGH,
    "card_number": PiiRisk.HIGH,
    "credit_card": PiiRisk.HIGH,
    "cc_number": PiiRisk.HIGH,
    "date_of_birth": PiiRisk.HIGH,
    "dob": PiiRisk.HIGH,
    "birth_date": PiiRisk.HIGH,
    "address": PiiRisk.MEDIUM,
    "street": PiiRisk.MEDIUM,
    "postcode": PiiRisk.MEDIUM,
    "postal_code": PiiRisk.MEDIUM,
    "zip_code": PiiRisk.MEDIUM,
    "first_name": PiiRisk.MEDIUM,
    "last_name": PiiRisk.MEDIUM,
    "full_name": PiiRisk.MEDIUM,
    "customer_name": PiiRisk.MEDIUM,
    "ip_address": PiiRisk.MEDIUM,
    "salary": PiiRisk.MEDIUM,
    "income": PiiRisk.MEDIUM,
    "latitude": PiiRisk.LOW,
    "longitude": PiiRisk.LOW,
}

PII_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str], PiiRisk], ...] = (
    ("email_value", re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"), PiiRisk.HIGH),
    ("long_digit_run", re.compile(r"^\d{9,19}$"), PiiRisk.MEDIUM),
    ("ipv4_value", re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"), PiiRisk.MEDIUM),
    ("uk_nino_like", re.compile(r"^[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]$", re.I), PiiRisk.HIGH),
)

_SAMPLE_LIMIT = 200


@dataclass
class ColumnSemantics:
    role: SemanticRole
    pii_risk: PiiRisk
    pii_signals: list[str] = field(default_factory=list)
    rationale: str = ""


def _matches(name: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        if re.search(pattern, name):
            return pattern
    return None


def infer_role(name: str, *, distinct_count: int, row_count: int, inferred_type: str) -> SemanticRole:
    """Guess a business role from the column name and its cardinality."""
    lowered = name.lower()

    if _matches(lowered, _GEO_PATTERNS):
        return SemanticRole.GEOGRAPHY
    if _matches(lowered, _DATE_PATTERNS):
        return SemanticRole.TIME
    if _matches(lowered, _FLAG_PATTERNS):
        return SemanticRole.FLAG
    if _matches(lowered, _TEXT_PATTERNS):
        return SemanticRole.FREE_TEXT
    if _matches(lowered, _ID_PATTERNS):
        # A numeric-looking "id" with as many distinct values as rows is an id.
        if distinct_count >= max(row_count * 0.95, 2) and inferred_type not in {"category"}:
            return SemanticRole.IDENTIFIER
        return SemanticRole.IDENTIFIER

    if inferred_type in {"number", "integer"} or _matches(lowered, _MEASURE_PATTERNS):
        return SemanticRole.MEASURE
    if _matches(lowered, _DIMENSION_PATTERNS) or inferred_type == "category":
        return SemanticRole.CATEGORY
    if inferred_type == "text":
        return SemanticRole.FREE_TEXT
    return SemanticRole.UNKNOWN


def detect_pii(name: str, series: pl.Series) -> ColumnSemantics:
    """Assess PII risk from the column name and a bounded sample of its values."""
    lowered = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    signals: list[str] = []
    risk = PiiRisk.NONE

    for token, level in PII_NAME_SIGNALS.items():
        if token in lowered:
            signals.append(f"name contains '{token}'")
            risk = max(risk, level, key=_risk_order)
            break

    non_null = series.drop_nulls().head(_SAMPLE_LIMIT)
    if non_null.len():
        as_text = non_null.cast(pl.String, strict=False)
        sampled_values = as_text.to_list()
        for signal, pattern, level in PII_VALUE_PATTERNS:
            matched = sum(
                1 for value in sampled_values if isinstance(value, str) and pattern.match(value)
            )
            if matched:
                share = matched / non_null.len()
                if share >= 0.2:
                    signals.append(f"{share:.0%} of sampled values match {signal}")
                    risk = max(risk, level, key=_risk_order)

    return ColumnSemantics(role=SemanticRole.UNKNOWN, pii_risk=risk, pii_signals=signals)


def _risk_order(risk: PiiRisk) -> int:
    return [PiiRisk.NONE, PiiRisk.LOW, PiiRisk.MEDIUM, PiiRisk.HIGH].index(risk)
