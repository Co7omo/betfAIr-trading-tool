"""Integration test for insert_decision writer."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import insert_decision
from betfair_trading.models.decision import Decision, DecisionOutcome, GateResult


async def test_insert_decision_persists_all_fields(pg_pool: asyncpg.Pool):
    fv_id = uuid4()
    decision = Decision(
        market_id="1.A",
        event_id="E-A",
        snapshot_id=None,
        decision_ts=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        model_version="STUB_V1",
        p_model={101: 0.55, 102: 0.25, 103: 0.20},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: 0.05, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        selected_runner_id=101,
        selected_edge_net=Decimal("0.022500"),
        gate_results={
            "kill_switch": GateResult(passed=True, reason="ok"),
            "edge_threshold": GateResult(passed=True, reason="ok"),
        },
        decision_outcome=DecisionOutcome.ALLOW,
        rationale="all gates passed",
        feature_vector_ids=[fv_id],
        config_snapshot_id=None,
    )

    async with pg_pool.acquire() as conn:
        decision_id = await insert_decision(conn, decision)

    assert decision_id == decision.decision_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM decisions WHERE decision_id = $1", decision_id)

    assert row is not None
    assert row["market_id"] == "1.A"
    assert row["event_id"] == "E-A"
    assert row["model_version"] == "STUB_V1"
    assert row["selected_runner_id"] == 101
    assert row["selected_edge_net"] == Decimal("0.022500")
    assert row["decision_outcome"] == "ALLOW"
    assert row["rationale"] == "all gates passed"
    assert list(row["feature_vector_ids"]) == [fv_id]

    p_model = row["p_model"]
    if isinstance(p_model, str):
        p_model = json.loads(p_model)
    # JSON keys come back as strings
    assert p_model["101"] == 0.55

    gate_results = row["gate_results"]
    if isinstance(gate_results, str):
        gate_results = json.loads(gate_results)
    assert gate_results["kill_switch"]["passed"] is True
    assert gate_results["kill_switch"]["reason"] == "ok"


async def test_insert_decision_with_inference_id(pg_pool: asyncpg.Pool):
    """The new inference_id column persists round-trip."""
    fv_id = uuid4()
    inf_id = uuid4()
    decision = Decision(
        market_id="1.A",
        event_id="E-A",
        decision_ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        model_version="logistic_v1",
        p_model={101: 0.55, 102: 0.25, 103: 0.20},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: 0.05, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        gate_results={"kill_switch": GateResult(passed=True, reason="ok")},
        decision_outcome=DecisionOutcome.ALLOW,
        feature_vector_ids=[fv_id],
        inference_id=inf_id,
    )
    async with pg_pool.acquire() as conn:
        decision_id = await insert_decision(conn, decision)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT inference_id FROM decisions WHERE decision_id = $1", decision_id
        )
    assert row["inference_id"] == inf_id
