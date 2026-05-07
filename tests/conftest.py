import pytest


@pytest.fixture
def elo_engine():
    from betfair_trading.elo.engine import EloEngine

    return EloEngine(k_factor=20.0, initial_rating=1500.0)


@pytest.fixture
def form_calculator():
    from betfair_trading.elo.form import FormCalculator

    return FormCalculator()
