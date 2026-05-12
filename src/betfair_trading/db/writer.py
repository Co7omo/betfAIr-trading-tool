import hashlib
import json
import uuid
from decimal import Decimal

import asyncpg

from betfair_trading.models.decision import Decision
from betfair_trading.models.external import ExternalFeatureBundle
from betfair_trading.models.features import FeatureVector
from betfair_trading.models.inference import ModelInference, ModelVersion
from betfair_trading.models.market import MarketCatalogue, MarketSnapshotBundle


async def insert_market(conn: asyncpg.Connection, catalogue: MarketCatalogue) -> None:
    await conn.execute(
        """INSERT INTO markets (market_id, event_id, event_name, competition_id,
           competition_name, country_code, start_time)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (market_id) DO NOTHING""",
        catalogue.market_id,
        catalogue.event_id,
        catalogue.event_name,
        catalogue.competition_id,
        catalogue.competition_name,
        catalogue.country_code,
        catalogue.start_time,
    )


async def insert_runners(conn: asyncpg.Connection, market_id: str, runners: list) -> None:
    for runner in runners:
        await conn.execute(
            """INSERT INTO runners (market_id, runner_id, runner_name, sort_priority)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (market_id, runner_id) DO NOTHING""",
            market_id,
            runner.runner_id,
            runner.runner_name,
            runner.sort_priority,
        )


async def insert_market_snapshots(
    conn: asyncpg.Connection, bundle: MarketSnapshotBundle
) -> list[uuid.UUID]:
    snapshot_ids = []
    for runner in bundle.runners:
        sid = await conn.fetchval(
            """INSERT INTO market_snapshots
               (market_id, runner_id, snapshot_ts, best_back_price, best_back_size,
                best_lay_price, best_lay_size, spread, traded_volume, total_matched,
                market_status, inplay, minutes_to_start)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
               RETURNING snapshot_id""",
            bundle.market_id,
            runner.runner_id,
            bundle.snapshot_ts,
            runner.best_back_price,
            runner.best_back_size,
            runner.best_lay_price,
            runner.best_lay_size,
            runner.spread,
            runner.traded_volume,
            bundle.total_matched,
            bundle.market_status,
            bundle.inplay,
            Decimal(str(bundle.minutes_to_start)),
        )
        snapshot_ids.append(sid)
    return snapshot_ids


async def insert_external_feature_snapshot(
    conn: asyncpg.Connection, bundle: ExternalFeatureBundle
) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO external_feature_snapshots
           (ext_snapshot_id, event_key, market_id, asof_ts, home_team, away_team,
            elo_home, elo_away, elo_delta,
            form_home_5, form_away_5, form_home_10, form_away_10,
            gd_home_5, gd_away_5,
            match_confidence, quality_flags)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
           RETURNING ext_snapshot_id""",
        bundle.ext_snapshot_id,
        bundle.event_key,
        bundle.market_id,
        bundle.asof_ts,
        bundle.home_team,
        bundle.away_team,
        bundle.elo_home,
        bundle.elo_away,
        bundle.elo_delta,
        bundle.form_home_5.points_per_match if bundle.form_home_5 else None,
        bundle.form_away_5.points_per_match if bundle.form_away_5 else None,
        bundle.form_home_10.points_per_match if bundle.form_home_10 else None,
        bundle.form_away_10.points_per_match if bundle.form_away_10 else None,
        bundle.form_home_5.goal_diff_per_match if bundle.form_home_5 else None,
        bundle.form_away_5.goal_diff_per_match if bundle.form_away_5 else None,
        bundle.match_confidence,
        json.dumps(bundle.quality_flags),
    )


async def insert_feature_vector(conn: asyncpg.Connection, fv: FeatureVector) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO feature_vectors
           (feature_vector_id, market_id, event_id, runner_id, decision_id,
            feature_set_version, snapshot_id, ext_snapshot_id,
            features, feature_hash, generated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           RETURNING feature_vector_id""",
        fv.feature_vector_id,
        fv.market_id,
        fv.event_id,
        fv.runner_id,
        fv.decision_id,
        fv.feature_set_version.value,
        fv.snapshot_id,
        fv.ext_snapshot_id,
        json.dumps(fv.features, default=str),
        fv.feature_hash,
        fv.generated_at,
    )


async def insert_config_snapshot(
    conn: asyncpg.Connection, config_payload: dict, kill_switch_active: bool = False
) -> uuid.UUID:
    payload_json = json.dumps(config_payload, sort_keys=True, default=str)
    config_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    return await conn.fetchval(
        """INSERT INTO config_snapshots
           (config_payload, config_hash, kill_switch_active)
           VALUES ($1, $2, $3)
           RETURNING config_snapshot_id""",
        payload_json,
        config_hash,
        kill_switch_active,
    )


async def insert_decision(conn: asyncpg.Connection, decision: Decision) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO decisions
           (decision_id, market_id, event_id, snapshot_id, decision_ts,
            model_version, p_model, p_market, edge_gross, edge_net,
            selected_runner_id, selected_edge_net,
            gate_results, decision_outcome, rationale,
            feature_vector_ids, config_snapshot_id, inference_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
           RETURNING decision_id""",
        decision.decision_id,
        decision.market_id,
        decision.event_id,
        decision.snapshot_id,
        decision.decision_ts,
        decision.model_version,
        json.dumps({str(k): v for k, v in decision.p_model.items()}),
        json.dumps({str(k): v for k, v in decision.p_market.items()}),
        json.dumps({str(k): v for k, v in decision.edge_gross.items()}),
        json.dumps({str(k): v for k, v in decision.edge_net.items()}),
        decision.selected_runner_id,
        decision.selected_edge_net,
        json.dumps(
            {k: {"passed": v.passed, "reason": v.reason} for k, v in decision.gate_results.items()}
        ),
        decision.decision_outcome.value,
        decision.rationale,
        decision.feature_vector_ids,
        decision.config_snapshot_id,
        decision.inference_id,
    )


async def insert_model_version(conn: asyncpg.Connection, mv: ModelVersion) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO model_versions
           (model_version_id, model_name, feature_set_version,
            file_path, training_data_hash, training_csv_path,
            training_params, metrics, feature_names, n_train, n_test)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           RETURNING model_version_id""",
        mv.model_version_id,
        mv.model_name,
        mv.feature_set_version,
        mv.file_path,
        mv.training_data_hash,
        mv.training_csv_path,
        json.dumps(mv.training_params, default=str),
        json.dumps(mv.metrics, default=str),
        json.dumps(mv.feature_names),
        mv.n_train,
        mv.n_test,
    )


async def insert_model_inference(conn: asyncpg.Connection, mi: ModelInference) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO model_inferences
           (inference_id, model_version_id, market_id, event_id, asof_ts,
            p_home, p_draw, p_away, feature_vector_ids, features_used)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           RETURNING inference_id""",
        mi.inference_id,
        mi.model_version_id,
        mi.market_id,
        mi.event_id,
        mi.asof_ts,
        mi.p_home,
        mi.p_draw,
        mi.p_away,
        mi.feature_vector_ids,
        json.dumps(mi.features_used, default=str),
    )
