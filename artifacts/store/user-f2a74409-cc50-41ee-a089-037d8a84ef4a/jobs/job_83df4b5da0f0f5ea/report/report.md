# Analysis of imdb top 5000 tv shows

**File:** `imdb_top_5000_tv_shows.csv`

> This is an **exploratory** report: the user did not ask a specific question, so the analysis is general and not targeted at a decision.

| Rows | Columns | Missing cells | Duplicate rows | Preprocessing | Verified findings | Charts |
| --- | --- | --- | --- | --- | --- | --- |
| 2,716 | 12 | 0.0% | 0 | export only | 31 | 7 |

## Contents

1. Executive summary
2. Dataset overview
3. Data-quality assessment
4. Data preprocessing
5. Descriptive statistics
6. Distributions
7. Relationships between measures
8. Key findings
9. Anomalies
10. Methodology
11. Limitations
12. Suggested next questions
13. Reproducibility

## 1. Executive summary

This report covers 2,716 rows and 12 columns. It was produced in response to this exploratory overview. 31 finding(s) were verified against computed values, 5 of them at medium or high importance. Each finding below links to the artifact it came from.

### Key findings
- **worst missing column (missingness_profile)** — The least complete column in this dataset is end_year. (confidence: high)
- **worst missing share (missingness_profile)** — That column has no usable values in 14.7% of rows. (confidence: high)
- **tconst: anomaly rate pct (detect_outliers)** — 6.0% of evaluated rows were flagged as unusual. (confidence: medium)
- **163 of 2,716 rows flagged by robust_zscore** — robust_zscore flagged 163 of 2,716 evaluated rows in 'tconst' as unusual relative to the stated baseline. 6.0% of the evaluated rows, flagged for review. (confidence: medium)
- **Most extreme robust_zscore flag: row 20** — 'tconst' is above the expected range for median mad: 30,263,074 against -17,150,068 to 26,299,138, derived from 2,716 value(s). (confidence: low)

## 2. Dataset overview

**Source file:** imdb_top_5000_tv_shows.csv
**Dimensions:** 2,716 rows x 12 columns
**Column types:** tconst (integer), primary_title (text), start_year (integer), end_year (number), rank (integer), average_rating (number), num_votes (integer), directors (text), writers (text), genres (text), imdb_link (text), title_imdb_link (text)
**Key measures:** tconst, start_year, end_year, rank, average_rating, num_votes

### Column overview

| Column | Type | Role | Missing | Missing % | Unique values | PII risk |
| --- | --- | --- | ---: | ---: | ---: | --- |
| tconst | integer | measure | 0 | 0.0% | 2,716 | none |
| primary_title | text | free text | 0 | 0.0% | 2,654 | none |
| start_year | integer | measure | 0 | 0.0% | 71 | none |
| end_year | number | measure | 0 | 0.0% | 68 | none |
| rank | integer | measure | 0 | 0.0% | 2,716 | none |
| average_rating | number | measure | 0 | 0.0% | 68 | none |
| num_votes | integer | measure | 0 | 0.0% | 2,642 | none |
| directors | text | free text | 0 | 0.0% | 2,644 | none |
| writers | text | free text | 0 | 0.0% | 2,612 | none |
| genres | text | free text | 0 | 0.0% | 279 | none |
| imdb_link | text | free text | 0 | 0.0% | 2,716 | none |
| title_imdb_link | text | free text | 0 | 0.0% | 2,716 | none |

_Types and roles are inferred from values and column names. Source: `eda_column_overview.csv`._

## 3. Data-quality assessment

**Other issues detected**
- [medium] `primary_title` 2653 normalised forms cover 2654 raw values: casing and whitespace differ between otherwise identical labels.
- [medium] `primary_title` 2,654 distinct values across 2,716 rows; unlikely to be a usable grouping key.
- [medium] `end_year` 14.7% of non-null values could not be parsed as number.
- [medium] `end_year` 399 values are infinite or not a number and will break arithmetic.
- [medium] `directors` 2,644 distinct values across 2,716 rows; unlikely to be a usable grouping key.
- [medium] `writers` 2,612 distinct values across 2,716 rows; unlikely to be a usable grouping key.
- [medium] `imdb_link` 2,716 distinct values across 2,716 rows; unlikely to be a usable grouping key.
- [medium] `title_imdb_link` 2,716 distinct values across 2,716 rows; unlikely to be a usable grouping key.

**Cleaning applied**

| Change | Rows affected | Reason | Reversible |
| --- | --- | --- | --- |
| parse_number on `tconst` | 2,716 | Parsed 'tconst' to a number with no loss of values. | no (restore from raw) |
| trim_whitespace on `primary_title` | 0 | Trimmed leading and trailing whitespace in 'primary_title'. | yes |
| normalise_categories on `primary_title` | 1 | 'primary_title' has 2654 raw labels for 2653 distinct values once case and whitespace are normalised. | yes |
| parse_number on `start_year` | 2,716 | Parsed 'start_year' to a number with no loss of values. | no (restore from raw) |
| parse_number on `end_year` | 2,716 | Parsed 'end_year' to a number; 14.7% of values did not parse and become null. | no (restore from raw) |
| parse_number on `rank` | 2,716 | Parsed 'rank' to a number with no loss of values. | no (restore from raw) |
| parse_number on `average_rating` | 2,716 | Parsed 'average_rating' to a number with no loss of values. | no (restore from raw) |
| parse_number on `num_votes` | 2,716 | Parsed 'num_votes' to a number with no loss of values. | no (restore from raw) |

1 proposed change(s) were **not** applied because they were not approved.

No column has missing values.

## 4. Data preprocessing

Preprocessing runs after cleaning and has two separate scopes:

- **Analysed data** - optional changes to the data every figure in this report is computed from. **No changes were applied**, so the figures reflect the cleaned data.
- **Model-ready export** - a separate copy prepared for machine learning (scaled, encoded, with date features). It is offered as a download and does not feed any figure in this report.

**Settings:** missing numbers = `none`, missing categories = `none`, outliers = `none`, scaling = `standard`, one-hot encoding = `on`, date features = `on`.

### Model-ready export

| Step | Column | What was done |
| --- | --- | --- |
| drop columns | - | Dropped 6 column(s) a model cannot use: primary_title (free text); directors (free text); writers (free text); genres (free text); imdb_link (free text); title_imdb_link (free text) |
| impute median | end_year | Filled 399 missing value(s) with the median (2,018). |
| scale standard | - | 6 numeric column(s) standardised to mean 0 and standard deviation 1 (scikit-learn StandardScaler). |

_Result: 2,716 rows x 6 columns, downloadable from the job's Preprocessing tab._

### Columns left out of the model-ready export

| Column | Reason |
| --- | --- |
| primary_title | free text |
| directors | free text |
| writers | free text |
| genres | free text |
| imdb_link | free text |
| title_imdb_link | free text |

## 5. Descriptive statistics

Summary statistics for every numeric measure and grouping column, computed on every analysed row.

### Numeric columns

| Column | Count | Mean | Std dev | Min | 25% | Median | 75% | Max | Skew | Kurtosis |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| tconst | 2,716 | 7,267,675 | 8,162,068 | 43,208 | 1,058,416 | 4,574,535 | 10,801,693 | 42,178,219 | 1.60 | 2.34 |
| start_year | 2,716 | 2,012.3 | 12.26 | 1,951 | 2,008 | 2,016 | 2,021 | 2,026 | -1.75 | 3.46 |
| end_year | 2,317 | 2,014.6 | 11.38 | 1,957 | 2,011 | 2,018 | 2,022 | 2,027 | -1.94 | 4.17 |
| rank | 2,716 | 1,358.5 | 784.19 | 1 | 679.75 | 1,358.5 | 2,037.2 | 2,716 | 0.00 | -1.20 |
| average_rating | 2,716 | 7.59 | 0.88 | 1.10 | 7.20 | 7.70 | 8.20 | 9.50 | -1.73 | 7.28 |
| num_votes | 2,716 | 60,997.6 | 132,216 | 10,003 | 14,504.2 | 23,994.5 | 51,744 | 2,682,902 | 9.13 | 135.31 |

_Count excludes missing values. Skew above 1 or below -1 indicates a strongly lopsided distribution. Source: `eda_numeric_summary.csv`._

## 6. Distributions

How the values of each column are spread: shape, centre, outliers and the most frequent categories.

![Distribution of numeric columns](charts/eda_histograms.png)

*Distribution of numeric columns.* Histograms with a smoothed density curve; solid line = mean, dashed = median.

![Spread and outliers](charts/eda_box_plots.png)

*Spread and outliers.* Box = middle 50% of values, line = median, whiskers = 1.5 x IQR; points beyond are unusual relative to the column, not necessarily errors.

![Distribution of tconst](charts/chart_f7dc5e366aed2495.png)

*Distribution of tconst.* 2,716 non-null values across 30 equal-width bins. Bins are equal width; a skewed measure is easier to read as a log scale, which is not shown.

## 7. Relationships between measures

Pairwise correlations between numeric measures. A correlation describes how two columns move together; it is an association, not evidence that one affects the other.

### Strongest correlations

| Column A | Column B | Pearson r | Strength | Rows used |
| --- | --- | ---: | --- | ---: |
| start_year | end_year | +0.940 | strong positive | 2,317 |
| rank | average_rating | -0.883 | strong negative | 2,716 |
| tconst | start_year | +0.636 | moderate positive | 2,716 |
| tconst | end_year | +0.544 | moderate positive | 2,317 |
| rank | num_votes | -0.342 | weak negative | 2,716 |
| average_rating | num_votes | +0.242 | weak positive | 2,716 |
| tconst | rank | +0.186 | negligible | 2,716 |
| tconst | average_rating | -0.182 | negligible | 2,716 |
| start_year | average_rating | -0.147 | negligible | 2,716 |
| start_year | rank | +0.131 | negligible | 2,716 |

_Source: `eda_top_correlations.csv`._

![Correlation matrix](charts/eda_correlation_heatmap.png)

*Correlation matrix.* Pearson correlation on rows where both values are present. Correlation measures association only; it does not show that one column affects another.

![start_year vs end_year](charts/eda_top_pair_scatter.png)

*start_year vs end_year.* The most strongly correlated pair (r = +0.94), with a least-squares line.

## 8. Key findings

#### worst missing column (missingness_profile)

The least complete column in this dataset is end_year.

**Evidence** (tbl_78566a73c577ced7)
  - `worst_missing_column` = end_year

**Confidence:** high  
**Severity:** medium

#### worst missing share (missingness_profile)

That column has no usable values in 14.7% of rows.

**Evidence** (tbl_78566a73c577ced7)
  - `worst_missing_share` = 14.69 %

**Confidence:** high  
**Severity:** medium

#### tconst: anomaly rate pct (detect_outliers)

6.0% of evaluated rows were flagged as unusual.

**Evidence** (tbl_5fd8c48a2198d868)
  - `tconst__anomaly_rate_pct` = 6.001 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Robust z-scores assume the bulk of the data is representative; a broad shift in the data can hide a genuine change.

#### 163 of 2,716 rows flagged by robust_zscore

robust_zscore flagged 163 of 2,716 evaluated rows in 'tconst' as unusual relative to the stated baseline. 6.0% of the evaluated rows, flagged for review.

**Evidence** (anomaly:f7c5e90e001d9f6b83a3fc6c6d003e2e)
  - `tconst__anomalies_flagged` = 163 records
  - `tconst__anomaly_rate_pct` = 6.001 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Robust z-scores assume the bulk of the data is representative; a broad shift in the data can hide a genuine change.

#### Most extreme robust_zscore flag: row 20

'tconst' is above the expected range for median mad: 30,263,074 against -17,150,068 to 26,299,138, derived from 2,716 value(s).

**Evidence** (anomaly:f7c5e90e001d9f6b83a3fc6c6d003e2e)
  - `tconst__observed_value` = 3.026e+07 tconst

**Confidence:** low  
**Severity:** medium

**Limitations**
- Robust z-scores assume the bulk of the data is representative; a broad shift in the data can hide a genuine change.
- Flagged for review by robust_zscore, not evidence of an error or wrongdoing. Robust z-scores assume the bulk of the data is representative; a broad shift in the data can hide a genuine change.

#### tconst: skewness (describe_measures, overview_measures)

The distribution is skewed (1.60), so averages understate the spread.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `tconst__skewness` = 1.603

**Confidence:** high  
**Severity:** low

#### start_year: skewness (describe_measures)

The distribution is skewed (-1.75), so averages understate the spread.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `start_year__skewness` = -1.753

**Confidence:** high  
**Severity:** low

#### end_year: skewness (describe_measures)

The distribution is skewed (-1.94), so averages understate the spread.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `end_year__skewness` = -1.942

**Confidence:** high  
**Severity:** low

#### rank: skewness (describe_measures)

The distribution is skewed (0.00), so averages understate the spread.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `rank__skewness` = 0

**Confidence:** high  
**Severity:** low

#### average_rating: skewness (describe_measures)

The distribution is skewed (-1.73), so averages understate the spread.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `average_rating__skewness` = -1.726

**Confidence:** high  
**Severity:** low

#### num_votes: skewness (describe_measures)

The distribution is skewed (9.13), so averages understate the spread.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `num_votes__skewness` = 9.127

**Confidence:** high  
**Severity:** low

#### tconst: skewness (value_distribution, primary_distribution)

The distribution is skewed (1.60), so averages understate the spread.

**Evidence** (tbl_2e079bc103079869)
  - `tconst__skewness` = 1.603

**Confidence:** high  
**Severity:** low

#### strongest correlation (correlation_matrix)

The strongest measured association in the data is 0.94.

**Evidence** (tbl_ca0b61299cf90dc4)
  - `strongest_correlation` = 0.9396

**Confidence:** medium  
**Severity:** low

**Limitations**
- Correlation measures association only. It does not establish that either variable causes the other, and a high correlation can arise from a shared third factor.

#### tconst: mean (describe_measures)

The 7,267,675 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `tconst__mean` = 7.268e+06

**Confidence:** high  
**Severity:** info

#### tconst: median (describe_measures)

The 4,574,535 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `tconst__median` = 4.575e+06

**Confidence:** high  
**Severity:** info

#### tconst: total (describe_measures)

The 19,739,005,641 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `tconst__total` = 1.974e+10

**Confidence:** high  
**Severity:** info

#### start_year: mean (describe_measures)

The 2,012 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `start_year__mean` = 2,012

**Confidence:** high  
**Severity:** info

#### start_year: median (describe_measures)

The 2,016 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `start_year__median` = 2,016

**Confidence:** high  
**Severity:** info

#### start_year: total (describe_measures)

The 5,465,389 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `start_year__total` = 5.465e+06

**Confidence:** high  
**Severity:** info

#### end_year: mean (describe_measures)

The 2,015 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `end_year__mean` = 2,015

**Confidence:** high  
**Severity:** info

#### end_year: median (describe_measures)

The 2,018 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `end_year__median` = 2,018

**Confidence:** high  
**Severity:** info

#### end_year: total (describe_measures)

The 4,667,826 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `end_year__total` = 4.668e+06

**Confidence:** high  
**Severity:** info

#### rank: mean (describe_measures)

The 1,358 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `rank__mean` = 1,358

**Confidence:** high  
**Severity:** info

#### rank: median (describe_measures)

The 1,358 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `rank__median` = 1,358

**Confidence:** high  
**Severity:** info

#### rank: total (describe_measures)

The 3,689,686 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `rank__total` = 3.69e+06

**Confidence:** high  
**Severity:** info

#### average_rating: mean (describe_measures)

The 7.59 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `average_rating__mean` = 7.586

**Confidence:** high  
**Severity:** info

#### average_rating: median (describe_measures)

The 7.70 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `average_rating__median` = 7.7

**Confidence:** high  
**Severity:** info

#### average_rating: total (describe_measures)

The 20,602 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `average_rating__total` = 2.06e+04

**Confidence:** high  
**Severity:** info

#### num_votes: mean (describe_measures)

The 60,998 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `num_votes__mean` = 6.1e+04

**Confidence:** high  
**Severity:** info

#### num_votes: median (describe_measures)

The 23,994 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `num_votes__median` = 2.399e+04

**Confidence:** high  
**Severity:** info

#### num_votes: total (describe_measures)

The 165,669,613 value of this measure was computed.

**Evidence** (tbl_862ac4a4e48e81f9)
  - `num_votes__total` = 1.657e+08

**Confidence:** high  
**Severity:** info

## 9. Anomalies

**A flagged record is unusual relative to a stated baseline. It is not evidence of an error, mistake or wrongdoing, and it may be entirely legitimate.**

### `tconst` - robust_zscore

- Flagged: **163** of 2,716 evaluated rows (6.0%)
- Baseline: Median of 'tconst' plus or minus 3.5 scaled median absolute deviations (MAD x 1.4826).
- Limitation: Robust z-scores assume the bulk of the data is representative; a broad shift in the data can hide a genuine change.

| Flagged value | Compared against | Severity | Why flagged |
| --- | --- | --- | --- |
| 30,263,074 | -17,150,068 to 26,299,138 | medium | 'tconst' is above the expected range for median mad: 30,263,074 against -17,150,068 to 26,299,138, derived from 2,716 va |
| 33,043,892 | -17,150,068 to 26,299,138 | medium | 'tconst' is above the expected range for median mad: 33,043,892 against -17,150,068 to 26,299,138, derived from 2,716 va |
| 31,938,062 | -17,150,068 to 26,299,138 | medium | 'tconst' is above the expected range for median mad: 31,938,062 against -17,150,068 to 26,299,138, derived from 2,716 va |
| 26,471,411 | -17,150,068 to 26,299,138 | medium | 'tconst' is above the expected range for median mad: 26,471,411 against -17,150,068 to 26,299,138, derived from 2,716 va |
| 27,497,448 | -17,150,068 to 26,299,138 | medium | 'tconst' is above the expected range for median mad: 27,497,448 against -17,150,068 to 26,299,138, derived from 2,716 va |
| 35,495,073 | -17,150,068 to 26,299,138 | medium | 'tconst' is above the expected range for median mad: 35,495,073 against -17,150,068 to 26,299,138, derived from 2,716 va |
| 36,988,358 | -17,150,068 to 26,299,138 | medium | 'tconst' is above the expected range for median mad: 36,988,358 against -17,150,068 to 26,299,138, derived from 2,716 va |
| 31,510,819 | -17,150,068 to 26,299,138 | medium | 'tconst' is above the expected range for median mad: 31,510,819 against -17,150,068 to 26,299,138, derived from 2,716 va |

**Suggested review order:** start with high-severity flags, then check whether the surrounding periods and groups corroborate them.

![Values flagged by iqr_fence](charts/chart_0e3972b2a9ea9f74.png)

*Values flagged by iqr_fence.* 163 flagged record(s) from tconst. Shown: 15. Detected by robust_zscore. A flagged record is unusual relative to the stated baseline. It is not evidence of an error, and it may be entirely legitimate.

## 10. Methodology

Every figure in this report was computed by a registered analysis tool and stored as an artifact before the report was written. No number was produced by a language model.

**Tools used**

- `describe_measures` v1 on dataset version 2 - 30 metric(s), 1 table(s)
- `missingness_profile` v1 on dataset version 2 - 2 metric(s), 1 table(s)
- `correlation_matrix` v1 on dataset version 2 - 11 metric(s), 1 table(s)
- `value_distribution` v1 on dataset version 2 - 3 metric(s), 1 table(s)
- `detect_outliers` v1 on dataset version 2 - 4 metric(s), 1 table(s)

**Exploratory analysis:** descriptive statistics, distributions, correlations, segment and trend views were computed with pandas and NumPy and drawn with seaborn and matplotlib. Charts over many rows use a fixed random sample, stated in each caption; tables use every row.

This report includes **7 chart(s)** and **8 evidence table(s)**. Each table cites the stored CSV that holds its unrounded values; the full list is in the appendix.

## 11. Limitations

**No causal claims:** where this report describes relationships, they are associations observed in the data. Establishing cause would require a different study design.

**Questions this dataset cannot answer**

- Questions about change over time: no date column was identified in this dataset.
- Questions comparing segments: no categorical dimension column was identified.

## 12. Suggested next questions

Suggested next questions, based on what this dataset can answer:
1. Drill into the flagged periods and check whether an operational event (campaign, outage, pricing change) lines up with them.
2. Add a target or prior-period column so the numbers can be judged against a goal rather than only against each other.

## 13. Reproducibility

- **Job:** `job_83df4b5da0f0f5ea`
- **Dataset:** `ds_710f18b1fde0d489`
- **Source file hash:** `126888d4a5e8063fa935a95eb8ac09fe3f6bfdfaed19f535ef5d24566ed38381`
- **Rows x columns:** 2,716 x 12
- **Profiler version:** 1
- **Report generated:** 2026-10-01T11:25:26.004809+00:00

Re-running the same file produces the same profile and the same computed metrics. Every table and chart in this report is stored as an artifact and can be downloaded alongside it.

## Appendix: evidence tables

| Artifact | Rows |
| --- | --- |
| `tbl_862ac4a4e48e81f9` | 6 |
| `tbl_78566a73c577ced7` | 12 |
| `tbl_ca0b61299cf90dc4` | 6 |
| `tbl_2e079bc103079869` | 29 |
| `tbl_5fd8c48a2198d868` | 163 |
| `eda_column_overview` | 12 |
| `eda_numeric_summary` | 6 |
| `eda_top_correlations` | 15 |

---

Generated by InsightFlow. Every figure above was computed by a registered analysis tool and stored as an artifact; no number in this report was produced by a language model.
