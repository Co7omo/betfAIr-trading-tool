"""Initial schema - append-only audit tables for Phase 1.

Revision ID: 001
Revises: None
Create Date: 2026-03-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    op.execute("""
    CREATE TABLE markets (
        market_id       TEXT        NOT NULL,
        event_id        TEXT        NOT NULL,
        sport_id        TEXT        NOT NULL DEFAULT '1',
        market_type     TEXT        NOT NULL DEFAULT 'MATCH_ODDS',
        competition_id  TEXT,
        competition_name TEXT,
        event_name      TEXT,
        country_code    TEXT,
        start_time      TIMESTAMPTZ NOT NULL,
        discovered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (market_id)
    );
    """)

    op.execute(
        "CREATE INDEX idx_markets_start_time ON markets (start_time);"
    )

    op.execute("""
    CREATE TABLE runners (
        market_id       TEXT        NOT NULL REFERENCES markets(market_id),
        runner_id       BIGINT      NOT NULL,
        runner_name     TEXT        NOT NULL,
        sort_priority   INT,
        discovered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (market_id, runner_id)
    );
    """)

    op.execute("""
    CREATE TABLE market_snapshots (
        snapshot_id     UUID        NOT NULL DEFAULT uuid_generate_v4(),
        market_id       TEXT        NOT NULL,
        runner_id       BIGINT      NOT NULL,
        snapshot_ts     TIMESTAMPTZ NOT NULL,
        best_back_price NUMERIC(10,4),
        best_back_size  NUMERIC(14,2),
        best_lay_price  NUMERIC(10,4),
        best_lay_size   NUMERIC(14,2),
        spread          NUMERIC(10,4),
        traded_volume   NUMERIC(14,2),
        total_matched   NUMERIC(14,2),
        market_status   TEXT,
        inplay          BOOLEAN     NOT NULL DEFAULT FALSE,
        minutes_to_start NUMERIC(8,2),
        PRIMARY KEY (snapshot_id)
    );
    """)

    op.execute(
        "CREATE INDEX idx_snapshots_market_ts ON market_snapshots (market_id, snapshot_ts);"
    )

    op.execute("""
    CREATE TABLE external_feature_snapshots (
        ext_snapshot_id UUID        NOT NULL DEFAULT uuid_generate_v4(),
        event_key       TEXT        NOT NULL,
        market_id       TEXT,
        asof_ts         TIMESTAMPTZ NOT NULL,
        home_team       TEXT        NOT NULL,
        away_team       TEXT        NOT NULL,
        elo_home        NUMERIC(8,2),
        elo_away        NUMERIC(8,2),
        elo_delta       NUMERIC(8,2),
        form_home_5     NUMERIC(6,4),
        form_away_5     NUMERIC(6,4),
        form_home_10    NUMERIC(6,4),
        form_away_10    NUMERIC(6,4),
        gd_home_5       NUMERIC(6,2),
        gd_away_5       NUMERIC(6,2),
        match_confidence TEXT       NOT NULL DEFAULT 'HIGH',
        quality_flags   JSONB       NOT NULL DEFAULT '{}',
        computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (ext_snapshot_id)
    );
    """)

    op.execute(
        "CREATE INDEX idx_ext_features_event ON external_feature_snapshots (event_key, asof_ts);"
    )

    op.execute("""
    CREATE TABLE feature_vectors (
        feature_vector_id   UUID        NOT NULL DEFAULT uuid_generate_v4(),
        market_id           TEXT        NOT NULL,
        event_id            TEXT        NOT NULL,
        runner_id           BIGINT      NOT NULL,
        decision_id         UUID,
        feature_set_version TEXT        NOT NULL,
        snapshot_id         UUID,
        ext_snapshot_id     UUID,
        features            JSONB       NOT NULL,
        feature_hash        TEXT        NOT NULL,
        generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (feature_vector_id)
    );
    """)

    op.execute("CREATE INDEX idx_fv_market ON feature_vectors (market_id, generated_at);")
    op.execute("CREATE INDEX idx_fv_version ON feature_vectors (feature_set_version);")

    op.execute("""
    CREATE TABLE config_snapshots (
        config_snapshot_id  UUID        NOT NULL DEFAULT uuid_generate_v4(),
        effective_ts        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        config_payload      JSONB       NOT NULL,
        config_hash         TEXT        NOT NULL,
        kill_switch_active  BOOLEAN     NOT NULL DEFAULT FALSE,
        PRIMARY KEY (config_snapshot_id)
    );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS config_snapshots;")
    op.execute("DROP TABLE IF EXISTS feature_vectors;")
    op.execute("DROP TABLE IF EXISTS external_feature_snapshots;")
    op.execute("DROP TABLE IF EXISTS market_snapshots;")
    op.execute("DROP TABLE IF EXISTS runners;")
    op.execute("DROP TABLE IF EXISTS markets;")
