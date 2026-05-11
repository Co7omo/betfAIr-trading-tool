"""Unit tests for Decision Pydantic contract."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from betfair_trading.models.decision import Decision, DecisionOutcome, GateResult


def test_decision_full_construction():
    fv_id = uuid4()
    snap_id = uuid4()
    cfg_id = uuid4()
    d = Decision(
        market_id="1.A",
        event_id="E-A",
        snapshot_id=snap_id,
        decision_ts=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        model_version="STUB_V1",
        p_model={101: 0.55, 102: 0.25, 103: 0.20},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: 0.05, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        selected_runner_id=101,
        selected_edge_net=Decimal("0.022500"),
        gate_results={
            "kill_switch": GateResult(passed=True, reason="ok"),
            "edge_threshold": GateResult(passed=True, reason="ok"),
        },
        decision_outcome=DecisionOutcome.ALLOW,
        rationale="all gates passed",
        feature_vector_ids=[fv_id],
        config_snapshot_id=cfg_id,
    )
    assert d.decision_outcome == DecisionOutcome.ALLOW
    assert d.selected_runner_id == 101
    assert d.p_model[101] == 0.55
    assert d.gate_results["kill_switch"].passed is True


def test_decision_outcome_enum_values():
    assert DecisionOutcome.ALLOW.value == "ALLOW"
    assert DecisionOutcome.BLOCK_SOFT.value == "BLOCK_SOFT"
    assert DecisionOutcome.BLOCK_HARD.value == "BLOCK_HARD"


def test_gate_result_minimal():
    g = GateResult(passed=False, reason="size_below_min")
    assert g.passed is False
    assert g.reason == "size_below_min"
