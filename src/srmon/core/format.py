"""The single set of value formatters used across summaries, peaks, and reports."""

from typing import Optional


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def format_gib_from_bytes(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value / (1024 ** 3):.2f} GiB"


def format_mib_per_sec_from_bytes(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value / (1024 ** 2):.2f} MiB/s"
