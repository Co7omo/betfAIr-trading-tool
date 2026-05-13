"""Decision Pydantic contract for the Decision Engine."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DecisionOutcome(StrEnum):
    ALLOW = "ALLOW"
    BLOCK_SOFT = "BLOCK_SOFT"
    BLOCK_HARD = "BLOCK_HARD"


class GateResult(BaseModel):
    passed: bool
    reason: str


class Decision(BaseModel):
    decision_id: UUID = Field(default_factory=uuid4)
    market_id: str
    event_id: str
    snapshot_id: UUID | None = None
    decision_ts: datetime

    model_version: str
    p_model: dict[int, float]
    p_market: dict[int, float]
    edge_gross: dict[int, float]
    edge_net: dict[int, float]

    selected_runner_id: int | None = None
    selected_edge_net: Decimal | None = None

    gate_results: dict[str, GateResult]
    decision_outcome: DecisionOutcome
    rationale: str | None = None

    feature_vector_ids: list[UUID]
    config_snapshot_id: UUID | None = None
    inference_id: UUID | None = None  # NEW
