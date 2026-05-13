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
        self._placed_orders: dict[str, dict] = {}
        self._matching_behavior: dict[str, str] = {}

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

    async def list_market_book(self, market_ids: list[str]) -> list[MarketSnapshotBundle]:
        out: list[MarketSnapshotBundle] = []
        for mid in market_ids:
            queue = self._book_queues.get(mid, [])
            if not queue:
                continue
            idx = self._book_call_count.get(mid, 0) % len(queue)
            out.append(queue[idx])
            self._book_call_count[mid] = self._book_call_count.get(mid, 0) + 1
        return out

    # ------------------------------------------------------------------
    # Order surface for ExecutionEngine / Reconciler tests
    # ------------------------------------------------------------------

    def queue_match_behavior(self, customer_order_ref: str, behavior: str) -> None:
        """Configure matching for a customer_order_ref before it is placed.

        Valid behaviors:
            'instant_match' - fully matched on first poll
            'partial'       - 50% matched on first poll, remaining stays open
            'no_match'      - never matched
            'lapse'         - status transitions to LAPSED on first poll
        """
        if behavior not in {"instant_match", "partial", "no_match", "lapse"}:
            raise ValueError(f"unknown behavior: {behavior}")
        self._matching_behavior[customer_order_ref] = behavior

    async def place_orders(
        self,
        market_id: str,
        customer_order_ref: str,
        selection_id: int,
        side: str,
        price,
        size,
        persistence_type: str = "LAPSE",
    ) -> dict:
        """Record the placement and return a synthetic instruction report."""
        if customer_order_ref in self._placed_orders:
            return {
                "status": "FAILURE",
                "error_code": "DUPLICATE_BETIDS",
                "order_status": "ERROR",
            }
        record = {
            "market_id": market_id,
            "selection_id": selection_id,
            "side": side,
            "price": float(price),
            "size": float(size),
            "persistence_type": persistence_type,
            "size_matched": 0.0,
            "size_remaining": float(size),
            "average_price_matched": 0.0,
            "order_status": "EXECUTABLE",
            "bet_id": f"FAKE-{customer_order_ref[:8]}",
        }
        self._placed_orders[customer_order_ref] = record
        return {
            "status": "SUCCESS",
            "order_status": "EXECUTABLE",
            "bet_id": record["bet_id"],
            "size_matched": 0.0,
            "average_price_matched": 0.0,
            "customer_order_ref": customer_order_ref,
        }

    async def list_current_orders(
        self, customer_order_refs: list[str]
    ) -> list[dict]:
        """Return synthetic state for placed orders, applying configured behavior."""
        out = []
        for ref in customer_order_refs:
            record = self._placed_orders.get(ref)
            if record is None:
                continue
            behavior = self._matching_behavior.get(ref, "no_match")
            requested_size = record["size"]
            requested_price = record["price"]

            if behavior == "instant_match":
                record["size_matched"] = requested_size
                record["size_remaining"] = 0.0
                record["average_price_matched"] = requested_price
                record["order_status"] = "EXECUTION_COMPLETE"
            elif behavior == "partial":
                record["size_matched"] = requested_size / 2.0
                record["size_remaining"] = requested_size / 2.0
                record["average_price_matched"] = requested_price
                # status remains EXECUTABLE
            elif behavior == "lapse":
                record["order_status"] = "LAPSED"
            # 'no_match' leaves state unchanged

            out.append({
                "customer_order_ref": ref,
                "order_status": record["order_status"],
                "size_matched": record["size_matched"],
                "size_remaining": record["size_remaining"],
                "average_price_matched": record["average_price_matched"],
                "bet_id": record["bet_id"],
            })
        return out
