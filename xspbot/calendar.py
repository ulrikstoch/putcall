"""Tid og handelskalender. Alle tider er America/New_York."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Halve handelsdage (lukning 13:00 ET). Kilde: NYSE Group "2026, 2027 and
# 2028 Holiday and Early Closings Calendar". Opdatér listen hvert år!
HALF_DAYS: frozenset[date] = frozenset(
    {
        date(2026, 11, 27),  # dagen efter Thanksgiving
        date(2026, 12, 24),  # juleaften
        date(2027, 11, 26),  # dagen efter Thanksgiving
        date(2028, 7, 3),  # dagen før Independence Day
        date(2028, 11, 24),  # dagen efter Thanksgiving
    }
)


def now_et() -> datetime:
    return datetime.now(ET)


def at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=ET)


def is_half_day(d: date) -> bool:
    return d in HALF_DAYS


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def half_day_list_is_stale(today: date) -> bool:
    """Sand hvis listen ikke dækker indeværende år (skal opdateres)."""
    return not any(h.year == today.year for h in HALF_DAYS)
