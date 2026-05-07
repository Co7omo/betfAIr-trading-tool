import hashlib
import json
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field


class FeatureSetVersion(StrEnum):
    A0 = "A0"  # Market-only
    A1 = "A1"  # Market + Elo
    A2 = "A2"  # Market + Elo + Form


class FeatureVector(BaseModel):
    feature_vector_id: UUID = Field(default_factory=uuid4)
    market_id: str
    event_id: str
    runner_id: int
    decision_id: UUID | None = None
    feature_set_version: FeatureSetVersion
    snapshot_id: UUID | None = None
    ext_snapshot_id: UUID | None = None
    features: dict
    generated_at: datetime

    @computed_field
    @property
    def feature_hash(self) -> str:
        canonical = json.dumps(self.features, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()
