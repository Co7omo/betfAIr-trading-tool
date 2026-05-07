"""Fake AsyncBetfairClient for integration tests.

Returns Pydantic models (MarketCatalogue, MarketSnapshotBundle) directly,
skipping the betfairlightweight `from_betfair` conversion. The MarketCollector
consumes these two methods: list_market_catalogue() and list_market_book(market_ids).
"""

from betfair_trading.models.market import MarketCatalogue, MarketSnapshotBundle


class FakeAsyncBetfairClient:
    def __init__(self):
        self._catalogue: list[MarketCatalogue] = []
        self._book_queues: dict[str, list[MarketSnapshotBundle]] = {}
        self._book_call_count: dict[str, int] = {}

    # Builder API for tests ---------------------------------------------------

    def add_market(self, catalogue: MarketCatalogue) -> None:
        """Append a MarketCatalogue to the result of list_market_catalogue()."""
        self._catalogue.append(catalogue)

    def queue_book(self, market_id: str, bundle: MarketSnapshotBundle) -> None:
        """Queue a MarketSnapshotBundle returned by the next list_market_book()
        for this market_id (round-robin over the queue)."""
        self._book_queues.setdefault(market_id, []).append(bundle)

    # Client surface ----------------------------------------------------------

    async def list_market_catalogue(
        self, hours_ahead: float = 2.5, max_results: int = 100
    ) -> list[MarketCatalogue]:
        return list(self._catalogue)

    async def list_market_book(
        self, market_ids: list[str]
    ) -> list[MarketSnapshotBundle]:
        out: list[MarketSnapshotBundle] = []
        for mid in market_ids:
            queue = self._book_queues.get(mid, [])
            if not queue:
                continue
            idx = self._book_call_count.get(mid, 0) % len(queue)
            out.append(queue[idx])
            self._book_call_count[mid] = self._book_call_count.get(mid, 0) + 1
        return out
