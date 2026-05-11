"""decisions table - Phase 2 Decision Engine.

Revision ID: 002
Revises: 001
Create Date: 2026-05-10
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE decisions (
        decision_id          UUID         NOT NULL DEFAULT uuid_generate_v4(),
        market_id            TEXT         NOT NULL,
        event_id             TEXT         NOT NULL,
        snapshot_id          UUID,
        decision_ts          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

        model_version        TEXT         NOT NULL,
        p_model              JSONB        NOT NULL,
        p_market             JSONB        NOT NULL,
        edge_gross           JSONB        NOT NULL,
        edge_net             JSONB        NOT NULL,

        selected_runner_id   BIGINT,
        selected_edge_net    NUMERIC(10,6),

        gate_results         JSONB        NOT NULL,
        decision_outcome     TEXT         NOT NULL,
        rationale            TEXT,

        feature_vector_ids   UUID[]       NOT NULL,
        config_snapshot_id   UUID,

        PRIMARY KEY (decision_id),
        CONSTRAINT decisions_outcome_check
            CHECK (decision_outcome IN ('ALLOW', 'BLOCK_SOFT', 'BLOCK_HARD'))
    );
    """)

    op.execute(
        "CREATE INDEX idx_decisions_event ON decisions (event_id, decision_ts);"
    )
    op.execute(
        "CREATE INDEX idx_decisions_market_outcome "
        "ON decisions (market_id, decision_outcome, decision_ts);"
    )
    op.execute(
        "CREATE INDEX idx_decisions_event_allow "
        "ON decisions (event_id) WHERE decision_outcome = 'ALLOW';"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decisions;")
