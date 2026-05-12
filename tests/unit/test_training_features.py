"""Unit tests for the shared feature schema (training/features.py)."""

import math

import numpy as np

from betfair_trading.training.features import (
    FEATURE_NAMES,
    build_feature_dict,
    feature_dict_to_array,
)


def test_feature_names_count_and_order():
    assert FEATURE_NAMES[0] == "elo_home"
    assert FEATURE_NAMES[1] == "elo_away"
    assert FEATURE_NAMES[2] == "elo_delta"
    assert len(FEATURE_NAMES) == 15


def test_build_feature_dict_complete_values():
    values = {
        "elo_home": 1510.0, "elo_away": 1490.0, "elo_delta": 20.0,
        "form_home_5_ppm": 2.0, "form_away_5_ppm": 1.5,
        "form_home_5_gd": 1.5, "form_away_5_gd": -0.5,
        "form_home_5_wr": 0.6, "form_away_5_wr": 0.4,
        "form_home_10_ppm": 1.8, "form_away_10_ppm": 1.2,
        "form_home_10_gd": 1.0, "form_away_10_gd": -0.2,
        "form_home_10_wr": 0.5, "form_away_10_wr": 0.3,
    }
    d = build_feature_dict(values)
    assert set(d.keys()) == set(FEATURE_NAMES)
    assert d["elo_home"] == 1510.0
    assert d["form_home_5_ppm"] == 2.0


def test_build_feature_dict_none_replaced_with_zero():
    values = {
        "elo_home": 1500.0, "elo_away": 1500.0, "elo_delta": 0.0,
        "form_home_5_ppm": None, "form_away_5_ppm": None,
        # All other form features unset → defaults
    }
    d = build_feature_dict(values)
    assert d["form_home_5_ppm"] == 0.0
    assert d["form_away_5_gd"] == 0.0  # missing key → 0.0


def test_build_feature_dict_elo_delta_auto_computed():
    values = {"elo_home": 1510.0, "elo_away": 1490.0}  # delta absent
    d = build_feature_dict(values)
    assert math.isclose(d["elo_delta"], 20.0)


def test_feature_dict_to_array_shape_and_order():
    d = {name: float(i) for i, name in enumerate(FEATURE_NAMES)}
    arr = feature_dict_to_array(d)
    assert arr.shape == (1, 15)
    assert arr[0, 0] == 0.0  # elo_home
    assert arr[0, 1] == 1.0  # elo_away
    assert arr[0, 14] == 14.0  # form_away_10_wr


def test_zero_skew_train_vs_inference_extraction():
    """Same underlying state → same feature dict, regardless of input shape."""
    train_values = {
        "elo_home": 1510.5, "elo_away": 1490.5,
        "form_home_5_ppm": 2.0, "form_home_5_gd": 1.5, "form_home_5_wr": 0.6,
        "form_away_5_ppm": 1.0, "form_away_5_gd": -1.0, "form_away_5_wr": 0.3,
    }
    inf_values = {
        "elo_home": 1510.5, "elo_away": 1490.5,
        "elo_delta": 20.0,
        "form_home_5_ppm": 2.0, "form_home_5_gd": 1.5, "form_home_5_wr": 0.6,
        "form_away_5_ppm": 1.0, "form_away_5_gd": -1.0, "form_away_5_wr": 0.3,
    }
    d_train = build_feature_dict(train_values)
    d_inf = build_feature_dict(inf_values)
    assert d_train == d_inf
