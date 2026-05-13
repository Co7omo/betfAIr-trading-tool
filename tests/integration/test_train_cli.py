"""Integration test for the train CLI."""

import csv
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from betfair_trading.training.train import main as train_main


def _make_results_csv(p: Path, n_matches: int) -> Path:
    teams = [f"Team{i}" for i in range(10)]
    random.seed(42)
    base_date = datetime(2025, 1, 1, tzinfo=UTC)
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG"])
        for i in range(n_matches):
            dt = base_date + timedelta(days=i)
            home = random.choice(teams)
            away = random.choice([t for t in teams if t != home])
            ftr = random.choice(["H", "D", "A"])
            fthg = random.randint(0, 4)
            ftag = random.randint(0, 4)
            w.writerow([dt.strftime("%d/%m/%Y"), home, away, ftr, fthg, ftag])
    return p


@pytest.fixture
def synthetic_csv(tmp_path: Path) -> Path:
    return _make_results_csv(tmp_path / "results.csv", n_matches=120)


async def test_train_end_to_end(
    pg_pool: asyncpg.Pool, synthetic_csv: Path, tmp_path: Path, monkeypatch
):
    output_dir = tmp_path / "models"
    output_dir.mkdir()

    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])

    await train_main(
        csv_path=synthetic_csv,
        model_name="test_v1",
        output_dir=output_dir,
        test_size=0.2,
    )

    # Joblib artifact created
    joblib_files = list(output_dir.glob("*.joblib"))
    assert len(joblib_files) == 1
    artifact_path = joblib_files[0]

    # model_versions row inserted
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM model_versions WHERE model_name = 'test_v1'")
    assert row is not None
    assert row["feature_set_version"] == "A2_EXT_ONLY"
    assert row["file_path"].endswith(artifact_path.name)
    # CSV hash matches
    import hashlib

    expected_hash = hashlib.sha256(synthetic_csv.read_bytes()).hexdigest()
    assert row["training_data_hash"] == expected_hash


async def test_train_temporal_split_respected(
    pg_pool: asyncpg.Pool, synthetic_csv: Path, tmp_path: Path, monkeypatch
):
    output_dir = tmp_path / "models"
    output_dir.mkdir()
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])

    # 120 matches, test_size=0.2 → 96 train / 24 test
    await train_main(
        csv_path=synthetic_csv,
        model_name="temporal_split_v1",
        output_dir=output_dir,
        test_size=0.2,
    )

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT n_train, n_test FROM model_versions WHERE model_name = 'temporal_split_v1'"
        )
    assert row["n_train"] == 96
    assert row["n_test"] == 24
