from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    price: float | None
    percent_change: float | None
    indicators: dict


class MarketDataProvider(Protocol):
    def get_snapshot(self, symbol: str) -> QuoteSnapshot:
        raise NotImplementedError
