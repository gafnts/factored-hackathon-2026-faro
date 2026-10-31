"""
ADR-0003's business clock, shared by the profile and the pipeline (ADR-0006, Freshness). A daily table's last complete
day is its last partition less the 99th percentile of how many days late its settled rows arrived; the business date is
the earliest of those across the daily tables; and the as-of instant is the earliest of their processing cutoffs, on the
morning after the business date.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta

COMPLETE = 0.99
# Rows younger than this may still be arriving, so they stay out of the lags the rule reads.
SETTLED_DAYS = 31


def settled_before(last_partition: date) -> date:
    return last_partition - timedelta(days=SETTLED_DAYS)


def lag_p99(settled: Mapping[int, int]) -> int | None:
    """
    From the settled rows' count per lag in days (process date minus event date); a negative lag is an event dated
    after its process date, within the processing day's cutoff the next morning, and isn't late.
    """
    total = sum(n for lag, n in settled.items() if lag >= 0)
    if not total:
        return None
    running = 0
    for lag in sorted(k for k in settled if k >= 0):
        running += settled[lag]
        if running >= COMPLETE * total:
            return lag
    raise AssertionError("the cumulative share never reached COMPLETE")


def last_complete_day(last_partition: date, p99: int | None) -> date | None:
    return last_partition - timedelta(days=p99) if p99 is not None else None


def business_date(last_complete_days: Iterable[date | None]) -> date | None:
    days = [day for day in last_complete_days if day is not None]
    return min(days) if days else None


def as_of(business: date, cutoffs: Iterable[time | None]) -> datetime:
    """
    A table with no events past midnight closes its processing day at midnight.
    """
    return datetime.combine(
        business + timedelta(days=1),
        min(c if c is not None else time() for c in cutoffs),
    )
