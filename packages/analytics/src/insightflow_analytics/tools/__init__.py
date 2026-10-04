"""Registration of every approved analysis tool.

Adding a tool here is the only way to make an analysis available to the agent.
The catalogue is also what `docs/tool-catalog.md` is generated from.
"""

from __future__ import annotations

from insightflow_analytics.registry import ParameterSpec, ToolRegistry
from insightflow_analytics.tools import (
    anomaly_tools,
    comparison,
    descriptive,
    relationship,
    trend,
)

TOOL_VERSION = "1"

GRAINS = ("daily", "weekly", "monthly", "quarterly", "yearly")

ANOMALY_METHODS = (
    "auto",
    "iqr_fence",
    "robust_zscore",
    "rolling_mad",
    "group_percentile",
    "isolation_forest",
)


def build_registry() -> ToolRegistry:
    """Construct the registry with the full MVP catalogue."""
    registry = ToolRegistry()
    _register_descriptive(registry)
    _register_comparison(registry)
    _register_trend(registry)
    _register_relationship(registry)
    _register_anomaly(registry)
    return registry


def _register_descriptive(registry: ToolRegistry) -> None:
    registry.register(
        name="describe_measures",
        version=TOOL_VERSION,
        analysis_type="descriptive",
        summary="Count, sum, mean, median, spread and shape for each numeric column.",
        parameters=(
            ParameterSpec(
                name="columns",
                type="string_list",
                description="Numeric columns to summarise. Empty means every numeric column.",
            ),
            ParameterSpec(name="unit", type="string", description="Unit label for the values."),
        ),
        handler=descriptive.describe_measures,
        outputs=("metric", "table"),
        tags=("overview", "first_line"),
    )
    registry.register(
        name="rank_categories",
        version=TOOL_VERSION,
        analysis_type="descriptive",
        summary="Rank a categorical column by row count or by a measure, with share of total.",
        parameters=(
            ParameterSpec(
                name="group_by", type="category_column", required=True, description="Grouping column."
            ),
            ParameterSpec(
                name="measure", type="numeric_column", description="Optional measure to rank by."
            ),
            ParameterSpec(
                name="limit", type="integer", default=20, minimum=1, maximum=200,
                description="How many groups to return.",
            ),
            ParameterSpec(name="chart", type="boolean", default=True),
            ParameterSpec(name="unit", type="string"),
            ParameterSpec(name="title", type="string"),
        ),
        handler=descriptive.rank_categories,
        outputs=("metric", "table", "chart"),
        tags=("overview", "ranking"),
    )
    registry.register(
        name="missingness_profile",
        version=TOOL_VERSION,
        analysis_type="descriptive",
        summary="Per-column missing and distinct counts for the data-quality section.",
        parameters=(
            ParameterSpec(name="columns", type="string_list", description="Columns to check."),
        ),
        handler=descriptive.missingness_profile,
        outputs=("metric", "table"),
        tags=("quality",),
    )
    registry.register(
        name="value_distribution",
        version=TOOL_VERSION,
        analysis_type="distribution",
        summary="Equal-width histogram counts plus skewness for one numeric column.",
        parameters=(
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(name="bins", type="integer", default=30, minimum=4, maximum=200),
        ),
        handler=descriptive.value_distribution,
        outputs=("metric", "table", "chart"),
        tags=("distribution",),
    )

    registry.register(
        name="distribution_by_group",
        version=TOOL_VERSION,
        analysis_type="distribution",
        summary="Five-number summary and box plot of a measure per group, from raw values.",
        parameters=(
            ParameterSpec(name="group_by", type="category_column", required=True),
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(
                name="max_groups", type="integer", default=12, minimum=2, maximum=60,
                description="Plot at most this many groups.",
            ),
            ParameterSpec(name="unit", type="string"),
        ),
        handler=descriptive.distribution_by_group,
        outputs=("metric", "table", "chart"),
        timeout_seconds=180,
        tags=("distribution", "comparison"),
    )


def _register_comparison(registry: ToolRegistry) -> None:
    registry.register(
        name="compare_groups",
        version=TOOL_VERSION,
        analysis_type="comparison",
        summary="Compare a measure across groups, with Welch/Mann-Whitney test and effect size.",
        parameters=(
            ParameterSpec(name="group_by", type="category_column", required=True),
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(
                name="max_groups", type="integer", default=50, minimum=2, maximum=500,
                description="Refuse to compare more groups than this.",
            ),
            ParameterSpec(name="chart", type="boolean", default=True),
            ParameterSpec(name="unit", type="string"),
            ParameterSpec(name="title", type="string"),
        ),
        handler=comparison.compare_groups,
        outputs=("metric", "table", "chart"),
        timeout_seconds=180,
        tags=("comparison", "statistics"),
    )
    registry.register(
        name="share_of_total",
        version=TOOL_VERSION,
        analysis_type="comparison",
        summary="Contribution of each group to the overall total, with cumulative share.",
        parameters=(
            ParameterSpec(name="group_by", type="category_column", required=True),
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(name="chart", type="boolean", default=True),
            ParameterSpec(name="unit", type="string"),
            ParameterSpec(name="title", type="string"),
        ),
        handler=comparison.share_of_total,
        outputs=("metric", "table", "chart"),
        tags=("comparison", "composition"),
    )
    registry.register(
        name="missingness_by_group",
        version=TOOL_VERSION,
        analysis_type="comparison",
        summary="Share of missing cells within each group value.",
        parameters=(ParameterSpec(name="group_by", type="category_column", required=True),),
        handler=comparison.missingness_by_group,
        outputs=("metric", "table"),
        timeout_seconds=180,
        tags=("quality", "comparison"),
    )


def _register_trend(registry: ToolRegistry) -> None:
    registry.register(
        name="time_series_aggregate",
        version=TOOL_VERSION,
        analysis_type="trend",
        summary="Aggregate measures by day, week, month, quarter or year with period-on-period change.",
        parameters=(
            ParameterSpec(name="date_column", type="temporal_column", required=True),
            ParameterSpec(name="grain", type="enum", required=True, choices=GRAINS),
            ParameterSpec(
                name="measures", type="string_list", description="Numeric columns to aggregate."
            ),
            ParameterSpec(name="unit", type="string"),
            ParameterSpec(name="title", type="string"),
        ),
        handler=trend.time_series_aggregate,
        outputs=("metric", "table", "chart"),
        timeout_seconds=300,
        tags=("trend", "time"),
    )
    registry.register(
        name="trend_line",
        version=TOOL_VERSION,
        analysis_type="trend",
        summary="Fit a linear trend to an aggregated series and report slope, fit and caveats.",
        parameters=(
            ParameterSpec(name="date_column", type="temporal_column", required=True),
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(name="x_label", type="string", default="period"),
            ParameterSpec(name="unit", type="string"),
            ParameterSpec(name="title", type="string"),
        ),
        handler=trend.trend_line,
        outputs=("metric", "table", "chart"),
        tags=("trend", "statistics"),
    )
    registry.register(
        name="growth_rates",
        version=TOOL_VERSION,
        analysis_type="trend",
        summary="Period-over-period growth, counting periods where growth is undefined.",
        parameters=(
            ParameterSpec(name="date_column", type="temporal_column", required=True),
            ParameterSpec(name="column", type="numeric_column", required=True),
        ),
        handler=trend.growth_rates,
        outputs=("metric", "table"),
        tags=("trend",),
    )
    registry.register(
        name="moving_average",
        version=TOOL_VERSION,
        analysis_type="trend",
        summary="Rolling mean over an ordered series.",
        parameters=(
            ParameterSpec(name="date_column", type="temporal_column", required=True),
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(name="window", type="integer", default=3, minimum=2, maximum=90),
            ParameterSpec(name="unit", type="string"),
        ),
        handler=trend.moving_average,
        outputs=("metric", "table"),
        tags=("trend", "smoothing"),
    )
    registry.register(
        name="seasonality_strength",
        version=TOOL_VERSION,
        analysis_type="trend",
        summary="Assess whether a series is long enough to judge seasonality, and measure it.",
        parameters=(
            ParameterSpec(name="date_column", type="temporal_column", required=True),
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(
                name="seasonal_period", type="integer", default=12, minimum=2, maximum=365,
                description="Expected cycle length in periods (12 = annual for monthly data).",
            ),
            ParameterSpec(name="unit", type="string"),
        ),
        handler=trend.seasonality_strength,
        outputs=("metric", "table"),
        tags=("trend", "seasonality"),
    )


def _register_relationship(registry: ToolRegistry) -> None:
    registry.register(
        name="correlation_matrix",
        version=TOOL_VERSION,
        analysis_type="relationship",
        summary="Pearson correlations between numeric columns on complete rows.",
        parameters=(
            ParameterSpec(name="columns", type="string_list", required=True, description="Numeric columns."),
            ParameterSpec(name="limit", type="integer", default=10, minimum=1, maximum=100),
            ParameterSpec(name="chart", type="boolean", default=True),
        ),
        handler=relationship.correlation_matrix,
        outputs=("metric", "table", "chart"),
        timeout_seconds=300,
        tags=("relationship", "statistics"),
    )
    registry.register(
        name="cross_tabulation",
        version=TOOL_VERSION,
        analysis_type="relationship",
        summary="Two-way counts or totals between two categorical columns.",
        parameters=(
            ParameterSpec(name="row_dimension", type="category_column", required=True),
            ParameterSpec(name="column_dimension", type="category_column", required=True),
            ParameterSpec(name="measure", type="numeric_column", description="Optional measure to total."),
            ParameterSpec(name="chart", type="boolean", default=True),
            ParameterSpec(name="unit", type="string"),
            ParameterSpec(name="title", type="string"),
        ),
        handler=relationship.cross_tabulation,
        outputs=("metric", "table", "chart"),
        timeout_seconds=300,
        tags=("relationship", "composition"),
    )
    registry.register(
        name="simple_regression",
        version=TOOL_VERSION,
        analysis_type="relationship",
        summary="Least-squares fit of one measure on another, with residual and causality caveats.",
        parameters=(
            ParameterSpec(name="response", type="numeric_column", required=True),
            ParameterSpec(name="predictor", type="numeric_column", required=True),
            ParameterSpec(name="chart", type="boolean", default=True),
            ParameterSpec(name="unit", type="string"),
            ParameterSpec(name="title", type="string"),
        ),
        handler=relationship.simple_regression,
        outputs=("metric", "table", "chart"),
        timeout_seconds=300,
        tags=("relationship", "statistics"),
    )


def _register_anomaly(registry: ToolRegistry) -> None:
    registry.register(
        name="detect_outliers",
        version=TOOL_VERSION,
        analysis_type="anomaly",
        summary=(
            "Flag values in one measure that are unusual for the dataset as a whole. The "
            "method is chosen from the data unless one is named."
        ),
        parameters=(
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(
                name="method", type="enum", default="auto", choices=ANOMALY_METHODS,
                description="'auto' selects from sample size, shape and time ordering.",
            ),
            ParameterSpec(name="threshold", type="number", minimum=0.1, maximum=100.0),
            ParameterSpec(
                name="contamination", type="number", minimum=0.001, maximum=0.2,
                description="Expected outlier rate, used only by isolation forest.",
            ),
            ParameterSpec(
                name="label_columns", type="string_list",
                description="Columns copied into the evidence table to identify each record.",
            ),
            ParameterSpec(
                name="method_options", type="json_object", default={},
                description=(
                    "Method-specific options, for example {'window': 7} to set a rolling "
                    "baseline length."
                ),
            ),
            ParameterSpec(
                name="max_anomalies", type="integer", default=500, minimum=1, maximum=5000,
            ),
        ),
        handler=anomaly_tools.detect_outliers,
        outputs=("metric", "table", "chart"),
        timeout_seconds=300,
        tags=("anomaly", "quality"),
    )
    registry.register(
        name="detect_group_outliers",
        version=TOOL_VERSION,
        analysis_type="anomaly",
        summary=(
            "Flag values unusual relative to their own group, so a small region is not "
            "compared against the whole dataset."
        ),
        parameters=(
            ParameterSpec(name="column", type="numeric_column", required=True),
            ParameterSpec(name="group_by", type="string_list", required=True),
            ParameterSpec(name="threshold", type="number", minimum=0.1, maximum=100.0),
            ParameterSpec(name="label_columns", type="string_list"),
            ParameterSpec(name="max_anomalies", type="integer", default=500, minimum=1, maximum=5000),
        ),
        handler=anomaly_tools.detect_group_outliers,
        outputs=("metric", "table", "chart"),
        timeout_seconds=300,
        tags=("anomaly", "comparison"),
    )
    registry.register(
        name="detect_multivariate_outliers",
        version=TOOL_VERSION,
        analysis_type="anomaly",
        summary=(
            "Flag records that differ from the overall pattern across several measures at "
            "once. Results are harder to explain and are labelled accordingly."
        ),
        parameters=(
            ParameterSpec(
                name="features", type="string_list", required=True,
                description="Two or more numeric columns to consider together.",
            ),
            ParameterSpec(name="contamination", type="number", minimum=0.001, maximum=0.2),
            ParameterSpec(name="label_columns", type="string_list"),
            ParameterSpec(name="max_anomalies", type="integer", default=500, minimum=1, maximum=5000),
        ),
        handler=anomaly_tools.detect_multivariate_outliers,
        outputs=("metric", "table"),
        timeout_seconds=600,
        tags=("anomaly", "multivariate"),
    )


#: Process-wide registry instance used by the orchestrator and the API.
REGISTRY = build_registry()
