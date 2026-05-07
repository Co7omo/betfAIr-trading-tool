"""Feature Builder: merges market snapshots with external features
into versioned feature vectors."""

import uuid
from datetime import UTC, datetime

import asyncpg
import structlog

from betfair_trading.db.writer import insert_feature_vector
from betfair_trading.models.features import FeatureSetVersion, FeatureVector
from betfair_trading.models.market import MarketSnapshotBundle
from betfair_trading.services.external_ingestor import ExternalDataIngestor

log = structlog.get_logger()


class FeatureBuilder:
    def __init__(
        self, db_pool: asyncpg.Pool, external_ingestor: ExternalDataIngestor | None = None
    ):
        self._pool = db_pool
        self._ingestor = external_ingestor

    async def on_market_snapshot(
        self, bundle: MarketSnapshotBundle, snapshot_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Called by MarketCollector after each poll cycle. Builds and persists feature vectors."""
        feature_vector_ids = []

        async with self._pool.acquire() as conn:
            # Build A0 (market-only) for every runner
            for i, runner in enumerate(bundle.runners):
                a0_features = self._build_a0(bundle, runner)
                snapshot_id = snapshot_ids[i] if i < len(snapshot_ids) else None

                fv = FeatureVector(
                    market_id=bundle.market_id,
                    event_id=bundle.event_id,
                    runner_id=runner.runner_id,
                    feature_set_version=FeatureSetVersion.A0,
                    snapshot_id=snapshot_id,
                    features=a0_features,
                    generated_at=datetime.now(UTC),
                )

                fv_id = await insert_feature_vector(conn, fv)
                feature_vector_ids.append(fv_id)

        log.debug(
            "features_built",
            market_id=bundle.market_id,
            version="A0",
            vectors=len(feature_vector_ids),
        )
        return feature_vector_ids

    @staticmethod
    def _build_a0(bundle: MarketSnapshotBundle, runner) -> dict:
        """A0: Market-only features."""
        back_price = float(runner.best_back_price) if runner.best_back_price else None
        lay_price = float(runner.best_lay_price) if runner.best_lay_price else None

        implied_prob_raw = None
        if back_price and back_price > 0:
            implied_prob_raw = 1.0 / back_price

        mid_price = None
        if back_price and lay_price:
            mid_price = (back_price + lay_price) / 2.0

        return {
            "best_back": back_price,
            "best_lay": lay_price,
            "best_back_size": float(runner.best_back_size) if runner.best_back_size else None,
            "best_lay_size": float(runner.best_lay_size) if runner.best_lay_size else None,
            "spread": float(runner.spread) if runner.spread else None,
            "mid_price": mid_price,
            "traded_volume": float(runner.traded_volume),
            "total_matched": float(bundle.total_matched) if bundle.total_matched else None,
            "implied_prob_raw": implied_prob_raw,
            "minutes_to_start": bundle.minutes_to_start,
            "market_status": bundle.market_status,
            "inplay": bundle.inplay,
        }
