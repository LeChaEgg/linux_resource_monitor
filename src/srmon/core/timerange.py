"""Date-range math and month helpers used by selection and the monthly report."""

from datetime import date, timedelta
from typing import Optional, Set, Tuple


def date_range_set(start_date: date, end_date: date) -> Set[date]:
    days: Set[date] = set()
    current = start_date
    while current <= end_date:
        days.add(current)
        current += timedelta(days=1)
    return days


def ranges_overlap(left_start: date, left_end: date, right_start: date, right_end: date) -> bool:
    return left_start <= right_end and right_start <= left_end


def select_recent_dates(dates: Set[date], days: Optional[int]) -> Set[date]:
    if days is None or days == 0:
        return dates
    return set(sorted(dates)[-days:])


def month_str_of(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def month_bounds(month: str) -> Tuple[date, date]:
    """Map a 'YYYY-MM' string to (first_day, last_day) of that month.

    Raises ValueError for malformed input or an out-of-range month.
    """
    parts = month.split("-") if isinstance(month, str) else []
    if len(parts) != 2:
        raise ValueError(f"--month must be in YYYY-MM format, got: {month!r}")
    try:
        year = int(parts[0])
        month_num = int(parts[1])
        first = date(year, month_num, 1)
    except ValueError:
        raise ValueError(f"--month must be in YYYY-MM format, got: {month!r}")

    if month_num == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month_num + 1, 1)
    return first, next_first - timedelta(days=1)


def current_month(today: date) -> str:
    return month_str_of(today)


def previous_month(today: date) -> str:
    first_of_this_month = today.replace(day=1)
    return month_str_of(first_of_this_month - timedelta(days=1))
