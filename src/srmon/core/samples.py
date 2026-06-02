"""Reading and iterating resource-monitor JSONL samples."""

import json
import sys
from datetime import date, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, Optional, Set, Tuple


def parse_iso_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone information, for example 2026-04-20T01:45:24Z")
    return parsed


def parse_sample_timestamp_date(sample: Dict[str, object]) -> Optional[date]:
    timestamp = sample.get("timestamp")
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_sample_date_from_line(line: str, hostname: Optional[str]) -> Optional[date]:
    try:
        sample = json.loads(line)
    except JSONDecodeError:
        return None
    if not isinstance(sample, dict):
        return None
    if hostname is not None and sample.get("hostname") != hostname:
        return None
    return parse_sample_timestamp_date(sample)


def collect_sample_dates(path: Path, hostname: Optional[str]) -> Set[date]:
    dates: Set[date] = set()
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            sample_date = parse_sample_date_from_line(line, hostname)
            if sample_date is not None:
                dates.add(sample_date)
    return dates


def warn_invalid_sample(path: Path, line_number: int, exc: JSONDecodeError) -> None:
    sys.stderr.write(
        f"Skipping invalid JSON sample in {path} line {line_number}: {exc.msg} at column {exc.colno}\n"
    )


def iter_samples(
    log_files: Iterable[Path],
    on_invalid: Optional[Callable[[Path, int, JSONDecodeError], None]] = None,
    warn: bool = True,
) -> Iterator[Tuple[Path, int, Dict[str, object]]]:
    """Yield (path, line_number, sample) for each valid JSON sample.

    Invalid lines are skipped; `on_invalid` (if given) is called for each, and a
    stderr warning is emitted unless `warn=False` — let a caller that walks the
    same files more than once warn on only one of the passes.
    """
    selected_dates_by_path = getattr(log_files, "selected_dates_by_path", {})
    hostname_filter = getattr(log_files, "hostname_filter", None)
    for path in log_files:
        selected_dates = selected_dates_by_path.get(path)
        with path.open(encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    sample = json.loads(line)
                except JSONDecodeError as exc:
                    if on_invalid is not None:
                        on_invalid(path, line_number, exc)
                    if warn:
                        warn_invalid_sample(path, line_number, exc)
                    continue
                if not isinstance(sample, dict):
                    continue
                if selected_dates is not None and parse_sample_timestamp_date(sample) not in selected_dates:
                    continue
                if hostname_filter is not None and sample.get("hostname") != hostname_filter:
                    continue
                yield path, line_number, sample
