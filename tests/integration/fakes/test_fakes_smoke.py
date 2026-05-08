"""Smoke test for the fake client and the builders."""

from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


async def test_fake_returns_added_catalogue():
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.add_market(make_market(market_id="1.B"))

    result = await fake.list_market_catalogue()

    assert len(result) == 2
    assert {m.market_id for m in result} == {"1.A", "1.B"}


async def test_fake_book_round_robin():
    fake = FakeAsyncBetfairClient()
    fake.queue_book("1.A", make_book(market_id="1.A", total_matched=10.0))
    fake.queue_book("1.A", make_book(market_id="1.A", total_matched=20.0))

    first = await fake.list_market_book(["1.A"])
    second = await fake.list_market_book(["1.A"])
    third = await fake.list_market_book(["1.A"])

    assert len(first) == 1 and float(first[0].total_matched) == 10.0
    assert len(second) == 1 and float(second[0].total_matched) == 20.0
    # round-robin: third returns to the first
    assert len(third) == 1 and float(third[0].total_matched) == 10.0


async def test_fake_book_skips_unqueued_market():
    fake = FakeAsyncBetfairClient()
    fake.queue_book("1.A", make_book(market_id="1.A"))

    result = await fake.list_market_book(["1.A", "1.B"])

    assert len(result) == 1
    assert result[0].market_id == "1.A"
