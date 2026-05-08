"""Feature Builder: merges market snapshots with external features
into versioned feature vectors."""

import uuid
from datetime import UTC, datetime

import asyncpg
import structlog

from betfair_trading.db.writer import (
    insert_external_feature_snapshot,
    insert_feature_vector,
)
from betfair_trading.models.external import ExternalFeatureBundle
from betfair_trading.models.features import FeatureSetVersion, FeatureVector
from betfair_trading.models.market import MarketSnapshotBundle, Runner
from betfair_trading.services.external_ingestor import ExternalDataIngestor

log = structlog.get_logger()


class FeatureBuilder:
    def __init__(
        self, db_pool: asyncpg.Pool, external_ingestor: ExternalDataIngestor | None = None
    ):
        self._pool = db_pool
        self._ingestor = external_ingestor
        # Caches per market_id (live for the lifetime of the process)
        self._ext_id_cache: dict[str, uuid.UUID] = {}
        self._ext_bundle_cache: dict[str, ExternalFeatureBundle] = {}
        self._runner_meta_cache: dict[str, list[Runner]] = {}

    async def on_market_snapshot(
        self, bundle: MarketSnapshotBundle, snapshot_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Called by MarketCollector after each poll cycle.
        Builds and persists A0 always; A1+A2 if external_ingestor is wired.
        """
        ext_snapshot_id, ext_bundle = await self._get_or_create_external(bundle)
        feature_vector_ids: list[uuid.UUID] = []

        async with self._pool.acquire() as conn:
            for i, runner in enumerate(bundle.runners):
                snap_id = snapshot_ids[i] if i < len(snapshot_ids) else None
                a0 = self._build_a0(bundle, runner)
                feature_vector_ids.append(
                    await insert_feature_vector(
                        conn,
                        FeatureVector(
                            market_id=bundle.market_id,
                            event_id=bundle.event_id,
                            runner_id=runner.runner_id,
                            feature_set_version=FeatureSetVersion.A0,
                            snapshot_id=snap_id,
                            ext_snapshot_id=None,
                            features=a0,
                            generated_at=datetime.now(UTC),
                        ),
                    )
                )
                if ext_snapshot_id is not None and ext_bundle is not None:
                    a1 = self._build_a1(a0, ext_bundle)
                    a2 = self._build_a2(a1, ext_bundle)
                    feature_vector_ids.append(
                        await insert_feature_vector(
                            conn,
                            FeatureVector(
                                market_id=bundle.market_id,
                                event_id=bundle.event_id,
                                runner_id=runner.runner_id,
                                feature_set_version=FeatureSetVersion.A1,
                                snapshot_id=snap_id,
                                ext_snapshot_id=ext_snapshot_id,
                                features=a1,
                                generated_at=datetime.now(UTC),
                            ),
                        )
                    )
                    feature_vector_ids.append(
                        await insert_feature_vector(
                            conn,
                            FeatureVector(
                                market_id=bundle.market_id,
                                event_id=bundle.event_id,
                                runner_id=runner.runner_id,
                                feature_set_version=FeatureSetVersion.A2,
                                snapshot_id=snap_id,
                                ext_snapshot_id=ext_snapshot_id,
                                features=a2,
                                generated_at=datetime.now(UTC),
                            ),
                        )
                    )

        log.debug(
            "features_built",
            market_id=bundle.market_id,
            with_external=ext_snapshot_id is not None,
            vectors=len(feature_vector_ids),
        )
        return feature_vector_ids

    async def _get_or_create_external(
        self, bundle: MarketSnapshotBundle
    ) -> tuple[uuid.UUID | None, ExternalFeatureBundle | None]:
        """Idempotent: returns cached ext_snapshot_id for the market if seen, else
        computes the ExternalFeatureBundle and persists it once."""
        if self._ingestor is None:
            return None, None
        if bundle.market_id in self._ext_id_cache:
            return (
                self._ext_id_cache[bundle.market_id],
                self._ext_bundle_cache[bundle.market_id],
            )

        async with self._pool.acquire() as conn:
            runners = await self._load_runner_metadata(conn, bundle.market_id)
            if not runners:
                # Market not yet in `runners` table — defensive: skip A1/A2 this cycle.
                log.warning("ext_skip_no_runner_meta", market_id=bundle.market_id)
                return None, None
            home, away = self._extract_teams(runners)
            ext_bundle = self._ingestor.get_features_asof(
                home_team=home,
                away_team=away,
                asof_ts=bundle.snapshot_ts,
                market_id=bundle.market_id,
            )
            ext_id = await insert_external_feature_snapshot(conn, ext_bundle)

        self._ext_id_cache[bundle.market_id] = ext_id
        self._ext_bundle_cache[bundle.market_id] = ext_bundle
        return ext_id, ext_bundle

    async def _load_runner_metadata(self, conn: asyncpg.Connection, market_id: str) -> list[Runner]:
        """Cached per market_id. Loads runner_name+sort_priority from `runners` table."""
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
        self._runner_meta_cache[market_id] = runners
        return runners

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

    @staticmethod
    def _extract_teams(runners: list[Runner]) -> tuple[str, str]:
        """Betfair Match Odds: sort_priority 1=home, 2=draw, 3=away.
        Runners with None sort_priority go last (defensive fallback).
        Home = lowest numeric sort_priority; away = highest numeric sort_priority.
        """
        sorted_runners = sorted(runners, key=lambda r: (r.sort_priority is None, r.sort_priority))
        # Runners with a numeric sort_priority come first in sorted order.
        # Home = first (lowest priority number), away = last with a numeric priority.
        numeric = [r for r in sorted_runners if r.sort_priority is not None]
        return numeric[0].runner_name, numeric[-1].runner_name

    @staticmethod
    def _build_a1(a0: dict, ext: ExternalFeatureBundle) -> dict:
        """A1 = A0 + Elo fields. Same fields for all runners; runner_id distinguishes."""
        return {
            **a0,
            "elo_home": float(ext.elo_home) if ext.elo_home is not None else None,
            "elo_away": float(ext.elo_away) if ext.elo_away is not None else None,
            "elo_delta": float(ext.elo_delta) if ext.elo_delta is not None else None,
            "match_confidence": ext.match_confidence,
        }

    @staticmethod
    def _build_a2(a1: dict, ext: ExternalFeatureBundle) -> dict:
        """A2 = A1 + form fields (home/away, n=5/10)."""

        def _form_dict(f):
            if f is None:
                return None
            return {
                "points_per_match": f.points_per_match,
                "goal_diff_per_match": f.goal_diff_per_match,
                "win_rate": f.win_rate,
            }

        return {
            **a1,
            "form_home_5": _form_dict(ext.form_home_5),
            "form_away_5": _form_dict(ext.form_away_5),
            "form_home_10": _form_dict(ext.form_home_10),
            "form_away_10": _form_dict(ext.form_away_10),
        }
