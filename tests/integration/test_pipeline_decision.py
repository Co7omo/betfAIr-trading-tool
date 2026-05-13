"""End-to-end Decision Engine tests."""

import json
from datetime import UTC, datetime, timedelta

import asyncpg

from betfair_trading.db.writer import insert_config_snapshot
from betfair_trading.services.decision_engine import DecisionEngine
from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from betfair_trading.services.probability_providers import (
    BiasedStubProvider,
    MarketImpliedProvider,
)
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


def _make_engine(pg_pool, provider, **overrides):
    defaults = dict(
        pool=pg_pool,
        provider=provider,
        edge_threshold=0.02,
        min_liquidity=100.0,
        max_spread=0.10,
        commission_rate=0.05,
        max_positions_per_event=1,
        window_start_minutes=120,
        window_end_minutes=10,
        daily_dd_max=0.05,
    )
    defaults.update(overrides)
    return DecisionEngine(**defaults)


async def _run_pipeline(pg_pool, fake, decision_engine, fb=None):
    """Run discovery + 1 poll cycle wired through fb + decision_engine."""
    if fb is None:
        fb = FeatureBuilder(pg_pool, external_ingestor=None)
    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)

    decisions_made = []

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            dec = await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)
            decisions_made.append(dec)

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)
    return decisions_made


async def test_allow_path_with_biased_provider(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(
        make_market(
            market_id="1.A",
            event_id="E-A",
            home="Liverpool",
            away="Arsenal",
            start_time=datetime.now(UTC) + timedelta(minutes=60),
        )
    )
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_outcome, selected_runner_id FROM decisions WHERE market_id = '1.A'"
        )
    assert len(rows) == 1
    assert rows[0]["decision_outcome"] == "ALLOW"
    assert rows[0]["selected_runner_id"] == 101  # home runner_id from make_market default


async def test_block_soft_when_market_implied_provider(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    engine = _make_engine(pg_pool, MarketImpliedProvider())
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_outcome, gate_results FROM decisions WHERE market_id='1.A'"
        )
    assert row["decision_outcome"] == "BLOCK_SOFT"
    gate = row["gate_results"]
    if isinstance(gate, str):
        gate = json.loads(gate)
    assert gate["edge_threshold"]["passed"] is False


async def test_block_soft_low_liquidity(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book(
        "1.A",
        make_book(
            market_id="1.A",
            runner_quotes=[
                (101, 2.0, 2.04, 50.0, 50.0),
                (102, 3.5, 3.6, 50.0, 50.0),
                (103, 4.0, 4.1, 50.0, 50.0),
            ],
        ),
    )

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_outcome, gate_results FROM decisions WHERE market_id='1.A'"
        )
    assert row["decision_outcome"] == "BLOCK_SOFT"
    gate = row["gate_results"]
    if isinstance(gate, str):
        gate = json.loads(gate)
    assert gate["liquidity"]["passed"] is False


async def test_block_soft_high_spread(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book(
        "1.A",
        make_book(
            market_id="1.A",
            runner_quotes=[
                (101, 2.0, 2.50, 500.0, 500.0),
                (102, 3.5, 3.6, 500.0, 500.0),
                (103, 4.0, 4.1, 500.0, 500.0),
            ],
        ),
    )

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_outcome, gate_results FROM decisions WHERE market_id='1.A'"
        )
    assert row["decision_outcome"] == "BLOCK_SOFT"
    gate = row["gate_results"]
    if isinstance(gate, str):
        gate = json.loads(gate)
    assert gate["spread"]["passed"] is False


async def test_block_hard_kill_switch(pg_pool: asyncpg.Pool):
    async with pg_pool.acquire() as conn:
        await insert_config_snapshot(conn, {"trading": {}}, kill_switch_active=True)

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_outcome, gate_results FROM decisions WHERE market_id='1.A'"
        )
    assert row["decision_outcome"] == "BLOCK_HARD"
    gate = row["gate_results"]
    if isinstance(gate, str):
        gate = json.loads(gate)
    assert gate["kill_switch"]["passed"] is False


async def test_position_limit_blocks_second_allow(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A"))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            await engine.evaluate(bundle, snapshot_ids, fv_ids)

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)
    await collector.run_poll_cycle(on_snapshot=on_snap)

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_outcome FROM decisions WHERE event_id = 'E-A' ORDER BY decision_ts"
        )
    assert len(rows) == 2
    assert rows[0]["decision_outcome"] == "ALLOW"
    assert rows[1]["decision_outcome"] == "BLOCK_SOFT"


async def test_decision_persists_full_audit(pg_pool: asyncpg.Pool):
    async with pg_pool.acquire() as conn:
        cfg_id = await insert_config_snapshot(
            conn, {"trading": {"edge_threshold": 0.02}}, kill_switch_active=False
        )

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A"))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT p_model, p_market, edge_gross, edge_net, gate_results, "
            "feature_vector_ids, config_snapshot_id, model_version "
            "FROM decisions WHERE market_id = '1.A'"
        )

    p_model = row["p_model"] if not isinstance(row["p_model"], str) else json.loads(row["p_model"])
    p_market = (
        row["p_market"] if not isinstance(row["p_market"], str) else json.loads(row["p_market"])
    )
    edge_gross = (
        row["edge_gross"]
        if not isinstance(row["edge_gross"], str)
        else json.loads(row["edge_gross"])
    )
    edge_net = (
        row["edge_net"] if not isinstance(row["edge_net"], str) else json.loads(row["edge_net"])
    )
    gate = (
        row["gate_results"]
        if not isinstance(row["gate_results"], str)
        else json.loads(row["gate_results"])
    )

    assert len(p_model) == 3
    assert len(p_market) == 3
    assert len(edge_gross) == 3
    assert len(edge_net) == 3

    assert set(gate.keys()) == {
        "kill_switch",
        "window",
        "edge_threshold",
        "liquidity",
        "spread",
        "position_limit",
        "daily_drawdown",
    }

    assert len(row["feature_vector_ids"]) > 0
    assert row["config_snapshot_id"] == cfg_id
    assert row["model_version"] == "STUB_BIAS_V1"
