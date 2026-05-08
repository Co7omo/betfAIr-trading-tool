"""Unit tests for ExternalDataIngestor.get_features_asof()."""

from datetime import UTC, datetime

from betfair_trading.elo.engine import EloEngine
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.services.external_ingestor import ExternalDataIngestor


def test_quality_flags_includes_history_loaded_false_when_not_loaded():
    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher()
    ingestor = ExternalDataIngestor(elo, form, matcher, db_pool=None)

    bundle = ingestor.get_features_asof(
        home_team="Liverpool", away_team="Arsenal",
        asof_ts=datetime(2026, 4, 1, tzinfo=UTC), market_id="1.X",
    )

    assert bundle is not None
    assert bundle.quality_flags["history_loaded"] is False


def test_quality_flags_includes_history_loaded_true_after_load(tmp_path):
    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher()
    ingestor = ExternalDataIngestor(elo, form, matcher, db_pool=None)
    # Force the flag without loading a real CSV file
    ingestor._loaded = True

    bundle = ingestor.get_features_asof(
        home_team="Liverpool", away_team="Arsenal",
        asof_ts=datetime(2026, 4, 1, tzinfo=UTC), market_id="1.X",
    )

    assert bundle.quality_flags["history_loaded"] is True
