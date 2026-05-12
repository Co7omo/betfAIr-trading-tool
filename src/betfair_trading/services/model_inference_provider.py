"""Real probability provider: loads the latest model_version + joblib artifact,
predicts on the A2 feature_vector, persists model_inferences, returns
(probabilities, inference_id).
"""

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import joblib
import structlog

from betfair_trading.db.writer import insert_model_inference
from betfair_trading.models.inference import ModelInference
from betfair_trading.models.market import MarketSnapshotBundle, Runner
from betfair_trading.services.probability_providers import MarketImpliedProvider
from betfair_trading.training.features import (
    build_feature_dict,
    feature_dict_to_array,
)

log = structlog.get_logger()


def _extract_values_from_a2(a2: dict) -> dict[str, float | None]:
    """Map the A2 feature_vector JSONB to training-shape values dict."""
    fh5 = a2.get("form_home_5") or {}
    fa5 = a2.get("form_away_5") or {}
    fh10 = a2.get("form_home_10") or {}
    fa10 = a2.get("form_away_10") or {}
    return {
        "elo_home": a2.get("elo_home"),
        "elo_away": a2.get("elo_away"),
        "elo_delta": a2.get("elo_delta"),
        "form_home_5_ppm": fh5.get("points_per_match"),
        "form_away_5_ppm": fa5.get("points_per_match"),
        "form_home_5_gd": fh5.get("goal_diff_per_match"),
        "form_away_5_gd": fa5.get("goal_diff_per_match"),
        "form_home_5_wr": fh5.get("win_rate"),
        "form_away_5_wr": fa5.get("win_rate"),
        "form_home_10_ppm": fh10.get("points_per_match"),
        "form_away_10_ppm": fa10.get("points_per_match"),
        "form_home_10_gd": fh10.get("goal_diff_per_match"),
        "form_away_10_gd": fa10.get("goal_diff_per_match"),
        "form_home_10_wr": fh10.get("win_rate"),
        "form_away_10_wr": fa10.get("win_rate"),
    }


class ModelInferenceProvider:
    """Loads the latest model at startup. On miss or missing A2 feature, falls back
    to MarketImpliedProvider (zero edge) with a warning log."""

    def __init__(self, pool: asyncpg.Pool, models_dir: str | Path = "models/"):
        self._pool = pool
        self._models_dir = Path(models_dir)
        self._model = None
        self._model_version_id: uuid.UUID | None = None
        self._model_name: str = "STUB_NO_MODEL"
        self._fallback = MarketImpliedProvider()

    @property
    def model_version(self) -> str:
        return self._model_name

    async def initialize(self) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT model_version_id, model_name, file_path "
                "FROM model_versions ORDER BY created_ts DESC LIMIT 1"
            )
        if row is None:
            log.warning("model_inference_no_version_available")
            return

        # The file_path in DB may be absolute or relative; try both.
        candidate = Path(row["file_path"])
        if not candidate.is_absolute() and not candidate.exists():
            candidate = self._models_dir / candidate.name
        if not candidate.exists():
            # Try basename inside models_dir as last resort
            alt = self._models_dir / candidate.name
            if alt.exists():
                candidate = alt
            else:
                log.error(
                    "model_inference_artifact_missing",
                    file_path=str(row["file_path"]),
                )
                return

        self._model = joblib.load(candidate)
        self._model_version_id = row["model_version_id"]
        self._model_name = row["model_name"]
        log.info("model_inference_loaded", version=self._model_name)

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> tuple[dict[int, float], uuid.UUID | None]:
        if self._model is None:
            probs, _ = await self._fallback.get_probabilities(
                bundle, runners, feature_vector_ids
            )
            return probs, None

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT features FROM feature_vectors "
                "WHERE feature_vector_id = ANY($1) "
                "AND feature_set_version = 'A2' LIMIT 1",
                feature_vector_ids,
            )
            if row is None:
                log.warning(
                    "model_inference_no_a2_falling_back",
                    market_id=bundle.market_id,
                )
                probs, _ = await self._fallback.get_probabilities(
                    bundle, runners, feature_vector_ids
                )
                return probs, None

            features_raw = row["features"]
            a2_features = (
                json.loads(features_raw) if isinstance(features_raw, str) else features_raw
            )
            values = _extract_values_from_a2(a2_features)
            feature_dict = build_feature_dict(values)
            X = feature_dict_to_array(feature_dict)

            proba = self._model.predict_proba(X)[0]  # [p_home, p_draw, p_away]

            sorted_r = sorted(
                runners, key=lambda r: (r.sort_priority is None, r.sort_priority)
            )
            result_probs = {
                sorted_r[0].runner_id: float(proba[0]),
                sorted_r[1].runner_id: float(proba[1]),
                sorted_r[2].runner_id: float(proba[2]),
            }

            inference = ModelInference(
                model_version_id=self._model_version_id,
                market_id=bundle.market_id,
                event_id=bundle.event_id,
                asof_ts=bundle.snapshot_ts,
                p_home=Decimal(str(round(float(proba[0]), 6))),
                p_draw=Decimal(str(round(float(proba[1]), 6))),
                p_away=Decimal(str(round(float(proba[2]), 6))),
                feature_vector_ids=feature_vector_ids,
                features_used=feature_dict,
            )
            inference_id = await insert_model_inference(conn, inference)

        return result_probs, inference_id
