#!/usr/bin/env python3

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from json import JSONDecodeError
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVER_LOG_DIR = Path("/var/log/system-resource-monitor")
DEFAULT_DOWNLOADED_LOG_DIR = REPO_ROOT / "local-debug-logs"
DEFAULT_LOG_DAYS = 30
COMBINED_LOG_RE = re.compile(
    r"^(?P<hostname>.+)_(?P<start>\d{4}-\d{2}-\d{2})_to_(?P<end>\d{4}-\d{2}-\d{2})\.jsonl$"
)


class LogFiles(list):
    def __init__(
        self,
        paths: Iterable[Path],
        selected_dates_by_path: Dict[Path, Optional[Set[date]]],
        hostname_filter: Optional[str] = None,
    ) -> None:
        super().__init__(paths)
        self.selected_dates_by_path = selected_dates_by_path
        self.hostname_filter = hostname_filter


def add_log_selection_args(parser: argparse.ArgumentParser, *, default_days: int = DEFAULT_LOG_DAYS) -> None:
    parser.add_argument(
        "--mode",
        choices=("auto", "server", "local"),
        default="auto",
        help=(
            "Log selection mode. auto uses server logs when present, otherwise local-debug-logs. "
            "server reads /var/log/system-resource-monitor. local reads local-debug-logs."
        ),
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Override the log directory. In local mode this should point at a downloaded-log directory.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=default_days,
        help=f"Number of recent recorded log days to include when no explicit date range is set. Default: {default_days}. Use 0 to include all.",
    )
    parser.add_argument(
        "--hostname",
        default=None,
        help="Local/auto mode: hostname to analyze from downloaded logs.",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Local mode: first log date to analyze, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Local mode: last log date to analyze, in YYYY-MM-DD format.",
    )


def resolve_log_dir(log_dir: Optional[str], mode: str) -> Path:
    if log_dir:
        return Path(log_dir).expanduser()
    if mode == "local":
        return DEFAULT_DOWNLOADED_LOG_DIR
    return DEFAULT_SERVER_LOG_DIR


def parse_date_arg(name: str, value: Optional[str]) -> date:
    if not value:
        raise SystemExit(f"{name} is required in local mode")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"{name} must use YYYY-MM-DD format")


def parse_optional_date_range(args: argparse.Namespace) -> Tuple[Optional[date], Optional[date]]:
    start_raw = getattr(args, "start_date", None)
    end_raw = getattr(args, "end_date", None)
    if bool(start_raw) != bool(end_raw):
        raise SystemExit("--start-date and --end-date must be provided together")
    if not start_raw and not end_raw:
        return None, None

    start_date = parse_date_arg("--start-date", start_raw)
    end_date = parse_date_arg("--end-date", end_raw)
    if end_date < start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    return start_date, end_date


def date_range_set(start_date: date, end_date: date) -> Set[date]:
    days: Set[date] = set()
    current = start_date
    while current <= end_date:
        days.add(current)
        current += timedelta(days=1)
    return days


def parse_log_file_date(path: Path) -> Optional[date]:
    suffix = path.stem.replace("metrics-", "", 1)
    try:
        return datetime.strptime(suffix, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_combined_log_file_date_range(path: Path) -> Optional[Tuple[date, date]]:
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
    return start, end


def combined_log_hostname(path: Path) -> Optional[str]:
    match = COMBINED_LOG_RE.match(path.name)
    if match is None:
        return None
    return match.group("hostname")


def ranges_overlap(left_start: date, left_end: date, right_start: date, right_end: date) -> bool:
    return left_start <= right_end and right_start <= left_end


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


def list_log_files(log_dir: Path, days: Optional[int]) -> List[Path]:
    dates_by_path: Dict[Path, Set[date]] = {}
    for path in log_dir.glob("metrics-*.jsonl"):
        file_date = parse_log_file_date(path)
        if file_date is None:
            continue
        dates_by_path[path] = {file_date}

    recorded_dates = sorted({sample_date for dates in dates_by_path.values() for sample_date in dates})
    if days is None or days == 0:
        selected_dates = set(recorded_dates)
    else:
        selected_dates = set(recorded_dates[-days:])

    selected_items: List[Tuple[date, str, Path, Optional[Set[date]]]] = []
    for path, file_dates in dates_by_path.items():
        selected_for_path = file_dates & selected_dates
        if not selected_for_path:
            continue
        date_filter = None if selected_for_path == file_dates else selected_for_path
        selected_items.append((min(selected_for_path), path.name, path, date_filter))

    selected_items.sort()
    selected_dates_by_path = {path: date_filter for _, _, path, date_filter in selected_items}
    return LogFiles([path for _, _, path, _ in selected_items], selected_dates_by_path)


def select_recent_dates(dates: Set[date], days: Optional[int]) -> Set[date]:
    if days is None or days == 0:
        return dates
    return set(sorted(dates)[-days:])


def list_local_log_files(
    log_dir: Path,
    hostname: Optional[str],
    start_date: Optional[date],
    end_date: Optional[date],
    days: Optional[int],
) -> List[Path]:
    candidates: List[Tuple[date, date, str, str, Path]] = []
    for path in log_dir.glob("*.jsonl"):
        date_range = parse_combined_log_file_date_range(path)
        if date_range is None:
            continue
        file_hostname = combined_log_hostname(path)
        if file_hostname is None:
            continue
        if hostname is not None and file_hostname != hostname:
            continue
        file_start, file_end = date_range
        candidates.append((file_start, file_end, file_hostname, path.name, path))

    candidates.sort(key=lambda item: (item[1], item[0], item[3]))
    selected_items: List[Tuple[date, str, Path]] = []
    selected_dates_by_path: Dict[Path, Optional[Set[date]]] = {}

    if start_date is not None and end_date is not None:
        selected_dates = date_range_set(start_date, end_date)
        for file_start, file_end, _, path_name, path in candidates:
            if not ranges_overlap(file_start, file_end, start_date, end_date):
                continue
            selected_items.append((max(file_start, start_date), path_name, path))
            selected_dates_by_path[path] = selected_dates
        selected_items.sort()
        return LogFiles([path for _, _, path in selected_items], selected_dates_by_path, hostname_filter=hostname)

    if not candidates:
        return LogFiles([], {}, hostname_filter=hostname)

    file_start, file_end, file_hostname, path_name, path = candidates[-1]
    sample_dates = collect_sample_dates(path, hostname or file_hostname)
    if not sample_dates:
        sample_dates = date_range_set(file_start, file_end)
    selected_dates = select_recent_dates(sample_dates, days)
    selected_items.append((min(selected_dates), path_name, path))
    selected_dates_by_path[path] = selected_dates

    return LogFiles(
        [path for _, _, path in selected_items],
        selected_dates_by_path,
        hostname_filter=hostname or file_hostname,
    )


def resolve_server_log_files(log_dir: Path, days: Optional[int]) -> Tuple[Path, List[Path]]:
    return log_dir, list_log_files(log_dir, days)


def resolve_local_log_files(args: argparse.Namespace, log_dir: Path, days: Optional[int]) -> Tuple[Path, List[Path]]:
    start_date, end_date = parse_optional_date_range(args)
    hostname = getattr(args, "hostname", None)
    return log_dir, list_local_log_files(log_dir, hostname, start_date, end_date, days)


def has_local_filters(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "hostname", None)
        or getattr(args, "start_date", None)
        or getattr(args, "end_date", None)
    )


def resolve_auto_log_files(args: argparse.Namespace, days: Optional[int]) -> Tuple[Path, List[Path]]:
    log_dir_arg = getattr(args, "log_dir", None)
    if has_local_filters(args):
        log_dir = resolve_log_dir(log_dir_arg, "local")
        if not log_dir.exists():
            raise SystemExit(f"Log directory does not exist: {log_dir}")
        return resolve_local_log_files(args, log_dir, days)

    if log_dir_arg:
        log_dir = Path(log_dir_arg).expanduser()
        if not log_dir.exists():
            raise SystemExit(f"Log directory does not exist: {log_dir}")
        server_files = list_log_files(log_dir, days)
        if server_files:
            return log_dir, server_files
        return resolve_local_log_files(args, log_dir, days)

    if DEFAULT_SERVER_LOG_DIR.exists():
        server_files = list_log_files(DEFAULT_SERVER_LOG_DIR, days)
        if server_files:
            return DEFAULT_SERVER_LOG_DIR, server_files

    if DEFAULT_DOWNLOADED_LOG_DIR.exists():
        local_files = list_local_log_files(DEFAULT_DOWNLOADED_LOG_DIR, None, None, None, days)
        if local_files:
            return DEFAULT_DOWNLOADED_LOG_DIR, local_files

    if DEFAULT_SERVER_LOG_DIR.exists():
        return DEFAULT_SERVER_LOG_DIR, []
    if DEFAULT_DOWNLOADED_LOG_DIR.exists():
        return DEFAULT_DOWNLOADED_LOG_DIR, []
    raise SystemExit(
        f"No log directory exists. Checked {DEFAULT_SERVER_LOG_DIR} and {DEFAULT_DOWNLOADED_LOG_DIR}"
    )


def resolve_log_files(args: argparse.Namespace) -> Tuple[Path, List[Path]]:
    mode = getattr(args, "mode", "auto")
    if mode not in {"auto", "server", "local"}:
        raise SystemExit("--mode must be auto, server, or local")
    if args.days < 0:
        raise SystemExit("--days must be 0 or greater")

    days = None if args.days == 0 else args.days
    if mode == "auto":
        return resolve_auto_log_files(args, days)

    log_dir = resolve_log_dir(getattr(args, "log_dir", None), mode)
    if not log_dir.exists():
        raise SystemExit(f"Log directory does not exist: {log_dir}")

    if mode == "local":
        return resolve_local_log_files(args, log_dir, days)
    return resolve_server_log_files(log_dir, days)


def warn_invalid_sample(path: Path, line_number: int, exc: JSONDecodeError) -> None:
    sys.stderr.write(
        f"Skipping invalid JSON sample in {path} line {line_number}: {exc.msg} at column {exc.colno}\n"
    )


def iter_samples(
    log_files: Iterable[Path],
    on_invalid: Optional[Callable[[Path, int, JSONDecodeError], None]] = None,
) -> Iterator[Tuple[Path, int, Dict[str, object]]]:
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
                    warn_invalid_sample(path, line_number, exc)
                    continue
                if not isinstance(sample, dict):
                    continue
                if selected_dates is not None and parse_sample_timestamp_date(sample) not in selected_dates:
                    continue
                if hostname_filter is not None and sample.get("hostname") != hostname_filter:
                    continue
                yield path, line_number, sample


def parse_iso_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone information, for example 2026-04-20T01:45:24Z")
    return parsed


def format_gib_from_bytes(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value / (1024 ** 3):.2f} GiB"


def format_mib_per_sec_from_bytes(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value / (1024 ** 2):.2f} MiB/s"
