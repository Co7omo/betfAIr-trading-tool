"""SQLAlchemy Core table definitions for the read path."""

import sqlalchemy as sa

metadata = sa.MetaData()

markets = sa.Table(
    "markets",
    metadata,
    sa.Column("market_id", sa.Text, primary_key=True),
    sa.Column("event_id", sa.Text, nullable=False),
    sa.Column("sport_id", sa.Text, nullable=False, server_default="1"),
    sa.Column("market_type", sa.Text, nullable=False, server_default="MATCH_ODDS"),
    sa.Column("competition_id", sa.Text),
    sa.Column("competition_name", sa.Text),
    sa.Column("event_name", sa.Text),
    sa.Column("country_code", sa.Text),
    sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
)

runners = sa.Table(
    "runners",
    metadata,
    sa.Column("market_id", sa.Text, sa.ForeignKey("markets.market_id"), primary_key=True),
    sa.Column("runner_id", sa.BigInteger, primary_key=True),
    sa.Column("runner_name", sa.Text, nullable=False),
    sa.Column("sort_priority", sa.Integer),
    sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
)

market_snapshots = sa.Table(
    "market_snapshots",
    metadata,
    sa.Column("snapshot_id", sa.Uuid, primary_key=True),
    sa.Column("market_id", sa.Text, nullable=False),
    sa.Column("runner_id", sa.BigInteger, nullable=False),
    sa.Column("snapshot_ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("best_back_price", sa.Numeric(10, 4)),
    sa.Column("best_back_size", sa.Numeric(14, 2)),
    sa.Column("best_lay_price", sa.Numeric(10, 4)),
    sa.Column("best_lay_size", sa.Numeric(14, 2)),
    sa.Column("spread", sa.Numeric(10, 4)),
    sa.Column("traded_volume", sa.Numeric(14, 2)),
    sa.Column("total_matched", sa.Numeric(14, 2)),
    sa.Column("market_status", sa.Text),
    sa.Column("inplay", sa.Boolean, nullable=False),
    sa.Column("minutes_to_start", sa.Numeric(8, 2)),
)

external_feature_snapshots = sa.Table(
    "external_feature_snapshots",
    metadata,
    sa.Column("ext_snapshot_id", sa.Uuid, primary_key=True),
    sa.Column("event_key", sa.Text, nullable=False),
    sa.Column("market_id", sa.Text),
    sa.Column("asof_ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("home_team", sa.Text, nullable=False),
    sa.Column("away_team", sa.Text, nullable=False),
    sa.Column("elo_home", sa.Numeric(8, 2)),
    sa.Column("elo_away", sa.Numeric(8, 2)),
    sa.Column("elo_delta", sa.Numeric(8, 2)),
    sa.Column("form_home_5", sa.Numeric(6, 4)),
    sa.Column("form_away_5", sa.Numeric(6, 4)),
    sa.Column("form_home_10", sa.Numeric(6, 4)),
    sa.Column("form_away_10", sa.Numeric(6, 4)),
    sa.Column("gd_home_5", sa.Numeric(6, 2)),
    sa.Column("gd_away_5", sa.Numeric(6, 2)),
    sa.Column("match_confidence", sa.Text, nullable=False),
    sa.Column("quality_flags", sa.JSON, nullable=False),
    sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
)

feature_vectors = sa.Table(
    "feature_vectors",
    metadata,
    sa.Column("feature_vector_id", sa.Uuid, primary_key=True),
    sa.Column("market_id", sa.Text, nullable=False),
    sa.Column("event_id", sa.Text, nullable=False),
    sa.Column("runner_id", sa.BigInteger, nullable=False),
    sa.Column("decision_id", sa.Uuid),
    sa.Column("feature_set_version", sa.Text, nullable=False),
    sa.Column("snapshot_id", sa.Uuid),
    sa.Column("ext_snapshot_id", sa.Uuid),
    sa.Column("features", sa.JSON, nullable=False),
    sa.Column("feature_hash", sa.Text, nullable=False),
    sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
)

config_snapshots = sa.Table(
    "config_snapshots",
    metadata,
    sa.Column("config_snapshot_id", sa.Uuid, primary_key=True),
    sa.Column("effective_ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("config_payload", sa.JSON, nullable=False),
    sa.Column("config_hash", sa.Text, nullable=False),
    sa.Column("kill_switch_active", sa.Boolean, nullable=False),
)
