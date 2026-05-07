"""Elo rating engine with strict as-of semantics.

CRITICAL: get_ratings_asof(ts) must never use data from matches completed after ts.
This is the single most important correctness constraint in the external data pipeline.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MatchResult(StrEnum):
    HOME_WIN = "H"
    DRAW = "D"
    AWAY_WIN = "A"


@dataclass
class EloUpdate:
    home_team: str
    away_team: str
    result: MatchResult
    completed_ts: datetime
    home_elo_before: float
    away_elo_before: float
    home_elo_after: float
    away_elo_after: float


class EloEngine:
    def __init__(self, k_factor: float = 20.0, initial_rating: float = 1500.0):
        self.k_factor = k_factor
        self.initial_rating = initial_rating
        self._ratings: dict[str, float] = {}
        self._history: list[EloUpdate] = []

    @property
    def history_size(self) -> int:
        return len(self._history)

    def get_rating(self, team: str) -> float:
        return self._ratings.get(team, self.initial_rating)

    def get_ratings_asof(
        self, home_team: str, away_team: str, asof_ts: datetime
    ) -> tuple[float, float]:
        """Return Elo ratings using only matches completed strictly before asof_ts.

        Walks the history in reverse to find the latest rating for each team
        that was computed from a match completed before the as-of timestamp.
        """
        home_rating = self.initial_rating
        away_rating = self.initial_rating

        home_found = False
        away_found = False

        # Walk history in reverse to find most recent ratings before asof_ts
        for update in reversed(self._history):
            if update.completed_ts >= asof_ts:
                continue

            if not home_found:
                if update.home_team == home_team:
                    home_rating = update.home_elo_after
                    home_found = True
                elif update.away_team == home_team:
                    home_rating = update.away_elo_after
                    home_found = True

            if not away_found:
                if update.home_team == away_team:
                    away_rating = update.home_elo_after
                    away_found = True
                elif update.away_team == away_team:
                    away_rating = update.away_elo_after
                    away_found = True

            if home_found and away_found:
                break

        return home_rating, away_rating

    def apply_result(
        self,
        home_team: str,
        away_team: str,
        result: MatchResult,
        completed_ts: datetime,
    ) -> EloUpdate:
        """Update ratings after a match result is known.

        Results must be applied in chronological order by completed_ts.
        """
        home_elo = self.get_rating(home_team)
        away_elo = self.get_rating(away_team)

        # Expected scores
        exp_home = 1.0 / (1.0 + 10 ** ((away_elo - home_elo) / 400.0))
        exp_away = 1.0 - exp_home

        # Actual scores
        if result == MatchResult.HOME_WIN:
            actual_home, actual_away = 1.0, 0.0
        elif result == MatchResult.AWAY_WIN:
            actual_home, actual_away = 0.0, 1.0
        else:  # Draw
            actual_home, actual_away = 0.5, 0.5

        # Update ratings
        new_home = home_elo + self.k_factor * (actual_home - exp_home)
        new_away = away_elo + self.k_factor * (actual_away - exp_away)

        self._ratings[home_team] = new_home
        self._ratings[away_team] = new_away

        update = EloUpdate(
            home_team=home_team,
            away_team=away_team,
            result=result,
            completed_ts=completed_ts,
            home_elo_before=home_elo,
            away_elo_before=away_elo,
            home_elo_after=new_home,
            away_elo_after=new_away,
        )
        self._history.append(update)
        return update
