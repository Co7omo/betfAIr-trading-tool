"""model_versions + model_inferences tables; decisions.inference_id column.

Revision ID: 003
Revises: 002
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE model_versions (
        model_version_id     UUID         NOT NULL DEFAULT uuid_generate_v4(),
        model_name           TEXT         NOT NULL,
        feature_set_version  TEXT         NOT NULL,
        created_ts           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        file_path            TEXT         NOT NULL,
        training_data_hash   TEXT         NOT NULL,
        training_csv_path    TEXT         NOT NULL,
        training_params      JSONB        NOT NULL DEFAULT '{}',
        metrics              JSONB        NOT NULL DEFAULT '{}',
        feature_names        JSONB        NOT NULL,
        n_train              INT          NOT NULL,
        n_test               INT          NOT NULL,
        PRIMARY KEY (model_version_id)
    );
    """)
    op.execute(
        "CREATE INDEX idx_model_versions_created ON model_versions (created_ts DESC);"
    )

    op.execute("""
    CREATE TABLE model_inferences (
        inference_id         UUID         NOT NULL DEFAULT uuid_generate_v4(),
        model_version_id     UUID         NOT NULL,
        market_id            TEXT         NOT NULL,
        event_id             TEXT         NOT NULL,
        inference_ts         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        asof_ts              TIMESTAMPTZ  NOT NULL,
        p_home               NUMERIC(8,6),
        p_draw               NUMERIC(8,6),
        p_away               NUMERIC(8,6),
        feature_vector_ids   UUID[]       NOT NULL,
        features_used        JSONB        NOT NULL,
        PRIMARY KEY (inference_id)
    );
    """)
    op.execute(
        "CREATE INDEX idx_model_inferences_market "
        "ON model_inferences (market_id, inference_ts);"
    )
    op.execute(
        "CREATE INDEX idx_model_inferences_version "
        "ON model_inferences (model_version_id);"
    )

    op.execute("ALTER TABLE decisions ADD COLUMN inference_id UUID;")
    op.execute(
        "CREATE INDEX idx_decisions_inference "
        "ON decisions (inference_id) WHERE inference_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_decisions_inference;")
    op.execute("ALTER TABLE decisions DROP COLUMN IF EXISTS inference_id;")
    op.execute("DROP TABLE IF EXISTS model_inferences;")
    op.execute("DROP TABLE IF EXISTS model_versions;")
