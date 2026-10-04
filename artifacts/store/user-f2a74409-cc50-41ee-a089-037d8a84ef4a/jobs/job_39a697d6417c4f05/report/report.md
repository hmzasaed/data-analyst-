# Analysis of title.basics

**File:** `title.basics.csv`

> This is an **exploratory** report: the user did not ask a specific question, so the analysis is general and not targeted at a decision.

| Rows | Columns | Missing cells | Duplicate rows | Preprocessing | Verified findings | Selected features | Charts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000 | 9 | 19.7% | 0 | export only | 34 | 12 | 17 |

## Contents

1. Executive summary
2. Dataset overview
3. Data-quality assessment
4. Data preprocessing
5. Descriptive statistics
6. Distributions
7. Relationships between measures
8. Trends and segments
9. Feature engineering and selection
10. Key findings
11. Anomalies
12. Methodology
13. Limitations
14. Suggested next questions
15. Reproducibility

## 1. Executive summary

This report covers 1,000,000 rows and 9 columns. It was produced in response to this exploratory overview. 34 finding(s) were verified against computed values, 12 of them at medium or high importance. Each finding below links to the artifact it came from.

### Key findings
- **worst missing column (missingness_profile)** — The least complete column in this dataset is end_year. (confidence: high)
- **worst missing share (missingness_profile)** — That column has no usable values in 98.7% of rows. (confidence: high)
- **title_type: top share (rank_categories)** — The leading group value accounts for 74.0% of the total. (confidence: high)
- **top3 share (share_of_total)** — The top groups account for 100.0% of the total. (confidence: high)
- **contribution concentration (share_of_total)** — Contribution is concentrated: the leading groups hold 100.0% of the total. (confidence: high)

**Most important data-quality caveat:** `end_year` is missing in 98.7% of rows, so any figure involving it is calculated on partial data.

## 2. Dataset overview

**Source file:** title.basics.csv
**Dimensions:** 1,000,000 rows x 9 columns
**Column types:** tconst (integer), title_type (category), primary_title (text), original_title (text), is_adult (boolean), start_year (integer), end_year (integer), runtime_minutes (integer), genres (text)
**Key measures:** tconst, start_year, end_year, runtime_minutes
**Grouping fields:** title_type

### Column overview

| Column | Type | Role | Missing | Missing % | Unique values | PII risk |
| --- | --- | --- | ---: | ---: | ---: | --- |
| tconst | integer | measure | 0 | 0.0% | 1,000,000 | none |
| title_type | category | category | 0 | 0.0% | 10 | none |
| primary_title | text | free text | 9 | 0.0% | 560,663 | none |
| original_title | text | free text | 9 | 0.0% | 562,406 | none |
| is_adult | boolean | flag | 0 | 0.0% | 5 | none |
| start_year | integer | measure | 115,785 | 11.6% | 145 | none |
| end_year | integer | measure | 987,299 | 98.7% | 87 | none |
| runtime_minutes | integer | measure | 624,436 | 62.4% | 523 | none |
| genres | text | free text | 42,268 | 4.2% | 1,794 | none |

_Types and roles are inferred from values and column names. Source: `eda_column_overview.csv`._

## 3. Data-quality assessment

**Missing values**
- `end_year`: 98.7% of rows
- `runtime_minutes`: 62.4% of rows
- `start_year`: 11.6% of rows
- `genres`: 4.2% of rows

**Other issues detected**
- [medium] `primary_title` 558866 normalised forms cover 560663 raw values: casing and whitespace differ between otherwise identical labels.
- [medium] `primary_title` 560,662 distinct values across 1,000,000 rows; unlikely to be a usable grouping key.
- [medium] `original_title` 560608 normalised forms cover 562406 raw values: casing and whitespace differ between otherwise identical labels.
- [medium] `original_title` 562,405 distinct values across 1,000,000 rows; unlikely to be a usable grouping key.
- [medium] `start_year` 115,785 of 1,000,000 rows (11.6%) have no value.
- [high] `end_year` 987,299 of 1,000,000 rows (98.7%) have no value.
- [high] `runtime_minutes` 624,436 of 1,000,000 rows (62.4%) have no value.
- [medium] `runtime_minutes` 3 values are infinite or not a number and will break arithmetic.

**Cleaning applied**

| Change | Rows affected | Reason | Reversible |
| --- | --- | --- | --- |
| parse_number on `tconst` | 1,000,000 | Parsed 'tconst' to a number with no loss of values. | no (restore from raw) |
| trim_whitespace on `primary_title` | 0 | Trimmed leading and trailing whitespace in 'primary_title'. | yes |
| normalise_categories on `primary_title` | 1,940 | 'primary_title' has 560662 raw labels for 558866 distinct values once case and whitespace are normalised. | yes |
| trim_whitespace on `original_title` | 0 | Trimmed leading and trailing whitespace in 'original_title'. | yes |
| normalise_categories on `original_title` | 1,942 | 'original_title' has 562405 raw labels for 560608 distinct values once case and whitespace are normalised. | yes |
| parse_number on `start_year` | 884,215 | Parsed 'start_year' to a number with no loss of values. | no (restore from raw) |
| parse_number on `end_year` | 12,701 | Parsed 'end_year' to a number with no loss of values. | no (restore from raw) |
| parse_number on `runtime_minutes` | 375,564 | Parsed 'runtime_minutes' to a number with no loss of values. | no (restore from raw) |

5 proposed change(s) were **not** applied because they were not approved.

### Missing values by column

| Column | Missing | Missing % |
| --- | ---: | ---: |
| end_year | 987,299 | 98.7% |
| runtime_minutes | 624,436 | 62.4% |
| start_year | 115,785 | 11.6% |
| genres | 42,268 | 4.2% |
| original_title | 9 | 0.0% |
| primary_title | 9 | 0.0% |

_Source: `eda_missing_values.csv`._

![Missing values by column](charts/eda_missing_values.png)

*Missing values by column.* Share of rows with no value, per column. Computed on the uploaded data before cleaning.

## 4. Data preprocessing

Preprocessing runs after cleaning and has two separate scopes:

- **Analysed data** - optional changes to the data every figure in this report is computed from. **No changes were applied**, so the figures reflect the cleaned data.
- **Model-ready export** - a separate copy prepared for machine learning (scaled, encoded, with date features). It is offered as a download and does not feed any figure in this report.

**Settings:** missing numbers = `none`, missing categories = `none`, outliers = `none`, scaling = `standard`, one-hot encoding = `on`, date features = `on`.

### Model-ready export

| Step | Column | What was done |
| --- | --- | --- |
| drop columns | - | Dropped 5 column(s) a model cannot use: primary_title (free text); original_title (free text); end_year (99% missing, above the 50% limit); runtime_minutes (62% missing, above the 50% limit); genres (free text) |
| impute median | start_year | Filled 115,785 missing value(s) with the median (2,014). |
| one hot encode | - | One-hot encoded 2 categorical column(s) into 15 indicator column(s). |
| scale standard | - | 2 numeric column(s) standardised to mean 0 and standard deviation 1 (scikit-learn StandardScaler). |

_Result: 1,000,000 rows x 17 columns, downloadable from the job's Preprocessing tab._

### Columns left out of the model-ready export

| Column | Reason |
| --- | --- |
| primary_title | free text |
| original_title | free text |
| end_year | 99% missing, above the 50% limit |
| runtime_minutes | 62% missing, above the 50% limit |
| genres | free text |

## 5. Descriptive statistics

Summary statistics for every numeric measure and grouping column, computed on every analysed row.

### Numeric columns

| Column | Count | Mean | Std dev | Min | 25% | Median | 75% | Max | Skew | Kurtosis |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| tconst | 1,000,000 | 15,452,339 | 12,980,237 | 13 | 4,525,353 | 11,711,801 | 27,072,774 | 47,661,711 | 0.70 | -0.77 |
| start_year | 884,215 | 2,007.0 | 20.15 | 1,878 | 2,002 | 2,014 | 2,020 | 2,032 | -2.11 | 5.35 |
| end_year | 12,701 | 2,008.4 | 24.23 | 16 | 2,002 | 2,015 | 2,020 | 2,028 | -44.21 | 3,597.35 |
| runtime_minutes | 375,561 | 53.44 | 6,025.3 | 1 | 21 | 30 | 60 | 3,692,080 | 612.60 | 375,375.14 |

_Count excludes missing values. Skew above 1 or below -1 indicates a strongly lopsided distribution. Source: `eda_numeric_summary.csv`._

### Categorical columns

| Column | Non-missing | Unique | Most common | Count | Share |
| --- | ---: | ---: | --- | ---: | ---: |
| title_type | 1,000,000 | 10 | tvEpisode | 772,220 | 77.2% |
| is_adult | 1,000,000 | 5 | 0 | 967,464 | 96.7% |

_Source: `eda_categorical_summary.csv`._

## 6. Distributions

How the values of each column are spread: shape, centre, outliers and the most frequent categories.

![Distribution of numeric columns](charts/eda_histograms.png)

*Distribution of numeric columns.* Histograms with a smoothed density curve; solid line = mean, dashed = median. Drawn from a fixed random sample of 20,000 of 1,000,000 rows.

![Spread and outliers](charts/eda_box_plots.png)

*Spread and outliers.* Box = middle 50% of values, line = median, whiskers = 1.5 x IQR; points beyond are unusual relative to the column, not necessarily errors. Drawn from a fixed random sample of 20,000 of 1,000,000 rows.

![Most frequent categories](charts/eda_category_frequencies.png)

*Most frequent categories.* Row counts for the 12 most frequent values of each grouping column, computed on every row.

![end_year by title_type](charts/chart_6d9dcd5164bd598b.png)

*end_year by title_type.* Top 10 of 10 distinct 'title_type' values. Shares are computed on the analysed rows only; filtered-out rows are not included.

![Distribution of end_year](charts/chart_b74372b30f7e4ec6.png)

*Distribution of end_year.* 12,701 non-null values across 30 equal-width bins. Bins are equal width; a skewed measure is easier to read as a log scale, which is not shown.

## 7. Relationships between measures

Pairwise correlations between numeric measures. A correlation describes how two columns move together; it is an association, not evidence that one affects the other.

### Strongest correlations

| Column A | Column B | Pearson r | Strength | Rows used |
| --- | --- | ---: | --- | ---: |
| start_year | end_year | +0.977 | strong positive | 12,700 |
| tconst | start_year | +0.282 | weak positive | 884,215 |
| tconst | end_year | +0.234 | weak positive | 12,701 |
| end_year | runtime_minutes | +0.009 | negligible | 5,944 |
| tconst | runtime_minutes | -0.001 | negligible | 375,561 |
| start_year | runtime_minutes | +0.000 | negligible | 355,110 |

_Source: `eda_top_correlations.csv`._

![Correlation matrix](charts/eda_correlation_heatmap.png)

*Correlation matrix.* Pearson correlation on rows where both values are present. Correlation measures association only; it does not show that one column affects another.

![start_year vs end_year](charts/eda_top_pair_scatter.png)

*start_year vs end_year.* The most strongly correlated pair (r = +0.98), with a least-squares line. Drawn from a fixed random sample of 20,000 of 1,000,000 rows.

## 8. Trends and segments

How the headline measure changes over time and differs between groups.

### tconst by title_type

| title_type | Rows | Total | Mean | Median | Share of total |
| --- | ---: | ---: | ---: | ---: | ---: |
| tvEpisode | 772,220 | 12,659,746,631,082 | 16,393,964 | 12,761,871 | 81.9% |
| short | 90,915 | 1,164,098,591,623 | 12,804,252 | 7,264,348 | 7.5% |
| movie | 59,481 | 632,618,382,063 | 10,635,638 | 4,888,582 | 4.1% |
| tvSeries | 23,956 | 329,043,469,332 | 13,735,326 | 9,168,159 | 2.1% |
| video | 26,032 | 303,832,289,391 | 11,671,492 | 6,942,371 | 2.0% |
| tvMiniSeries | 5,703 | 105,576,931,662 | 18,512,525 | 14,050,960 | 0.7% |
| tvMovie | 12,148 | 99,892,076,504 | 8,222,924 | 3,069,361 | 0.6% |
| tvSpecial | 4,790 | 84,387,229,149 | 17,617,376 | 13,780,587 | 0.5% |
| videoGame | 3,909 | 66,822,182,544 | 17,094,444 | 12,331,416 | 0.4% |
| tvShort | 846 | 6,321,228,696 | 7,471,902 | 5,170,265 | 0.0% |

_Source: `eda_segments_title_type.csv`._

![Total tconst by title_type](charts/eda_segment_totals_title_type.png)

*Total tconst by title_type.* Sum over every row in each group, largest first.

![Spread of tconst within each title_type](charts/eda_segment_spread_title_type.png)

*Spread of tconst within each title_type.* How values vary inside each group, for the largest groups. Drawn from a fixed random sample of 20,000 of 1,000,000 rows.

![end_year by title_type](charts/chart_0ab46074d33cae61.png)

*end_year by title_type.* Mean of 'end_year' across 3 title_type values; group sizes range from 1 to 9403 rows. Means are sensitive to outliers. Check the accompanying table for group sizes and medians before relying on small differences.

![Share of total end_year by title_type](charts/chart_e48d023da88a89f0.png)

*Share of total end_year by title_type.* 3 groups; shares sum to 100% of the analysed rows. Groups with null measures are excluded from both numerator and denominator.

## 9. Feature engineering and selection

Two agents prepare the data for predictive modelling. This does not change any figure elsewhere in this report: engineered features exist only in the downloadable feature set.

- **Target:** `tconst` (regression), the dataset's headline measure.
- **Feature engineering agent:** 16 feature(s) created, 0 proposal(s) rejected; proposals were rule-based.
- **Feature selection agent:** 12 of 24 candidate(s) kept, ranked on 3,000 rows.

The agent treated this as a regression problem: predicting `tconst`. Of 24 candidate features, 12 were kept. The strongest were `start_year_squared`, `title_type=tvEpisode`, `start_year_mean_by_title_type`, `runtime_minutes_was_missing`, `start_year_was_missing`. Not kept: 7 near-constant, 5 redundant. Cross-validated r2 was 0.412 with the selected set and 0.411 with every candidate. Rankings describe association with the target in this data, not cause and effect.

> **Warnings**
> - Ranking and cross-validation used a fixed random sample of 3,000 rows.

### Engineered features

| Feature | Kind | Inputs | Source | Why |
| --- | --- | --- | --- | --- |
| log1p_runtime_minutes | log1p | runtime_minutes | rule | 'runtime_minutes' is right-skewed (skew 612.60); a log scale tames large values. |
| start_year_per_runtime_minutes | ratio | start_year, runtime_minutes | rule | Rate of 'start_year' per unit of 'runtime_minutes'. |
| end_year_per_start_year | ratio | end_year, start_year | rule | Rate of 'end_year' per unit of 'start_year'. |
| end_year_per_runtime_minutes | ratio | end_year, runtime_minutes | rule | Rate of 'end_year' per unit of 'runtime_minutes'. |
| start_year_x_runtime_minutes | product | start_year, runtime_minutes | rule | Interaction: lets a model see 'start_year' and 'runtime_minutes' acting together. |
| start_year_x_end_year | product | start_year, end_year | rule | Interaction: lets a model see 'start_year' and 'end_year' acting together. |
| runtime_minutes_x_end_year | product | runtime_minutes, end_year | rule | Interaction: lets a model see 'runtime_minutes' and 'end_year' acting together. |
| start_year_squared | square | start_year | rule | Curvature term for 'start_year'. |
| runtime_minutes_squared | square | runtime_minutes | rule | Curvature term for 'runtime_minutes'. |
| title_type_frequency | frequency | title_type | rule | How common each 'title_type' value is. |
| is_adult_frequency | frequency | is_adult | rule | How common each 'is_adult' value is. |
| start_year_mean_by_title_type | group mean | start_year, title_type | rule | Typical 'start_year' within each 'title_type'. |
| runtime_minutes_mean_by_title_type | group mean | runtime_minutes, title_type | rule | Typical 'runtime_minutes' within each 'title_type'. |
| start_year_was_missing | missing flag | start_year | rule | 12% of 'start_year' is missing; whether it is missing can itself be informative. |
| runtime_minutes_was_missing | missing flag | runtime_minutes | rule | 62% of 'runtime_minutes' is missing; whether it is missing can itself be informative. |
| genres_was_missing | missing flag | genres | rule | 4% of 'genres' is missing; whether it is missing can itself be informative. |

### Feature ranking

| Rank | Feature | Origin | Mutual information | \|Correlation\| | Importance | Status |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 1 | start_year_squared | engineered | 0.860 | 0.258 | 0.447 | selected |
| 2 | start_year | original | 0.859 | 0.256 | 0.408 | redundant with start_year_squared |
| 3 | title_type=tvEpisode | encoded | 0.073 | 0.114 | 0.021 | selected |
| 4 | title_type_frequency | engineered | 0.067 | 0.115 | 0.017 | redundant with title_type=tvEpisode |
| 5 | start_year_mean_by_title_type | engineered | 0.067 | 0.117 | 0.017 | selected |
| 6 | runtime_minutes_was_missing | engineered | 0.025 | 0.099 | 0.031 | selected |
| 7 | start_year_was_missing | engineered | 0.062 | 0.029 | 0.026 | selected |
| 8 | title_type=movie | encoded | 0.030 | 0.098 | 0.004 | selected |
| 9 | runtime_minutes_mean_by_title_type | engineered | 0.063 | 0.033 | 0.007 | selected |
| 10 | title_type=short | encoded | 0.010 | 0.034 | 0.003 | selected |
| 11 | is_adult=1 | encoded | 0.005 | 0.010 | 0.003 | selected |
| 12 | genres_was_missing | engineered | 0.004 | 0.010 | 0.010 | selected |
| 13 | title_type=tvMovie | encoded | 0.001 | 0.053 | 0.001 | selected |
| 14 | is_adult=0 | encoded | 0.005 | 0.010 | 0.002 | redundant with is_adult=1 |
| 15 | is_adult_frequency | engineered | 0.005 | 0.010 | 0.003 | redundant with is_adult=1 |
| 16 | title_type=video | encoded | 0.000 | 0.046 | 0.001 | selected |
| 17 | title_type=tvSeries | encoded | 0.003 | 0.014 | 0.001 | redundant with runtime_minutes_mean_by_title_type |
| - | title_type=tvMiniSeries | encoded | - | - | - | near-constant |
| - | title_type=tvShort | encoded | - | - | - | near-constant |
| - | title_type=tvSpecial | encoded | - | - | - | near-constant |
| - | title_type=videoGame | encoded | - | - | - | near-constant |
| - | is_adult=2022 | encoded | - | - | - | near-constant |
| - | is_adult=2023 | encoded | - | - | - | near-constant |
| - | is_adult=2025 | encoded | - | - | - | near-constant |

_Source: `feature_scores.csv`._

### Cross-validation (5 folds, RandomForestRegressor (80 trees))

| Feature set | Features | Mean r2 | Std dev |
| --- | ---: | ---: | ---: |
| All candidates | 24 | 0.411 | 0.033 |
| Selected | 12 | 0.412 | 0.032 |

![Feature ranking for predicting tconst](charts/feature_importance.png)

*Feature ranking for predicting tconst.* The 17 highest-ranked candidates ([x] = selected, darker label). Cells show each method's raw score, shaded relative to the best feature for that method. Scores describe association with the target in this data, not that a feature affects it.

![Cross-validated score: all candidates vs selected](charts/feature_cv_comparison.png)

*Cross-validated score: all candidates vs selected.* Mean R² over 5 folds; bars show one standard deviation. Higher is better.

**Methods:** variance filter (a column whose most common value covers 99% or more of rows); mutual information (scikit-learn mutual_info_regression / mutual_info_classif); absolute Pearson correlation with the target (regression and two-class targets); random-forest impurity importance (80 trees, seeded); redundancy pruning (|r| > 0.95 with a higher-ranked feature); 5-fold cross-validation of all candidates against the selected set.

## 10. Key findings

#### worst missing column (missingness_profile)

The least complete column in this dataset is end_year.

**Evidence** (tbl_1ab0b334611611ee)
  - `worst_missing_column` = end_year

**Confidence:** high  
**Severity:** medium

#### worst missing share (missingness_profile)

That column has no usable values in 98.7% of rows.

**Evidence** (tbl_1ab0b334611611ee)
  - `worst_missing_share` = 98.73 %

**Confidence:** high  
**Severity:** medium

#### title_type: top share (rank_categories)

The leading group value accounts for 74.0% of the total.

**Evidence** (tbl_a916252d83744816)
  - `title_type__top_share` = 73.98 %

**Confidence:** high  
**Severity:** medium

#### top3 share (share_of_total)

The top groups account for 100.0% of the total.

**Evidence** (tbl_cb74a3c71a0ca83d)
  - `top3_share` = 100 %

**Confidence:** high  
**Severity:** medium

#### contribution concentration (share_of_total)

Contribution is concentrated: the leading groups hold 100.0% of the total.

**Evidence** (tbl_cb74a3c71a0ca83d)
  - `contribution_concentration` = 100 %

**Confidence:** high  
**Severity:** medium

#### group gap mean (compare_groups)

The gap between the highest and lowest group mean is 1,997.

**Evidence** (tbl_c23bb46cd984cb5c)
  - `group_gap_mean` = 1,997

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Values deviate strongly from normality (Shapiro p=1.4e-55 < 0.01), so a rank-based test was used instead of a t-test.

#### end_year: anomaly rate pct (detect_outliers, anomalies)

0.1% of evaluated rows were flagged as unusual.

**Evidence** (tbl_c1cb6ccb3d4c5484)
  - `end_year__anomaly_rate_pct` = 0.05 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### end_year: anomaly rate pct (detect_group_outliers, anomalies_by_group)

0.1% of evaluated rows were flagged as unusual.

**Evidence** (tbl_7267062e85c742fe)
  - `end_year__anomaly_rate_pct` = 0.05 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Needs adequate group size; small groups are unreliable and are usually excluded rather than silently scored.

#### 500 of 1,000,000 rows flagged by iqr_fence

iqr_fence flagged 500 of 1,000,000 evaluated rows in 'end_year' as unusual relative to the stated baseline. 0.1% of the evaluated rows, flagged for review.

**Evidence** (anomaly:318b71a863640cfb34b1d3a02ad81d3d)
  - `end_year__anomalies_flagged` = 500 records
  - `end_year__anomaly_rate_pct` = 0.05 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### 500 of 1,000,000 rows flagged by group_percentile

group_percentile flagged 500 of 1,000,000 evaluated rows in 'end_year' as unusual relative to the stated baseline. 0.1% of the evaluated rows, flagged for review.

**Evidence** (anomaly:19d471457c9d9eafda892e8e2e0c6c62)
  - `end_year__anomalies_flagged` = 500 records
  - `end_year__anomaly_rate_pct` = 0.05 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Needs adequate group size; small groups are unreliable and are usually excluded rather than silently scored.

#### Most extreme iqr_fence flag: row 969

'end_year' is below the expected range for interquartile fence: 1,960 against 1,975 to 2,047, derived from 12,701 value(s).

**Evidence** (anomaly:318b71a863640cfb34b1d3a02ad81d3d)
  - `end_year__observed_value` = 1,960 end_year

**Confidence:** low  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.
- Flagged for review by iqr_fence, not evidence of an error or wrongdoing. Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### Most extreme group_percentile flag: title_type=tvSeries

'end_year' is above the range typical for this group: 1,960 against 1,969 to 2,049 (group quartiles 1,999 to 2,019).

**Evidence** (anomaly:19d471457c9d9eafda892e8e2e0c6c62)
  - `end_year__observed_value` = 1,960 end_year

**Confidence:** low  
**Severity:** medium

**Limitations**
- Needs adequate group size; small groups are unreliable and are usually excluded rather than silently scored.
- Flagged for review by group_percentile, not evidence of an error or wrongdoing. Needs adequate group size; small groups are unreliable and are usually excluded rather than silently scored.

#### end_year: skewness (describe_measures, overview_measures)

The distribution is skewed (-44.20), so averages understate the spread.

**Evidence** (tbl_3bcbc39635b641d1)
  - `end_year__skewness` = -44.2

**Confidence:** high  
**Severity:** low

#### runtime_minutes: skewness (describe_measures)

The distribution is skewed (612.60), so averages understate the spread.

**Evidence** (tbl_3bcbc39635b641d1)
  - `runtime_minutes__skewness` = 612.6

**Confidence:** high  
**Severity:** low

#### start_year: skewness (describe_measures)

The distribution is skewed (-2.11), so averages understate the spread.

**Evidence** (tbl_3bcbc39635b641d1)
  - `start_year__skewness` = -2.108

**Confidence:** high  
**Severity:** low

#### tconst: skewness (describe_measures)

The distribution is skewed (0.70), so averages understate the spread.

**Evidence** (tbl_3bcbc39635b641d1)
  - `tconst__skewness` = 0.7026

**Confidence:** high  
**Severity:** low

#### end_year: skewness (value_distribution, primary_distribution)

The distribution is skewed (-44.20), so averages understate the spread.

**Evidence** (tbl_025264c30c6c1e4f)
  - `end_year__skewness` = -44.2

**Confidence:** high  
**Severity:** low

#### group difference p value (compare_groups)

The difference between the two highest-mean groups has a p-value of 0.00.

**Evidence** (tbl_adab9b287f1fbbda)
  - `group_difference_p_value` = 3.253e-127

**Confidence:** medium  
**Severity:** low

**Limitations**
- Values deviate strongly from normality (Shapiro p=1.4e-55 < 0.01), so a rank-based test was used instead of a t-test.

#### group difference cohens d (compare_groups)

The standardised difference between the two highest-mean groups is 0.41.

**Evidence** (tbl_adab9b287f1fbbda)
  - `group_difference_cohens_d` = 0.4106

**Confidence:** medium  
**Severity:** low

**Limitations**
- Values deviate strongly from normality (Shapiro p=1.4e-55 < 0.01), so a rank-based test was used instead of a t-test.

#### strongest correlation (correlation_matrix)

The strongest measured association in the data is 0.98.

**Evidence** (tbl_a384d3885a7a40b3)
  - `strongest_correlation` = 0.9795

**Confidence:** medium  
**Severity:** low

**Limitations**
- Correlation measures association only. It does not establish that either variable causes the other, and a high correlation can arise from a shared third factor.

#### end_year: mean (describe_measures)

The 2,008 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `end_year__mean` = 2,008

**Confidence:** high  
**Severity:** info

#### end_year: median (describe_measures)

The 2,015 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `end_year__median` = 2,015

**Confidence:** high  
**Severity:** info

#### end_year: total (describe_measures)

The 25,509,191 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `end_year__total` = 2.551e+07

**Confidence:** high  
**Severity:** info

#### runtime_minutes: mean (describe_measures)

The 53.44 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `runtime_minutes__mean` = 53.44

**Confidence:** high  
**Severity:** info

#### runtime_minutes: median (describe_measures)

The 30.00 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `runtime_minutes__median` = 30

**Confidence:** high  
**Severity:** info

#### runtime_minutes: total (describe_measures)

The 20,070,588 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `runtime_minutes__total` = 2.007e+07

**Confidence:** high  
**Severity:** info

#### start_year: mean (describe_measures)

The 2,007 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `start_year__mean` = 2,007

**Confidence:** high  
**Severity:** info

#### start_year: median (describe_measures)

The 2,014 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `start_year__median` = 2,014

**Confidence:** high  
**Severity:** info

#### start_year: total (describe_measures)

The 1,774,619,196 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `start_year__total` = 1.775e+09

**Confidence:** high  
**Severity:** info

#### tconst: mean (describe_measures)

The 15,452,339 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `tconst__mean` = 1.545e+07

**Confidence:** high  
**Severity:** info

#### tconst: median (describe_measures)

The 11,711,801 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `tconst__median` = 1.171e+07

**Confidence:** high  
**Severity:** info

#### tconst: total (describe_measures)

The 15,452,339,012,046 value of this measure was computed.

**Evidence** (tbl_3bcbc39635b641d1)
  - `tconst__total` = 1.545e+13

**Confidence:** high  
**Severity:** info

#### largest group mean (compare_groups)

The highest group mean is 2,013.

**Evidence** (tbl_c23bb46cd984cb5c)
  - `largest_group_mean` = 2,013

**Confidence:** high  
**Severity:** info

**Limitations**
- Values deviate strongly from normality (Shapiro p=1.4e-55 < 0.01), so a rank-based test was used instead of a t-test.

#### end_year: observed total (rank_categories)

The measure totals 25,509,191 across the analysed rows.

**Evidence** (tbl_a916252d83744816)
  - `end_year__observed_total` = 2.551e+07

**Confidence:** high  
**Severity:** info

## 11. Anomalies

**A flagged record is unusual relative to a stated baseline. It is not evidence of an error, mistake or wrongdoing, and it may be entirely legitimate.**

### `end_year` - iqr_fence

- Flagged: **500** of 1,000,000 evaluated rows (0.1%)
- Baseline: First and third quartiles of 'end_year', extended by 1.5 times the interquartile range on each side.
- Limitation: Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

| Flagged value | Compared against | Severity | Why flagged |
| --- | --- | --- | --- |
| 1,960 | 1,975 to 2,047 | medium | 'end_year' is below the expected range for interquartile fence: 1,960 against 1,975 to 2,047, derived from 12,701 value( |
| 1,972 | 1,975 to 2,047 | medium | 'end_year' is below the expected range for interquartile fence: 1,972 against 1,975 to 2,047, derived from 12,701 value( |
| 1,961 | 1,975 to 2,047 | medium | 'end_year' is below the expected range for interquartile fence: 1,961 against 1,975 to 2,047, derived from 12,701 value( |
| 1,971 | 1,975 to 2,047 | medium | 'end_year' is below the expected range for interquartile fence: 1,971 against 1,975 to 2,047, derived from 12,701 value( |
| 1,967 | 1,975 to 2,047 | medium | 'end_year' is below the expected range for interquartile fence: 1,967 against 1,975 to 2,047, derived from 12,701 value( |
| 1,963 | 1,975 to 2,047 | medium | 'end_year' is below the expected range for interquartile fence: 1,963 against 1,975 to 2,047, derived from 12,701 value( |
| 1,967 | 1,975 to 2,047 | medium | 'end_year' is below the expected range for interquartile fence: 1,967 against 1,975 to 2,047, derived from 12,701 value( |
| 1,962 | 1,975 to 2,047 | medium | 'end_year' is below the expected range for interquartile fence: 1,962 against 1,975 to 2,047, derived from 12,701 value( |

### `end_year` - group_percentile

- Flagged: **500** of 1,000,000 evaluated rows (0.1%)
- Baseline: Within-group quartiles of 'end_year', extended by 1.5 times the within-group interquartile range. Groups with fewer than 8 rows, or with no variation, are skipped.
- Limitation: Needs adequate group size; small groups are unreliable and are usually excluded rather than silently scored.

| Flagged value | Compared against | Severity | Why flagged |
| --- | --- | --- | --- |
| 1,960 | group_interquartile_fence | medium | 'end_year' is above the range typical for this group: 1,960 against 1,969 to 2,049 (group quartiles 1,999 to 2,019). |
| 1,961 | group_interquartile_fence | medium | 'end_year' is above the range typical for this group: 1,961 against 1,969 to 2,049 (group quartiles 1,999 to 2,019). |
| 1,982 | group_interquartile_fence | medium | 'end_year' is above the range typical for this group: 1,982 against 1,993 to 2,041 (group quartiles 2,011 to 2,023). |
| 1,967 | group_interquartile_fence | medium | 'end_year' is above the range typical for this group: 1,967 against 1,969 to 2,049 (group quartiles 1,999 to 2,019). |
| 1,963 | group_interquartile_fence | medium | 'end_year' is above the range typical for this group: 1,963 against 1,993 to 2,041 (group quartiles 2,011 to 2,023). |
| 1,967 | group_interquartile_fence | medium | 'end_year' is above the range typical for this group: 1,967 against 1,969 to 2,049 (group quartiles 1,999 to 2,019). |
| 1,962 | group_interquartile_fence | medium | 'end_year' is above the range typical for this group: 1,962 against 1,969 to 2,049 (group quartiles 1,999 to 2,019). |
| 1,959 | group_interquartile_fence | medium | 'end_year' is above the range typical for this group: 1,959 against 1,969 to 2,049 (group quartiles 1,999 to 2,019). |

**Suggested review order:** start with high-severity flags, then check whether the surrounding periods and groups corroborate them.

![Values flagged by iqr_fence](charts/chart_920bdf70e7a420cf.png)

*Values flagged by iqr_fence.* 500 flagged record(s) from end_year. Shown: 15. Detected by iqr_fence. A flagged record is unusual relative to the stated baseline. It is not evidence of an error, and it may be entirely legitimate.

![Values flagged by group_percentile](charts/chart_c6d11fbc20d04c38.png)

*Values flagged by group_percentile.* 500 flagged record(s) from end_year. Shown: 15. Detected by group_percentile. A flagged record is unusual relative to the stated baseline. It is not evidence of an error, and it may be entirely legitimate.

## 12. Methodology

Every figure in this report was computed by a registered analysis tool and stored as an artifact before the report was written. No number was produced by a language model.

**Tools used**

- `describe_measures` v1 on dataset version 2 - 20 metric(s), 1 table(s)
- `missingness_profile` v1 on dataset version 2 - 2 metric(s), 1 table(s)
- `compare_groups` v1 on dataset version 2 - 7 metric(s), 2 table(s)
- `rank_categories` v1 on dataset version 2 - 3 metric(s), 1 table(s)
- `share_of_total` v1 on dataset version 2 - 3 metric(s), 1 table(s)
- `correlation_matrix` v1 on dataset version 2 - 7 metric(s), 1 table(s)
- `value_distribution` v1 on dataset version 2 - 3 metric(s), 1 table(s)
- `detect_outliers` v1 on dataset version 2 - 4 metric(s), 1 table(s)
- `detect_group_outliers` v1 on dataset version 2 - 4 metric(s), 1 table(s)

**Exploratory analysis:** descriptive statistics, distributions, correlations, segment and trend views were computed with pandas and NumPy and drawn with seaborn and matplotlib. Charts over many rows use a fixed random sample, stated in each caption; tables use every row.

This report includes **17 chart(s)** and **17 evidence table(s)**. Each table cites the stored CSV that holds its unrounded values; the full list is in the appendix.

## 13. Limitations

**Missing fields:** `end_year` (99%), `runtime_minutes` (62%). Aggregates over these columns exclude missing rows, which can bias totals downward.
**No causal claims:** where this report describes relationships, they are associations observed in the data. Establishing cause would require a different study design.

**Questions this dataset cannot answer**

- Questions about change over time: no date column was identified in this dataset.

## 14. Suggested next questions

Suggested next questions, based on what this dataset can answer:
1. Drill into the flagged periods and check whether an operational event (campaign, outage, pricing change) lines up with them.
2. Establish whether the missing values in `start_year` are random or systematic, because that changes how the results should be read.
3. Add a target or prior-period column so the numbers can be judged against a goal rather than only against each other.

## 15. Reproducibility

- **Job:** `job_39a697d6417c4f05`
- **Dataset:** `ds_bd7d63297da395d0`
- **Source file hash:** `f074d50b1769f8bb34d7af5f8b183e295184e0e14484bf738385a0a83ffd551e`
- **Rows x columns:** 1,000,000 x 9
- **Profiler version:** 1
- **Report generated:** 2026-10-01T12:42:53.494770+00:00

Re-running the same file produces the same profile and the same computed metrics. Every table and chart in this report is stored as an artifact and can be downloaded alongside it.

## Appendix: evidence tables

| Artifact | Rows |
| --- | --- |
| `feature_scores` | 24 |
| `tbl_3bcbc39635b641d1` | 4 |
| `tbl_1ab0b334611611ee` | 9 |
| `tbl_c23bb46cd984cb5c` | 3 |
| `tbl_adab9b287f1fbbda` | 2 |
| `tbl_a916252d83744816` | 10 |
| `tbl_cb74a3c71a0ca83d` | 3 |
| `tbl_a384d3885a7a40b3` | 4 |
| `tbl_025264c30c6c1e4f` | 3 |
| `tbl_c1cb6ccb3d4c5484` | 500 |
| `tbl_7267062e85c742fe` | 500 |
| `eda_column_overview` | 9 |
| `eda_missing_values` | 6 |
| `eda_numeric_summary` | 4 |
| `eda_categorical_summary` | 2 |
| `eda_top_correlations` | 6 |
| `eda_segments_title_type` | 10 |

---

Generated by InsightFlow. Every figure above was computed by a registered analysis tool and stored as an artifact; no number in this report was produced by a language model.
