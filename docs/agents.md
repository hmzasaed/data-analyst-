# Agents

InsightFlow's agents are deliberately constrained. Each one may *decide* or
*phrase* something; none of them produces a number that appears in a report.
Every agent has a deterministic path that runs when no model is configured, and
every model output is a schema-validated proposal that code checks before
anything executes.

| Agent | Stage | Decides | Model's role (optional) | Validation boundary | Prompt |
| --- | --- | --- | --- | --- | --- |
| `AnalysisPlanner` | `planned` | Which registered tools to run, with which columns | Proposes an `AnalysisPlan` | `PlanValidator`: tool exists, parameters match the schema and column types | `prompts/planner.md` |
| `CleaningExplainer` | `cleaning_proposed` | How to describe proposed cleaning | Rewrites explanations | Explanations only; operations come from code | `prompts/cleaning.md` |
| `FeatureEngineeringAgent` | `features_engineered` | Target column, new model features | Proposes a `FeaturePlan` | `validate_spec`: registered kind, real inputs of the right type, no target, no PII, no identifiers | `prompts/feature-engineer.md` |
| `FeatureSelectionAgent` | `features_selected` | Selection settings, explanation | Phrases the explanation | Numbers come from `select_features`; banned causal or absolute wording rejects the text | `prompts/feature-selector.md` |
| `FindingFactory` | `verified` | Which computed metrics become findings | – | Findings must cite a metric and its artifact | – |
| `VerificationAgent` | `verified` | Whether each finding stands | Optional language review | Re-reads the cited artifact, checks dataset version, contradictions, banned language | `prompts/verifier.md` |
| `ReportComposer` | `reported` | Report structure and prose | Writes the executive summary | Copies numbers from verified findings and stored tables only | `prompts/report-composer.md`, `prompts/report-style.md` |

**When is a model used?** Only when `INSIGHTFLOW_LLM_ENABLED=true`, an API key is
set, and, for the feature agents, the upload consented to sending a redacted
profile (`consent_third_party_llm`). The model sees the redacted profile (column
names, types, roles, aggregates), never raw rows. All calls are fail-soft: a
timeout, an HTTP error or an invalid response falls back to the deterministic
path, and the report says which path ran (`source`: `deterministic`, `model`,
`deterministic_fallback`).

---

## Feature engineering agent

`insightflow_agents.features.FeatureEngineeringAgent` works on a **copy** of the
analysed data. Engineered columns go into the downloadable feature set and never
into the data the analysis tools read, so no reported figure changes.

### 1. Choosing the target

In order, the first one that can be modelled (at least 20 non-missing rows and
two or more values):

1. the column you named in the **Target column** option;
2. a column your question names ("what predicts **revenue**");
3. the dataset's headline measure (names such as revenue, sales, amount, GDP);
4. the first measure or low-cardinality category.

Identifiers and personal-data columns are never targets. The task is
**regression** for a numeric target, and **classification** for a category, a
boolean, or a whole-number column with at most 10 distinct values (a rating, a
code). `100.0 / 123.5 / 147.0` stays regression however few values it has.

### 2. Proposing features

Rule-based proposals, most broadly useful first:

| Kind | Example | When |
| --- | --- | --- |
| `log1p` | `log1p_revenue` | Non-negative measure with skew above 1 |
| `ratio` | `revenue_per_units` | Pairs of the top four measures, denominator non-zero in 95%+ of rows |
| `product` | `units_x_unit_price` | Pairs of the top three measures (interactions) |
| `square` | `units_squared` | Top two measures (curvature) |
| `difference` | `cost_minus_discount` | Model proposals only |
| `date_ordinal` | `order_date_days_since_start` | Every date column |
| `cyclical_month` | `order_date_month_cycle_sin`/`_cos` | Every date column; December sits next to January |
| `frequency` | `region_frequency` | Categories with three or more values |
| `group_mean` | `units_mean_by_region` | Top two measures within the main category |
| `missing_flag` | `discount_was_missing` | Columns 1-90% missing |

With a model enabled and consented, the model's `FeaturePlan` is tried first and
the rule proposals fill in behind it. The budget is `max_engineered` (default 20).

### 3. Validating every proposal

`validate_spec` is the only door to becoming a column, for rules and models
alike. A proposal is **rejected, with its reason recorded in the report**, when:

- it uses the target as an input (it would leak the answer);
- an input does not exist, is personal data, or is an identifier;
- the input types do not fit the kind (arithmetic needs numbers, date kinds
  need dates, `group_mean` needs `[measure, category]`);
- `log1p` would see negative values;
- the name is unsafe or already taken;
- the computed column is constant or empty.

---

## Feature selection agent

`insightflow_agents.features.FeatureSelectionAgent` builds the model matrix with
the same encoding, imputation and scaling as the model-ready export, then runs
`insightflow_features.select_features`:

1. **Variance filter.** A column whose most common value covers 99% or more of
   rows is dropped as near-constant.
2. **Ranking.** Three independent relevance scores, each turned into a rank:
   - mutual information (`mutual_info_regression` / `mutual_info_classif`);
   - absolute Pearson correlation with the target (regression and two-class
     targets; it is meaningless for an arbitrary multi-class coding);
   - random-forest impurity importance (80 trees, seeded).

   The **combined rank** is the mean of the three ranks.
3. **Redundancy pruning.** Walking down the ranking, a feature whose |r| with an
   already-kept feature exceeds 0.95 is dropped as `redundant with <feature>`.
4. **No signal.** Zero mutual information and negligible importance means the
   feature is dropped.
5. **Cutoff.** The best `max_features` (default 15) survivors are selected.
6. **Cross-validation.** 5-fold (stratified for classification) cross-validation
   of the same random forest on *all candidates* and on the *selected set*:
   R² for regression, balanced accuracy for classification. The report shows
   both, so you can see whether selection cost accuracy.

Everything is seeded and single-threaded, so the same data gives the same
result. Ranking and cross-validation use a fixed random sample when there are
more than 3,000 rows, and the report says so.

The agent then writes a plain-language explanation: deterministic by default,
model-written when a model is enabled. Model text that uses causal or absolute
wording ("drives", "proves") is refused, not edited.

### Outputs

| Output | Where |
| --- | --- |
| Feature set CSV (selected features + target) | Features tab → **Download feature set**, or `GET /api/v1/jobs/{id}/artifacts/feature_set` |
| Full ranking CSV | `GET /api/v1/jobs/{id}/artifacts/feature_scores` |
| Engineering and selection records | `GET /api/v1/jobs/{id}/features` |
| Report section | **Feature engineering and selection**: engineered features, rejected proposals, ranking heatmap, ranking table, cross-validation chart and table |

### Options

Sent as the `features` JSON form field of `POST /api/v1/jobs`, or set in the
**Feature engineering & selection** panel of the New analysis form:

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Run both stages; when `false` they record "skipped" |
| `target_column` | `null` | Column to predict; `null` lets the agent choose |
| `max_features` | `15` | Upper bound on selected features (1-100) |
| `max_engineered` | `20` | Upper bound on engineered features (0-100) |

### Resuming

Both stages are resumable across processes. On resume, the recorded feature
specs are **re-applied** rather than re-proposed, so a model is not asked again
and the feature set is reproduced exactly.
