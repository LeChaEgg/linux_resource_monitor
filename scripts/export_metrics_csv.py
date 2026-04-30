#!/usr/bin/env python3

import argparse
import csv
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Set

from log_analysis_utils import (
    DEFAULT_DOWNLOADED_LOG_DIR,
    add_log_selection_args,
    iter_samples,
    parse_sample_timestamp_date,
    resolve_log_files,
)


def first_dict(items: object) -> Optional[Dict[str, object]]:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export resource monitor samples to CSV for plotting.")
    add_log_selection_args(parser)
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Write CSV to this path. Use - to write to stdout. "
            "Default: local-debug-logs/resource-monitor_<host>_<start>_to_<end>.csv"
        ),
    )
    return parser.parse_args()


def safe_filename_part(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in value.strip())
    return cleaned.strip("._-") or "unknown"


def default_output_path(hostnames: Set[str], sample_dates: Set[date]) -> Path:
    if len(hostnames) == 1:
        host_part = safe_filename_part(next(iter(hostnames)))
    elif hostnames:
        host_part = "multi-host"
    else:
        host_part = "unknown-host"

    if sample_dates:
        start_date = min(sample_dates).isoformat()
        end_date = max(sample_dates).isoformat()
    else:
        start_date = "unknown"
        end_date = "unknown"

    return DEFAULT_DOWNLOADED_LOG_DIR / f"resource-monitor_{host_part}_{start_date}_to_{end_date}.csv"


def main() -> int:
    args = parse_args()
    log_dir, log_files = resolve_log_files(args)
    if not log_files:
        print(f"No log files found in {log_dir}", file=sys.stderr)
        return 0

    fieldnames = [
        "timestamp",
        "hostname",
        "cpu_used_pct",
        "loadavg_1m",
        "mem_used_pct",
        "swap_used_pct",
        "disk_read_mib_per_sec",
        "disk_write_mib_per_sec",
        "network_rx_mib_per_sec",
        "network_tx_mib_per_sec",
        "top_memory_process_name",
        "top_memory_process_pid",
        "top_memory_process_rss_gib",
        "hot_thread_process_name",
        "hot_thread_thread_name",
        "hot_thread_pid",
        "hot_thread_tid",
        "hot_thread_cpu_pct",
    ]

    output_handle = sys.stdout
    managed_handle = None
    temp_output_path: Optional[Path] = None

    if args.output is None:
        DEFAULT_DOWNLOADED_LOG_DIR.mkdir(parents=True, exist_ok=True)
        temp_file = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=DEFAULT_DOWNLOADED_LOG_DIR,
            prefix=".resource-monitor-export-",
            suffix=".csv",
            delete=False,
        )
        managed_handle = temp_file
        temp_output_path = Path(temp_file.name)
        output_handle = managed_handle
    elif args.output != "-":
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        managed_handle = output_path.open("w", encoding="utf-8", newline="")
        output_handle = managed_handle

    try:
        writer = csv.DictWriter(output_handle, fieldnames=fieldnames)
        writer.writeheader()
        hostnames: Set[str] = set()
        sample_dates: Set[date] = set()

        for _, _, sample in iter_samples(log_files):
            hostname = sample.get("hostname")
            if hostname is not None:
                hostnames.add(str(hostname))
            sample_date = parse_sample_timestamp_date(sample)
            if sample_date is not None:
                sample_dates.add(sample_date)

            cpu = sample.get("cpu", {})
            memory = sample.get("memory", {})
            disk = sample.get("disk", {})
            network = sample.get("network", {})
            top_process = first_dict(sample.get("top_memory_processes"))
            hot_thread = first_dict(sample.get("top_cpu_threads"))

            writer.writerow(
                {
                    "timestamp": sample.get("timestamp"),
                    "hostname": sample.get("hostname"),
                    "cpu_used_pct": cpu.get("used_pct") if isinstance(cpu, dict) else None,
                    "loadavg_1m": cpu.get("loadavg_1m") if isinstance(cpu, dict) else None,
                    "mem_used_pct": memory.get("mem_used_pct") if isinstance(memory, dict) else None,
                    "swap_used_pct": memory.get("swap_used_pct") if isinstance(memory, dict) else None,
                    "disk_read_mib_per_sec": (
                        round(float(disk.get("read_bytes_per_sec", 0.0)) / (1024 ** 2), 4)
                        if isinstance(disk, dict) and disk.get("read_bytes_per_sec") is not None
                        else None
                    ),
                    "disk_write_mib_per_sec": (
                        round(float(disk.get("write_bytes_per_sec", 0.0)) / (1024 ** 2), 4)
                        if isinstance(disk, dict) and disk.get("write_bytes_per_sec") is not None
                        else None
                    ),
                    "network_rx_mib_per_sec": (
                        round(float(network.get("rx_bytes_per_sec", 0.0)) / (1024 ** 2), 4)
                        if isinstance(network, dict) and network.get("rx_bytes_per_sec") is not None
                        else None
                    ),
                    "network_tx_mib_per_sec": (
                        round(float(network.get("tx_bytes_per_sec", 0.0)) / (1024 ** 2), 4)
                        if isinstance(network, dict) and network.get("tx_bytes_per_sec") is not None
                        else None
                    ),
                    "top_memory_process_name": top_process.get("process_name") if top_process else None,
                    "top_memory_process_pid": top_process.get("pid") if top_process else None,
                    "top_memory_process_rss_gib": (
                        round(float(top_process.get("rss_bytes", 0.0)) / (1024 ** 3), 4) if top_process else None
                    ),
                    "hot_thread_process_name": hot_thread.get("process_name") if hot_thread else None,
                    "hot_thread_thread_name": hot_thread.get("thread_name") if hot_thread else None,
                    "hot_thread_pid": hot_thread.get("pid") if hot_thread else None,
                    "hot_thread_tid": hot_thread.get("tid") if hot_thread else None,
                    "hot_thread_cpu_pct": hot_thread.get("cpu_pct") if hot_thread else None,
                }
            )
    finally:
        if managed_handle is not None:
            managed_handle.close()
    if temp_output_path is not None:
        final_output_path = default_output_path(hostnames, sample_dates)
        temp_output_path.replace(final_output_path)
        print(f"Wrote CSV: {final_output_path}")
    elif args.output and args.output != "-":
        print(f"Wrote CSV: {Path(args.output).expanduser()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
