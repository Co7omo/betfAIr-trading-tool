from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class CorrelationContext(BaseModel):
    market_id: str
    event_id: str
    decision_id: UUID | None = None

    def log_dict(self) -> dict:
        d = {"market_id": self.market_id, "event_id": self.event_id}
        if self.decision_id:
            d["decision_id"] = str(self.decision_id)
        return d


class TimestampedModel(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)


class IdentifiedModel(TimestampedModel):
    id: UUID = Field(default_factory=uuid4)
