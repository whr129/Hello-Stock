import pandas as pd

from news_agent.markets.indicators import calculate_indicators


def test_calculate_indicators_returns_basic_values() -> None:
    close = pd.Series(range(1, 80), dtype="float")

    result = calculate_indicators(close)

    assert result.sma_20 is not None
    assert result.sma_50 is not None
    assert result.trend == "uptrend"
