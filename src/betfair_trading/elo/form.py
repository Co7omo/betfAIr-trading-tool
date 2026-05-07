"""Rolling form features with strict as-of semantics."""

from dataclasses import dataclass
from datetime import datetime

from betfair_trading.elo.engine import MatchResult
from betfair_trading.models.external import FormFeatures


@dataclass
class MatchRecord:
    team: str
    opponent: str
    is_home: bool
    result: MatchResult
    goals_for: int
    goals_against: int
    completed_ts: datetime

    @property
    def points(self) -> int:
        if self.result == MatchResult.HOME_WIN:
            return 3 if self.is_home else 0
        elif self.result == MatchResult.AWAY_WIN:
            return 0 if self.is_home else 3
        else:
            return 1


class FormCalculator:
    def __init__(self):
        self._matches: list[MatchRecord] = []

    def add_match(
        self,
        home_team: str,
        away_team: str,
        result: MatchResult,
        home_goals: int,
        away_goals: int,
        completed_ts: datetime,
    ) -> None:
        # Add record for home team
        self._matches.append(
            MatchRecord(
                team=home_team,
                opponent=away_team,
                is_home=True,
                result=result,
                goals_for=home_goals,
                goals_against=away_goals,
                completed_ts=completed_ts,
            )
        )
        # Add record for away team
        self._matches.append(
            MatchRecord(
                team=away_team,
                opponent=home_team,
                is_home=False,
                result=result,
                goals_for=away_goals,
                goals_against=home_goals,
                completed_ts=completed_ts,
            )
        )

    def compute_form(self, team: str, asof_ts: datetime, n: int) -> FormFeatures | None:
        """Compute form from last N completed matches strictly before asof_ts."""
        team_matches = [m for m in self._matches if m.team == team and m.completed_ts < asof_ts]
        # Sort by completion time descending, take last N
        team_matches.sort(key=lambda m: m.completed_ts, reverse=True)
        recent = team_matches[:n]

        if not recent:
            return None

        count = len(recent)
        total_points = sum(m.points for m in recent)
        total_gd = sum(m.goals_for - m.goals_against for m in recent)

        wins = sum(1 for m in recent if m.points == 3)
        draws = sum(1 for m in recent if m.points == 1)
        losses = sum(1 for m in recent if m.points == 0)

        return FormFeatures(
            points_per_match=total_points / count,
            goal_diff_per_match=total_gd / count,
            win_rate=wins / count,
            draw_rate=draws / count,
            loss_rate=losses / count,
        )
