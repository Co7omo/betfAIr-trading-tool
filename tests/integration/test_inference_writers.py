"""Integration tests for insert_model_version and insert_model_inference."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import insert_model_inference, insert_model_version
from betfair_trading.models.inference import ModelInference, ModelVersion


async def test_insert_model_version_persists(pg_pool: asyncpg.Pool):
    mv = ModelVersion(
        model_name="logistic_v1",
        feature_set_version="A2_EXT_ONLY",
        file_path="models/logistic_v1.joblib",
        training_data_hash="abc123",
        training_csv_path="data/results.csv",
        training_params={"C": 1.0},
        metrics={"log_loss": 1.05},
        feature_names=["elo_home", "elo_away"],
        n_train=80,
        n_test=20,
    )
    async with pg_pool.acquire() as conn:
        mv_id = await insert_model_version(conn, mv)
    assert mv_id == mv.model_version_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM model_versions WHERE model_version_id = $1", mv_id)
    assert row["model_name"] == "logistic_v1"
    assert row["feature_set_version"] == "A2_EXT_ONLY"
    assert row["n_train"] == 80
    params = row["training_params"]
    if isinstance(params, str):
        params = json.loads(params)
    assert params["C"] == 1.0
    fnames = row["feature_names"]
    if isinstance(fnames, str):
        fnames = json.loads(fnames)
    assert fnames == ["elo_home", "elo_away"]


async def test_insert_model_inference_persists(pg_pool: asyncpg.Pool):
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
    async with pg_pool.acquire() as conn:
        inf_id = await insert_model_inference(conn, mi)
    assert inf_id == mi.inference_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM model_inferences WHERE inference_id = $1", inf_id)
    assert row["market_id"] == "1.A"
    assert row["p_home"] == Decimal("0.550000")
    assert list(row["feature_vector_ids"]) == [fv_id]
    feats = row["features_used"]
    if isinstance(feats, str):
        feats = json.loads(feats)
    assert feats["elo_home"] == 1510.0
