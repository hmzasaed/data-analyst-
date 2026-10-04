"""Feature engineering and selection agents: validation, leakage, ranking, determinism."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from insightflow_contracts import (
    ColumnProfile,
    DatasetProfile,
    FeatureOptions,
    FeaturePlan,
    FeatureSpec,
    InferredType,
    LLMSettings,
    PiiRisk,
    PreprocessingOptions,
    SemanticRole,
)


@pytest.fixture
def frame() -> pl.DataFrame:
    rng = np.random.default_rng(7)
    n = 300
    units = rng.integers(1, 40, n).astype(float)
    price = rng.choice([19.0, 49.0, 99.0], n)
    return pl.DataFrame(
        {
            "order_id": np.arange(n),
            "day": pl.date_range(pl.date(2024, 1, 1), pl.date(2024, 1, 1) + pl.duration(days=n - 1), eager=True),
            "region": rng.choice(["North", "South", "East"], n),
            "units": units,
            "unit_price": price,
            "noise": rng.normal(0, 1, n),
            "email": [f"user{i}@example.com" for i in range(n)],
            "revenue": units * price + rng.normal(0, 5, n),
        }
    )


@pytest.fixture
def profile(frame) -> DatasetProfile:
    roles = {
        "order_id": (InferredType.INTEGER, SemanticRole.IDENTIFIER, PiiRisk.NONE),
        "day": (InferredType.DATE, SemanticRole.TIME, PiiRisk.NONE),
        "region": (InferredType.CATEGORY, SemanticRole.GEOGRAPHY, PiiRisk.NONE),
        "units": (InferredType.NUMBER, SemanticRole.MEASURE, PiiRisk.NONE),
        "unit_price": (InferredType.NUMBER, SemanticRole.MEASURE, PiiRisk.NONE),
        "noise": (InferredType.NUMBER, SemanticRole.MEASURE, PiiRisk.NONE),
        "email": (InferredType.TEXT, SemanticRole.FREE_TEXT, PiiRisk.HIGH),
        "revenue": (InferredType.NUMBER, SemanticRole.MEASURE, PiiRisk.NONE),
    }
    return DatasetProfile(
        dataset_id="ds", source_file_hash="h", row_count=frame.height, column_count=frame.width,
        columns=[ColumnProfile(name=n, inferred_type=t, semantic_role=r, pii_risk=p) for n, (t, r, p) in roles.items()],
    )


class TestTargetChoice:
    def test_question_names_the_target(self, frame, profile):
        from insightflow_features import choose_target

        choice = choose_target(frame, profile, question="What predicts units sold?", requested=None)
        assert (choice.column, choice.task, choice.reason) == ("units", "regression", "named in your question")

    def test_requested_target_wins_and_bad_requests_fall_back(self, frame, profile):
        from insightflow_features import choose_target

        assert choose_target(frame, profile, question=None, requested="unit_price").column == "unit_price"
        assert choose_target(frame, profile, question=None, requested="unit_price").task == "classification"
        fallback = choose_target(frame, profile, question=None, requested="email")
        assert fallback.column == "revenue"  # the headline measure
        assert fallback.warnings

    def test_identifiers_are_never_targets(self, frame, profile):
        from insightflow_features import choose_target

        assert choose_target(frame, profile, question="predict order_id", requested=None).column != "order_id"


class TestValidation:
    @pytest.mark.parametrize(
        ("spec", "reason"),
        [
            (FeatureSpec(name="leak", kind="ratio", inputs=["revenue", "units"]), "leak the answer"),
            (FeatureSpec(name="pii", kind="frequency", inputs=["email"]), "personal data"),
            (FeatureSpec(name="ghost", kind="log1p", inputs=["no_such_column"]), "not a column"),
            (FeatureSpec(name="bad", kind="log1p", inputs=["region"]), "numeric inputs"),
            (FeatureSpec(name="units", kind="square", inputs=["noise"]), "already exists"),
            (FeatureSpec(name="neg", kind="log1p", inputs=["noise"]), "non-negative"),
            (FeatureSpec(name="arity", kind="product", inputs=["units"]), "takes 2 input"),
            (FeatureSpec(name="nodate", kind="cyclical_month", inputs=["units"]), "date column"),
            (FeatureSpec(name="ids", kind="square", inputs=["order_id"]), "identifier"),
        ],
    )
    def test_invalid_proposals_are_refused(self, frame, profile, spec, reason):
        from insightflow_features import validate_spec

        assert reason in (validate_spec(spec, frame, profile, "revenue", set()) or "")

    def test_computed_values_are_right(self, frame, profile):
        from insightflow_features import apply_features

        specs = [
            FeatureSpec(name="gross", kind="product", inputs=["units", "unit_price"]),
            FeatureSpec(name="m", kind="cyclical_month", inputs=["day"]),
            FeatureSpec(name="freq", kind="frequency", inputs=["region"]),
        ]
        widened, created, rejected = apply_features(frame, specs, profile, "revenue")
        assert not rejected and len(created) == 3
        assert widened["gross"].to_list() == (frame["units"] * frame["unit_price"]).to_list()
        assert {"m_sin", "m_cos"} <= set(widened.columns)
        share = (frame["region"] == frame["region"][0]).mean()
        assert widened["freq"][0] == pytest.approx(share)
        assert "gross" not in frame.columns  # the input frame is untouched


class TestAgents:
    def test_rule_based_engineering_never_uses_the_target(self, frame, profile):
        from insightflow_agents import FeatureEngineeringAgent

        outcome = FeatureEngineeringAgent().run(frame, profile, FeatureOptions(), question="predict revenue")
        assert outcome.result.target_column == "revenue"
        assert outcome.result.created
        for item in outcome.result.created:
            assert "revenue" not in item.spec.inputs
            assert "email" not in item.spec.inputs

    def test_a_models_leaking_or_invented_features_are_rejected(self, frame, profile):
        from insightflow_agents import FeatureEngineeringAgent, LLMClient

        client = LLMClient(settings=LLMSettings(enabled=True, api_key="test"))

        def fake_complete_json(**_kwargs):
            from insightflow_agents.llm import LLMResponse

            plan = FeaturePlan(features=[
                FeatureSpec(name="cheat", kind="ratio", inputs=["revenue", "units"], rationale="x"),
                FeatureSpec(name="invented", kind="log1p", inputs=["profit_margin"], rationale="x"),
                FeatureSpec(name="gross_value", kind="product", inputs=["units", "unit_price"], rationale="x"),
            ])
            return plan, LLMResponse(model="fake-model", payload={})

        client.complete_json = fake_complete_json  # type: ignore[method-assign]
        outcome = FeatureEngineeringAgent(client, allow_model=True).run(
            frame, profile, FeatureOptions(), question="predict revenue"
        )
        result = outcome.result
        assert result.source == "model" and result.model_used == "fake-model"
        rejected = {item.name: item.reason for item in result.rejected}
        assert "leak" in rejected["cheat"]
        assert "not a column" in rejected["invented"]
        created = {item.spec.name: item.source for item in result.created}
        assert created["gross_value"] == "model"

    def test_without_consent_no_model_is_called(self, frame, profile):
        from insightflow_agents import FeatureEngineeringAgent, LLMClient

        client = LLMClient(settings=LLMSettings(enabled=True, api_key="test"))
        client.complete_json = lambda **_: pytest.fail("model called without consent")  # type: ignore[method-assign]
        result = FeatureEngineeringAgent(client, allow_model=False).run(
            frame, profile, FeatureOptions(), question=None
        ).result
        assert result.source == "deterministic"

    def test_selection_finds_the_signal_and_drops_the_noise(self, frame, profile):
        from insightflow_agents import FeatureEngineeringAgent, FeatureSelectionAgent

        options = FeatureOptions(max_features=5)
        engineering = FeatureEngineeringAgent().run(frame, profile, options, question="predict revenue")
        selection, feature_set = FeatureSelectionAgent().run(
            engineering, profile, options, PreprocessingOptions()
        )
        assert selection.selected[0] == "units_x_unit_price"
        assert "noise" not in selection.selected
        assert len(selection.selected) <= 5
        cv = selection.cross_validation
        assert cv is not None and cv.metric == "r2" and cv.selected_mean > 0.9
        assert list(feature_set.columns) == [*selection.selected, "revenue"]
        assert "causes" not in selection.explanation.lower()

    def test_selection_is_deterministic(self, frame, profile):
        from insightflow_agents import FeatureEngineeringAgent, FeatureSelectionAgent

        def run():
            engineering = FeatureEngineeringAgent().run(frame, profile, FeatureOptions(), question=None)
            return FeatureSelectionAgent().run(engineering, profile, FeatureOptions(), PreprocessingOptions())[0]

        first, second = run(), run()
        assert first.model_dump() == second.model_dump()

    def test_classification_targets_are_supported(self, frame, profile):
        from insightflow_agents import FeatureEngineeringAgent, FeatureSelectionAgent

        options = FeatureOptions(target_column="region")
        engineering = FeatureEngineeringAgent().run(frame, profile, options, question=None)
        assert engineering.result.task == "classification"
        selection, _ = FeatureSelectionAgent().run(engineering, profile, options, PreprocessingOptions())
        assert selection.cross_validation.metric == "balanced_accuracy"
        assert not any(score.feature.startswith("region=") for score in selection.scores)

    def test_disabled_features_are_skipped_cleanly(self, frame, profile):
        from insightflow_agents import FeatureEngineeringAgent, FeatureSelectionAgent

        options = FeatureOptions(enabled=False)
        engineering = FeatureEngineeringAgent().run(frame, profile, options, question=None)
        selection, feature_set = FeatureSelectionAgent().run(engineering, profile, options, PreprocessingOptions())
        assert engineering.result.skipped_reason and selection.skipped_reason
        assert feature_set is None
