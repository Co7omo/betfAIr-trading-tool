"""Helper builders for MarketCatalogue / MarketSnapshotBundle in tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from betfair_trading.models.market import (
    MarketCatalogue,
    MarketSnapshotBundle,
    Runner,
    RunnerSnapshot,
)


def make_market(
    market_id: str = "1.100",
    event_id: str = "EVT-1",
    home: str = "Manchester United",
    away: str = "Chelsea",
    competition: str = "EPL",
    start_time: datetime | None = None,
    runner_ids: tuple[int, int, int] = (101, 102, 103),
) -> MarketCatalogue:
    """1X2 MarketCatalogue with 3 runners: home, draw, away."""
    if start_time is None:
        start_time = datetime.now(UTC) + timedelta(minutes=60)

    return MarketCatalogue(
        market_id=market_id,
        event_id=event_id,
        event_name=f"{home} v {away}",
        competition_id="C1",
        competition_name=competition,
        country_code="GB",
        start_time=start_time,
        runners=[
            Runner(runner_id=runner_ids[0], runner_name=home, sort_priority=1),
            Runner(runner_id=runner_ids[1], runner_name="The Draw", sort_priority=2),
            Runner(runner_id=runner_ids[2], runner_name=away, sort_priority=3),
        ],
    )


def make_book(
    market_id: str = "1.100",
    event_id: str = "EVT-1",
    runner_quotes: list[tuple[int, float, float, float, float]] | None = None,
    status: str = "OPEN",
    inplay: bool = False,
    total_matched: float = 1000.0,
    snapshot_ts: datetime | None = None,
    minutes_to_start: float = 60.0,
) -> MarketSnapshotBundle:
    """MarketSnapshotBundle. runner_quotes = [(runner_id, back, lay, size_back, size_lay), ...]."""
    if runner_quotes is None:
        runner_quotes = [
            (101, 2.0, 2.04, 500.0, 500.0),
            (102, 3.5, 3.6, 200.0, 200.0),
            (103, 4.0, 4.1, 300.0, 300.0),
        ]
    if snapshot_ts is None:
        snapshot_ts = datetime.now(UTC)

    runners = []
    for rid, back, lay, sb, sl in runner_quotes:
        runners.append(
            RunnerSnapshot(
                runner_id=rid,
                best_back_price=Decimal(str(back)),
                best_back_size=Decimal(str(sb)),
                best_lay_price=Decimal(str(lay)),
                best_lay_size=Decimal(str(sl)),
                spread=Decimal(str(round(lay - back, 4))),
                traded_volume=Decimal("100.0"),
            )
        )

    return MarketSnapshotBundle(
        market_id=market_id,
        event_id=event_id,
        snapshot_ts=snapshot_ts,
        runners=runners,
        market_status=status,
        inplay=inplay,
        total_matched=Decimal(str(total_matched)),
        minutes_to_start=minutes_to_start,
    )
