"""Unit tests for Order Pydantic contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from betfair_trading.models.order import (
    ExecutionMode,
    Fill,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
    TradeIntent,
)


def test_trade_intent_construction():
    dec_id = uuid4()
    intent = TradeIntent(
        decision_id=dec_id,
        market_id="1.A",
        event_id="E-A",
        selection_id=101,
        side=OrderSide.BACK,
        price=Decimal("2.00"),
        size=Decimal("20.00"),
        customer_order_ref=dec_id.hex,
    )
    assert intent.side == OrderSide.BACK
    assert intent.customer_order_ref == dec_id.hex
    assert len(intent.customer_order_ref) == 32  # hex no dashes


def test_order_event_defaults():
    dec_id = uuid4()
    event = OrderEvent(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        event_id="E-A",
        selection_id=101,
        side=OrderSide.BACK,
        requested_price=Decimal("2.00"),
        requested_size=Decimal("20.00"),
        status=OrderStatus.PENDING,
        event_type=OrderEventType.PLACED,
        mode=ExecutionMode.DRY_RUN,
    )
    assert event.matched_size == Decimal("0")
    assert event.average_price_matched is None
    assert event.event_ts is None  # DB default
    assert event.api_response is None


def test_fill_construction():
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
    assert fill.matched_size_delta == Decimal("10.00")
    assert fill.remaining_size == Decimal("10.00")


def test_execution_mode_values():
    assert ExecutionMode.DRY_RUN.value == "dry_run"
    assert ExecutionMode.PAPER.value == "paper"
    # Live is NOT defined — verify by attribute check
    assert not hasattr(ExecutionMode, "LIVE")


def test_order_status_values():
    assert OrderStatus.PENDING.value == "PENDING"
    assert OrderStatus.EXECUTABLE.value == "EXECUTABLE"
    assert OrderStatus.EXECUTION_COMPLETE.value == "EXECUTION_COMPLETE"
    assert OrderStatus.ERROR.value == "ERROR"
