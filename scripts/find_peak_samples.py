#!/usr/bin/env python3

import argparse
from typing import Dict, List, Optional

from log_analysis_utils import add_log_source_args, format_gib_from_bytes, iter_samples, resolve_log_files


def sort_key(value: Optional[float]) -> float:
    if value is None:
        return float("-inf")
    return float(value)


def top_process_by_rss(sample: Dict[str, object], process_name: Optional[str]) -> Optional[Dict[str, object]]:
    processes = sample.get("top_memory_processes", [])
    if not isinstance(processes, list):
        return None
    for process in processes:
        if not isinstance(process, dict):
            continue
        if process_name and process.get("process_name") != process_name:
            continue
        return process
    return None


def top_thread_by_cpu(sample: Dict[str, object], process_name: Optional[str]) -> Optional[Dict[str, object]]:
    threads = sample.get("top_cpu_threads", [])
    if not isinstance(threads, list):
        return None
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        if process_name and thread.get("process_name") != process_name:
            continue
        return thread
    return None


def format_metric_section(title: str, rows: List[str]) -> str:
    if not rows:
        rows = ["  No matching samples."]
    return "\n".join([title, *rows])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List high-watermark samples from resource monitor logs.")
    add_log_source_args(parser, default_days=7)
    parser.add_argument("--limit", type=int, default=5, help="Number of rows to show per section. Default: 5")
    parser.add_argument(
        "--process-name",
        default=None,
        help="Only consider rows where this process is present in top_memory_processes or top_cpu_threads.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be greater than 0")

    log_dir, log_files = resolve_log_files(args)
    if not log_files:
        print(f"No log files found in {log_dir}")
        return 0

    cpu_rows: List[Dict[str, object]] = []
    mem_rows: List[Dict[str, object]] = []
    swap_rows: List[Dict[str, object]] = []
    rss_rows: List[Dict[str, object]] = []

    for _, _, sample in iter_samples(log_files):
        timestamp = str(sample.get("timestamp", "unknown"))
        hostname = str(sample.get("hostname", "unknown"))
        memory = sample.get("memory", {})
        cpu = sample.get("cpu", {})
        if isinstance(cpu, dict):
            cpu_used_pct = cpu.get("used_pct")
            if cpu_used_pct is not None:
                if not args.process_name or top_thread_by_cpu(sample, args.process_name):
                    cpu_rows.append(
                        {"timestamp": timestamp, "hostname": hostname, "value": float(cpu_used_pct), "sample": sample}
                    )

        if isinstance(memory, dict):
            mem_used_pct = memory.get("mem_used_pct")
            if mem_used_pct is not None:
                if not args.process_name or top_process_by_rss(sample, args.process_name):
                    mem_rows.append(
                        {"timestamp": timestamp, "hostname": hostname, "value": float(mem_used_pct), "sample": sample}
                    )
            swap_used_pct = memory.get("swap_used_pct")
            if swap_used_pct is not None:
                if not args.process_name or top_process_by_rss(sample, args.process_name):
                    swap_rows.append(
                        {"timestamp": timestamp, "hostname": hostname, "value": float(swap_used_pct), "sample": sample}
                    )

        process = top_process_by_rss(sample, args.process_name)
        if process and process.get("rss_bytes") is not None:
            rss_rows.append(
                {
                    "timestamp": timestamp,
                    "hostname": hostname,
                    "value": float(process["rss_bytes"]),
                    "sample": sample,
                    "process": process,
                }
            )

    cpu_rows.sort(key=lambda item: sort_key(item["value"]), reverse=True)
    mem_rows.sort(key=lambda item: sort_key(item["value"]), reverse=True)
    swap_rows.sort(key=lambda item: sort_key(item["value"]), reverse=True)
    rss_rows.sort(key=lambda item: sort_key(item["value"]), reverse=True)

    def cpu_line(row: Dict[str, object]) -> str:
        thread = top_thread_by_cpu(row["sample"], args.process_name)
        thread_desc = "no hot thread captured"
        if thread:
            thread_desc = (
                f"{thread.get('process_name')}/{thread.get('thread_name')} "
                f"cpu={float(thread.get('cpu_pct', 0.0)):.2f}%"
            )
        return f"  {row['timestamp']} host={row['hostname']} cpu_used={float(row['value']):.2f}% {thread_desc}"

    def mem_line(row: Dict[str, object]) -> str:
        process = top_process_by_rss(row["sample"], args.process_name)
        process_desc = "no memory process captured"
        if process:
            process_desc = (
                f"{process.get('process_name')} pid={process.get('pid')} "
                f"rss={format_gib_from_bytes(float(process.get('rss_bytes', 0.0)))}"
            )
        return f"  {row['timestamp']} host={row['hostname']} mem_used={float(row['value']):.2f}% {process_desc}"

    def swap_line(row: Dict[str, object]) -> str:
        process = top_process_by_rss(row["sample"], args.process_name)
        process_desc = "no memory process captured"
        if process:
            process_desc = (
                f"{process.get('process_name')} pid={process.get('pid')} "
                f"rss={format_gib_from_bytes(float(process.get('rss_bytes', 0.0)))}"
            )
        return f"  {row['timestamp']} host={row['hostname']} swap_used={float(row['value']):.2f}% {process_desc}"

    def rss_line(row: Dict[str, object]) -> str:
        process = row["process"]
        return (
            f"  {row['timestamp']} host={row['hostname']} "
            f"{process.get('process_name')} pid={process.get('pid')} "
            f"rss={format_gib_from_bytes(float(row['value']))}"
        )

    sections = [
        format_metric_section("Top CPU Samples", [cpu_line(row) for row in cpu_rows[: args.limit]]),
        format_metric_section("Top Memory Samples", [mem_line(row) for row in mem_rows[: args.limit]]),
        format_metric_section("Top Swap Samples", [swap_line(row) for row in swap_rows[: args.limit]]),
        format_metric_section("Top Process RSS Samples", [rss_line(row) for row in rss_rows[: args.limit]]),
    ]

    print(f"Log directory: {log_dir}")
    print(f"Log files: {len(log_files)}")
    if args.process_name:
        print(f"Process filter: {args.process_name}")
    print("")
    print("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
