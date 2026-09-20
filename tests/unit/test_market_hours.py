from datetime import datetime
from zoneinfo import ZoneInfo

from news_agent.markets.hours import is_us_market_open, market_early_closes, market_holidays

ET = ZoneInfo("America/New_York")


def test_market_open_during_regular_session() -> None:
    assert is_us_market_open(datetime(2026, 5, 29, 10, 0, tzinfo=ET)) is True


def test_market_closed_on_weekend() -> None:
    assert is_us_market_open(datetime(2026, 5, 30, 10, 0, tzinfo=ET)) is False


def test_market_closed_on_holiday() -> None:
    assert is_us_market_open(datetime(2026, 1, 1, 10, 0, tzinfo=ET)) is False
    assert datetime(2026, 1, 1).date() in market_holidays(2026)


def test_market_respects_early_close() -> None:
    early_close_day = datetime(2026, 11, 27).date()
    assert early_close_day in market_early_closes(2026)
    assert is_us_market_open(datetime(2026, 11, 27, 12, 59, tzinfo=ET)) is True
    assert is_us_market_open(datetime(2026, 11, 27, 13, 0, tzinfo=ET)) is False


def test_market_time_zone_conversion() -> None:
    assert is_us_market_open(datetime(2026, 5, 29, 14, 0, tzinfo=ZoneInfo("UTC"))) is True
