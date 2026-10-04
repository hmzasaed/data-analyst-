# Analysis of name.basics

**File:** `name.basics.csv`

> This is an **exploratory** report: the user did not ask a specific question, so the analysis is general and not targeted at a decision.

| Rows | Columns | Missing cells | Duplicate rows | Preprocessing | Verified findings | Charts |
| --- | --- | --- | --- | --- | --- | --- |
| 1,000,000 | 6 | 37.7% | 0 | export only | 23 | 8 |

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

This report covers 1,000,000 rows and 6 columns. It was produced in response to this exploratory overview. 23 finding(s) were verified against computed values, 5 of them at medium or high importance. Each finding below links to the artifact it came from.

### Key findings
- **worst missing column (missingness_profile)** — The least complete column in this dataset is death_year. (confidence: high)
- **worst missing share (missingness_profile)** — That column has no usable values in 98.3% of rows. (confidence: high)
- **death_year: anomaly rate pct (detect_outliers)** — 0.0% of evaluated rows were flagged as unusual. (confidence: medium)
- **388 of 1,000,000 rows flagged by iqr_fence** — iqr_fence flagged 388 of 1,000,000 evaluated rows in 'death_year' as unusual relative to the stated baseline. 0.0% of the evaluated rows, flagged for review. (confidence: medium)
- **Most extreme iqr_fence flag: row 1287** — 'death_year' is below the expected range for interquartile fence: 1,918 against 1,930 to 2,070, derived from 16,669 value(s). (confidence: low)

**Most important data-quality caveat:** `death_year` is missing in 98.3% of rows, so any figure involving it is calculated on partial data.

## 2. Dataset overview

**Source file:** name.basics.csv
**Dimensions:** 1,000,000 rows x 6 columns
**Column types:** nconst (integer), primary_name (text), birth_year (integer), death_year (integer), primary_profession (text), known_for_titles (integer)
**Key measures:** nconst, birth_year, death_year, known_for_titles

### Column overview

| Column | Type | Role | Missing | Missing % | Unique values | PII risk |
| --- | --- | --- | ---: | ---: | ---: | --- |
| nconst | integer | measure | 0 | 0.0% | 1,000,000 | none |
| primary_name | text | free text | 8 | 0.0% | 927,763 | none |
| birth_year | integer | measure | 956,529 | 95.7% | 257 | none |
| death_year | integer | measure | 983,331 | 98.3% | 194 | none |
| primary_profession | text | free text | 201,474 | 20.2% | 11,611 | none |
| known_for_titles | integer | measure | 120,295 | 12.0% | 666,574 | none |

_Types and roles are inferred from values and column names. Source: `eda_column_overview.csv`._

## 3. Data-quality assessment

**Missing values**
- `death_year`: 98.3% of rows
- `birth_year`: 95.7% of rows
- `primary_profession`: 20.2% of rows
- `known_for_titles`: 12.0% of rows

**Other issues detected**
- [medium] `primary_name` 927517 normalised forms cover 927763 raw values: casing and whitespace differ between otherwise identical labels.
- [medium] `primary_name` 927,762 distinct values across 1,000,000 rows; unlikely to be a usable grouping key.
- [high] `birth_year` 956,529 of 1,000,000 rows (95.7%) have no value.
- [high] `death_year` 983,331 of 1,000,000 rows (98.3%) have no value.
- [medium] `primary_profession` 201,474 of 1,000,000 rows (20.2%) have no value.
- [medium] `known_for_titles` 120,295 of 1,000,000 rows (12.0%) have no value.

**Cleaning applied**

| Change | Rows affected | Reason | Reversible |
| --- | --- | --- | --- |
| parse_number on `nconst` | 1,000,000 | Parsed 'nconst' to a number with no loss of values. | no (restore from raw) |
| trim_whitespace on `primary_name` | 0 | Trimmed leading and trailing whitespace in 'primary_name'. | yes |
| normalise_categories on `primary_name` | 247 | 'primary_name' has 927762 raw labels for 927517 distinct values once case and whitespace are normalised. | yes |
| parse_number on `birth_year` | 43,471 | Parsed 'birth_year' to a number with no loss of values. | no (restore from raw) |
| parse_number on `death_year` | 16,669 | Parsed 'death_year' to a number with no loss of values. | no (restore from raw) |
| parse_number on `known_for_titles` | 879,705 | Parsed 'known_for_titles' to a number with no loss of values. | no (restore from raw) |

4 proposed change(s) were **not** applied because they were not approved.

### Missing values by column

| Column | Missing | Missing % |
| --- | ---: | ---: |
| death_year | 983,331 | 98.3% |
| birth_year | 956,529 | 95.7% |
| primary_profession | 201,474 | 20.2% |
| known_for_titles | 120,295 | 12.0% |
| primary_name | 8 | 0.0% |

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
| drop columns | - | Dropped 4 column(s) a model cannot use: primary_name (free text); birth_year (96% missing, above the 50% limit); death_year (98% missing, above the 50% limit); primary_profession (free text) |
| impute median | known_for_titles | Filled 120,295 missing value(s) with the median (2.524e+07). |
| scale standard | - | 2 numeric column(s) standardised to mean 0 and standard deviation 1 (scikit-learn StandardScaler). |

_Result: 1,000,000 rows x 2 columns, downloadable from the job's Preprocessing tab._

### Columns left out of the model-ready export

| Column | Reason |
| --- | --- |
| primary_name | free text |
| birth_year | 96% missing, above the 50% limit |
| death_year | 98% missing, above the 50% limit |
| primary_profession | free text |

## 5. Descriptive statistics

Summary statistics for every numeric measure and grouping column, computed on every analysed row.

### Numeric columns

| Column | Count | Mean | Std dev | Min | 25% | Median | 75% | Max | Skew | Kurtosis |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| nconst | 1,000,000 | 9,289,369 | 5,524,575 | 7 | 4,407,448 | 9,320,211 | 13,993,858 | 18,963,961 | 0.03 | -1.21 |
| birth_year | 43,471 | 1,955.0 | 33.96 | 1,150 | 1,934 | 1,961 | 1,981 | 2,025 | -1.99 | 21.99 |
| death_year | 16,669 | 1,995.7 | 31.20 | 1,220 | 1,982 | 2,004 | 2,017 | 2,026 | -5.32 | 78.37 |
| known_for_titles | 879,705 | 726,508,973,871,049,703,890,893,144,064 | 4,393,250,270,748,034,954,753,357,119,488 | 32 | 3,458,542 | 25,244,380 | 33,245,203,654,850,318,336 | 47,656,666,374,299,974,273,745,029,169,152 | 7.05 | 51.52 |

_Count excludes missing values. Skew above 1 or below -1 indicates a strongly lopsided distribution. Source: `eda_numeric_summary.csv`._

## 6. Distributions

How the values of each column are spread: shape, centre, outliers and the most frequent categories.

![Distribution of numeric columns](charts/eda_histograms.png)

*Distribution of numeric columns.* Histograms with a smoothed density curve; solid line = mean, dashed = median. Drawn from a fixed random sample of 20,000 of 1,000,000 rows.

![Spread and outliers](charts/eda_box_plots.png)

*Spread and outliers.* Box = middle 50% of values, line = median, whiskers = 1.5 x IQR; points beyond are unusual relative to the column, not necessarily errors. Drawn from a fixed random sample of 20,000 of 1,000,000 rows.

![Distribution of death_year](charts/chart_5079cfa7ef1bd921.png)

*Distribution of death_year.* 16,669 non-null values across 30 equal-width bins. Bins are equal width; a skewed measure is easier to read as a log scale, which is not shown.

## 7. Relationships between measures

Pairwise correlations between numeric measures. A correlation describes how two columns move together; it is an association, not evidence that one affects the other.

### Strongest correlations

| Column A | Column B | Pearson r | Strength | Rows used |
| --- | --- | ---: | --- | ---: |
| birth_year | death_year | +0.849 | strong positive | 15,531 |
| nconst | birth_year | +0.385 | weak positive | 43,471 |
| birth_year | known_for_titles | +0.186 | negligible | 41,636 |
| nconst | death_year | +0.121 | negligible | 16,669 |
| nconst | known_for_titles | +0.110 | negligible | 879,705 |
| death_year | known_for_titles | +0.031 | negligible | 15,582 |

_Source: `eda_top_correlations.csv`._

![Correlation matrix](charts/eda_correlation_heatmap.png)

*Correlation matrix.* Pearson correlation on rows where both values are present. Correlation measures association only; it does not show that one column affects another.

![birth_year vs death_year](charts/eda_top_pair_scatter.png)

*birth_year vs death_year.* The most strongly correlated pair (r = +0.85), with a least-squares line. Drawn from a fixed random sample of 20,000 of 1,000,000 rows.

## 8. Key findings

#### worst missing column (missingness_profile)

The least complete column in this dataset is death_year.

**Evidence** (tbl_d14ebbef982da579)
  - `worst_missing_column` = death_year

**Confidence:** high  
**Severity:** medium

#### worst missing share (missingness_profile)

That column has no usable values in 98.3% of rows.

**Evidence** (tbl_d14ebbef982da579)
  - `worst_missing_share` = 98.33 %

**Confidence:** high  
**Severity:** medium

#### death_year: anomaly rate pct (detect_outliers)

0.0% of evaluated rows were flagged as unusual.

**Evidence** (tbl_31690be4163ebd77)
  - `death_year__anomaly_rate_pct` = 0.0388 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### 388 of 1,000,000 rows flagged by iqr_fence

iqr_fence flagged 388 of 1,000,000 evaluated rows in 'death_year' as unusual relative to the stated baseline. 0.0% of the evaluated rows, flagged for review.

**Evidence** (anomaly:03b6c0fae9fc5f5f7ebfa2f782e3989a)
  - `death_year__anomalies_flagged` = 388 records
  - `death_year__anomaly_rate_pct` = 0.0388 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### Most extreme iqr_fence flag: row 1287

'death_year' is below the expected range for interquartile fence: 1,918 against 1,930 to 2,070, derived from 16,669 value(s).

**Evidence** (anomaly:03b6c0fae9fc5f5f7ebfa2f782e3989a)
  - `death_year__observed_value` = 1,918 death_year

**Confidence:** low  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.
- Flagged for review by iqr_fence, not evidence of an error or wrongdoing. Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### death_year: skewness (describe_measures, overview_measures)

The distribution is skewed (-5.32), so averages understate the spread.

**Evidence** (tbl_4eb03f08965e3847)
  - `death_year__skewness` = -5.32

**Confidence:** high  
**Severity:** low

#### birth_year: skewness (describe_measures)

The distribution is skewed (-1.99), so averages understate the spread.

**Evidence** (tbl_4eb03f08965e3847)
  - `birth_year__skewness` = -1.989

**Confidence:** high  
**Severity:** low

#### known_for_titles: skewness (describe_measures)

The distribution is skewed (7.05), so averages understate the spread.

**Evidence** (tbl_4eb03f08965e3847)
  - `known_for_titles__skewness` = 7.047

**Confidence:** high  
**Severity:** low

#### nconst: skewness (describe_measures)

The distribution is skewed (0.03), so averages understate the spread.

**Evidence** (tbl_4eb03f08965e3847)
  - `nconst__skewness` = 0.03288

**Confidence:** high  
**Severity:** low

#### death_year: skewness (value_distribution, primary_distribution)

The distribution is skewed (-5.32), so averages understate the spread.

**Evidence** (tbl_0335384693f4885c)
  - `death_year__skewness` = -5.32

**Confidence:** high  
**Severity:** low

#### strongest correlation (correlation_matrix)

The strongest measured association in the data is 0.86.

**Evidence** (tbl_be1ba0aa58e6231d)
  - `strongest_correlation` = 0.8585

**Confidence:** medium  
**Severity:** low

**Limitations**
- Correlation measures association only. It does not establish that either variable causes the other, and a high correlation can arise from a shared third factor.

#### death_year: mean (describe_measures)

The 1,996 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `death_year__mean` = 1,996

**Confidence:** high  
**Severity:** info

#### death_year: median (describe_measures)

The 2,004 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `death_year__median` = 2,004

**Confidence:** high  
**Severity:** info

#### death_year: total (describe_measures)

The 33,266,759 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `death_year__total` = 3.327e+07

**Confidence:** high  
**Severity:** info

#### birth_year: mean (describe_measures)

The 1,955 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `birth_year__mean` = 1,955

**Confidence:** high  
**Severity:** info

#### birth_year: median (describe_measures)

The 1,961 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `birth_year__median` = 1,961

**Confidence:** high  
**Severity:** info

#### birth_year: total (describe_measures)

The 84,987,514 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `birth_year__total` = 8.499e+07

**Confidence:** high  
**Severity:** info

#### known_for_titles: mean (describe_measures)

The 726,508,973,871,049,703,890,893,144,064 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `known_for_titles__mean` = 7.265e+29

**Confidence:** high  
**Severity:** info

#### known_for_titles: median (describe_measures)

The 25,244,380 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `known_for_titles__median` = 2.524e+07

**Confidence:** high  
**Severity:** info

#### known_for_titles: total (describe_measures)

The 639,113,576,859,231,752,766,199,036,886,646,784 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `known_for_titles__total` = 6.391e+35

**Confidence:** high  
**Severity:** info

#### nconst: mean (describe_measures)

The 9,289,369 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `nconst__mean` = 9.289e+06

**Confidence:** high  
**Severity:** info

#### nconst: median (describe_measures)

The 9,320,211 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `nconst__median` = 9.32e+06

**Confidence:** high  
**Severity:** info

#### nconst: total (describe_measures)

The 9,289,369,275,770 value of this measure was computed.

**Evidence** (tbl_4eb03f08965e3847)
  - `nconst__total` = 9.289e+12

**Confidence:** high  
**Severity:** info

## 9. Anomalies

**A flagged record is unusual relative to a stated baseline. It is not evidence of an error, mistake or wrongdoing, and it may be entirely legitimate.**

### `death_year` - iqr_fence

- Flagged: **388** of 1,000,000 evaluated rows (0.0%)
- Baseline: First and third quartiles of 'death_year', extended by 1.5 times the interquartile range on each side.
- Limitation: Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

| Flagged value | Compared against | Severity | Why flagged |
| --- | --- | --- | --- |
| 1,918 | 1,930 to 2,070 | medium | 'death_year' is below the expected range for interquartile fence: 1,918 against 1,930 to 2,070, derived from 16,669 valu |
| 1,929 | 1,930 to 2,070 | medium | 'death_year' is below the expected range for interquartile fence: 1,929 against 1,930 to 2,070, derived from 16,669 valu |
| 1,928 | 1,930 to 2,070 | medium | 'death_year' is below the expected range for interquartile fence: 1,928 against 1,930 to 2,070, derived from 16,669 valu |
| 1,653 | 1,930 to 2,070 | medium | 'death_year' is below the expected range for interquartile fence: 1,653 against 1,930 to 2,070, derived from 16,669 valu |
| 1,888 | 1,930 to 2,070 | medium | 'death_year' is below the expected range for interquartile fence: 1,888 against 1,930 to 2,070, derived from 16,669 valu |
| 1,923 | 1,930 to 2,070 | medium | 'death_year' is below the expected range for interquartile fence: 1,923 against 1,930 to 2,070, derived from 16,669 valu |
| 1,917 | 1,930 to 2,070 | medium | 'death_year' is below the expected range for interquartile fence: 1,917 against 1,930 to 2,070, derived from 16,669 valu |
| 1,913 | 1,930 to 2,070 | medium | 'death_year' is below the expected range for interquartile fence: 1,913 against 1,930 to 2,070, derived from 16,669 valu |

**Suggested review order:** start with high-severity flags, then check whether the surrounding periods and groups corroborate them.

![Values flagged by iqr_fence](charts/chart_c13b05452eccd546.png)

*Values flagged by iqr_fence.* 388 flagged record(s) from death_year. Shown: 15. Detected by iqr_fence. A flagged record is unusual relative to the stated baseline. It is not evidence of an error, and it may be entirely legitimate.

## 10. Methodology

Every figure in this report was computed by a registered analysis tool and stored as an artifact before the report was written. No number was produced by a language model.

**Tools used**

- `describe_measures` v1 on dataset version 2 - 20 metric(s), 1 table(s)
- `missingness_profile` v1 on dataset version 2 - 2 metric(s), 1 table(s)
- `correlation_matrix` v1 on dataset version 2 - 7 metric(s), 1 table(s)
- `value_distribution` v1 on dataset version 2 - 3 metric(s), 1 table(s)
- `detect_outliers` v1 on dataset version 2 - 4 metric(s), 1 table(s)

**Exploratory analysis:** descriptive statistics, distributions, correlations, segment and trend views were computed with pandas and NumPy and drawn with seaborn and matplotlib. Charts over many rows use a fixed random sample, stated in each caption; tables use every row.

This report includes **8 chart(s)** and **9 evidence table(s)**. Each table cites the stored CSV that holds its unrounded values; the full list is in the appendix.

## 11. Limitations

**Missing fields:** `birth_year` (96%), `death_year` (98%), `primary_profession` (20%). Aggregates over these columns exclude missing rows, which can bias totals downward.
**No causal claims:** where this report describes relationships, they are associations observed in the data. Establishing cause would require a different study design.

**Questions this dataset cannot answer**

- Questions about change over time: no date column was identified in this dataset.
- Questions comparing segments: no categorical dimension column was identified.

## 12. Suggested next questions

Suggested next questions, based on what this dataset can answer:
1. Drill into the flagged periods and check whether an operational event (campaign, outage, pricing change) lines up with them.
2. Establish whether the missing values in `birth_year` are random or systematic, because that changes how the results should be read.
3. Add a target or prior-period column so the numbers can be judged against a goal rather than only against each other.

## 13. Reproducibility

- **Job:** `job_9996a6f40de3f22a`
- **Dataset:** `ds_0d956819221a3004`
- **Source file hash:** `6fd1f702a187bbeb757f4fb574da08423d7a770f670e907f4e92231e8bc03950`
- **Rows x columns:** 1,000,000 x 6
- **Profiler version:** 1
- **Report generated:** 2026-10-01T11:32:23.709965+00:00

Re-running the same file produces the same profile and the same computed metrics. Every table and chart in this report is stored as an artifact and can be downloaded alongside it.

## Appendix: evidence tables

| Artifact | Rows |
| --- | --- |
| `tbl_4eb03f08965e3847` | 4 |
| `tbl_d14ebbef982da579` | 6 |
| `tbl_be1ba0aa58e6231d` | 4 |
| `tbl_0335384693f4885c` | 23 |
| `tbl_31690be4163ebd77` | 388 |
| `eda_column_overview` | 6 |
| `eda_missing_values` | 5 |
| `eda_numeric_summary` | 4 |
| `eda_top_correlations` | 6 |

---

Generated by InsightFlow. Every figure above was computed by a registered analysis tool and stored as an artifact; no number in this report was produced by a language model.
