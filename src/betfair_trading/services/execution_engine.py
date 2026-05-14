"""ExecutionEngine: receives ALLOW decisions, computes Kelly-sized intent,
places orders (dry_run or paper), persists lifecycle events.
"""

import uuid
from decimal import Decimal

import asyncpg
import structlog

from betfair_trading.db.writer import insert_order_event
from betfair_trading.models.decision import Decision, DecisionOutcome
from betfair_trading.models.order import (
    ExecutionMode,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
    TradeIntent,
)
from betfair_trading.services.sizer import compute_stake

log = structlog.get_logger()


class ExecutionEngine:
    def __init__(
        self,
        pool: asyncpg.Pool,
        bf_client,
        mode: ExecutionMode = ExecutionMode.DRY_RUN,
        bankroll: float = 1000.0,
        kelly_multiplier: float = 0.25,
        max_stake_fraction: float = 0.02,
        min_stake: float = 2.0,
    ):
        self._pool = pool
        self._bf = bf_client
        self._mode = mode
        self._bankroll = bankroll
        self._kelly_multiplier = kelly_multiplier
        self._max_stake_fraction = max_stake_fraction
        self._min_stake = min_stake

    async def on_decision_allow(self, decision: Decision) -> uuid.UUID | None:
        """If decision.decision_outcome == ALLOW, build intent + place + persist.
        Returns the order_event_id (PLACED event) or None if skipped."""
        if decision.decision_outcome != DecisionOutcome.ALLOW:
            return None
        if decision.selected_runner_id is None:
            return None

        async with self._pool.acquire() as conn:
            quote_row = await conn.fetchrow(
                "SELECT best_back_price FROM market_snapshots "
                "WHERE market_id = $1 AND runner_id = $2 "
                "ORDER BY snapshot_ts DESC LIMIT 1",
                decision.market_id,
                decision.selected_runner_id,
            )
            if quote_row is None or quote_row["best_back_price"] is None:
                log.warning(
                    "execution_skip_no_quote",
                    market_id=decision.market_id,
                    runner_id=decision.selected_runner_id,
                )
                return None
            odds = float(quote_row["best_back_price"])

            p_model = decision.p_model.get(decision.selected_runner_id, 0.0)

            stake = compute_stake(
                bankroll=self._bankroll,
                p_model=p_model,
                odds=odds,
                kelly_multiplier=self._kelly_multiplier,
                max_stake_fraction=self._max_stake_fraction,
                min_stake=self._min_stake,
            )
            if stake is None:
                log.info(
                    "execution_skip_below_min_stake",
                    market_id=decision.market_id,
                    p_model=p_model,
                    odds=odds,
                )
                return None

            customer_order_ref = decision.decision_id.hex
            intent = TradeIntent(
                decision_id=decision.decision_id,
                market_id=decision.market_id,
                event_id=decision.event_id,
                selection_id=decision.selected_runner_id,
                side=OrderSide.BACK,
                price=Decimal(str(odds)),
                size=stake,
                customer_order_ref=customer_order_ref,
            )

            event = await self._build_and_place_event(intent)
            order_event_id = await insert_order_event(conn, event)

        log.info(
            "order_placed",
            customer_order_ref=customer_order_ref,
            mode=self._mode.value,
            status=event.status.value,
            size=float(stake),
        )
        return order_event_id

    async def _build_and_place_event(self, intent: TradeIntent) -> OrderEvent:
        """Either log-only (dry_run) or call bf_client.place_orders (paper)."""
        if self._mode == ExecutionMode.DRY_RUN:
            return OrderEvent(
                customer_order_ref=intent.customer_order_ref,
                decision_id=intent.decision_id,
                market_id=intent.market_id,
                event_id=intent.event_id,
                selection_id=intent.selection_id,
                side=intent.side,
                requested_price=intent.price,
                requested_size=intent.size,
                status=OrderStatus.PENDING,
                event_type=OrderEventType.PLACED,
                api_response=None,
                mode=ExecutionMode.DRY_RUN,
            )

        # PAPER mode
        try:
            response = await self._bf.place_orders(
                market_id=intent.market_id,
                customer_order_ref=intent.customer_order_ref,
                selection_id=intent.selection_id,
                side=intent.side.value,
                price=intent.price,
                size=intent.size,
            )
            if response.get("status") == "SUCCESS":
                order_status = OrderStatus(
                    response.get("order_status", OrderStatus.EXECUTABLE.value)
                )
            else:
                order_status = OrderStatus.ERROR

            avg = response.get("average_price_matched")
            return OrderEvent(
                customer_order_ref=intent.customer_order_ref,
                decision_id=intent.decision_id,
                market_id=intent.market_id,
                event_id=intent.event_id,
                selection_id=intent.selection_id,
                side=intent.side,
                requested_price=intent.price,
                requested_size=intent.size,
                matched_size=Decimal(str(response.get("size_matched", 0))),
                average_price_matched=Decimal(str(avg)) if avg else None,
                status=order_status,
                event_type=OrderEventType.PLACED,
                api_response=response,
                mode=ExecutionMode.PAPER,
            )
        except Exception as e:
            log.exception(
                "execution_place_error",
                market_id=intent.market_id,
                customer_order_ref=intent.customer_order_ref,
            )
            return OrderEvent(
                customer_order_ref=intent.customer_order_ref,
                decision_id=intent.decision_id,
                market_id=intent.market_id,
                event_id=intent.event_id,
                selection_id=intent.selection_id,
                side=intent.side,
                requested_price=intent.price,
                requested_size=intent.size,
                status=OrderStatus.ERROR,
                event_type=OrderEventType.ERROR,
                api_response={"error": str(e)},
                mode=ExecutionMode.PAPER,
            )
