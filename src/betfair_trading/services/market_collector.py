from datetime import UTC, datetime, timedelta

import asyncpg
import structlog

from betfair_trading.betfair_client.client import AsyncBetfairClient
from betfair_trading.db.writer import insert_market, insert_market_snapshots, insert_runners
from betfair_trading.models.market import MarketCatalogue

log = structlog.get_logger()


class MarketCollector:
    def __init__(
        self,
        bf_client: AsyncBetfairClient,
        db_pool: asyncpg.Pool,
        window_start_minutes: int = 120,
        window_end_minutes: int = 10,
    ):
        self._bf = bf_client
        self._pool = db_pool
        self._window_start = timedelta(minutes=window_start_minutes)
        self._window_end = timedelta(minutes=window_end_minutes)
        self._tracked_markets: dict[str, MarketCatalogue] = {}

    @property
    def tracked_count(self) -> int:
        return len(self._tracked_markets)

    async def run_discovery(self) -> int:
        catalogues = await self._bf.list_market_catalogue()
        new_count = 0

        async with self._pool.acquire() as conn:
            for cat in catalogues:
                if cat.market_id not in self._tracked_markets:
                    await insert_market(conn, cat)
                    await insert_runners(conn, cat.market_id, cat.runners)
                    self._tracked_markets[cat.market_id] = cat
                    new_count += 1

        log.info(
            "discovery_complete",
            new_markets=new_count,
            total_tracked=len(self._tracked_markets),
        )
        return new_count

    async def run_poll_cycle(self, on_snapshot=None) -> int:
        eligible = self._get_eligible_markets()
        if not eligible:
            return 0

        market_ids = [m.market_id for m in eligible]
        bundles = await self._bf.list_market_book(market_ids)

        # Enrich bundles with event_id from catalogue
        catalogue_map = {m.market_id: m for m in eligible}
        snapshot_count = 0

        async with self._pool.acquire() as conn:
            for bundle in bundles:
                cat = catalogue_map.get(bundle.market_id)
                if cat:
                    bundle.event_id = cat.event_id

                snapshot_ids = await insert_market_snapshots(conn, bundle)
                snapshot_count += len(snapshot_ids)

                if on_snapshot:
                    await on_snapshot(bundle, snapshot_ids)

        log.info(
            "poll_cycle_complete",
            markets_polled=len(bundles),
            snapshots_stored=snapshot_count,
        )
        return snapshot_count

    def _get_eligible_markets(self) -> list[MarketCatalogue]:
        now = datetime.now(UTC)
        eligible = []
        expired = []

        for market_id, cat in self._tracked_markets.items():
            time_to_start = cat.start_time - now
            if self._window_end <= time_to_start <= self._window_start:
                eligible.append(cat)
            elif time_to_start < self._window_end:
                expired.append(market_id)

        # Remove expired markets from tracking
        for mid in expired:
            del self._tracked_markets[mid]
            log.info("market_expired", market_id=mid)

        return eligible
