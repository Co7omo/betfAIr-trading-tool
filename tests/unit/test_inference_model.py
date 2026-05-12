"""Unit tests for ModelVersion and ModelInference Pydantic contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from betfair_trading.models.inference import ModelInference, ModelVersion


def test_model_version_full_construction():
    mv = ModelVersion(
        model_name="logistic_v1",
        feature_set_version="A2_EXT_ONLY",
        file_path="models/logistic_v1_20260511.joblib",
        training_data_hash="abc123",
        training_csv_path="data/results.csv",
        training_params={"C": 1.0, "calibration": "sigmoid"},
        metrics={"log_loss": 1.05, "accuracy": 0.45},
        feature_names=["elo_home", "elo_away", "elo_delta"],
        n_train=800,
        n_test=200,
    )
    assert mv.model_name == "logistic_v1"
    assert mv.feature_set_version == "A2_EXT_ONLY"
    assert mv.n_train == 800
    assert mv.created_ts is None  # DB default


def test_model_inference_full_construction():
    fv_id = uuid4()
    mv_id = uuid4()
    mi = ModelInference(
        model_version_id=mv_id,
        market_id="1.A",
        event_id="E-A",
        asof_ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        p_home=Decimal("0.550000"),
        p_draw=Decimal("0.250000"),
        p_away=Decimal("0.200000"),
        feature_vector_ids=[fv_id],
        features_used={"elo_home": 1510.0, "elo_away": 1490.0},
    )
    assert mi.model_version_id == mv_id
    assert mi.p_home == Decimal("0.550000")
    assert mi.feature_vector_ids == [fv_id]
