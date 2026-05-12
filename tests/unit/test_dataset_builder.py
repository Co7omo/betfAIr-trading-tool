"""Unit tests for DatasetBuilder (CSV replay)."""

import csv
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from betfair_trading.training.dataset import DatasetBuilder
from betfair_trading.training.features import FEATURE_NAMES


def _write_csv(p: Path, rows: list[tuple]) -> None:
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG"])
        for r in rows:
            w.writerow(r)


def test_build_emits_n_rows_for_n_matches(tmp_path):
    csv_path = tmp_path / "results.csv"
    _write_csv(csv_path, [
        ("01/01/2025", "Liverpool", "Arsenal", "H", 2, 0),
        ("08/01/2025", "Arsenal", "Liverpool", "D", 1, 1),
        ("15/01/2025", "Chelsea", "Liverpool", "A", 0, 2),
        ("22/01/2025", "Arsenal", "Chelsea", "H", 3, 1),
    ])
    builder = DatasetBuilder()
    X, y, dates = builder.build(csv_path)
    assert X.shape == (4, len(FEATURE_NAMES))
    assert y.shape == (4,)
    assert len(dates) == 4


def test_build_labels_mapped_correctly(tmp_path):
    csv_path = tmp_path / "results.csv"
    _write_csv(csv_path, [
        ("01/01/2025", "A", "B", "H", 1, 0),
        ("08/01/2025", "A", "B", "D", 1, 1),
        ("15/01/2025", "A", "B", "A", 0, 1),
    ])
    builder = DatasetBuilder()
    _X, y, _dates = builder.build(csv_path)
    assert list(y) == [0, 1, 2]  # H, D, A


def test_build_anti_leakage(tmp_path):
    """The features of match i must not reflect the result of match i."""
    csv_path = tmp_path / "results.csv"
    # Two Liverpool-vs-Arsenal matches; second one's Elo must equal the
    # post-first-match ratings (not post-second-match).
    _write_csv(csv_path, [
        ("01/01/2025", "Liverpool", "Arsenal", "H", 2, 0),  # match 1
        ("08/01/2025", "Liverpool", "Arsenal", "H", 3, 0),  # match 2
    ])
    builder = DatasetBuilder()
    X, _y, _dates = builder.build(csv_path)

    # Match 1: both teams at initial 1500 → features 1500/1500/0
    assert X[0, 0] == 1500.0  # elo_home
    assert X[0, 1] == 1500.0  # elo_away
    assert X[0, 2] == 0.0     # elo_delta

    # Match 2: Liverpool won match 1 → Elo_home > 1500, Elo_away < 1500
    # (but NOT yet reflecting match 2's result)
    assert X[1, 0] > 1500.0
    assert X[1, 1] < 1500.0
    # Expected after match 1: home_elo = 1500 + 20*(1 - 0.5) = 1510
    assert abs(X[1, 0] - 1510.0) < 0.01
