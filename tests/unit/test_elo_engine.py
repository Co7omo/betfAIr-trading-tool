"""Tests for Elo engine — especially as-of correctness."""

from datetime import UTC, datetime

from betfair_trading.elo.engine import EloEngine, MatchResult


def make_ts(day: int) -> datetime:
    return datetime(2025, 1, day, tzinfo=UTC)


def test_initial_rating(elo_engine: EloEngine):
    assert elo_engine.get_rating("Unknown Team") == 1500.0


def test_home_win_updates_ratings(elo_engine: EloEngine):
    elo_engine.apply_result("TeamA", "TeamB", MatchResult.HOME_WIN, make_ts(1))

    assert elo_engine.get_rating("TeamA") > 1500.0
    assert elo_engine.get_rating("TeamB") < 1500.0


def test_draw_keeps_ratings_equal(elo_engine: EloEngine):
    elo_engine.apply_result("TeamA", "TeamB", MatchResult.DRAW, make_ts(1))

    assert elo_engine.get_rating("TeamA") == elo_engine.get_rating("TeamB")


def test_elo_is_zero_sum(elo_engine: EloEngine):
    elo_engine.apply_result("TeamA", "TeamB", MatchResult.HOME_WIN, make_ts(1))

    total = elo_engine.get_rating("TeamA") + elo_engine.get_rating("TeamB")
    assert abs(total - 3000.0) < 0.01


def test_asof_does_not_leak_future_data(elo_engine: EloEngine):
    """CRITICAL: ratings as-of day 5 must NOT include match on day 10."""
    elo_engine.apply_result("TeamA", "TeamB", MatchResult.HOME_WIN, make_ts(1))
    elo_engine.apply_result("TeamA", "TeamB", MatchResult.HOME_WIN, make_ts(10))

    # As-of day 5 should only reflect the day-1 match
    home, away = elo_engine.get_ratings_asof("TeamA", "TeamB", make_ts(5))
    home_full, away_full = elo_engine.get_ratings_asof("TeamA", "TeamB", make_ts(15))

    # After one win, TeamA should be ~1510, not ~1519.5 (two wins)
    assert home < home_full
    assert home > 1500.0
    assert away < 1500.0


def test_asof_before_any_matches_returns_initial(elo_engine: EloEngine):
    elo_engine.apply_result("TeamA", "TeamB", MatchResult.HOME_WIN, make_ts(10))

    home, away = elo_engine.get_ratings_asof("TeamA", "TeamB", make_ts(5))
    assert home == 1500.0
    assert away == 1500.0


def test_asof_with_multiple_teams(elo_engine: EloEngine):
    elo_engine.apply_result("TeamA", "TeamB", MatchResult.HOME_WIN, make_ts(1))
    elo_engine.apply_result("TeamC", "TeamD", MatchResult.AWAY_WIN, make_ts(2))
    elo_engine.apply_result("TeamA", "TeamC", MatchResult.DRAW, make_ts(5))

    # As-of day 3, TeamA should have day-1 result, TeamC should have day-2 result
    home, away = elo_engine.get_ratings_asof("TeamA", "TeamC", make_ts(3))
    assert home > 1500.0  # TeamA won on day 1
    assert away < 1500.0  # TeamC lost on day 2


def test_history_size_tracks_matches(elo_engine: EloEngine):
    assert elo_engine.history_size == 0
    elo_engine.apply_result("TeamA", "TeamB", MatchResult.HOME_WIN, make_ts(1))
    assert elo_engine.history_size == 1
    elo_engine.apply_result("TeamC", "TeamD", MatchResult.DRAW, make_ts(2))
    assert elo_engine.history_size == 2
