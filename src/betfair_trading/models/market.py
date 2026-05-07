from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class Runner(BaseModel):
    runner_id: int
    runner_name: str
    sort_priority: int | None = None


class MarketCatalogue(BaseModel):
    market_id: str
    event_id: str
    event_name: str
    competition_id: str | None = None
    competition_name: str | None = None
    country_code: str | None = None
    start_time: datetime
    runners: list[Runner]

    @classmethod
    def from_betfair(cls, raw) -> "MarketCatalogue":
        return cls(
            market_id=raw.market_id,
            event_id=raw.event.id if raw.event else "",
            event_name=raw.event.name if raw.event else "",
            competition_id=raw.competition.id if raw.competition else None,
            competition_name=raw.competition.name if raw.competition else None,
            country_code=raw.event.country_code if raw.event else None,
            start_time=raw.market_start_time,
            runners=[
                Runner(
                    runner_id=r.selection_id,
                    runner_name=r.runner_name,
                    sort_priority=r.sort_priority,
                )
                for r in raw.runners
            ],
        )


class RunnerSnapshot(BaseModel):
    runner_id: int
    best_back_price: Decimal | None = None
    best_back_size: Decimal | None = None
    best_lay_price: Decimal | None = None
    best_lay_size: Decimal | None = None
    spread: Decimal | None = None
    traded_volume: Decimal = Decimal("0")

    @classmethod
    def from_betfair(cls, runner) -> "RunnerSnapshot":
        best_back = runner.ex.available_to_back[0] if runner.ex.available_to_back else None
        best_lay = runner.ex.available_to_lay[0] if runner.ex.available_to_lay else None

        back_price = Decimal(str(best_back.price)) if best_back else None
        lay_price = Decimal(str(best_lay.price)) if best_lay else None
        spread = (lay_price - back_price) if (back_price and lay_price) else None

        return cls(
            runner_id=runner.selection_id,
            best_back_price=back_price,
            best_back_size=Decimal(str(best_back.size)) if best_back else None,
            best_lay_price=lay_price,
            best_lay_size=Decimal(str(best_lay.size)) if best_lay else None,
            spread=spread,
            traded_volume=Decimal(str(runner.ex.traded_volume or 0)),
        )


class MarketSnapshotBundle(BaseModel):
    """Inter-service contract: Market Data Collector -> Feature Builder.
    This model survives the Kafka migration — only the transport changes.
    """

    market_id: str
    event_id: str
    snapshot_ts: datetime
    runners: list[RunnerSnapshot]
    market_status: str
    inplay: bool
    total_matched: Decimal | None = None
    minutes_to_start: float
