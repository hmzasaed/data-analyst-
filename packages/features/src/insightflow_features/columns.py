"""Column roles as the feature stages see them."""

from __future__ import annotations

import re

import polars as pl
from insightflow_contracts import ColumnProfile, DatasetProfile, SemanticRole

#: Numeric codes that are labels, not quantities.
CODE_NAME = re.compile(r"(^|_)(id|code|key|zip|zipcode|postcode|postal_code)$")
#: Names that usually mark the headline business measure.
HEADLINE_HINTS = ("revenue", "sales", "amount", "total", "profit", "gdp", "income", "value",
                  "price", "cost", "spend", "quantity", "units")
_CATEGORICAL_ROLES = {SemanticRole.DIMENSION, SemanticRole.CATEGORY, SemanticRole.GEOGRAPHY,
                      SemanticRole.FLAG}


class Columns:
    """Classifies a frame's columns using its profile; unprofiled columns go by dtype."""

    def __init__(self, frame: pl.DataFrame, profile: DatasetProfile | None) -> None:
        self.frame = frame
        self.profiled: dict[str, ColumnProfile] = {c.name: c for c in (profile.columns if profile else [])}

    def column(self, name: str) -> ColumnProfile | None:
        return self.profiled.get(name)

    def is_personal(self, name: str) -> bool:
        column = self.column(name)
        return column is not None and column.pii_risk.value in {"medium", "high"}

    def is_identifier(self, name: str) -> bool:
        column = self.column(name)
        return bool(CODE_NAME.search(name.lower())) or (
            column is not None and column.semantic_role is SemanticRole.IDENTIFIER
        )

    def measures(self) -> list[str]:
        names = []
        for name, dtype in self.frame.schema.items():
            column = self.column(name)
            if not dtype.is_numeric() or self.is_personal(name) or self.is_identifier(name):
                continue
            if column is not None and column.semantic_role in {SemanticRole.TIME, SemanticRole.FREE_TEXT}:
                continue
            if self.frame.get_column(name).drop_nulls().n_unique() < 2:
                continue
            names.append(name)
        return names

    def categoricals(self, *, max_levels: int = 500) -> list[str]:
        names = []
        for name, dtype in self.frame.schema.items():
            if dtype.is_numeric() or dtype.is_temporal() or self.is_personal(name) or self.is_identifier(name):
                continue
            column = self.column(name)
            if column is not None and column.semantic_role not in _CATEGORICAL_ROLES:
                continue
            levels = self.frame.get_column(name).n_unique()
            if 2 <= levels <= max_levels:
                names.append(name)
        return names

    def dates(self) -> list[str]:
        return [name for name, dtype in self.frame.schema.items() if dtype.is_temporal()]

    def headline_measure(self, candidates: list[str]) -> str | None:
        if not candidates:
            return None

        def rank(name: str) -> tuple[int, int, int]:
            lowered = name.lower()
            hint = next((i for i, word in enumerate(HEADLINE_HINTS) if word in lowered), len(HEADLINE_HINTS))
            return (-hint, self.frame.get_column(name).count(), -candidates.index(name))

        return max(candidates, key=rank)
