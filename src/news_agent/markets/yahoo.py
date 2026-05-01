import yfinance as yf
from pandas import Series

from news_agent.markets.indicators import calculate_indicators
from news_agent.markets.provider import QuoteSnapshot


class YahooMarketDataProvider:
    def get_snapshot(self, symbol: str) -> QuoteSnapshot:
        ticker = yf.Ticker(symbol)
        daily_history = ticker.history(period="6mo", interval="1d")
        intraday_history = ticker.history(period="5d", interval="5m")
        fast_info = getattr(ticker, "fast_info", {})
        info = _ticker_info(ticker)

        close = daily_history["Close"] if not daily_history.empty else Series(dtype="float")
        intraday_close = (
            intraday_history["Close"] if not intraday_history.empty else Series(dtype="float")
        )
        price, price_source = _latest_price(fast_info, info, intraday_close, close)
        if price is None:
            return QuoteSnapshot(
                symbol=symbol.upper(),
                price=None,
                percent_change=None,
                indicators={},
            )

        percent_change = _percent_change(fast_info, info)
        if percent_change is None:
            previous = _previous_price(fast_info, info, intraday_close, close, price, price_source)
            percent_change = round(((price - previous) / previous) * 100, 2) if previous else None
        indicators = calculate_indicators(close).as_dict() if not close.empty else {}
        return QuoteSnapshot(
            symbol=symbol.upper(),
            price=round(float(price), 2),
            percent_change=percent_change,
            indicators=indicators,
        )


def _latest_price(
    fast_info, info: dict, intraday_close: Series, close: Series
) -> tuple[float | None, str | None]:
    price = _fast_value(fast_info, "last_price", "lastPrice", "regular_market_price")
    if price is not None:
        return float(price), "live"
    price = _info_value(info, "regularMarketPrice", "currentPrice")
    if price is not None:
        return float(price), "live"
    if not intraday_close.empty:
        return float(intraday_close.iloc[-1]), "intraday"
    if not close.empty:
        return float(close.iloc[-1]), "daily"
    return None, None


def _previous_price(
    fast_info,
    info: dict,
    intraday_close: Series,
    close: Series,
    fallback_price: float,
    price_source: str | None,
) -> float | None:
    previous = _fast_value(
        fast_info,
        "previous_close",
        "previousClose",
        "regular_market_previous_close",
    )
    if previous is not None:
        return float(previous)
    previous = _info_value(info, "regularMarketPreviousClose", "previousClose")
    if previous is not None:
        return float(previous)
    if price_source in {"live", "intraday"} and len(close) > 1:
        return float(close.iloc[-2])
    if len(close) > 1:
        return float(close.iloc[-2])
    if len(intraday_close) > 1:
        return float(intraday_close.iloc[-2])
    return fallback_price


def _fast_value(fast_info, *keys: str) -> float | None:
    for key in keys:
        try:
            value = fast_info.get(key)
        except AttributeError:
            value = getattr(fast_info, key, None)
        if value is not None:
            return float(value)
    return None


def _info_value(info: dict, *keys: str) -> float | None:
    for key in keys:
        value = info.get(key)
        if value is not None:
            return float(value)
    return None


def _percent_change(fast_info, info: dict) -> float | None:
    value = _fast_value(
        fast_info,
        "regular_market_change_percent",
        "regularMarketChangePercent",
        "day_change_percent",
    )
    if value is None:
        value = _info_value(info, "regularMarketChangePercent")
    if value is None:
        return None
    return round(float(value), 2)


def _ticker_info(ticker) -> dict:
    try:
        return dict(getattr(ticker, "info", {}) or {})
    except Exception:
        return {}
