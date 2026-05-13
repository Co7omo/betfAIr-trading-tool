"""End-to-end pipeline test: collector → fb → decision → execution → reconcile."""

from datetime import UTC, datetime, timedelta

import asyncpg

from betfair_trading.models.decision import DecisionOutcome
from betfair_trading.models.order import ExecutionMode
from betfair_trading.services.decision_engine import DecisionEngine
from betfair_trading.services.execution_engine import ExecutionEngine
from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from betfair_trading.services.probability_providers import BiasedStubProvider
from betfair_trading.services.reconciler import Reconciler
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


async def _build_pipeline(pg_pool, mode: ExecutionMode):
    """Construct collector, feature_builder, decision_engine, execution_engine, reconciler."""
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A",
                                home="Liverpool", away="Arsenal",
                                start_time=datetime.now(UTC) + timedelta(minutes=60)))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    provider = BiasedStubProvider(home_bias=0.05)
    de = DecisionEngine(
        pool=pg_pool, provider=provider,
        edge_threshold=0.02, min_liquidity=100.0, max_spread=0.10,
        max_positions_per_event=1,
    )
    ee = ExecutionEngine(
        pool=pg_pool, bf_client=fake, mode=mode,
        bankroll=1000.0, min_stake=2.0,
    )
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=mode)
    return fake, collector, fb, de, ee, rec


async def test_full_pipeline_paper_allow_to_placed_order(pg_pool: asyncpg.Pool):
    fake, collector, fb, de, ee, rec = await _build_pipeline(pg_pool, ExecutionMode.PAPER)

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if not fv_ids:
            return
        decision = await de.evaluate(bundle, snapshot_ids, fv_ids)
        if decision and decision.decision_outcome == DecisionOutcome.ALLOW:
            order_event_id = await ee.on_decision_allow(decision)
            if order_event_id is not None:
                fake.queue_match_behavior(decision.decision_id.hex, "instant_match")

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)
    await rec.reconcile_open_orders()

    async with pg_pool.acquire() as conn:
        orders = await conn.fetch("SELECT * FROM orders ORDER BY event_ts")
        fills = await conn.fetch("SELECT * FROM fills")

    placed = [o for o in orders if o["event_type"] == "PLACED"]
    lifecycle = [o for o in orders if o["event_type"] == "LIFECYCLE"]
    assert len(placed) == 1
    assert placed[0]["mode"] == "paper"
    assert placed[0]["status"] == "EXECUTABLE"
    assert len(lifecycle) == 1
    assert lifecycle[0]["status"] == "EXECUTION_COMPLETE"
    assert len(fills) == 1


async def test_dry_run_pipeline_writes_pending_no_fills(pg_pool: asyncpg.Pool):
    fake, collector, fb, de, ee, rec = await _build_pipeline(pg_pool, ExecutionMode.DRY_RUN)

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if not fv_ids:
            return
        decision = await de.evaluate(bundle, snapshot_ids, fv_ids)
        if decision and decision.decision_outcome == DecisionOutcome.ALLOW:
            await ee.on_decision_allow(decision)

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)
    reconciled = await rec.reconcile_open_orders()

    async with pg_pool.acquire() as conn:
        orders = await conn.fetch("SELECT * FROM orders")
        fills_count = await conn.fetchval("SELECT COUNT(*) FROM fills")

    assert len(orders) == 1
    assert orders[0]["mode"] == "dry_run"
    assert orders[0]["status"] == "PENDING"
    assert orders[0]["api_response"] is None
    assert reconciled == 1
    assert fills_count == 0
    assert fake._placed_orders == {}
