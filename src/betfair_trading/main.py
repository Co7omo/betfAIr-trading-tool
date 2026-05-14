"""Main entrypoint: wires all services and starts the scheduler."""

import asyncio
import contextlib
import signal
from pathlib import Path

import structlog
import yaml

from betfair_trading.betfair_client import auth as bf_auth
from betfair_trading.betfair_client.client import AsyncBetfairClient
from betfair_trading.db.pool import close_pool, create_pool
from betfair_trading.db.writer import insert_config_snapshot
from betfair_trading.elo.engine import EloEngine
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.models.decision import DecisionOutcome
from betfair_trading.models.order import ExecutionMode
from betfair_trading.observability.health import start_health_server
from betfair_trading.observability.logging import configure_logging
from betfair_trading.services.decision_engine import DecisionEngine
from betfair_trading.services.execution_engine import ExecutionEngine
from betfair_trading.services.external_ingestor import ExternalDataIngestor
from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from betfair_trading.services.model_inference_provider import ModelInferenceProvider
from betfair_trading.services.reconciler import Reconciler
from betfair_trading.services.scheduler import Scheduler
from betfair_trading.settings import Settings


def load_trading_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


async def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger()
    log.info("system_starting", version="0.1.0")

    # Load trading config
    trading_config = load_trading_config(settings.trading_config_path)
    trading = trading_config.get("trading", {})

    # Initialize DB pool
    pool = await create_pool(settings.database_url)

    # Store initial config snapshot
    async with pool.acquire() as conn:
        await insert_config_snapshot(
            conn,
            trading_config,
            kill_switch_active=trading_config.get("kill_switch", {}).get("active", False),
        )

    # Initialize Betfair client
    raw_client = bf_auth.create_betfair_client(settings)
    await bf_auth.login(raw_client)
    bf_client = AsyncBetfairClient(raw_client)

    # Initialize external data pipeline
    elo_engine = EloEngine()
    form_calculator = FormCalculator()
    team_matcher = TeamMatcher(Path("config/team_mappings.yaml"))
    ingestor = ExternalDataIngestor(elo_engine, form_calculator, team_matcher, pool)

    # Load historical results if available
    results_loaded = await ingestor.load_historical_results(settings.results_data_path)
    log.info("external_data_ready", results_loaded=results_loaded)

    # Initialize feature builder
    feature_builder = FeatureBuilder(pool, ingestor)

    # Initialize decision engine (Phase 3: real model inference provider)
    provider = ModelInferenceProvider(pool=pool, models_dir="models/")
    await provider.initialize()
    decision_engine = DecisionEngine(
        pool=pool,
        provider=provider,
        edge_threshold=trading.get("edge_threshold", 0.02),
        min_liquidity=trading.get("min_liquidity", 100.0),
        max_spread=trading.get("max_spread", 0.10),
        commission_rate=0.05,
        max_positions_per_event=trading.get("max_positions_per_event", 1),
        window_start_minutes=trading.get("window_start_minutes", 120),
        window_end_minutes=trading.get("window_end_minutes", 10),
        daily_dd_max=trading.get("daily_stop_loss_fraction", 0.05),
    )

    # Initialize execution engine + reconciler (Phase 3 baseline)
    execution_mode = ExecutionMode(trading.get("execution_mode", "dry_run"))
    execution_engine = ExecutionEngine(
        pool=pool,
        bf_client=bf_client,
        mode=execution_mode,
        bankroll=trading.get("initial_bankroll", 1000.0),
        kelly_multiplier=trading.get("kelly_fraction", 0.25),
        max_stake_fraction=trading.get("max_stake_fraction", 0.02),
        min_stake=trading.get("min_stake", 2.0),
    )
    reconciler = Reconciler(pool=pool, bf_client=bf_client, mode=execution_mode)

    # Initialize market collector
    collector = MarketCollector(
        bf_client,
        pool,
        window_start_minutes=trading.get("window_start_minutes", 120),
        window_end_minutes=trading.get("window_end_minutes", 10),
    )

    # Initialize scheduler
    scheduler = Scheduler(
        collector,
        raw_client,
        poll_interval=trading.get("poll_interval", 10),
        discovery_interval=trading.get("discovery_interval", 300),
        reconciler=reconciler,
        reconcile_interval=trading.get("reconcile_interval", 10),
    )

    async def on_snapshot_with_pipeline(bundle, snapshot_ids):
        fv_ids = await feature_builder.on_market_snapshot(bundle, snapshot_ids)
        if not fv_ids:
            return
        decision = await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)
        if decision is not None and decision.decision_outcome == DecisionOutcome.ALLOW:
            await execution_engine.on_decision_allow(decision)

    scheduler.set_snapshot_callback(on_snapshot_with_pipeline)

    # Start health check server
    health_runner = await start_health_server(pool, settings.health_port)

    # Handle graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler():
        log.info("shutdown_signal_received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    log.info("system_started", version="0.1.0")

    # Run scheduler until shutdown
    scheduler_task = asyncio.create_task(scheduler.run())

    await shutdown_event.wait()

    # Graceful shutdown
    log.info("system_shutting_down")
    await scheduler.stop()
    scheduler_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await scheduler_task

    await health_runner.cleanup()
    await bf_auth.logout(raw_client)
    await close_pool(pool)
    log.info("system_stopped")


if __name__ == "__main__":
    asyncio.run(main())
