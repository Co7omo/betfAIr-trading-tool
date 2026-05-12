"""Shared feature schema for training and inference. Single source of truth.

Any change here requires retraining: the model's input dimension and ordering
is fixed by FEATURE_NAMES.
"""

import numpy as np

FEATURE_NAMES: list[str] = [
    "elo_home",
    "elo_away",
    "elo_delta",
    "form_home_5_ppm",
    "form_away_5_ppm",
    "form_home_5_gd",
    "form_away_5_gd",
    "form_home_5_wr",
    "form_away_5_wr",
    "form_home_10_ppm",
    "form_away_10_ppm",
    "form_home_10_gd",
    "form_away_10_gd",
    "form_home_10_wr",
    "form_away_10_wr",
]


def build_feature_dict(values: dict[str, float | None]) -> dict[str, float]:
    """Normalize a feature values dict to FEATURE_NAMES order, replacing None with 0.0.

    If elo_delta is absent but elo_home and elo_away are present, it is auto-computed.
    """
    if (
        "elo_delta" not in values
        and values.get("elo_home") is not None
        and values.get("elo_away") is not None
    ):
        values = {**values, "elo_delta": float(values["elo_home"]) - float(values["elo_away"])}
    out: dict[str, float] = {}
    for name in FEATURE_NAMES:
        v = values.get(name)
        out[name] = float(v) if v is not None else 0.0
    return out


def feature_dict_to_array(d: dict[str, float]) -> np.ndarray:
    """Shape (1, len(FEATURE_NAMES)) in FEATURE_NAMES order, ready for predict_proba."""
    return np.array([[d[name] for name in FEATURE_NAMES]])
