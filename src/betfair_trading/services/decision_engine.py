"""Decision Engine: consumes feature_vectors, computes per-outcome edge,
applies risk gates, persists audit-complete decisions."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import structlog

from betfair_trading.db.writer import insert_decision
from betfair_trading.models.decision import (
    Decision,
    DecisionOutcome,
    GateResult,
)
from betfair_trading.models.market import MarketSnapshotBundle, Runner
from betfair_trading.services.edge import compute_market_probs, compute_net_edge
from betfair_trading.services.gates import (
    check_daily_drawdown,
    check_edge_threshold,
    check_kill_switch,
    check_liquidity,
    check_position_limit,
    check_spread,
    check_window,
)
from betfair_trading.services.probability_providers import ProbabilityProvider

log = structlog.get_logger()


class DecisionEngine:
    def __init__(
        self,
        pool: asyncpg.Pool,
        provider: ProbabilityProvider,
        edge_threshold: float = 0.02,
        min_liquidity: float = 100.0,
        max_spread: float = 0.10,
        commission_rate: float = 0.05,
        max_positions_per_event: int = 1,
        window_start_minutes: int = 120,
        window_end_minutes: int = 10,
        daily_dd_max: float = 0.05,
    ):
        self._pool = pool
        self._provider = provider
        self._edge_threshold = edge_threshold
        self._min_liquidity = min_liquidity
        self._max_spread = max_spread
        self._commission_rate = commission_rate
        self._max_positions_per_event = max_positions_per_event
        self._window_start_minutes = window_start_minutes
        self._window_end_minutes = window_end_minutes
        self._daily_dd_max = daily_dd_max
        self._runner_meta_cache: dict[str, list[Runner]] = {}

    async def evaluate(
        self,
        bundle: MarketSnapshotBundle,
        snapshot_ids: list[uuid.UUID],
        feature_vector_ids: list[uuid.UUID],
    ) -> uuid.UUID | None:
        async with self._pool.acquire() as conn:
            runners_meta = await self._load_runners(conn, bundle.market_id)
            if not runners_meta:
                log.warning("decision_skip_no_runner_meta", market_id=bundle.market_id)
                return None

            cfg_row = await conn.fetchrow(
                "SELECT config_snapshot_id, kill_switch_active "
                "FROM config_snapshots ORDER BY effective_ts DESC LIMIT 1"
            )
            kill_switch_active = bool(cfg_row["kill_switch_active"]) if cfg_row else False
            config_snapshot_id = cfg_row["config_snapshot_id"] if cfg_row else None

            bundle_by_id = {rs.runner_id: rs for rs in bundle.runners}

            runner_quotes: dict[int, float | None] = {
                r.runner_id: (
                    float(bundle_by_id[r.runner_id].best_back_price)
                    if (
                        r.runner_id in bundle_by_id
                        and bundle_by_id[r.runner_id].best_back_price is not None
                    )
                    else None
                )
                for r in runners_meta
            }
            p_market = compute_market_probs(runner_quotes)

            p_model = await self._provider.get_probabilities(
                bundle, runners_meta, feature_vector_ids
            )

            edge_gross: dict[int, float] = {}
            edge_net: dict[int, float] = {}
            for r in runners_meta:
                gross, net = compute_net_edge(
                    p_model.get(r.runner_id, 0.0),
                    p_market.get(r.runner_id, 0.0),
                    self._commission_rate,
                )
                edge_gross[r.runner_id] = gross
                edge_net[r.runner_id] = net

            selected_runner_id = max(edge_net, key=lambda rid: edge_net[rid])
            selected_edge_net = edge_net[selected_runner_id]
            selected_runner_snapshot = bundle_by_id.get(selected_runner_id)

            allow_count = await conn.fetchval(
                "SELECT COUNT(*) FROM decisions WHERE event_id = $1 AND decision_outcome = 'ALLOW'",
                bundle.event_id,
            )

            def _gr(result: tuple[bool, str]) -> GateResult:
                return GateResult(passed=result[0], reason=result[1])

            best_back_size = (
                selected_runner_snapshot.best_back_size if selected_runner_snapshot else None
            )
            best_spread = selected_runner_snapshot.spread if selected_runner_snapshot else None

            gate_results: dict[str, GateResult] = {
                "kill_switch": _gr(check_kill_switch(kill_switch_active)),
                "window": _gr(
                    check_window(
                        bundle.minutes_to_start,
                        self._window_start_minutes,
                        self._window_end_minutes,
                    )
                ),
                "edge_threshold": _gr(
                    check_edge_threshold(selected_edge_net, self._edge_threshold)
                ),
                "liquidity": _gr(check_liquidity(best_back_size, self._min_liquidity)),
                "spread": _gr(check_spread(best_spread, self._max_spread)),
                "position_limit": _gr(
                    check_position_limit(allow_count, self._max_positions_per_event)
                ),
                "daily_drawdown": _gr(check_daily_drawdown(0.0, self._daily_dd_max)),
            }

            outcome = self._determine_outcome(gate_results)
            rationale = self._build_rationale(gate_results, outcome)
            snapshot_id = snapshot_ids[0] if snapshot_ids else None

            decision = Decision(
                market_id=bundle.market_id,
                event_id=bundle.event_id,
                snapshot_id=snapshot_id,
                decision_ts=datetime.now(UTC),
                model_version=self._provider.model_version,
                p_model=p_model,
                p_market=p_market,
                edge_gross=edge_gross,
                edge_net=edge_net,
                selected_runner_id=selected_runner_id,
                selected_edge_net=Decimal(str(round(selected_edge_net, 6))),
                gate_results=gate_results,
                decision_outcome=outcome,
                rationale=rationale,
                feature_vector_ids=feature_vector_ids,
                config_snapshot_id=config_snapshot_id,
            )
            decision_id = await insert_decision(conn, decision)

        log.info(
            "decision_made",
            market_id=bundle.market_id,
            outcome=outcome.value,
            selected_runner=selected_runner_id,
            edge_net=selected_edge_net,
        )
        return decision_id

    @staticmethod
    def _determine_outcome(gate_results: dict[str, GateResult]) -> DecisionOutcome:
        if not gate_results["kill_switch"].passed:
            return DecisionOutcome.BLOCK_HARD
        if any(not r.passed for r in gate_results.values()):
            return DecisionOutcome.BLOCK_SOFT
        return DecisionOutcome.ALLOW

    @staticmethod
    def _build_rationale(gate_results: dict[str, GateResult], outcome: DecisionOutcome) -> str:
        if outcome == DecisionOutcome.ALLOW:
            return "all gates passed"
        failed = [f"{name}:{r.reason}" for name, r in gate_results.items() if not r.passed]
        return "; ".join(failed)

    async def _load_runners(self, conn: asyncpg.Connection, market_id: str) -> list[Runner]:
        if market_id in self._runner_meta_cache:
            return self._runner_meta_cache[market_id]
        rows = await conn.fetch(
            "SELECT runner_id, runner_name, sort_priority FROM runners "
            "WHERE market_id = $1 ORDER BY sort_priority NULLS LAST, runner_id",
            market_id,
        )
        runners = [
            Runner(
                runner_id=r["runner_id"],
                runner_name=r["runner_name"],
                sort_priority=r["sort_priority"],
            )
            for r in rows
        ]
        if runners:
            self._runner_meta_cache[market_id] = runners
        return runners
