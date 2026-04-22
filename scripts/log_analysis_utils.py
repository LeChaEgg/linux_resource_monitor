#!/usr/bin/env python3

import argparse
import json
import sys
from datetime import date, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVER_LOG_DIR = Path("/var/log/system-resource-monitor")
DEFAULT_DOWNLOADED_LOG_DIR = REPO_ROOT / "local-debug-logs"


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


def list_log_files(log_dir: Path, days: Optional[int]) -> List[Path]:
    dated_files: List[Tuple[date, Path]] = []
    for path in log_dir.glob("metrics-*.jsonl"):
        file_date = parse_log_file_date(path)
        if file_date is None:
            continue
        dated_files.append((file_date, path))

    dated_files.sort()
    if days is None:
        return [path for _, path in dated_files]
    return [path for _, path in dated_files[-days:]]


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
    for path in log_files:
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
