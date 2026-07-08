"""Calendar-month period helpers for Finance (docs/modules/finance.md §4).

Dues are calendar-month periods identified by ``(year, month)``. These stdlib-only
helpers (no ``python-dateutil`` — avoided per the build plan's dependency review)
do the month arithmetic every finance feature shares: iterate month ranges, build
a due date on a given due day, and pack/unpack the ``YYYYMM`` ints prepaid blocks
store.
"""
from __future__ import annotations

import calendar
from datetime import date


def period_key(year: int, month: int) -> int:
    """Pack ``(year, month)`` into a sortable ``YYYYMM`` int (e.g. 202608)."""
    return year * 100 + month


def unpack_period(key: int) -> tuple[int, int]:
    """Inverse of :func:`period_key`."""
    return key // 100, key % 100


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    """Return the ``(year, month)`` ``delta`` months after the given period.

    ``delta`` may be negative. Month is 1–12.
    """
    index = (year * 12 + (month - 1)) + delta
    return index // 12, (index % 12) + 1


def month_range(
    start: tuple[int, int], end: tuple[int, int]
) -> list[tuple[int, int]]:
    """Inclusive list of ``(year, month)`` periods from ``start`` to ``end``.

    Empty when ``start`` is after ``end`` (never raises).
    """
    start_k = period_key(*start)
    end_k = period_key(*end)
    if start_k > end_k:
        return []
    result: list[tuple[int, int]] = []
    y, m = start
    while period_key(y, m) <= end_k:
        result.append((y, m))
        y, m = add_months(y, m, 1)
    return result


def due_date_for(year: int, month: int, due_day: int) -> date:
    """The due date for a period, clamped to the month's last day if needed.

    ``due_day`` is 1–28 per config, so clamping is a safety net only.
    """
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(due_day, last))


def period_of(d: date) -> tuple[int, int]:
    """The ``(year, month)`` a date falls in."""
    return d.year, d.month
