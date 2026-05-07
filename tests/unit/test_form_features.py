"""Tests for form feature computation with as-of semantics."""

from datetime import UTC, datetime

from betfair_trading.elo.engine import MatchResult
from betfair_trading.elo.form import FormCalculator


def make_ts(day: int) -> datetime:
    return datetime(2025, 1, day, tzinfo=UTC)


def test_no_matches_returns_none(form_calculator: FormCalculator):
    result = form_calculator.compute_form("TeamA", make_ts(10), n=5)
    assert result is None


def test_basic_form_computation(form_calculator: FormCalculator):
    # 3 wins for TeamA
    form_calculator.add_match("TeamA", "TeamB", MatchResult.HOME_WIN, 2, 0, make_ts(1))
    form_calculator.add_match("TeamA", "TeamC", MatchResult.HOME_WIN, 1, 0, make_ts(2))
    form_calculator.add_match("TeamA", "TeamD", MatchResult.HOME_WIN, 3, 1, make_ts(3))

    form = form_calculator.compute_form("TeamA", make_ts(10), n=5)
    assert form is not None
    assert form.win_rate == 1.0
    assert form.loss_rate == 0.0
    assert form.points_per_match == 3.0


def test_form_asof_excludes_future(form_calculator: FormCalculator):
    """CRITICAL: form as-of day 5 must NOT include match on day 10."""
    form_calculator.add_match("TeamA", "TeamB", MatchResult.HOME_WIN, 2, 0, make_ts(1))
    form_calculator.add_match("TeamA", "TeamC", MatchResult.DRAW, 1, 1, make_ts(10))

    # As-of day 5: only the day-1 win
    form = form_calculator.compute_form("TeamA", make_ts(5), n=5)
    assert form is not None
    assert form.win_rate == 1.0  # Only win visible
    assert form.points_per_match == 3.0

    # As-of day 15: both matches visible
    form_full = form_calculator.compute_form("TeamA", make_ts(15), n=5)
    assert form_full is not None
    assert form_full.win_rate == 0.5
    assert form_full.draw_rate == 0.5


def test_form_n_limit(form_calculator: FormCalculator):
    for day in range(1, 8):
        form_calculator.add_match("TeamA", f"Opp{day}", MatchResult.HOME_WIN, 1, 0, make_ts(day))

    # N=5 should only use last 5 matches
    form = form_calculator.compute_form("TeamA", make_ts(20), n=5)
    assert form is not None
    assert form.points_per_match == 3.0


def test_goal_diff_computation(form_calculator: FormCalculator):
    form_calculator.add_match("TeamA", "TeamB", MatchResult.HOME_WIN, 3, 1, make_ts(1))
    form_calculator.add_match("TeamA", "TeamC", MatchResult.DRAW, 0, 0, make_ts(2))

    form = form_calculator.compute_form("TeamA", make_ts(10), n=5)
    assert form is not None
    assert form.goal_diff_per_match == 1.0  # (2 + 0) / 2


def test_away_team_form(form_calculator: FormCalculator):
    # TeamB is away and loses
    form_calculator.add_match("TeamA", "TeamB", MatchResult.HOME_WIN, 2, 0, make_ts(1))

    form = form_calculator.compute_form("TeamB", make_ts(10), n=5)
    assert form is not None
    assert form.loss_rate == 1.0
    assert form.points_per_match == 0.0
    assert form.goal_diff_per_match == -2.0
