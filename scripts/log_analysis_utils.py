#!/usr/bin/env python3

import argparse
import json
import re
import sys
from datetime import date, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVER_LOG_DIR = Path("/var/log/system-resource-monitor")
DEFAULT_DOWNLOADED_LOG_DIR = REPO_ROOT / "local-debug-logs"
COMBINED_LOG_RE = re.compile(
    r"^(?P<hostname>.+)_(?P<start>\d{4}-\d{2}-\d{2})_to_(?P<end>\d{4}-\d{2}-\d{2})\.jsonl$"
)


class LogFiles(list):
    def __init__(self, paths: Iterable[Path], selected_dates_by_path: Dict[Path, Optional[Set[date]]]) -> None:
        super().__init__(paths)
        self.selected_dates_by_path = selected_dates_by_path


def add_log_source_args(parser: argparse.ArgumentParser, *, default_days: int = 7) -> None:
    parser.add_argument(
        "--source",
        choices=("server", "downloaded"),
        default="server",
        help="Choose between the default server log directory and the repo-local downloaded log directory.",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Override the log directory. When set, this takes precedence over --source.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=default_days,
        help="Number of recent log days with records to include. Use 0 to include all files.",
    )


def resolve_log_dir(log_dir: Optional[str], source: str) -> Path:
    if log_dir:
        return Path(log_dir).expanduser()
    if source == "downloaded":
        return DEFAULT_DOWNLOADED_LOG_DIR
    return DEFAULT_SERVER_LOG_DIR


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


def parse_sample_timestamp_date(sample: Dict[str, object]) -> Optional[date]:
    timestamp = sample.get("timestamp")
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_sample_date_from_line(line: str) -> Optional[date]:
    try:
        sample = json.loads(line)
    except JSONDecodeError:
        return None
    if not isinstance(sample, dict):
        return None
    return parse_sample_timestamp_date(sample)


def collect_sample_dates(path: Path) -> Set[date]:
    dates: Set[date] = set()
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            sample_date = parse_sample_date_from_line(line)
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

    for path in log_dir.glob("*.jsonl"):
        if path in dates_by_path or parse_combined_log_file_date_range(path) is None:
            continue
        sample_dates = collect_sample_dates(path)
        if sample_dates:
            dates_by_path[path] = sample_dates

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


def resolve_log_files(args: argparse.Namespace) -> Tuple[Path, List[Path]]:
    log_dir = resolve_log_dir(args.log_dir, args.source)
    if not log_dir.exists():
        raise SystemExit(f"Log directory does not exist: {log_dir}")
    if args.days < 0:
        raise SystemExit("--days must be 0 or greater")

    days = None if args.days == 0 else args.days
    return log_dir, list_log_files(log_dir, days)


def warn_invalid_sample(path: Path, line_number: int, exc: JSONDecodeError) -> None:
    sys.stderr.write(
        f"Skipping invalid JSON sample in {path} line {line_number}: {exc.msg} at column {exc.colno}\n"
    )


def iter_samples(log_files: Iterable[Path]) -> Iterator[Tuple[Path, int, Dict[str, object]]]:
    selected_dates_by_path = getattr(log_files, "selected_dates_by_path", {})
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
                    warn_invalid_sample(path, line_number, exc)
                    continue
                if not isinstance(sample, dict):
                    continue
                if selected_dates is not None and parse_sample_timestamp_date(sample) not in selected_dates:
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
