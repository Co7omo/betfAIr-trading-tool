"""orders + fills tables for Execution Engine baseline.

Revision ID: 004
Revises: 003
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE orders (
        order_event_id           UUID         NOT NULL DEFAULT uuid_generate_v4(),
        customer_order_ref       TEXT         NOT NULL,
        decision_id              UUID         NOT NULL,
        market_id                TEXT         NOT NULL,
        event_id                 TEXT         NOT NULL,
        selection_id             BIGINT       NOT NULL,
        side                     TEXT         NOT NULL,
        requested_price          NUMERIC(10,4) NOT NULL,
        requested_size           NUMERIC(14,2) NOT NULL,
        matched_size             NUMERIC(14,2) NOT NULL DEFAULT 0,
        average_price_matched    NUMERIC(10,4),
        status                   TEXT         NOT NULL,
        event_type               TEXT         NOT NULL,
        event_ts                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        api_response             JSONB,
        mode                     TEXT         NOT NULL,
        PRIMARY KEY (order_event_id),
        CONSTRAINT orders_side_check CHECK (side IN ('BACK', 'LAY')),
        CONSTRAINT orders_event_type_check
            CHECK (event_type IN ('PLACED', 'LIFECYCLE', 'CANCELLED', 'ERROR')),
        CONSTRAINT orders_mode_check CHECK (mode IN ('dry_run', 'paper'))
    );
    """)
    op.execute(
        "CREATE INDEX idx_orders_customer_ref ON orders (customer_order_ref, event_ts DESC);"
    )
    op.execute("CREATE INDEX idx_orders_decision ON orders (decision_id);")
    op.execute("CREATE INDEX idx_orders_market ON orders (market_id, event_ts);")

    op.execute("""
    CREATE TABLE fills (
        fill_id                  UUID         NOT NULL DEFAULT uuid_generate_v4(),
        customer_order_ref       TEXT         NOT NULL,
        decision_id              UUID         NOT NULL,
        market_id                TEXT         NOT NULL,
        selection_id             BIGINT       NOT NULL,
        fill_ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        matched_size_delta       NUMERIC(14,2) NOT NULL,
        average_price_matched    NUMERIC(10,4) NOT NULL,
        cumulative_matched_size  NUMERIC(14,2) NOT NULL,
        remaining_size           NUMERIC(14,2) NOT NULL DEFAULT 0,
        PRIMARY KEY (fill_id)
    );
    """)
    op.execute(
        "CREATE INDEX idx_fills_customer_ref ON fills (customer_order_ref, fill_ts);"
    )
    op.execute("CREATE INDEX idx_fills_market ON fills (market_id, fill_ts);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fills;")
    op.execute("DROP TABLE IF EXISTS orders;")
