"""Pydantic contracts for ModelVersion and ModelInference."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ModelVersion(BaseModel):
    model_version_id: UUID = Field(default_factory=uuid4)
    model_name: str
    feature_set_version: str
    created_ts: datetime | None = None  # DB default NOW()
    file_path: str
    training_data_hash: str
    training_csv_path: str
    training_params: dict
    metrics: dict
    feature_names: list[str]
    n_train: int
    n_test: int


class ModelInference(BaseModel):
    inference_id: UUID = Field(default_factory=uuid4)
    model_version_id: UUID
    market_id: str
    event_id: str
    inference_ts: datetime | None = None  # DB default NOW()
    asof_ts: datetime
    p_home: Decimal
    p_draw: Decimal
    p_away: Decimal
    feature_vector_ids: list[UUID]
    features_used: dict[str, float]
