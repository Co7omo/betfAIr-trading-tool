import asyncio

import structlog

from betfair_trading.betfair_client import auth as bf_auth
from betfair_trading.services.market_collector import MarketCollector

log = structlog.get_logger()


class Scheduler:
    def __init__(
        self,
        collector: MarketCollector,
        bf_client,
        poll_interval: int = 10,
        discovery_interval: int = 300,
        keepalive_interval: int = 3600,
    ):
        self._collector = collector
        self._bf_client = bf_client
        self._poll_interval = poll_interval
        self._discovery_interval = discovery_interval
        self._keepalive_interval = keepalive_interval
        self._running = False
        self._on_snapshot = None

    def set_snapshot_callback(self, callback) -> None:
        self._on_snapshot = callback

    async def run(self) -> None:
        self._running = True
        log.info(
            "scheduler_started",
            poll_interval=self._poll_interval,
            discovery_interval=self._discovery_interval,
        )

        tasks = [
            asyncio.create_task(self._discovery_loop(), name="discovery"),
            asyncio.create_task(self._poll_loop(), name="polling"),
            asyncio.create_task(self._keepalive_loop(), name="keepalive"),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("scheduler_cancelled")
        finally:
            self._running = False
            for task in tasks:
                task.cancel()

    async def stop(self) -> None:
        self._running = False

    async def _discovery_loop(self) -> None:
        while self._running:
            try:
                await self._collector.run_discovery()
            except Exception:
                log.exception("discovery_error")
            await asyncio.sleep(self._discovery_interval)

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                start = asyncio.get_event_loop().time()
                await self._collector.run_poll_cycle(on_snapshot=self._on_snapshot)
                elapsed = asyncio.get_event_loop().time() - start

                if elapsed > self._poll_interval:
                    log.warning(
                        "poll_cycle_slow",
                        elapsed_seconds=round(elapsed, 2),
                        interval=self._poll_interval,
                    )

                sleep_time = max(0, self._poll_interval - elapsed)
                await asyncio.sleep(sleep_time)
            except Exception:
                log.exception("poll_error")
                await asyncio.sleep(self._poll_interval)

    async def _keepalive_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._keepalive_interval)
            try:
                await bf_auth.keep_alive(self._bf_client)
            except Exception:
                log.exception("keepalive_error")
