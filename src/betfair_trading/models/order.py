"""Pydantic contracts for orders, fills, and trade intents."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class OrderSide(StrEnum):
    BACK = "BACK"
    LAY = "LAY"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTABLE = "EXECUTABLE"
    EXECUTION_COMPLETE = "EXECUTION_COMPLETE"
    CANCELLED = "CANCELLED"
    LAPSED = "LAPSED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class OrderEventType(StrEnum):
    PLACED = "PLACED"
    LIFECYCLE = "LIFECYCLE"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class ExecutionMode(StrEnum):
    DRY_RUN = "dry_run"
    PAPER = "paper"
    # LIVE intentionally absent — adding requires explicit enum extension + opt-in.


class TradeIntent(BaseModel):
    """Computed intent before order placement."""

    decision_id: UUID
    market_id: str
    event_id: str
    selection_id: int
    side: OrderSide
    price: Decimal
    size: Decimal
    customer_order_ref: str


class OrderEvent(BaseModel):
    """One row in `orders`. Append-only lifecycle event."""

    order_event_id: UUID = Field(default_factory=uuid4)
    customer_order_ref: str
    decision_id: UUID
    market_id: str
    event_id: str
    selection_id: int
    side: OrderSide
    requested_price: Decimal
    requested_size: Decimal
    matched_size: Decimal = Decimal("0")
    average_price_matched: Decimal | None = None
    status: OrderStatus
    event_type: OrderEventType
    event_ts: datetime | None = None  # DB default
    api_response: dict | None = None
    mode: ExecutionMode


class Fill(BaseModel):
    """One row in `fills`. Incremental match delta."""

    fill_id: UUID = Field(default_factory=uuid4)
    customer_order_ref: str
    decision_id: UUID
    market_id: str
    selection_id: int
    fill_ts: datetime | None = None  # DB default
    matched_size_delta: Decimal
    average_price_matched: Decimal
    cumulative_matched_size: Decimal
    remaining_size: Decimal
