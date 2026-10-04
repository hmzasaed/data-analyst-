"""Preprocessing options and the record of what preprocessing did.

Preprocessing is separate from cleaning. Cleaning repairs the file (types,
whitespace, duplicates). Preprocessing prepares data for analysis and modelling,
and has two scopes that are kept strictly apart:

* **analysis** - optional changes to the data the report is computed from
  (imputation, dropping incomplete rows, capping outliers). Off by default,
  because they change the figures; every change is logged with its row count.
* **export** - a model-ready copy (scaled, encoded, with date features). It is a
  downloadable artifact and never feeds a reported figure, because scaled or
  one-hot values are meaningless as business numbers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from insightflow_contracts.profile import StrictModel

MissingNumeric = Literal["none", "median", "mean", "zero", "drop_rows"]
MissingCategorical = Literal["none", "mode", "constant"]
OutlierTreatment = Literal["none", "cap_iqr"]
Scaling = Literal["none", "standard", "minmax", "robust"]


class PreprocessingOptions(StrictModel):
    # --- analysis scope: changes the data every reported number comes from
    missing_numeric: MissingNumeric = "none"
    missing_categorical: MissingCategorical = "none"
    outliers: OutlierTreatment = "none"
    # --- export scope: the model-ready dataset only
    drop_columns_missing_above: float | None = Field(default=0.5, ge=0.0, le=1.0)
    scaling: Scaling = "standard"
    encode_categoricals: bool = True
    date_features: bool = True
    #: One-hot encoding is skipped for columns with more levels than this.
    max_encoding_levels: int = Field(default=20, ge=2, le=200)

    def changes_analysis(self) -> bool:
        return (
            self.missing_numeric != "none"
            or self.missing_categorical != "none"
            or self.outliers != "none"
        )


class PreprocessingStep(StrictModel):
    step_id: str
    scope: Literal["analysis", "export"]
    action: str
    column: str | None = None
    detail: str
    rows_affected: int = Field(default=0, ge=0)
    columns_added: list[str] = Field(default_factory=list)
    columns_removed: list[str] = Field(default_factory=list)


class DroppedColumn(StrictModel):
    column: str
    reason: str


class ExportSummary(StrictModel):
    artifact_id: str
    rows: int = Field(ge=0)
    columns: int = Field(ge=0)
    column_names: list[str] = Field(default_factory=list)
    scaling: str = "none"
    encoded_columns: list[str] = Field(default_factory=list)
    date_feature_columns: list[str] = Field(default_factory=list)
    dropped_columns: list[DroppedColumn] = Field(default_factory=list)


class PreprocessingResult(StrictModel):
    options: PreprocessingOptions = Field(default_factory=PreprocessingOptions)
    analysis_changed: bool = False
    rows_before: int = Field(default=0, ge=0)
    rows_after: int = Field(default=0, ge=0)
    columns_before: int = Field(default=0, ge=0)
    columns_after: int = Field(default=0, ge=0)
    steps: list[PreprocessingStep] = Field(default_factory=list)
    missing_before: dict[str, int] = Field(default_factory=dict)
    missing_after: dict[str, int] = Field(default_factory=dict)
    export: ExportSummary | None = None
    warnings: list[str] = Field(default_factory=list)
