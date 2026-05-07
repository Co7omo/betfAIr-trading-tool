from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EloRating(BaseModel):
    team: str
    rating: float
    as_of: datetime


class FormFeatures(BaseModel):
    points_per_match: float | None = None
    goal_diff_per_match: float | None = None
    win_rate: float | None = None
    draw_rate: float | None = None
    loss_rate: float | None = None


class ExternalFeatureBundle(BaseModel):
    """Inter-service contract: External Data Ingestor -> Feature Builder."""

    ext_snapshot_id: UUID = Field(default_factory=uuid4)
    event_key: str
    market_id: str | None = None
    asof_ts: datetime
    home_team: str
    away_team: str
    elo_home: Decimal | None = None
    elo_away: Decimal | None = None
    elo_delta: Decimal | None = None
    form_home_5: FormFeatures | None = None
    form_away_5: FormFeatures | None = None
    form_home_10: FormFeatures | None = None
    form_away_10: FormFeatures | None = None
    match_confidence: str = "HIGH"
    quality_flags: dict = Field(default_factory=dict)
