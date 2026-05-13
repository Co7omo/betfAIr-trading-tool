"""End-to-end tests for ExecutionEngine."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import insert_decision, insert_market_snapshots
from betfair_trading.models.decision import (
    Decision,
    DecisionOutcome,
    GateResult,
)
from betfair_trading.models.market import (
    MarketSnapshotBundle,
    RunnerSnapshot,
)
from betfair_trading.models.order import ExecutionMode
from betfair_trading.services.execution_engine import ExecutionEngine
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient


def _make_decision(outcome=DecisionOutcome.ALLOW, market_id="1.A", event_id="E-A",
                   selected_runner_id=101, p_model_home=0.55):
    fv_id = uuid4()
    return Decision(
        market_id=market_id,
        event_id=event_id,
        decision_ts=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
        model_version="logistic_v1",
        p_model={101: p_model_home, 102: 0.25, 103: 1.0 - p_model_home - 0.25},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: p_model_home - 0.5, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        selected_runner_id=selected_runner_id,
        selected_edge_net=Decimal("0.022500"),
        gate_results={"kill_switch": GateResult(passed=True, reason="ok")},
        decision_outcome=outcome,
        feature_vector_ids=[fv_id],
    )


async def _seed_market_snapshot(pg_pool, market_id, runner_id, best_back_price):
    """Insert a market_snapshot so ExecutionEngine can read the latest quote."""
    bundle = MarketSnapshotBundle(
        market_id=market_id, event_id="E-A",
        snapshot_ts=datetime.now(UTC),
        runners=[
            RunnerSnapshot(
                runner_id=runner_id,
                best_back_price=Decimal(str(best_back_price)),
                best_lay_price=Decimal(str(best_back_price + 0.04)),
                traded_volume=Decimal("0"),
            ),
        ],
        market_status="OPEN", inplay=False,
        total_matched=Decimal("1000"), minutes_to_start=60.0,
    )
    async with pg_pool.acquire() as conn:
        await insert_market_snapshots(conn, bundle)


async def test_dry_run_writes_placed_event_no_api_call(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision()
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)
    async with pg_pool.acquire() as conn:
        await insert_decision(conn, decision)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.DRY_RUN,
        bankroll=1000.0,
    )
    order_event_id = await engine.on_decision_allow(decision)

    assert order_event_id is not None
    assert fake._placed_orders == {}

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_event_id = $1", order_event_id
        )
    assert row["mode"] == "dry_run"
    assert row["event_type"] == "PLACED"
    assert row["status"] == "PENDING"
    assert row["api_response"] is None
    assert row["customer_order_ref"] == decision.decision_id.hex


async def test_paper_mode_calls_fake_client_writes_lifecycle(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision()
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)
    async with pg_pool.acquire() as conn:
        await insert_decision(conn, decision)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.PAPER,
        bankroll=1000.0,
    )
    order_event_id = await engine.on_decision_allow(decision)

    assert order_event_id is not None
    assert decision.decision_id.hex in fake._placed_orders

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_event_id = $1", order_event_id
        )
    assert row["mode"] == "paper"
    assert row["status"] == "EXECUTABLE"
    assert row["api_response"] is not None


async def test_sizing_below_min_stake_skips_order(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision()
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)
    async with pg_pool.acquire() as conn:
        await insert_decision(conn, decision)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.DRY_RUN,
        bankroll=10.0,
        min_stake=2.0,
    )
    order_event_id = await engine.on_decision_allow(decision)

    assert order_event_id is None
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM orders")
    assert count == 0


async def test_customer_order_ref_is_decision_id_hex(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision()
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)
    async with pg_pool.acquire() as conn:
        await insert_decision(conn, decision)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.DRY_RUN,
        bankroll=1000.0,
    )
    await engine.on_decision_allow(decision)

    async with pg_pool.acquire() as conn:
        ref = await conn.fetchval(
            "SELECT customer_order_ref FROM orders LIMIT 1"
        )
    assert ref == decision.decision_id.hex
    assert len(ref) == 32


async def test_block_outcome_does_not_trigger_execution(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision(outcome=DecisionOutcome.BLOCK_SOFT)
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.PAPER,
        bankroll=1000.0,
    )
    result = await engine.on_decision_allow(decision)

    assert result is None
    assert fake._placed_orders == {}
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM orders")
    assert count == 0
