import pandas as pd

from news_agent.markets.yahoo import YahooMarketDataProvider


class FakeTicker:
    fast_info = {"last_price": 105.0, "previous_close": 100.0}
    info = {}

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, period: str, interval: str):
        if interval == "5m":
            return pd.DataFrame({"Close": [104.0, 105.0]})
        return pd.DataFrame({"Close": list(range(1, 80))})


class DailyOnlyTicker:
    fast_info = {}
    info = {}

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, period: str, interval: str):
        if interval == "5m":
            return pd.DataFrame({"Close": []})
        return pd.DataFrame({"Close": [10.0, 12.0]})


class IntradayWithDailyPreviousTicker:
    fast_info = {}
    info = {}

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, period: str, interval: str):
        if interval == "5m":
            return pd.DataFrame({"Close": [104.0, 105.0]})
        return pd.DataFrame({"Close": [95.0, 100.0, 105.0]})


class InfoChangePercentTicker:
    fast_info = {"last_price": 105.0}
    info = {"regularMarketChangePercent": 4.25, "regularMarketPreviousClose": 100.0}

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, period: str, interval: str):
        if interval == "5m":
            return pd.DataFrame({"Close": [104.0, 105.0]})
        return pd.DataFrame({"Close": [95.0, 100.0, 105.0]})


def test_yahoo_provider_prefers_fast_info_price(monkeypatch) -> None:
    monkeypatch.setattr("news_agent.markets.yahoo.yf.Ticker", FakeTicker)

    snapshot = YahooMarketDataProvider().get_snapshot("aapl")

    assert snapshot.symbol == "AAPL"
    assert snapshot.price == 105.0
    assert snapshot.percent_change == 5.0
    assert snapshot.indicators["trend"] == "uptrend"


def test_yahoo_provider_falls_back_to_daily_history(monkeypatch) -> None:
    monkeypatch.setattr("news_agent.markets.yahoo.yf.Ticker", DailyOnlyTicker)

    snapshot = YahooMarketDataProvider().get_snapshot("msft")

    assert snapshot.symbol == "MSFT"
    assert snapshot.price == 12.0
    assert snapshot.percent_change == 20.0


def test_yahoo_provider_intraday_price_compares_to_previous_close(monkeypatch) -> None:
    monkeypatch.setattr("news_agent.markets.yahoo.yf.Ticker", IntradayWithDailyPreviousTicker)

    snapshot = YahooMarketDataProvider().get_snapshot("nvda")

    assert snapshot.symbol == "NVDA"
    assert snapshot.price == 105.0
    assert snapshot.percent_change == 5.0


def test_yahoo_provider_prefers_quote_change_percent(monkeypatch) -> None:
    monkeypatch.setattr("news_agent.markets.yahoo.yf.Ticker", InfoChangePercentTicker)

    snapshot = YahooMarketDataProvider().get_snapshot("goog")

    assert snapshot.symbol == "GOOG"
    assert snapshot.price == 105.0
    assert snapshot.percent_change == 4.25
