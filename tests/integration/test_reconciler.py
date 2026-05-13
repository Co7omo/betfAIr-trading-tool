"""Integration tests for Reconciler."""

from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import insert_order_event
from betfair_trading.models.order import (
    ExecutionMode,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
)
from betfair_trading.services.reconciler import Reconciler
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient


def _make_event(decision_id=None, status=OrderStatus.EXECUTABLE,
                event_type=OrderEventType.PLACED, matched=Decimal("0"),
                mode=ExecutionMode.PAPER, requested_size=Decimal("20.00")):
    dec_id = decision_id or uuid4()
    return OrderEvent(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        event_id="E-A",
        selection_id=101,
        side=OrderSide.BACK,
        requested_price=Decimal("2.0000"),
        requested_size=requested_size,
        matched_size=matched,
        status=status,
        event_type=event_type,
        mode=mode,
    )


async def test_reconcile_no_open_orders_returns_zero(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)
    count = await rec.reconcile_open_orders()
    assert count == 0


async def test_reconcile_instant_match_writes_fill_and_lifecycle(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)

    event = _make_event(requested_size=Decimal("20.00"))
    fake._placed_orders[event.customer_order_ref] = {
        "market_id": event.market_id,
        "selection_id": event.selection_id,
        "side": "BACK",
        "price": 2.0,
        "size": 20.0,
        "persistence_type": "LAPSE",
        "size_matched": 0.0,
        "size_remaining": 20.0,
        "average_price_matched": 0.0,
        "order_status": "EXECUTABLE",
        "bet_id": "FAKE-TEST",
    }
    fake.queue_match_behavior(event.customer_order_ref, "instant_match")

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, event)

    count = await rec.reconcile_open_orders()
    assert count == 1

    async with pg_pool.acquire() as conn:
        fills = await conn.fetch("SELECT * FROM fills")
        lifecycle_events = await conn.fetch(
            "SELECT * FROM orders WHERE event_type = 'LIFECYCLE'"
        )

    assert len(fills) == 1
    assert fills[0]["matched_size_delta"] == Decimal("20.00")
    assert fills[0]["cumulative_matched_size"] == Decimal("20.00")
    assert fills[0]["remaining_size"] == Decimal("0")

    assert len(lifecycle_events) == 1
    assert lifecycle_events[0]["status"] == "EXECUTION_COMPLETE"


async def test_reconcile_partial_match_writes_delta(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)

    event = _make_event(requested_size=Decimal("20.00"))
    fake._placed_orders[event.customer_order_ref] = {
        "market_id": event.market_id, "selection_id": event.selection_id,
        "side": "BACK", "price": 2.0, "size": 20.0, "persistence_type": "LAPSE",
        "size_matched": 0.0, "size_remaining": 20.0,
        "average_price_matched": 0.0, "order_status": "EXECUTABLE",
        "bet_id": "FAKE-TEST",
    }
    fake.queue_match_behavior(event.customer_order_ref, "partial")

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, event)

    await rec.reconcile_open_orders()

    async with pg_pool.acquire() as conn:
        fill = await conn.fetchrow("SELECT * FROM fills")
        latest = await conn.fetchrow(
            "SELECT status, matched_size FROM orders "
            "ORDER BY event_ts DESC LIMIT 1"
        )

    assert fill["matched_size_delta"] == Decimal("10.00")
    assert fill["remaining_size"] == Decimal("10.00")
    assert latest["status"] == "EXECUTABLE"
    assert latest["matched_size"] == Decimal("10.00")


async def test_reconcile_no_change_writes_nothing(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)

    event = _make_event()
    fake._placed_orders[event.customer_order_ref] = {
        "market_id": event.market_id, "selection_id": event.selection_id,
        "side": "BACK", "price": 2.0, "size": 20.0, "persistence_type": "LAPSE",
        "size_matched": 0.0, "size_remaining": 20.0,
        "average_price_matched": 0.0, "order_status": "EXECUTABLE",
        "bet_id": "FAKE-TEST",
    }
    fake.queue_match_behavior(event.customer_order_ref, "no_match")

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, event)
        before_count = await conn.fetchval("SELECT COUNT(*) FROM orders")

    await rec.reconcile_open_orders()

    async with pg_pool.acquire() as conn:
        after_count = await conn.fetchval("SELECT COUNT(*) FROM orders")
        fills = await conn.fetchval("SELECT COUNT(*) FROM fills")

    assert after_count == before_count  # no new lifecycle event
    assert fills == 0


async def test_reconcile_terminal_state_not_picked_up(pg_pool: asyncpg.Pool):
    """An order in EXECUTION_COMPLETE is NOT reconciled again."""
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)

    event = _make_event(status=OrderStatus.EXECUTION_COMPLETE)
    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, event)

    count = await rec.reconcile_open_orders()
    assert count == 0
