"""CLI: train and persist a baseline LogisticRegression model.

Usage:
    uv run python -m betfair_trading.training.train \\
        --csv-path data/results.csv \\
        --model-name logistic_v1 \\
        --output-dir models/ \\
        --test-size 0.2
"""

import argparse
import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import joblib
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from betfair_trading.db.writer import insert_model_version
from betfair_trading.models.inference import ModelVersion
from betfair_trading.training.dataset import DatasetBuilder
from betfair_trading.training.features import FEATURE_NAMES


async def main(
    csv_path: Path,
    model_name: str,
    output_dir: Path,
    test_size: float = 0.2,
) -> None:
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build dataset
    builder = DatasetBuilder()
    X, y, dates = builder.build(csv_path)
    n = len(X)
    if n == 0:
        raise SystemExit("Dataset is empty — no valid matches in CSV")

    # 2. Temporal split (NOT random)
    split_idx = int(n * (1 - test_size))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    # 3. Pipeline + Platt calibration
    base = LogisticRegression(solver="lbfgs", max_iter=1000, C=1.0)
    pipe = Pipeline([("scaler", StandardScaler()), ("clf", base)])
    model = CalibratedClassifierCV(pipe, method="sigmoid", cv=5)

    # 4. Fit
    model.fit(X_train, y_train)

    # 5. Eval
    proba_test = model.predict_proba(X_test)
    pred_test = model.predict(X_test)
    metrics = {
        "log_loss": float(log_loss(y_test, proba_test)),
        "accuracy": float(accuracy_score(y_test, pred_test)),
        "brier_home": float(
            brier_score_loss((y_test == 0).astype(int), proba_test[:, 0])
        ),
        "brier_draw": float(
            brier_score_loss((y_test == 1).astype(int), proba_test[:, 1])
        ),
        "brier_away": float(
            brier_score_loss((y_test == 2).astype(int), proba_test[:, 2])
        ),
        "confusion_matrix": confusion_matrix(y_test, pred_test).tolist(),
    }

    # 6. Save joblib artifact
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    artifact_filename = f"{model_name}_{timestamp}.joblib"
    artifact_path = output_dir / artifact_filename
    joblib.dump(model, artifact_path)

    # 7. SHA256 of input CSV
    training_data_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    # 8. INSERT model_versions
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL not set; cannot persist model_versions")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await insert_model_version(
                conn,
                ModelVersion(
                    model_name=model_name,
                    feature_set_version="A2_EXT_ONLY",
                    file_path=str(artifact_path),
                    training_data_hash=training_data_hash,
                    training_csv_path=str(csv_path),
                    training_params={
                        "solver": "lbfgs",
                        "C": 1.0,
                        "max_iter": 1000,
                        "calibration": "sigmoid",
                        "cv": 5,
                    },
                    metrics=metrics,
                    feature_names=FEATURE_NAMES,
                    n_train=int(len(X_train)),
                    n_test=int(len(X_test)),
                ),
            )
    finally:
        await pool.close()

    print(f"Trained '{model_name}': n_train={len(X_train)}, n_test={len(X_test)}")
    print(f"  log_loss={metrics['log_loss']:.4f} accuracy={metrics['accuracy']:.4f}")
    print(f"  artifact={artifact_path}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv-path", type=Path, required=True)
    p.add_argument("--model-name", type=str, default="logistic_v1")
    p.add_argument("--output-dir", type=Path, default=Path("models"))
    p.add_argument("--test-size", type=float, default=0.2)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        main(
            csv_path=args.csv_path,
            model_name=args.model_name,
            output_dir=args.output_dir,
            test_size=args.test_size,
        )
    )
