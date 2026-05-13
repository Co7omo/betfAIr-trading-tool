"""Integration tests for order/fill writers."""

import json
from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import (
    fetch_open_orders,
    insert_fill,
    insert_order_event,
)
from betfair_trading.models.order import (
    ExecutionMode,
    Fill,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
)


def _make_order_event(
    decision_id=None, status=OrderStatus.EXECUTABLE, mode=ExecutionMode.PAPER,
    event_type=OrderEventType.PLACED, matched_size=Decimal("0"),
):
    dec_id = decision_id or uuid4()
    return OrderEvent(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        event_id="E-A",
        selection_id=101,
        side=OrderSide.BACK,
        requested_price=Decimal("2.0000"),
        requested_size=Decimal("20.00"),
        matched_size=matched_size,
        status=status,
        event_type=event_type,
        mode=mode,
    )


async def test_insert_order_event_persists(pg_pool: asyncpg.Pool):
    event = _make_order_event()
    async with pg_pool.acquire() as conn:
        order_event_id = await insert_order_event(conn, event)
    assert order_event_id == event.order_event_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_event_id = $1", order_event_id
        )
    assert row["customer_order_ref"] == event.customer_order_ref
    assert row["status"] == "EXECUTABLE"
    assert row["mode"] == "paper"
    assert row["requested_price"] == Decimal("2.0000")


async def test_insert_fill_persists(pg_pool: asyncpg.Pool):
    dec_id = uuid4()
    fill = Fill(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        selection_id=101,
        matched_size_delta=Decimal("10.00"),
        average_price_matched=Decimal("2.04"),
        cumulative_matched_size=Decimal("10.00"),
        remaining_size=Decimal("10.00"),
    )
    async with pg_pool.acquire() as conn:
        fill_id = await insert_fill(conn, fill)
    assert fill_id == fill.fill_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM fills WHERE fill_id = $1", fill_id
        )
    assert row["matched_size_delta"] == Decimal("10.00")
    assert row["average_price_matched"] == Decimal("2.0400")


async def test_fetch_open_orders_distinct_on_latest(pg_pool: asyncpg.Pool):
    """Two events for the same customer_order_ref: latest wins, only one row returned."""
    dec_id = uuid4()
    e1 = _make_order_event(decision_id=dec_id, status=OrderStatus.PENDING,
                            event_type=OrderEventType.PLACED)
    e2 = _make_order_event(decision_id=dec_id, status=OrderStatus.EXECUTABLE,
                            event_type=OrderEventType.LIFECYCLE,
                            matched_size=Decimal("0"))

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, e1)
        await insert_order_event(conn, e2)

        open_orders = await fetch_open_orders(conn, mode=ExecutionMode.PAPER)

    assert len(open_orders) == 1
    assert open_orders[0].status == OrderStatus.EXECUTABLE  # latest
    assert open_orders[0].customer_order_ref == dec_id.hex


async def test_fetch_open_orders_filters_terminal(pg_pool: asyncpg.Pool):
    """Orders in EXECUTION_COMPLETE are NOT returned."""
    e1 = _make_order_event(status=OrderStatus.EXECUTION_COMPLETE)
    e2 = _make_order_event(status=OrderStatus.PENDING)
    e3 = _make_order_event(status=OrderStatus.EXECUTABLE)

    async with pg_pool.acquire() as conn:
        for e in (e1, e2, e3):
            await insert_order_event(conn, e)
        open_orders = await fetch_open_orders(conn, mode=ExecutionMode.PAPER)

    statuses = {o.status for o in open_orders}
    assert OrderStatus.EXECUTION_COMPLETE not in statuses
    assert OrderStatus.PENDING in statuses
    assert OrderStatus.EXECUTABLE in statuses


async def test_fetch_open_orders_filters_by_mode(pg_pool: asyncpg.Pool):
    """Only orders matching the given mode are returned."""
    e_paper = _make_order_event(mode=ExecutionMode.PAPER)
    e_dry = _make_order_event(mode=ExecutionMode.DRY_RUN)

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, e_paper)
        await insert_order_event(conn, e_dry)
        paper_only = await fetch_open_orders(conn, mode=ExecutionMode.PAPER)
        dry_only = await fetch_open_orders(conn, mode=ExecutionMode.DRY_RUN)

    assert len(paper_only) == 1 and paper_only[0].mode == ExecutionMode.PAPER
    assert len(dry_only) == 1 and dry_only[0].mode == ExecutionMode.DRY_RUN
