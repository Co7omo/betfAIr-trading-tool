"""End-to-end: MarketCollector.run_discovery() + run_poll_cycle() persist to Postgres."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

from betfair_trading.services.market_collector import MarketCollector
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


async def test_discovery_persists_markets_and_runners(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A", home="Liverpool", away="Arsenal"))
    fake.add_market(make_market(market_id="1.B", event_id="E-B", home="Chelsea", away="Spurs"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)

    new_count = await collector.run_discovery()

    assert new_count == 2
    async with pg_pool.acquire() as conn:
        markets = await conn.fetch("SELECT market_id, event_id FROM markets ORDER BY market_id")
        runners = await conn.fetch("SELECT market_id, runner_id FROM runners ORDER BY market_id, runner_id")

    assert [m["market_id"] for m in markets] == ["1.A", "1.B"]
    assert {(m["market_id"], m["event_id"]) for m in markets} == {("1.A", "E-A"), ("1.B", "E-B")}
    assert len(runners) == 6  # 3 runner per market * 2 market


async def test_polling_persists_snapshots(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    start_time = datetime.now(UTC) + timedelta(minutes=60)
    fake.add_market(make_market(market_id="1.A", event_id="E-A", start_time=start_time))

    # Three different books in the queue → three poll cycles consume them in order
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A",
                                     runner_quotes=[(101, 2.0, 2.04, 500, 500),
                                                    (102, 3.5, 3.6, 200, 200),
                                                    (103, 4.0, 4.1, 300, 300)],
                                     total_matched=1000.0))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A",
                                     runner_quotes=[(101, 2.10, 2.14, 500, 500),
                                                    (102, 3.40, 3.50, 200, 200),
                                                    (103, 3.95, 4.05, 300, 300)],
                                     total_matched=1500.0))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A",
                                     runner_quotes=[(101, 2.20, 2.24, 500, 500),
                                                    (102, 3.30, 3.40, 200, 200),
                                                    (103, 3.90, 4.00, 300, 300)],
                                     total_matched=2000.0))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    await collector.run_discovery()

    n1 = await collector.run_poll_cycle()
    n2 = await collector.run_poll_cycle()
    n3 = await collector.run_poll_cycle()

    assert n1 == n2 == n3 == 3  # 3 runner per cycle

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT runner_id, best_back_price, total_matched FROM market_snapshots "
            "WHERE market_id = '1.A' ORDER BY snapshot_ts, runner_id"
        )
    assert len(rows) == 9
    # The total_matched observed on the home runner (101) across the 3 cycles
    home_totals = [
        r["total_matched"] for r in rows if r["runner_id"] == 101
    ]
    assert home_totals == [Decimal("1000.0"), Decimal("1500.0"), Decimal("2000.0")]


async def test_minutes_to_start_persisted(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    start_time = datetime.now(UTC) + timedelta(minutes=60)
    fake.add_market(make_market(market_id="1.A", start_time=start_time))
    fake.queue_book("1.A", make_book(market_id="1.A", minutes_to_start=60.0))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    await collector.run_discovery()
    await collector.run_poll_cycle()

    async with pg_pool.acquire() as conn:
        m2s = await conn.fetchval(
            "SELECT minutes_to_start FROM market_snapshots WHERE market_id = '1.A' LIMIT 1"
        )
    assert m2s is not None
    # The bundle has minutes_to_start=60.0 hardcoded → DB Decimal(8,2)
    assert abs(float(m2s) - 60.0) < 0.01
