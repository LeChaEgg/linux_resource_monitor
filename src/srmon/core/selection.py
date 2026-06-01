"""Log directory / file selection across server and downloaded ("local") logs.

The --mode default is "auto": it reads /var/log/system-resource-monitor when those
server logs are present, otherwise the newest combined file in local-debug-logs/.
This keeps the on-server summary working (it must find /var/log/...) while a
workstation with no server logs transparently falls back to the downloaded copies.
"""

import argparse
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from srmon.core.naming import (
    combined_log_hostname,
    parse_combined_log_file_date_range,
    parse_log_file_date,
)
from srmon.core.samples import collect_sample_dates
from srmon.core.timerange import date_range_set, ranges_overlap, select_recent_dates


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVER_LOG_DIR = Path("/var/log/system-resource-monitor")
DEFAULT_DOWNLOADED_LOG_DIR = REPO_ROOT / "local-debug-logs"
DEFAULT_LOG_DAYS = 30


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
    from datetime import datetime

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
