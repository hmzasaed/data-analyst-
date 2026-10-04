# Analysis tools

19 tools are registered in `insightflow_analytics.REGISTRY`. Each declares a
parameter schema, the column types it requires, a timeout, whether it mutates
data, whether it needs user approval, and whether it is deterministic.

Adding a tool means writing the function, decorating it with `@tool(...)`, and
adding a test. The registry handles validation, catalogue generation and planner
prompting.

## Conventions

- **Metrics name their table.** `outcome.metric(..., table="ranking_region")` is
  what lets a report figure be traced back to a specific evidence artifact.
- **Percentages are stored as percentages.** If a metric reports `42.5` meaning
  42.5%, the table has a column holding 42.5. The evaluation suite fails
  otherwise, which is deliberate: a reader should never have to guess a scale.
- **Denominators are stored too.** If a metric reports a total, the table
  contains that total.
- **Unnotable zero.** Metrics whose name ends in `_rate` or `_share` and whose
  value is exactly zero are not promoted to findings. Zero percent of revenue is
  not news.
- **Small groups are refused.** Statistical tests below the minimum group size
  raise `ToolExecutionError` rather than reporting a meaningless p-value.

## Descriptive

### `describe_measures`
`columns: string_list?, unit: string?`

Count, sum, mean, median, standard deviation, quartiles, skewness, missing count
and distinct count per numeric column. Writes one summary table plus an optional
distribution table per column.

### `missingness_profile`
`columns: string_list?`

Missing and distinct counts per column, plus a dataset-level missing share.
Feeds the data-quality section of the report.

### `rank_categories`
`group_by: category_column, measure: numeric_column?, limit: integer?, chart: boolean?, unit: string?, title: string?`

Ranks a categorical column by row count or by a measure. The table includes
`share`, `share_pct`, `cumulative_share`, `cumulative_share_pct` and
`observed_total` — share is computed against the whole dataset, not just the
ranked rows, so a truncated table still sums to a truthful fraction.

### `value_distribution`
`column: numeric_column, bins: integer?`

Equal-width histogram counts plus observed min, max and skewness, all in the same
table as the bins.

## Comparison

### `compare_groups`
`group_by: category_column, column: numeric_column, max_groups: integer?, chart: boolean?, unit: string?, title: string?`

Compares a measure across groups with per-group summary statistics, Welch's
t-test, effect size and confidence intervals. If the data deviates strongly from
normality, it switches to Mann-Whitney and records a warning explaining the
substitution.

### `share_of_total`
`group_by: category_column, column: numeric_column, chart: boolean?, unit: string?, title: string?`

Contribution of each group to the overall total with cumulative share. Refuses a
zero or near-zero total rather than dividing by it.

### `missingness_by_group`
`group_by: category_column`

Share of missing cells within each group value. Useful for spotting a group whose
data quality is worse than the rest.

## Trend

### `time_series_aggregate`
`date_column: temporal_column, grain: enum, measures: string_list?, unit: string?, title: string?`

Aggregates by day, week, month, quarter or year with period-on-period change.
Refuses a grain that would produce a single period, because a trend needs at
least two.

### `trend_line`
`date_column: temporal_column, column: numeric_column, x_label: string?, unit: string?, title: string?`

Least-squares fit to the aggregated series, reporting slope, R² and the caveats.
The fit is association, never causation.

### `moving_average`
`date_column: temporal_column, column: numeric_column, window: integer?, unit: string?`

Rolling mean. A window of 1 is refused. Uses a rolling median absolute deviation
baseline, which degrades honestly when the series has zero spread instead of
dividing by zero.

### `growth_rates`
`date_column: temporal_column, column: numeric_column`

Period-over-period growth. Periods where growth is undefined — a zero or missing
prior value, a negative base — are counted and reported, not silently dropped.

### `seasonality_strength`
`date_column: temporal_column, column: numeric_column, seasonal_period: integer?, unit: string?`

First checks whether the series is long enough to judge seasonality at all. If
not, it says so rather than computing a meaningless index.

## Relationship

### `correlation_matrix`
`columns: string_list?, limit: integer?, chart: boolean?`

Pearson correlations between numeric columns on complete rows. Writes full
precision so every reported coefficient can be checked against the table.
Non-numeric columns are dropped with a recorded note.

### `simple_regression`
`response: numeric_column, predictor: numeric_column, chart: boolean?, unit: string?, title: string?`

Least-squares fit with slope, intercept, R² and residual spread. Always carries
the causality caveat and notes correlation is not causation.

### `cross_tabulation`
`row_dimension: category_column, column_dimension: category_column, measure: numeric_column?, chart: boolean?, unit: string?, title: string?`

Two-way counts or totals. Useful for spotting a distribution that looks ordinary
until split by a second dimension.

## Distribution

### `distribution_by_group`
`group_by: category_column, column: numeric_column, max_groups: integer?, unit: string?`

Five-number summary and box plot per group, computed from raw values rather than
from pre-aggregated statistics.

## Anomaly

Anomaly findings are always framed defensively: a flagged record is unusual
against a stated baseline and warrants review. It is not an error and not
evidence of wrongdoing.

### `detect_outliers`
`column: numeric_column, method: enum?, threshold: number?, contamination: number?, label_columns: string_list?, method_options: json_object?, max_anomalies: integer?`

Flags values unusual for the dataset as a whole. Methods:

- `iqr` — Tukey fences, the default when nothing is specified.
- `robust_z` — median and MAD, robust to the outliers being sought.
- `rolling_mad` — against a trailing baseline, for time series.
- `isolation_forest` — multivariate, requires scikit-learn.

The method is chosen from the data unless one is named explicitly.

### `detect_group_outliers`
`column: numeric_column, group_by: string_list, threshold: number?, label_columns: string_list?, max_anomalies: integer?`

Flags values unusual *within their own group*. Without this, a small region is
compared against the whole dataset and every one of its values looks anomalous.
Fences are computed per group.

### `detect_multivariate_outliers`
`features: string_list, contamination: number?, label_columns: string_list?, max_anomalies: integer?`

Isolation Forest across several measures at once. Results are harder to explain
and are labelled as such in the report.

## Known environment limitation

scikit-learn's native extension cannot load under Windows Application Control in
this environment. `detect_multivariate_outliers` and the `isolation_forest`
method of `detect_outliers` raise `ToolExecutionError` with an explanation
rather than failing obscurely, and the other anomaly methods remain fully
available. See [operations.md](operations.md).