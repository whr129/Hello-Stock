from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class IndicatorSnapshot:
    sma_20: float | None
    sma_50: float | None
    rsi: float | None
    macd: float | None
    trend: str

    def as_dict(self) -> dict[str, float | str | None]:
        return {
            "sma_20": self.sma_20,
            "sma_50": self.sma_50,
            "rsi": self.rsi,
            "macd": self.macd,
            "trend": self.trend,
        }


def _last(series: pd.Series) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return round(float(clean.iloc[-1]), 2)


def calculate_indicators(close: pd.Series) -> IndicatorSnapshot:
    if close.empty:
        return IndicatorSnapshot(None, None, None, None, "unknown")

    sma_20 = close.rolling(20).mean()
    sma_50 = close.rolling(50).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))

    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    macd = ema_12 - ema_26

    latest_close = float(close.iloc[-1])
    latest_sma_20 = _last(sma_20)
    latest_sma_50 = _last(sma_50)
    if latest_sma_20 is None or latest_sma_50 is None:
        trend = "insufficient data"
    elif latest_close > latest_sma_20 > latest_sma_50:
        trend = "uptrend"
    elif latest_close < latest_sma_20 < latest_sma_50:
        trend = "downtrend"
    else:
        trend = "mixed"

    return IndicatorSnapshot(
        sma_20=latest_sma_20,
        sma_50=latest_sma_50,
        rsi=_last(rsi),
        macd=_last(macd),
        trend=trend,
    )
