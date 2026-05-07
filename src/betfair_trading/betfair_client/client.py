import asyncio
from datetime import UTC, datetime, timedelta

import betfairlightweight
import structlog
from betfairlightweight.filters import market_filter

from betfair_trading.models.market import (
    MarketCatalogue,
    MarketSnapshotBundle,
    RunnerSnapshot,
)

log = structlog.get_logger()


class AsyncBetfairClient:
    def __init__(self, client: betfairlightweight.APIClient):
        self._client = client

    async def list_market_catalogue(
        self,
        hours_ahead: float = 2.5,
        max_results: int = 100,
    ) -> list[MarketCatalogue]:
        now = datetime.now(UTC)
        time_range = betfairlightweight.filters.time_range(
            from_=now,
            to=now + timedelta(hours=hours_ahead),
        )
        mf = market_filter(
            event_type_ids=["1"],  # Football
            market_type_codes=["MATCH_ODDS"],
            market_start_time=time_range,
            in_play_only=False,
        )

        raw = await asyncio.to_thread(
            self._client.betting.list_market_catalogue,
            filter=mf,
            market_projection=[
                "COMPETITION",
                "EVENT",
                "EVENT_TYPE",
                "RUNNER_METADATA",
                "MARKET_START_TIME",
            ],
            max_results=max_results,
        )

        catalogues = [MarketCatalogue.from_betfair(m) for m in raw]
        log.info("market_catalogue_fetched", count=len(catalogues))
        return catalogues

    async def list_market_book(self, market_ids: list[str]) -> list[MarketSnapshotBundle]:
        if not market_ids:
            return []

        now = datetime.now(UTC)
        # Betfair allows up to ~40 market IDs per call, batch if needed
        bundles = []
        for batch_start in range(0, len(market_ids), 40):
            batch = market_ids[batch_start : batch_start + 40]
            raw = await asyncio.to_thread(
                self._client.betting.list_market_book,
                market_ids=batch,
                price_projection=betfairlightweight.filters.price_projection(
                    price_data=["EX_BEST_OFFERS", "EX_TRADED"],
                ),
            )

            for book in raw:
                start_time = book.market_definition.market_time if book.market_definition else now
                minutes_to_start = (start_time - now).total_seconds() / 60.0

                runners = [RunnerSnapshot.from_betfair(r) for r in book.runners]
                bundle = MarketSnapshotBundle(
                    market_id=book.market_id,
                    event_id="",  # Filled by caller from catalogue
                    snapshot_ts=now,
                    runners=runners,
                    market_status=book.status,
                    inplay=book.inplay,
                    total_matched=book.total_matched,
                    minutes_to_start=minutes_to_start,
                )
                bundles.append(bundle)

        log.debug("market_book_fetched", count=len(bundles))
        return bundles
