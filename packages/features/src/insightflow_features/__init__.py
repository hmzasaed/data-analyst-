"""Feature engineering and feature selection building blocks.

The agents in `insightflow_agents.features` decide *what* to do; this package
does it: validated transforms, the model matrix, ranking and cross-validation.
"""

from insightflow_features.charts import FeatureChart, render_feature_charts
from insightflow_features.engineering import (
    apply_features,
    output_columns,
    propose_features,
    validate_spec,
)
from insightflow_features.matrix import build_matrix
from insightflow_features.selection import METHODS, SelectionOutcome, select_features
from insightflow_features.target import TargetChoice, choose_target, task_for

__all__ = [
    "METHODS",
    "FeatureChart",
    "SelectionOutcome",
    "TargetChoice",
    "apply_features",
    "build_matrix",
    "choose_target",
    "output_columns",
    "propose_features",
    "render_feature_charts",
    "select_features",
    "task_for",
    "validate_spec",
]
