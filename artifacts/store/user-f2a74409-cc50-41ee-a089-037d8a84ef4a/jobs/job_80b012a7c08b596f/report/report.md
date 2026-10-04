# Analysis of Global Economy Indicators

> This is an **exploratory** report: the user did not ask a specific question, so the analysis is general and not targeted at a decision.

## Executive summary

This report covers 10,512 rows and 26 columns. It was produced in response to this exploratory overview. 38 finding(s) were verified against computed values, 9 of them at medium or high importance. Each finding below links to the artifact it came from.

### Key findings
- **worst missing column (missingness_profile)** — The least complete column in this dataset is changes_in_inventories. (confidence: high)
- **worst missing share (missingness_profile)** — That column has no usable values in 17.5% of rows. (confidence: high)
- **changes_in_inventories: overall change pct (time_series_aggregate)** — The measure changed by 1338.6% between the first and last period. (confidence: medium)
- **changes_in_inventories: anomaly rate pct (detect_outliers, anomalies)** — 4.8% of evaluated rows were flagged as unusual. (confidence: medium)
- **changes_in_inventories: anomaly rate pct (detect_outliers, anomalies_over_time)** — 4.8% of evaluated rows were flagged as unusual. (confidence: medium)

**Most important data-quality caveat:** `changes_in_inventories` is missing in 17.5% of rows, so any figure involving it is calculated on partial data.

## Dataset overview

**Source file:** Global Economy Indicators.csv
**Dimensions:** 10,512 rows x 26 columns
**Column types:** country_id (integer), country (text), year (integer), ama_exchange_rate (number), imf_based_exchange_rate (number), population (integer), currency (text), per_capita_gni (integer), agriculture_hunting_forestry_fishing_isic_a_b (integer), changes_in_inventories (integer), construction_isic_f (integer), exports_of_goods_and_services (integer)
**Key measures:** ama_exchange_rate, imf_based_exchange_rate, population, per_capita_gni, agriculture_hunting_forestry_fishing_isic_a_b, changes_in_inventories, construction_isic_f, exports_of_goods_and_services
**Grouping fields:** country_id, country
**Personal data detected:** agriculture_hunting_forestry_fishing_isic_a_b (medium risk), changes_in_inventories (medium risk), construction_isic_f (medium risk), exports_of_goods_and_services (medium risk), final_consumption_expenditure (medium risk), general_government_final_consumption_expenditure (medium risk), gross_capital_formation (medium risk), gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables (medium risk), household_consumption_expenditure_including_non_profit_institutions_serving_households (medium risk), imports_of_goods_and_services (medium risk), manufacturing_isic_d (medium risk), mining_manufacturing_utilities_isic_c_e (medium risk), other_activities_isic_j_p (medium risk), total_value_added (medium risk), transport_storage_and_communication_isic_i (medium risk), wholesale_retail_trade_restaurants_and_hotels_isic_g_h (medium risk), gross_national_income_gni_in_usd (medium risk), gross_domestic_product_gdp (medium risk). Values from these columns are not reproduced in this report.

## Data-quality assessment

**Missing values**
- `changes_in_inventories`: 17.5% of rows
- `agriculture_hunting_forestry_fishing_isic_a_b`: 1.1% of rows
- `general_government_final_consumption_expenditure`: 0.5% of rows
- `gross_capital_formation`: 0.5% of rows
- `gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables`: 0.5% of rows
- `household_consumption_expenditure_including_non_profit_institutions_serving_households`: 0.5% of rows
- `transport_storage_and_communication_isic_i`: 0.5% of rows
- `wholesale_retail_trade_restaurants_and_hotels_isic_g_h`: 0.5% of rows

**Other issues detected**
- [medium] `agriculture_hunting_forestry_fishing_isic_a_b` 121 of 10,512 rows (1.1%) have no value.
- [medium] `changes_in_inventories` 1,841 of 10,512 rows (17.5%) have no value.
- [medium] `exports_of_goods_and_services` 21 of 10,512 rows (0.2%) have no value.
- [medium] `general_government_final_consumption_expenditure` 52 of 10,512 rows (0.5%) have no value.
- [medium] `gross_capital_formation` 52 of 10,512 rows (0.5%) have no value.
- [medium] `gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables` 52 of 10,512 rows (0.5%) have no value.
- [medium] `household_consumption_expenditure_including_non_profit_institutions_serving_households` 52 of 10,512 rows (0.5%) have no value.
- [medium] `imports_of_goods_and_services` 42 of 10,512 rows (0.4%) have no value.

**Cleaning applied**

| Change | Rows affected | Reason | Reversible |
| --- | --- | --- | --- |
| parse_number on `country_id` | 10,512 | Parsed 'country_id' to a number with no loss of values. | no (restore from raw) |
| parse_number on `year` | 10,512 | Parsed 'year' to a number with no loss of values. | no (restore from raw) |
| parse_number on `ama_exchange_rate` | 10,512 | Parsed 'ama_exchange_rate' to a number with no loss of values. | no (restore from raw) |
| parse_number on `imf_based_exchange_rate` | 10,512 | Parsed 'imf_based_exchange_rate' to a number with no loss of values. | no (restore from raw) |
| parse_number on `population` | 10,512 | Parsed 'population' to a number with no loss of values. | no (restore from raw) |
| parse_number on `per_capita_gni` | 10,512 | Parsed 'per_capita_gni' to a number with no loss of values. | no (restore from raw) |
| parse_number on `agriculture_hunting_forestry_fishing_isic_a_b` | 10,391 | Parsed 'agriculture_hunting_forestry_fishing_isic_a_b' to a number with no loss of values. | no (restore from raw) |
| parse_number on `changes_in_inventories` | 8,671 | Parsed 'changes_in_inventories' to a number with no loss of values. | no (restore from raw) |
| parse_number on `construction_isic_f` | 10,512 | Parsed 'construction_isic_f' to a number with no loss of values. | no (restore from raw) |
| parse_number on `exports_of_goods_and_services` | 10,491 | Parsed 'exports_of_goods_and_services' to a number with no loss of values. | no (restore from raw) |
| parse_number on `final_consumption_expenditure` | 10,512 | Parsed 'final_consumption_expenditure' to a number with no loss of values. | no (restore from raw) |
| parse_number on `general_government_final_consumption_expenditure` | 10,460 | Parsed 'general_government_final_consumption_expenditure' to a number with no loss of values. | no (restore from raw) |

29 proposed change(s) were **not** applied because they were not approved.

Personal data was detected. Values from those columns are excluded from this report.

## Key findings

#### worst missing column (missingness_profile)

The least complete column in this dataset is changes_in_inventories.

**Evidence** (tbl_54e2cb8e2178ad5c)
  - `worst_missing_column` = changes_in_inventories

**Confidence:** high  
**Severity:** medium

#### worst missing share (missingness_profile)

That column has no usable values in 17.5% of rows.

**Evidence** (tbl_54e2cb8e2178ad5c)
  - `worst_missing_share` = 17.51 %

**Confidence:** high  
**Severity:** medium

#### changes_in_inventories: overall change pct (time_series_aggregate)

The measure changed by 1338.6% between the first and last period.

**Evidence** (tbl_ee62fa5cc411fb04)
  - `changes_in_inventories__overall_change_pct` = 1,339 %

**Confidence:** medium  
**Severity:** medium

#### changes_in_inventories: anomaly rate pct (detect_outliers, anomalies)

4.8% of evaluated rows were flagged as unusual.

**Evidence** (tbl_ca718ae5dfc11d8c)
  - `changes_in_inventories__anomaly_rate_pct` = 4.756 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### changes_in_inventories: anomaly rate pct (detect_outliers, anomalies_over_time)

4.8% of evaluated rows were flagged as unusual.

**Evidence** (tbl_158199e92f307952)
  - `changes_in_inventories__anomaly_rate_pct` = 4.756 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Requires reliable time ordering and enough history; results change with the window size and are unstable for short series.
- The first 7 point(s) have no trailing history and cannot be scored.
- 11 point(s) followed a perfectly flat window. With no variation to compare against, any departure from that level is flagged, so expect these flags to be easy to explain and easy to dismiss.

#### 500 of 10,512 rows flagged by iqr_fence

iqr_fence flagged 500 of 10,512 evaluated rows in 'changes_in_inventories' as unusual relative to the stated baseline. 4.8% of the evaluated rows, flagged for review.

**Evidence** (anomaly:42db9cc1e64a6646422fe11aa719effe)
  - `changes_in_inventories__anomalies_flagged` = 500 records
  - `changes_in_inventories__anomaly_rate_pct` = 4.756 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### 500 of 10,512 rows flagged by iqr_fence

iqr_fence flagged 500 of 10,512 evaluated rows in 'changes_in_inventories' as unusual relative to the stated baseline. 4.8% of the evaluated rows, flagged for review.

**Evidence** (anomaly:8a570a3c83b85e14f1fbe7275d345fd1)
  - `changes_in_inventories__anomalies_flagged` = 500 records
  - `changes_in_inventories__anomaly_rate_pct` = 4.756 %

**Confidence:** medium  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### Most extreme iqr_fence flag: row 114

'changes_in_inventories' is above the expected range for interquartile fence: 2,193,385,763 against -1,035,444,162 to 1,726,986,548, derived from 8,671 value(s).

**Evidence** (anomaly:42db9cc1e64a6646422fe11aa719effe)
  - `changes_in_inventories__observed_value` = 2.193e+09 changes_in_inventories

**Confidence:** low  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.
- Flagged for review by iqr_fence, not evidence of an error or wrongdoing. Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### Most extreme iqr_fence flag: row 114

'changes_in_inventories' is above the expected range for interquartile fence: 2,193,385,763 against -1,035,444,162 to 1,726,986,548, derived from 8,671 value(s).

**Evidence** (anomaly:8a570a3c83b85e14f1fbe7275d345fd1)
  - `changes_in_inventories__observed_value` = 2.193e+09 changes_in_inventories

**Confidence:** low  
**Severity:** medium

**Limitations**
- Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.
- Flagged for review by iqr_fence, not evidence of an error or wrongdoing. Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

#### changes_in_inventories: skewness (describe_measures, overview_measures)

The distribution is skewed (10.12), so averages understate the spread.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `changes_in_inventories__skewness` = 10.12

**Confidence:** high  
**Severity:** low

#### agriculture_hunting_forestry_fishing_isic_a_b: skewness (describe_measures)

The distribution is skewed (18.91), so averages understate the spread.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `agriculture_hunting_forestry_fishing_isic_a_b__skewness` = 18.91

**Confidence:** high  
**Severity:** low

#### general_government_final_consumption_expenditure: skewness (describe_measures)

The distribution is skewed (11.62), so averages understate the spread.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `general_government_final_consumption_expenditure__skewness` = 11.62

**Confidence:** high  
**Severity:** low

#### gross_capital_formation: skewness (describe_measures)

The distribution is skewed (14.53), so averages understate the spread.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `gross_capital_formation__skewness` = 14.53

**Confidence:** high  
**Severity:** low

#### gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables: skewness (describe_measures)

The distribution is skewed (14.53), so averages understate the spread.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables__skewness` = 14.53

**Confidence:** high  
**Severity:** low

#### household_consumption_expenditure_including_non_profit_institutions_serving_households: skewness (describe_measures)

The distribution is skewed (15.04), so averages understate the spread.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `household_consumption_expenditure_including_non_profit_institutions_serving_households__skewness` = 15.04

**Confidence:** high  
**Severity:** low

#### changes_in_inventories: skewness (value_distribution, primary_distribution)

The distribution is skewed (10.12), so averages understate the spread.

**Evidence** (tbl_3a84c421ea92e453)
  - `changes_in_inventories__skewness` = 10.12

**Confidence:** high  
**Severity:** low

#### strongest correlation (correlation_matrix)

The strongest measured association in the data is 1.00.

**Evidence** (tbl_d53845341dae98a6)
  - `strongest_correlation` = 0.9997

**Confidence:** medium  
**Severity:** low

**Limitations**
- Correlation measures association only. It does not establish that either variable causes the other, and a high correlation can arise from a shared third factor.

#### changes_in_inventories: mean (describe_measures)

The 1,626,543,203 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `changes_in_inventories__mean` = 1.627e+09

**Confidence:** high  
**Severity:** info

#### changes_in_inventories: median (describe_measures)

The 68,296,349 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `changes_in_inventories__median` = 6.83e+07

**Confidence:** high  
**Severity:** info

#### changes_in_inventories: total (describe_measures)

The 14,103,756,112,774 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `changes_in_inventories__total` = 1.41e+13

**Confidence:** high  
**Severity:** info

#### agriculture_hunting_forestry_fishing_isic_a_b: mean (describe_measures)

The 7,793,212,164 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `agriculture_hunting_forestry_fishing_isic_a_b__mean` = 7.793e+09

**Confidence:** high  
**Severity:** info

#### agriculture_hunting_forestry_fishing_isic_a_b: median (describe_measures)

The 931,367,444 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `agriculture_hunting_forestry_fishing_isic_a_b__median` = 9.314e+08

**Confidence:** high  
**Severity:** info

#### agriculture_hunting_forestry_fishing_isic_a_b: total (describe_measures)

The 80,979,267,599,877 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `agriculture_hunting_forestry_fishing_isic_a_b__total` = 8.098e+13

**Confidence:** high  
**Severity:** info

#### general_government_final_consumption_expenditure: mean (describe_measures)

The 30,853,786,134 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `general_government_final_consumption_expenditure__mean` = 3.085e+10

**Confidence:** high  
**Severity:** info

#### general_government_final_consumption_expenditure: median (describe_measures)

The 1,122,262,120 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `general_government_final_consumption_expenditure__median` = 1.122e+09

**Confidence:** high  
**Severity:** info

#### general_government_final_consumption_expenditure: total (describe_measures)

The 322,730,602,964,999 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `general_government_final_consumption_expenditure__total` = 3.227e+14

**Confidence:** high  
**Severity:** info

#### gross_capital_formation: mean (describe_measures)

The 46,537,415,280 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `gross_capital_formation__mean` = 4.654e+10

**Confidence:** high  
**Severity:** info

#### gross_capital_formation: median (describe_measures)

The 1,766,391,186 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `gross_capital_formation__median` = 1.766e+09

**Confidence:** high  
**Severity:** info

#### gross_capital_formation: total (describe_measures)

The 486,781,363,830,717 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `gross_capital_formation__total` = 4.868e+14

**Confidence:** high  
**Severity:** info

#### gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables: mean (describe_measures)

The 45,189,202,120 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables__mean` = 4.519e+10

**Confidence:** high  
**Severity:** info

#### gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables: median (describe_measures)

The 1,631,733,278 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables__median` = 1.632e+09

**Confidence:** high  
**Severity:** info

#### gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables: total (describe_measures)

The 472,679,054,173,822 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `gross_fixed_capital_formation_including_acquisitions_less_disposals_of_valuables__total` = 4.727e+14

**Confidence:** high  
**Severity:** info

#### household_consumption_expenditure_including_non_profit_institutions_serving_households: mean (describe_measures)

The 105,504,146,606 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `household_consumption_expenditure_including_non_profit_institutions_serving_households__mean` = 1.055e+11

**Confidence:** high  
**Severity:** info

#### household_consumption_expenditure_including_non_profit_institutions_serving_households: median (describe_measures)

The 5,154,424,420 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `household_consumption_expenditure_including_non_profit_institutions_serving_households__median` = 5.154e+09

**Confidence:** high  
**Severity:** info

#### household_consumption_expenditure_including_non_profit_institutions_serving_households: total (describe_measures)

The 1,103,573,373,503,293 value of this measure was computed.

**Evidence** (tbl_f4f17d0bd9e118aa)
  - `household_consumption_expenditure_including_non_profit_institutions_serving_households__total` = 1.104e+15

**Confidence:** high  
**Severity:** info

#### period count (time_series_aggregate)

The series covers 52 periods periods.

**Evidence** (tbl_ee62fa5cc411fb04)
  - `period_count` = 52 periods

**Confidence:** high  
**Severity:** info

#### changes_in_inventories: first period total (time_series_aggregate)

The period total was 46,553,060,712.

**Evidence** (tbl_ee62fa5cc411fb04)
  - `changes_in_inventories__first_period_total` = 4.655e+10

**Confidence:** high  
**Severity:** info

#### changes_in_inventories: last period total (time_series_aggregate)

The period total was 669,720,069,370.

**Evidence** (tbl_ee62fa5cc411fb04)
  - `changes_in_inventories__last_period_total` = 6.697e+11

**Confidence:** high  
**Severity:** info

## Anomalies

**A flagged record is unusual relative to a stated baseline. It is not evidence of an error, mistake or wrongdoing, and it may be entirely legitimate.**

### `changes_in_inventories` - iqr_fence

- Flagged: **500** of 10,512 evaluated rows (4.8%)
- Baseline: First and third quartiles of 'changes_in_inventories', extended by 1.5 times the interquartile range on each side.
- Limitation: Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

| Flagged value | Compared against | Severity | Why flagged |
| --- | --- | --- | --- |
| 2,193,385,763 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 2,193,385,763 against -1,035,444,162 to 1, |
| 1,749,922,955 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 1,749,922,955 against -1,035,444,162 to 1, |
| 1,987,224,514 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 1,987,224,514 against -1,035,444,162 to 1, |
| 2,709,734,364 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 2,709,734,364 against -1,035,444,162 to 1, |
| 1,866,610,977 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 1,866,610,977 against -1,035,444,162 to 1, |
| 1,868,800,310 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 1,868,800,310 against -1,035,444,162 to 1, |
| 2,575,931,627 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 2,575,931,627 against -1,035,444,162 to 1, |
| 3,597,477,745 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 3,597,477,745 against -1,035,444,162 to 1, |

### `changes_in_inventories` - iqr_fence

- Flagged: **500** of 10,512 evaluated rows (4.8%)
- Baseline: First and third quartiles of 'changes_in_inventories', extended by 1.5 times the interquartile range on each side.
- Limitation: Univariate fences miss anomalies that only look unusual in context, and assume a roughly symmetric distribution.

| Flagged value | Compared against | Severity | Why flagged |
| --- | --- | --- | --- |
| 2,193,385,763 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 2,193,385,763 against -1,035,444,162 to 1, |
| 1,749,922,955 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 1,749,922,955 against -1,035,444,162 to 1, |
| 1,987,224,514 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 1,987,224,514 against -1,035,444,162 to 1, |
| 2,709,734,364 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 2,709,734,364 against -1,035,444,162 to 1, |
| 1,866,610,977 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 1,866,610,977 against -1,035,444,162 to 1, |
| 1,868,800,310 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 1,868,800,310 against -1,035,444,162 to 1, |
| 2,575,931,627 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 2,575,931,627 against -1,035,444,162 to 1, |
| 3,597,477,745 | -1,035,444,162 to 1,726,986,548 | medium | 'changes_in_inventories' is above the expected range for interquartile fence: 3,597,477,745 against -1,035,444,162 to 1, |

**Suggested review order:** start with high-severity flags, then check whether the surrounding periods and groups corroborate them.

## Methodology

Every figure in this report was computed by a registered analysis tool and stored as an artifact before the report was written. No number was produced by a language model.

**Tools used**

- `describe_measures` v1 on dataset version 2 - 30 metric(s), 1 table(s)
- `missingness_profile` v1 on dataset version 2 - 2 metric(s), 1 table(s)
- `time_series_aggregate` v1 on dataset version 2 - 4 metric(s), 1 table(s)
- `growth_rates` v1 on dataset version 2 - 4 metric(s), 1 table(s)
- `correlation_matrix` v1 on dataset version 2 - 11 metric(s), 1 table(s)
- `value_distribution` v1 on dataset version 2 - 3 metric(s), 1 table(s)
- `detect_outliers` v1 on dataset version 2 - 4 metric(s), 1 table(s)
- `detect_outliers` v1 on dataset version 2 - 4 metric(s), 1 table(s)

**Charts**

- `chart_df117ea85bbe9c33` (image/png)
- `chart_3f53808c2b054c55` (image/png)
- `chart_9cecf88af38e15a5` (image/png)
- `chart_adc7e876b59355ef` (image/png)
- `chart_f29b96c927ebc7f6` (image/png)

**Evidence tables**

- `tbl_f4f17d0bd9e118aa` - 6 rows
- `tbl_54e2cb8e2178ad5c` - 26 rows
- `tbl_ee62fa5cc411fb04` - 52 rows
- `tbl_4a7d9d2092212272` - 10,512 rows
- `tbl_d53845341dae98a6` - 6 rows
- `tbl_3a84c421ea92e453` - 24 rows
- `tbl_ca718ae5dfc11d8c` - 500 rows
- `tbl_158199e92f307952` - 500 rows

## Limitations

**No causal claims:** where this report describes relationships, they are associations observed in the data. Establishing cause would require a different study design.

## Reproducibility

- **Job:** `job_80b012a7c08b596f`
- **Dataset:** `ds_0d06f3cc69889acb`
- **Source file hash:** `858e69c3d62a9272513e9d49ed69fa4cf9a141e7bc6dd3d0a45330d235ef8d9a`
- **Rows x columns:** 10,512 x 26
- **Profiler version:** 1
- **Report generated:** 2026-10-01T09:40:38.278715+00:00

Re-running the same file produces the same profile and the same computed metrics. Every table and chart in this report is stored as an artifact and can be downloaded alongside it.

## Suggested next questions

Suggested next questions, based on what this dataset can answer:
1. Compare `country_id` across time rather than in total, to separate mix shift from volume change.
2. Drill into the flagged periods and check whether an operational event (campaign, outage, pricing change) lines up with them.
3. Establish whether the missing values in `changes_in_inventories` are random or systematic, because that changes how the results should be read.
4. Add a target or prior-period column so the numbers can be judged against a goal rather than only against each other.

## Charts

- `chart_df117ea85bbe9c33` - image/png (75,640 bytes)
- `chart_3f53808c2b054c55` - image/png (80,393 bytes)
- `chart_9cecf88af38e15a5` - image/png (70,885 bytes)
- `chart_adc7e876b59355ef` - image/png (55,554 bytes)
- `chart_f29b96c927ebc7f6` - image/png (54,475 bytes)

## Evidence tables

| Artifact | Rows |
| --- | --- |
| `tbl_f4f17d0bd9e118aa` | 6 |
| `tbl_54e2cb8e2178ad5c` | 26 |
| `tbl_ee62fa5cc411fb04` | 52 |
| `tbl_4a7d9d2092212272` | 10,512 |
| `tbl_d53845341dae98a6` | 6 |
| `tbl_3a84c421ea92e453` | 24 |
| `tbl_ca718ae5dfc11d8c` | 500 |
| `tbl_158199e92f307952` | 500 |

---

Generated by InsightFlow. Every figure above was computed by a registered analysis tool and stored as an artifact; no number in this report was produced by a language model.
