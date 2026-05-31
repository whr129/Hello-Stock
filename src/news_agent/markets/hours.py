from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def is_us_market_open(moment: datetime | None = None) -> bool:
    local = _as_market_time(moment or datetime.now(UTC))
    if local.date() in market_holidays(local.year) or local.weekday() >= 5:
        return False
    close_at = EARLY_CLOSE if local.date() in market_early_closes(local.year) else REGULAR_CLOSE
    return REGULAR_OPEN <= local.time() < close_at


def market_holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    return {holiday for holiday in holidays if holiday.year == year}


def market_early_closes(year: int) -> set[date]:
    candidates = {
        _early_close_before_independence_day(year),
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),
        date(year, 12, 24),
    }
    holidays = market_holidays(year)
    return {
        candidate
        for candidate in candidates
        if candidate.year == year and candidate.weekday() < 5 and candidate not in holidays
    }


def _as_market_time(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(MARKET_TIMEZONE)


def _observed(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + ((nth - 1) * 7))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    correction = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * correction) // 451
    month = (h + correction - 7 * m + 114) // 31
    day = ((h + correction - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _early_close_before_independence_day(year: int) -> date:
    independence_day = date(year, 7, 4)
    if independence_day.weekday() == 5:
        return date(year, 7, 2)
    if independence_day.weekday() == 6:
        return date(year, 7, 3)
    return date(year, 7, 3)
