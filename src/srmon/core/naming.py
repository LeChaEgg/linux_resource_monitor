"""Log file naming: the single home for the combined-log filename grammar.

The log-selection and download code both import the regex and parse/build helpers
from here, so the filename format is defined in exactly one place.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple


COMBINED_LOG_RE = re.compile(
    r"^(?P<hostname>.+)_(?P<start>\d{4}-\d{2}-\d{2})_to_(?P<end>\d{4}-\d{2}-\d{2})\.jsonl$"
)


@dataclass(frozen=True)
class CombinedLogName:
    hostname: str
    start_date: date
    end_date: date


def parse_combined_log_name(path: Path) -> Optional[CombinedLogName]:
    match = COMBINED_LOG_RE.match(path.name)
    if match is None:
        return None
    try:
        start = datetime.strptime(match.group("start"), "%Y-%m-%d").date()
        end = datetime.strptime(match.group("end"), "%Y-%m-%d").date()
    except ValueError:
        return None
    if end < start:
        return None
    return CombinedLogName(hostname=match.group("hostname"), start_date=start, end_date=end)


def build_combined_log_filename(hostname: str, start_date: date, end_date: date) -> str:
    return f"{hostname}_{start_date.isoformat()}_to_{end_date.isoformat()}.jsonl"


def parse_combined_log_file_date_range(path: Path) -> Optional[Tuple[date, date]]:
    parsed = parse_combined_log_name(path)
    if parsed is None:
        return None
    return parsed.start_date, parsed.end_date


def combined_log_hostname(path: Path) -> Optional[str]:
    parsed = parse_combined_log_name(path)
    if parsed is None:
        return None
    return parsed.hostname


def parse_log_file_date(path: Path) -> Optional[date]:
    """Parse a server-side daily filename, e.g. metrics-2026-04-20.jsonl."""
    suffix = path.stem.replace("metrics-", "", 1)
    try:
        return datetime.strptime(suffix, "%Y-%m-%d").date()
    except ValueError:
        return None
