import yfinance as yf

from news_agent.markets.indicators import calculate_indicators
from news_agent.markets.provider import QuoteSnapshot


class YahooMarketDataProvider:
    def get_snapshot(self, symbol: str) -> QuoteSnapshot:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="6mo", interval="1d")
        if history.empty:
            return QuoteSnapshot(
                symbol=symbol.upper(),
                price=None,
                percent_change=None,
                indicators={},
            )

        close = history["Close"]
        price = round(float(close.iloc[-1]), 2)
        previous = float(close.iloc[-2]) if len(close) > 1 else price
        percent_change = round(((price - previous) / previous) * 100, 2) if previous else None
        indicators = calculate_indicators(close).as_dict()
        return QuoteSnapshot(
            symbol=symbol.upper(),
            price=price,
            percent_change=percent_change,
            indicators=indicators,
        )
