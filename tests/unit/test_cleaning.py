"""Cleaning: proposals, deterministic transforms, reversibility and the audit log."""

from __future__ import annotations

import polars as pl
import pytest
from insightflow_cleaning import apply_cleaning, next_snapshot, plan_cleaning
from insightflow_cleaning.transforms import canonical_category_map
from insightflow_contracts import ReviewLevel, TransformKind


@pytest.fixture
def messy_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "region": ["West", "west", " WEST ", "North", "north", "North"],
            "revenue": ["1,200.50", "$980", "n/a", "1200.50", "1450", "990.25"],
            "order_date": [
                "2024-01-05", "2024-01-06", "06/02/2024", "2024-02-10", "2024-02-11", "03/01/2024"
            ],
        }
    )


@pytest.fixture
def messy_snapshot(messy_frame: pl.DataFrame):
    from insightflow_contracts import DatasetSnapshot

    return DatasetSnapshot(
        dataset_id="ds_messy",
        artifact_id="art_v1",
        version=1,
        row_count=messy_frame.height,
        column_count=messy_frame.width,
        columns=messy_frame.columns,
        source_file_hash="h",
    )


@pytest.fixture
def messy_profile(messy_frame: pl.DataFrame, messy_snapshot):
    from insightflow_profiling import profile_dataset

    return profile_dataset(messy_frame, messy_snapshot)


class TestPlanning:
    def test_plan_proposes_parsing_for_measures(self, messy_profile):
        plan = plan_cleaning(messy_profile)
        kinds = {op.kind for op in plan.operations}
        assert TransformKind.PARSE_NUMBER in kinds
        assert TransformKind.PARSE_DATETIME in kinds

    def test_parse_with_failures_needs_review(self, messy_profile):
        # `revenue` contains one unparseable value, so conversion is proposed but
        # not applied automatically.
        plan = plan_cleaning(messy_profile)
        parse = next(op for op in plan.operations if op.kind is TransformKind.PARSE_NUMBER)
        assert parse.review_level is ReviewLevel.RECOMMENDED

    def test_clean_parse_is_automatic(self):
        from insightflow_cleaning import plan_cleaning as _plan
        from insightflow_contracts import DatasetSnapshot
        from insightflow_profiling import profile_dataset

        frame = pl.DataFrame(
            {
                "amount": ["1,200.50", "$980", "990.25", "1450.00", "310.75", "2,000.00"],
            }
        )
        snapshot = DatasetSnapshot(
            dataset_id="d", artifact_id="a", version=1, row_count=frame.height,
            column_count=frame.width, columns=frame.columns, source_file_hash="h",
        )
        plan = _plan(profile_dataset(frame, snapshot))
        parse = next(op for op in plan.operations if op.kind is TransformKind.PARSE_NUMBER)
        assert parse.review_level is ReviewLevel.AUTOMATIC

    def test_imputation_is_never_automatic(self, messy_profile):
        plan = plan_cleaning(messy_profile)
        for operation in plan.operations:
            if operation.kind in {TransformKind.FILL_NULL_WITH_MEDIAN, TransformKind.FILL_NULL_WITH_CONSTANT}:
                assert operation.review_level is not ReviewLevel.AUTOMATIC

    def test_no_operation_proposes_dropping_rows_but_duplicates(self, messy_profile):
        plan = plan_cleaning(messy_profile)
        structural = {op.kind for op in plan.operations if op.column is None}
        assert structural <= {TransformKind.DROP_EXACT_DUPLICATE_ROWS}

    def test_pii_columns_are_proposed_for_masking(self, sales_frame, sales_snapshot):
        from insightflow_contracts import DatasetSnapshot
        from insightflow_profiling import profile_dataset

        frame = sales_frame.with_columns(
            pl.Series("customer_email", [f"user{i}@example.com" for i in range(sales_frame.height)])
        )
        snapshot = DatasetSnapshot(
            dataset_id="d", artifact_id="a", version=1, row_count=frame.height,
            column_count=frame.width, columns=frame.columns, source_file_hash="h",
        )
        plan = plan_cleaning(profile_dataset(frame, snapshot))
        assert any(op.kind is TransformKind.MASK_PII for op in plan.operations)

    def test_operation_ids_are_stable(self, messy_profile):
        first = [op.operation_id for op in plan_cleaning(messy_profile).operations]
        second = [op.operation_id for op in plan_cleaning(messy_profile).operations]
        assert first == second


@pytest.fixture
def parse_only_plan(messy_profile):
    """Every operation approved except imputation, which changes what a null means.

    Approving imputation is covered separately, because it is the one transform that
    makes a missing value look like a measurement.
    """
    plan = plan_cleaning(messy_profile)
    approved = {
        op.operation_id
        for op in plan.operations
        if op.kind not in {TransformKind.FILL_NULL_WITH_MEDIAN, TransformKind.FILL_NULL_WITH_CONSTANT}
    }
    return plan, approved


@pytest.fixture
def everything_approved(messy_profile):
    plan = plan_cleaning(messy_profile)
    return plan, {op.operation_id for op in plan.operations}


class TestExecution:
    def test_only_automatic_operations_apply_by_default(self, messy_frame, messy_profile, messy_snapshot):
        plan = plan_cleaning(messy_profile)
        _, execution = apply_cleaning(messy_frame, plan, messy_snapshot)
        assert all(entry.review_level is ReviewLevel.AUTOMATIC for entry in execution.applied)
        assert execution.rejected

    def test_date_parsing_chooses_a_format_and_reports_failures(
        self, messy_frame, parse_only_plan, messy_snapshot
    ):
        plan, approved = parse_only_plan
        cleaned, execution = apply_cleaning(
            messy_frame, plan, messy_snapshot, approved_operation_ids=approved
        )
        assert cleaned.schema["order_date"] == pl.Datetime
        parsed_entry = next(
            entry for entry in execution.applied if entry.kind is TransformKind.PARSE_DATETIME
        )
        # Every non-null row was touched by the conversion...
        assert parsed_entry.rows_affected == messy_frame["order_date"].drop_nulls().len()
        # ...and the rows that did not parse are visible as a rise in nulls rather
        # than being silently dropped or guessed at.
        failures = parsed_entry.null_count_after - (parsed_entry.null_count_before or 0)
        assert failures > 0
        assert cleaned["order_date"].null_count() == parsed_entry.null_count_after

    def test_number_parsing_keeps_valid_values(self, messy_frame, parse_only_plan, messy_snapshot):
        plan, approved = parse_only_plan
        cleaned, _ = apply_cleaning(
            messy_frame, plan, messy_snapshot, approved_operation_ids=approved
        )
        values = cleaned["revenue"].to_list()
        assert 1200.50 in values
        assert 980.0 in values

    def test_unparseable_number_becomes_null_not_zero(self, messy_frame, parse_only_plan, messy_snapshot):
        plan, approved = parse_only_plan
        cleaned, _ = apply_cleaning(
            messy_frame, plan, messy_snapshot, approved_operation_ids=approved
        )
        assert cleaned["revenue"].null_count() >= 1
        assert 0.0 not in cleaned["revenue"].to_list()

    def test_approved_imputation_is_applied_and_recorded(
        self, messy_frame, everything_approved, messy_snapshot
    ):
        # Imputing is offered for review precisely because it makes a missing value
        # indistinguishable from a measured one, so it must be approved explicitly.
        plan, approved = everything_approved
        _, execution = apply_cleaning(
            messy_frame, plan, messy_snapshot, approved_operation_ids=approved
        )
        imputations = [
            entry for entry in execution.applied
            if entry.kind is TransformKind.FILL_NULL_WITH_MEDIAN
        ]
        if not imputations:
            pytest.skip("this fixture produced no imputation proposal")
        assert imputations[0].review_level is not ReviewLevel.AUTOMATIC
        assert imputations[0].rows_affected >= 1

    def test_change_log_records_before_and_after(self, messy_frame, parse_only_plan, messy_snapshot):
        plan, approved = parse_only_plan
        _, execution = apply_cleaning(
            messy_frame, plan, messy_snapshot, approved_operation_ids=approved
        )
        assert execution.applied
        for entry in execution.applied:
            assert entry.rows_before > 0
            assert entry.reason
            assert entry.inverse_note

    def test_dataset_version_advances(self, messy_frame, parse_only_plan, messy_snapshot):
        plan, approved = parse_only_plan
        _, execution = apply_cleaning(
            messy_frame, plan, messy_snapshot, approved_operation_ids=approved
        )
        assert execution.to_version == messy_snapshot.version + 1
        assert next_snapshot(messy_snapshot, execution).version == 2

    def test_original_frame_is_not_mutated(self, messy_frame, parse_only_plan, messy_snapshot):
        before = messy_frame.to_dicts()
        plan, approved = parse_only_plan
        apply_cleaning(messy_frame, plan, messy_snapshot, approved_operation_ids=approved)
        assert messy_frame.to_dicts() == before

    def test_explicit_approval_applies_reviewed_operations(self, messy_frame, parse_only_plan, messy_snapshot):
        plan, _all_approved = parse_only_plan
        approved = {
            op.operation_id
            for op in plan.operations
            if op.kind is TransformKind.NORMALISE_CATEGORIES
        }
        if not approved:
            pytest.skip("this fixture does not produce a category-normalisation proposal")
        cleaned, execution = apply_cleaning(
            messy_frame, plan, messy_snapshot, approved_operation_ids=approved
        )
        assert any(entry.kind is TransformKind.NORMALISE_CATEGORIES for entry in execution.applied)
        assert cleaned["region"].n_unique() < messy_frame["region"].n_unique()

    def test_duplicate_removal_is_visible(self, messy_frame, messy_snapshot):
        from insightflow_cleaning.transforms import drop_exact_duplicates

        doubled = pl.concat([messy_frame, messy_frame.head(3)])
        unique, removed = drop_exact_duplicates(doubled)
        assert removed == 3
        assert unique.height == messy_frame.height


class TestDeterminism:
    def test_category_map_is_stable(self):
        series = pl.Series("region", ["West", "west", "WEST", "East", "east", "North"])
        first = canonical_category_map(series)
        second = canonical_category_map(pl.Series("region", series.to_list()[::-1]))
        assert first == second

    def test_execution_is_reproducible(self, messy_frame, parse_only_plan, messy_snapshot):
        plan, approved = parse_only_plan
        _, first = apply_cleaning(messy_frame, plan, messy_snapshot, approved_operation_ids=approved)
        _, second = apply_cleaning(messy_frame, plan, messy_snapshot, approved_operation_ids=approved)
        assert first.transform_digest == second.transform_digest


class TestMedianFillSafety:
    """Regression: 'Approve all' on an IMDb export failed the job with
    "must specify either a fill `value` or `strategy`" - a median fill had been
    offered for a text column (`genres`), which has no median."""

    @pytest.fixture
    def imdb_like(self):
        from insightflow_contracts import DatasetSnapshot
        from insightflow_profiling import profile_dataset

        n = 100
        frame = pl.DataFrame({
            "tconst": [f"tt{i:07d}" for i in range(n)],
            "genres": [None if i % 4 == 0 else ["Drama", "Comedy", "Short"][i % 3] for i in range(n)],
            "start_year": [None if i % 10 == 0 else 1990 + i % 30 for i in range(n)],
            "end_year": [2000 + i if i < 5 else None for i in range(n)],
            "runtime": [None if i % 3 == 0 else 60 + i for i in range(n)],
        })
        snapshot = DatasetSnapshot(dataset_id="ds", artifact_id="a", version=1, row_count=n,
                                   column_count=frame.width, columns=frame.columns, source_file_hash="h")
        return frame, snapshot, profile_dataset(frame, snapshot)

    def _fills(self, plan):
        return [op for op in plan.operations if op.kind is TransformKind.FILL_NULL_WITH_MEDIAN]

    def test_median_fill_is_offered_only_for_numeric_mostly_complete_columns(self, imdb_like):
        _, _, profile = imdb_like
        fills = self._fills(plan_cleaning(profile))
        columns = [op.column for op in fills]
        assert "genres" not in columns          # text has no median
        assert "end_year" not in columns        # 95% missing: imputing would invent data
        assert len(columns) == len(set(columns))  # at most one fill per column
        assert {"start_year", "runtime"} <= set(columns)

    def test_approving_everything_cannot_fail_the_cleaning(self, imdb_like):
        frame, snapshot, profile = imdb_like
        plan = plan_cleaning(profile)
        # Simulate an old plan that still carries a median fill for the text column.
        bogus = self._fills(plan)[0].model_copy(update={"operation_id": "op_bogus", "column": "genres"})
        plan.operations.append(bogus)
        approved = {op.operation_id for op in plan.operations}
        cleaned, execution = apply_cleaning(frame, plan, snapshot, approved_operation_ids=approved)
        assert "op_bogus" in {op.operation_id for op in execution.rejected}
        assert cleaned["genres"].null_count() == frame["genres"].null_count()
        assert cleaned["runtime"].null_count() == 0

    def test_integer_columns_keep_their_type(self, imdb_like):
        from insightflow_cleaning.transforms import fill_null_with_median

        frame, _, _ = imdb_like
        filled, affected = fill_null_with_median(frame, "start_year")
        assert filled["start_year"].dtype == frame["start_year"].dtype
        assert affected == frame["start_year"].null_count()
