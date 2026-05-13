"""Dataset builder: replay temporale del CSV per generare (X, y, dates).

Riusa EloEngine + FormCalculator per garantire le stesse semantiche as-of
del runtime live (anti-leakage).
"""

import csv
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from betfair_trading.elo.engine import EloEngine, MatchResult
from betfair_trading.elo.form import FormCalculator
from betfair_trading.training.features import (
    FEATURE_NAMES,
    build_feature_dict,
)

_RESULT_TO_INT: dict[str, int] = {"H": 0, "D": 1, "A": 2}
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y")


def _parse_date(s: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


class DatasetBuilder:
    def __init__(self, k_factor: float = 20.0, initial_rating: float = 1500.0):
        self.elo = EloEngine(k_factor=k_factor, initial_rating=initial_rating)
        self.form = FormCalculator()

    def build(self, csv_path: Path) -> tuple[np.ndarray, np.ndarray, list[datetime]]:
        """Iterate the CSV chronologically. For each match:
        1. Read features as-of pre-kickoff.
        2. Append to dataset.
        3. Apply match result to engines (anti-leakage: AFTER reading).

        Returns (X, y, dates). X: (n_samples, len(FEATURE_NAMES)). y: int labels [0=H, 1=D, 2=A].
        """
        matches: list[tuple[datetime, str, str, str, int, int]] = []
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                dt = _parse_date(row.get("Date", "") or "")
                if dt is None:
                    continue
                home = (row.get("HomeTeam") or "").strip()
                away = (row.get("AwayTeam") or "").strip()
                ftr = (row.get("FTR") or "").strip()
                try:
                    fthg = int(row.get("FTHG", 0) or 0)
                    ftag = int(row.get("FTAG", 0) or 0)
                except ValueError:
                    continue
                if not (home and away and ftr in _RESULT_TO_INT):
                    continue
                matches.append((dt, home, away, ftr, fthg, ftag))

        matches.sort(key=lambda m: m[0])

        X_rows: list[list[float]] = []
        y_rows: list[int] = []
        dates: list[datetime] = []

        for dt, home, away, ftr, fthg, ftag in matches:
            elo_h, elo_a = self.elo.get_ratings_asof(home, away, dt)
            fh5 = self.form.compute_form(home, dt, 5)
            fa5 = self.form.compute_form(away, dt, 5)
            fh10 = self.form.compute_form(home, dt, 10)
            fa10 = self.form.compute_form(away, dt, 10)

            values: dict[str, float | None] = {
                "elo_home": elo_h,
                "elo_away": elo_a,
                "form_home_5_ppm": fh5.points_per_match if fh5 else None,
                "form_away_5_ppm": fa5.points_per_match if fa5 else None,
                "form_home_5_gd": fh5.goal_diff_per_match if fh5 else None,
                "form_away_5_gd": fa5.goal_diff_per_match if fa5 else None,
                "form_home_5_wr": fh5.win_rate if fh5 else None,
                "form_away_5_wr": fa5.win_rate if fa5 else None,
                "form_home_10_ppm": fh10.points_per_match if fh10 else None,
                "form_away_10_ppm": fa10.points_per_match if fa10 else None,
                "form_home_10_gd": fh10.goal_diff_per_match if fh10 else None,
                "form_away_10_gd": fa10.goal_diff_per_match if fa10 else None,
                "form_home_10_wr": fh10.win_rate if fh10 else None,
                "form_away_10_wr": fa10.win_rate if fa10 else None,
            }
            d = build_feature_dict(values)
            X_rows.append([d[name] for name in FEATURE_NAMES])
            y_rows.append(_RESULT_TO_INT[ftr])
            dates.append(dt)

            # Apply result AFTER reading features (anti-leakage)
            result = MatchResult(ftr)
            self.elo.apply_result(home, away, result, dt)
            self.form.add_match(home, away, result, fthg, ftag, dt)

        return np.array(X_rows), np.array(y_rows), dates
