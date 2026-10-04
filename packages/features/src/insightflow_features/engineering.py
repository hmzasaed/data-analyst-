"""Feature proposals, their validation, and their computation.

Proposals come from rules (here) or a language model (the agent). Either way a
proposal is a `FeatureSpec`, and `validate_spec` is the only door to becoming a
column: the kind must be registered, the inputs must exist with the right types,
personal data and identifiers cannot be inputs, and the target can never be one.
"""

from __future__ import annotations

import math
import re

import polars as pl
from insightflow_contracts import (
    DatasetProfile,
    EngineeredFeature,
    FeatureSpec,
    RejectedFeature,
)

from insightflow_features.columns import Columns

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")
_ARITY = {
    "log1p": 1, "square": 1, "date_ordinal": 1, "cyclical_month": 1, "frequency": 1,
    "missing_flag": 1, "ratio": 2, "difference": 2, "product": 2, "group_mean": 2,
}
_NUMERIC_KINDS = {"log1p", "square", "ratio", "difference", "product"}


def output_columns(spec: FeatureSpec) -> list[str]:
    """Columns a spec produces (cyclical month produces a sine and a cosine)."""
    if spec.kind == "cyclical_month":
        return [f"{spec.name}_sin", f"{spec.name}_cos"]
    return [spec.name]


# ------------------------------------------------------------------ proposals
def propose_features(
    frame: pl.DataFrame,
    profile: DatasetProfile | None,
    target: str | None,
    *,
    limit: int,
) -> list[FeatureSpec]:
    """Deterministic proposals, most broadly useful first."""
    columns = Columns(frame, profile)
    measures = [m for m in columns.measures() if m != target]
    ranked = sorted(measures, key=lambda m: (-frame.get_column(m).count(), measures.index(m)))
    categoricals = [c for c in columns.categoricals() if c != target]
    proposals: list[FeatureSpec] = []

    def add(name: str, kind: str, inputs: list[str], why: str) -> None:
        proposals.append(FeatureSpec(name=_name(name), kind=kind, inputs=inputs, rationale=why))

    for name in ranked:
        values = frame.get_column(name).cast(pl.Float64).drop_nulls()
        if values.len() > 10 and values.min() >= 0 and values.n_unique() > 10:
            skew = _skew(values)
            if skew is not None and skew > 1:
                add(f"log1p_{name}", "log1p", [name],
                    f"'{name}' is right-skewed (skew {skew:.2f}); a log scale tames large values.")
    top = ranked[:4]
    for i, left in enumerate(top):
        for right in top[i + 1:]:
            numerator, denominator = sorted(
                [left, right], key=lambda c: -abs(frame.get_column(c).cast(pl.Float64).mean() or 0)
            )
            share_non_zero = (frame.get_column(denominator) != 0).mean() or 0
            if share_non_zero >= 0.95:
                add(f"{numerator}_per_{denominator}", "ratio", [numerator, denominator],
                    f"Rate of '{numerator}' per unit of '{denominator}'.")
    for i, left in enumerate(ranked[:3]):
        for right in ranked[i + 1:3]:
            add(f"{left}_x_{right}", "product", [left, right],
                f"Interaction: lets a model see '{left}' and '{right}' acting together.")
    for name in ranked[:2]:
        add(f"{name}_squared", "square", [name], f"Curvature term for '{name}'.")
    for name in columns.dates():
        add(f"{name}_days_since_start", "date_ordinal", [name], f"Elapsed days, so a model can follow the trend in '{name}'.")
        add(f"{name}_month_cycle", "cyclical_month", [name],
            "Month as a point on a circle, so December sits next to January.")
    for name in categoricals[:3]:
        if frame.get_column(name).n_unique() >= 3:
            add(f"{name}_frequency", "frequency", [name], f"How common each '{name}' value is.")
    if categoricals:
        for measure in ranked[:2]:
            add(f"{measure}_mean_by_{categoricals[0]}", "group_mean", [measure, categoricals[0]],
                f"Typical '{measure}' within each '{categoricals[0]}'.")
    for name in frame.columns:
        if name == target or columns.is_personal(name) or columns.is_identifier(name):
            continue
        share = frame.get_column(name).null_count() / max(frame.height, 1)
        if 0.01 <= share <= 0.9:
            add(f"{name}_was_missing", "missing_flag", [name],
                f"{share:.0%} of '{name}' is missing; whether it is missing can itself be informative.")
    return proposals[: max(limit, 0)]


# ----------------------------------------------------------------- validation
def validate_spec(
    spec: FeatureSpec,
    frame: pl.DataFrame,
    profile: DatasetProfile | None,
    target: str | None,
    taken: set[str],
) -> str | None:
    """Return why `spec` is refused, or None when it may be computed."""
    columns = Columns(frame, profile)
    if not _SAFE_NAME.match(spec.name):
        return "name may only contain letters, digits, '_', '.' and '-'"
    if any(name in taken or name in frame.columns for name in output_columns(spec)):
        return "a column with this name already exists"
    if len(spec.inputs) != _ARITY[spec.kind]:
        return f"{spec.kind} takes {_ARITY[spec.kind]} input(s)"
    for name in spec.inputs:
        if name not in frame.columns:
            return f"input '{name}' is not a column"
        if target is not None and name == target:
            return "uses the target column, which would leak the answer"
        if columns.is_personal(name):
            return f"input '{name}' is personal data"
        if columns.is_identifier(name) and spec.kind != "missing_flag":
            return f"input '{name}' is an identifier"
    schema = frame.schema
    if spec.kind in _NUMERIC_KINDS and not all(schema[n].is_numeric() for n in spec.inputs):
        return f"{spec.kind} needs numeric inputs"
    if spec.kind == "log1p":
        low = frame.get_column(spec.inputs[0]).min()
        if low is not None and low < 0:
            return "log1p needs non-negative values"
    if spec.kind in {"date_ordinal", "cyclical_month"} and not schema[spec.inputs[0]].is_temporal():
        return f"{spec.kind} needs a date column"
    if spec.kind == "frequency" and schema[spec.inputs[0]].is_numeric():
        return "frequency encoding needs a categorical column"
    if spec.kind == "group_mean":
        measure, group = spec.inputs
        if not schema[measure].is_numeric() or schema[group].is_numeric() or schema[group].is_temporal():
            return "group_mean takes [numeric measure, categorical column]"
    if spec.kind == "missing_flag" and frame.get_column(spec.inputs[0]).null_count() == 0:
        return "the column has no missing values"
    return None


# ---------------------------------------------------------------- computation
def apply_features(
    frame: pl.DataFrame,
    specs: list[FeatureSpec],
    profile: DatasetProfile | None,
    target: str | None,
    *,
    source: dict[str, str] | None = None,
) -> tuple[pl.DataFrame, list[EngineeredFeature], list[RejectedFeature]]:
    """Validate and compute each spec; returns the widened frame and the record."""
    created: list[EngineeredFeature] = []
    rejected: list[RejectedFeature] = []
    taken: set[str] = set()
    working = frame
    for spec in specs:
        reason = validate_spec(spec, working.select(frame.columns), profile, target, taken)
        if reason:
            rejected.append(RejectedFeature(name=spec.name, kind=spec.kind, reason=reason))
            continue
        expressions = _expressions(spec)
        candidate = working.with_columns(expressions)
        outputs = output_columns(spec)
        values = candidate.select(outputs).to_series(0).cast(pl.Float64)
        values = values.filter(values.is_finite())
        if values.len() == 0 or values.n_unique() < 2:
            rejected.append(RejectedFeature(name=spec.name, kind=spec.kind, reason="constant or empty once computed"))
            continue
        working = candidate.with_columns(
            pl.when(pl.col(out).cast(pl.Float64).is_finite()).then(pl.col(out).cast(pl.Float64)).otherwise(None).alias(out)
            for out in outputs
        )
        taken.update(outputs)
        created.append(EngineeredFeature(
            spec=spec,
            source=(source or {}).get(spec.name, "rule"),
            non_missing=int(values.len()),
            mean=_finite(values.mean()),
            std=_finite(values.std()),
        ))
    return working, created, rejected


def _expressions(spec: FeatureSpec) -> list[pl.Expr]:
    a = spec.inputs[0]
    b = spec.inputs[1] if len(spec.inputs) > 1 else None
    x = pl.col(a).cast(pl.Float64)
    if spec.kind == "log1p":
        return [x.log1p().alias(spec.name)]
    if spec.kind == "square":
        return [(x * x).alias(spec.name)]
    if spec.kind in {"ratio", "difference", "product"}:
        y = pl.col(b).cast(pl.Float64)
        expression = {
            "ratio": pl.when(y != 0).then(x / y).otherwise(None),
            "difference": x - y,
            "product": x * y,
        }[spec.kind]
        return [expression.alias(spec.name)]
    if spec.kind == "date_ordinal":
        day = pl.col(a).cast(pl.Date)
        return [(day - day.min()).dt.total_days().cast(pl.Float64).alias(spec.name)]
    if spec.kind == "cyclical_month":
        month = pl.col(a).dt.month().cast(pl.Float64)
        angle = 2 * math.pi * (month - 1) / 12
        return [angle.sin().alias(f"{spec.name}_sin"), angle.cos().alias(f"{spec.name}_cos")]
    if spec.kind == "frequency":
        key = pl.col(a).cast(pl.String)
        return [(pl.len().over(key) / pl.len()).cast(pl.Float64).alias(spec.name)]
    if spec.kind == "group_mean":
        return [pl.col(a).cast(pl.Float64).mean().over(pl.col(b).cast(pl.String)).alias(spec.name)]
    if spec.kind == "missing_flag":
        return [pl.col(a).is_null().cast(pl.Float64).alias(spec.name)]
    raise ValueError(f"unregistered feature kind {spec.kind}")  # pragma: no cover - Literal-guarded


def _name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.\-]+", "_", text).strip("_")
    return cleaned[:120] or "feature"


def _skew(values: pl.Series) -> float | None:
    mean, std = values.mean(), values.std()
    if mean is None or not std:
        return None
    return float(((values - mean) ** 3).mean() / std**3)


def _finite(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None
