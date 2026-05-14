"""Reconciler: background task polling open orders, writing lifecycle + fills."""

from decimal import Decimal

import asyncpg
import structlog

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
    OrderStatus,
)

log = structlog.get_logger()


class Reconciler:
    def __init__(
        self,
        pool: asyncpg.Pool,
        bf_client,
        mode: ExecutionMode = ExecutionMode.DRY_RUN,
    ):
        self._pool = pool
        self._bf = bf_client
        self._mode = mode

    async def reconcile_open_orders(self) -> int:
        """Return the number of orders processed."""
        async with self._pool.acquire() as conn:
            open_orders = await fetch_open_orders(conn, mode=self._mode)
            if not open_orders:
                return 0

            # DRY_RUN: count open orders but skip API + write
            if self._mode == ExecutionMode.DRY_RUN:
                return len(open_orders)

            refs = [o.customer_order_ref for o in open_orders]
            current = await self._bf.list_current_orders(refs)
            current_by_ref = {c["customer_order_ref"]: c for c in current}

            for prev in open_orders:
                cur = current_by_ref.get(prev.customer_order_ref)
                if cur is None:
                    continue

                new_matched = Decimal(str(cur.get("size_matched", 0)))
                new_status_str = cur.get("order_status", prev.status.value)
                new_status = OrderStatus(new_status_str)
                matched_delta = new_matched - prev.matched_size

                avg_price = cur.get("average_price_matched")
                avg_decimal = Decimal(str(avg_price)) if avg_price else None

                if matched_delta > 0:
                    remaining = Decimal(str(cur.get("size_remaining", 0)))
                    await insert_fill(
                        conn,
                        Fill(
                            customer_order_ref=prev.customer_order_ref,
                            decision_id=prev.decision_id,
                            market_id=prev.market_id,
                            selection_id=prev.selection_id,
                            matched_size_delta=matched_delta,
                            average_price_matched=avg_decimal or Decimal("0"),
                            cumulative_matched_size=new_matched,
                            remaining_size=remaining,
                        ),
                    )

                if matched_delta > 0 or new_status != prev.status:
                    await insert_order_event(
                        conn,
                        OrderEvent(
                            customer_order_ref=prev.customer_order_ref,
                            decision_id=prev.decision_id,
                            market_id=prev.market_id,
                            event_id=prev.event_id,
                            selection_id=prev.selection_id,
                            side=prev.side,
                            requested_price=prev.requested_price,
                            requested_size=prev.requested_size,
                            matched_size=new_matched,
                            average_price_matched=avg_decimal,
                            status=new_status,
                            event_type=OrderEventType.LIFECYCLE,
                            api_response=cur,
                            mode=prev.mode,
                        ),
                    )

        log.debug(
            "reconcile_complete",
            count=len(open_orders),
            mode=self._mode.value,
        )
        return len(open_orders)
