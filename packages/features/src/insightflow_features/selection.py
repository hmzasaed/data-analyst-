"""Feature selection: filter, rank, prune redundancy, and check the result.

1. **Filter** - near-constant columns carry no information.
2. **Rank** - three independent views of relevance to the target: mutual
   information (any relationship), absolute correlation (linear, regression and
   binary targets only) and random-forest impurity importance (non-linear,
   interactions). Each gives a rank; the combined rank is their mean.
3. **Prune** - walking down the ranking, a feature whose correlation with an
   already-kept feature exceeds `COLLINEAR_THRESHOLD` is redundant.
4. **Check** - k-fold cross-validation of the same model on every candidate and
   on the selected set, so the report can show whether selection cost accuracy.

Everything is seeded and single-threaded, so the same data gives the same result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from insightflow_contracts import CrossValidation, FeatureScore
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.feature_selection import mutual_info_classif, mutual_info_regression
from sklearn.model_selection import KFold, StratifiedKFold, cross_val_score

SEED = 0
MAX_MODEL_ROWS = 3_000
TREES = 80
FOLDS = 5
COLLINEAR_THRESHOLD = 0.95
NEAR_CONSTANT_SHARE = 0.99
METHODS = (
    "variance filter (a column whose most common value covers 99% or more of rows)",
    "mutual information (scikit-learn mutual_info_regression / mutual_info_classif)",
    "absolute Pearson correlation with the target (regression and two-class targets)",
    f"random-forest impurity importance ({TREES} trees, seeded)",
    f"redundancy pruning (|r| > {COLLINEAR_THRESHOLD} with a higher-ranked feature)",
    f"{FOLDS}-fold cross-validation of all candidates against the selected set",
)


@dataclass
class SelectionOutcome:
    scores: list[FeatureScore]
    selected: list[str]
    cross_validation: CrossValidation | None
    rows_used: int
    warnings: list[str]


def select_features(
    matrix: pd.DataFrame,
    target: pd.Series,
    task: str,
    *,
    max_features: int,
    origins: dict[str, str],
) -> SelectionOutcome:
    """Rank and select columns of `matrix` (numeric, complete) for predicting `target`."""
    warnings: list[str] = []
    keep = target.notna()
    features, y = matrix.loc[keep].reset_index(drop=True), target.loc[keep].reset_index(drop=True)
    if len(features) > MAX_MODEL_ROWS:
        sample = features.sample(n=MAX_MODEL_ROWS, random_state=SEED).index
        features, y = features.loc[sample].reset_index(drop=True), y.loc[sample].reset_index(drop=True)
        warnings.append(f"Ranking and cross-validation used a fixed random sample of {MAX_MODEL_ROWS:,} rows.")
    features = features.astype("float64")
    classification = task == "classification"
    labels = y.astype(str) if classification else y.astype("float64")

    status: dict[str, str] = {}
    for column in features.columns:
        share = features[column].value_counts(normalize=True, dropna=False).iloc[0] if len(features) else 1.0
        if share >= NEAR_CONSTANT_SHARE:
            status[column] = "near-constant"
    candidates = [c for c in features.columns if c not in status]

    mi: dict[str, float] = {}
    corr: dict[str, float | None] = {}
    importance: dict[str, float] = {}
    if candidates:
        data = features[candidates].to_numpy()
        mi_values = (mutual_info_classif if classification else mutual_info_regression)(
            data, labels, random_state=SEED
        )
        mi = dict(zip(candidates, (float(v) for v in mi_values), strict=True))
        numeric_y = _numeric_target(labels, classification)
        for column in candidates:
            corr[column] = _abs_corr(features[column], numeric_y) if numeric_y is not None else None
        model = _model(classification)
        model.fit(data, labels)
        importance = dict(zip(candidates, (float(v) for v in model.feature_importances_), strict=True))

    combined = _combined_rank(candidates, [mi, {k: v for k, v in corr.items() if v is not None}, importance])
    ordered = sorted(candidates, key=lambda c: (combined[c], c))

    selected: list[str] = []
    for column in ordered:
        if mi.get(column, 0.0) <= 1e-9 and importance.get(column, 0.0) < 1e-4:
            status[column] = "no measurable signal"
            continue
        twin = _redundant_with(features, column, selected)
        if twin:
            status[column] = f"redundant with {twin}"
            continue
        if len(selected) >= max_features:
            status[column] = "below cutoff"
            continue
        selected.append(column)
        status[column] = "selected"

    scores = [
        FeatureScore(
            feature=column,
            origin=origins.get(column, "encoded"),  # type: ignore[arg-type]
            mutual_information=_round(mi.get(column)),
            correlation=_round(corr.get(column)),
            importance=_round(importance.get(column)),
            combined_rank=combined.get(column),
            selected=column in selected,
            status=status.get(column, "below cutoff"),
        )
        for column in [*ordered, *[c for c in features.columns if c not in combined]]
    ]

    cv = _cross_validate(features, labels, selected, classification, warnings) if selected else None
    return SelectionOutcome(scores=scores, selected=selected, cross_validation=cv,
                            rows_used=len(features), warnings=warnings)


# --------------------------------------------------------------------- helpers
def _model(classification: bool):
    options = {"n_estimators": TREES, "random_state": SEED, "n_jobs": 1, "min_samples_leaf": 2}
    if classification:
        return RandomForestClassifier(class_weight="balanced", **options)
    return RandomForestRegressor(**options)


def _numeric_target(labels: pd.Series, classification: bool) -> pd.Series | None:
    if not classification:
        return labels
    classes = sorted(labels.unique())
    if len(classes) != 2:
        return None  # correlation with an arbitrary class coding is meaningless
    return (labels == classes[1]).astype("float64")


def _abs_corr(a: pd.Series, b: pd.Series) -> float | None:
    if a.std() == 0 or b.std() == 0:
        return None
    value = float(np.corrcoef(a.to_numpy(dtype="float64"), b.to_numpy(dtype="float64"))[0, 1])
    return abs(value) if math.isfinite(value) else None


def _redundant_with(features: pd.DataFrame, column: str, kept: list[str]) -> str | None:
    for other in kept:
        value = _abs_corr(features[column], features[other])
        if value is not None and value > COLLINEAR_THRESHOLD:
            return other
    return None


def _combined_rank(candidates: list[str], views: list[dict[str, float]]) -> dict[str, int]:
    """Mean of per-method ranks (1 = most relevant), as a dense integer ranking."""
    if not candidates:
        return {}
    ranks = pd.DataFrame(index=candidates)
    for index, view in enumerate(views):
        if view:
            series = pd.Series(view).reindex(candidates)
            ranks[f"m{index}"] = series.rank(ascending=False, method="average", na_option="bottom")
    mean = ranks.mean(axis=1) if not ranks.empty else pd.Series(0.0, index=candidates)
    order = sorted(candidates, key=lambda c: (mean[c], c))
    return {column: position for position, column in enumerate(order, start=1)}


def _cross_validate(features, labels, selected, classification, warnings) -> CrossValidation | None:
    if len(features) < 30:
        warnings.append("Fewer than 30 rows: cross-validation was skipped.")
        return None
    folds = FOLDS
    if classification:
        smallest = int(labels.value_counts().min())
        folds = min(FOLDS, smallest)
        if folds < 2:
            warnings.append("A class has a single row: cross-validation was skipped.")
            return None
        splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=SEED)
        metric = "balanced_accuracy"
    else:
        splitter = KFold(n_splits=folds, shuffle=True, random_state=SEED)
        metric = "r2"
    every = cross_val_score(_model(classification), features.to_numpy(), labels, cv=splitter, scoring=metric, n_jobs=1)
    chosen = cross_val_score(_model(classification), features[selected].to_numpy(), labels, cv=splitter,
                             scoring=metric, n_jobs=1)
    return CrossValidation(
        metric=metric, folds=folds,
        all_features_mean=_round(every.mean()), all_features_std=_round(every.std()),
        selected_mean=_round(chosen.mean()), selected_std=_round(chosen.std()),
        all_feature_count=int(features.shape[1]), selected_feature_count=len(selected),
        model=f"RandomForest{'Classifier' if classification else 'Regressor'} ({TREES} trees)",
    )


def _round(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return round(number, 6) if math.isfinite(number) else None
