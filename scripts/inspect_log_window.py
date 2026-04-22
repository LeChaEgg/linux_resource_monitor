#!/usr/bin/env python3

import argparse
from datetime import timedelta
from typing import Dict, List

from log_analysis_utils import (
    add_log_source_args,
    format_gib_from_bytes,
    format_mib_per_sec_from_bytes,
    iter_samples,
    parse_iso_timestamp,
    resolve_log_files,
)


def summarize_top_memory(sample: Dict[str, object], limit: int) -> List[str]:
    items: List[str] = []
    processes = sample.get("top_memory_processes", [])
    if not isinstance(processes, list):
        return items
    for process in processes[:limit]:
        if not isinstance(process, dict):
            continue
        items.append(
            f"{process.get('process_name')} pid={process.get('pid')} rss={format_gib_from_bytes(float(process.get('rss_bytes', 0.0)))}"
        )
    return items


def summarize_top_threads(sample: Dict[str, object], limit: int) -> List[str]:
    items: List[str] = []
    threads = sample.get("top_cpu_threads", [])
    if not isinstance(threads, list):
        return items
    for thread in threads[:limit]:
        if not isinstance(thread, dict):
            continue
        items.append(
            f"{thread.get('process_name')}/{thread.get('thread_name')} "
            f"pid={thread.get('pid')} tid={thread.get('tid')} "
            f"cpu={float(thread.get('cpu_pct', 0.0)):.2f}%"
        )
    return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect a time window around a specific resource-monitor timestamp.")
    add_log_source_args(parser, default_days=0)
    parser.add_argument(
        "--timestamp",
        required=True,
        help="Center timestamp in ISO format, for example 2026-04-20T01:45:24Z.",
    )
    parser.add_argument("--minutes-before", type=int, default=15, help="Minutes to include before the timestamp.")
    parser.add_argument("--minutes-after", type=int, default=15, help="Minutes to include after the timestamp.")
    parser.add_argument(
        "--top-limit",
        type=int,
        default=3,
        help="Number of top memory processes and CPU threads to show per sample. Default: 3",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.minutes_before < 0 or args.minutes_after < 0:
        raise SystemExit("--minutes-before and --minutes-after must be 0 or greater")
    if args.top_limit <= 0:
        raise SystemExit("--top-limit must be greater than 0")

    target = parse_iso_timestamp(args.timestamp)
    window_start = target - timedelta(minutes=args.minutes_before)
    window_end = target + timedelta(minutes=args.minutes_after)

    log_dir, log_files = resolve_log_files(args)
    if not log_files:
        print(f"No log files found in {log_dir}")
        return 0

    matches: List[str] = []
    for _, _, sample in iter_samples(log_files):
        timestamp_raw = sample.get("timestamp")
        if not isinstance(timestamp_raw, str):
            continue
        sample_time = parse_iso_timestamp(timestamp_raw)
        if sample_time < window_start or sample_time > window_end:
            continue

        cpu = sample.get("cpu", {})
        memory = sample.get("memory", {})
        disk = sample.get("disk", {})
        network = sample.get("network", {})
        gpu = sample.get("gpu", {})

        lines: List[str] = [f"{timestamp_raw} host={sample.get('hostname', 'unknown')}"]
        if isinstance(cpu, dict) and isinstance(memory, dict):
            lines.append(
                "  "
                f"cpu={float(cpu.get('used_pct', 0.0)):.2f}% "
                f"mem={float(memory.get('mem_used_pct', 0.0)):.2f}% "
                f"swap={float(memory.get('swap_used_pct', 0.0)):.2f}% "
                f"load1={float(cpu.get('loadavg_1m', 0.0)):.2f}"
            )
        if isinstance(disk, dict) and isinstance(network, dict):
            lines.append(
                "  "
                f"disk_read={format_mib_per_sec_from_bytes(float(disk.get('read_bytes_per_sec', 0.0)))} "
                f"disk_write={format_mib_per_sec_from_bytes(float(disk.get('write_bytes_per_sec', 0.0)))} "
                f"net_rx={format_mib_per_sec_from_bytes(float(network.get('rx_bytes_per_sec', 0.0)))} "
                f"net_tx={format_mib_per_sec_from_bytes(float(network.get('tx_bytes_per_sec', 0.0)))}"
            )
        if isinstance(gpu, dict):
            devices = gpu.get("devices", [])
            if isinstance(devices, list) and devices:
                device_summaries = []
                for device in devices:
                    if not isinstance(device, dict):
                        continue
                    device_summaries.append(
                        f"gpu{device.get('index')} util={float(device.get('utilization_gpu_pct') or 0.0):.0f}% mem={float(device.get('memory_used_pct') or 0.0):.2f}%"
                    )
                if device_summaries:
                    lines.append("  " + " | ".join(device_summaries))

        top_memory = summarize_top_memory(sample, args.top_limit)
        if top_memory:
            lines.append("  top_memory: " + " | ".join(top_memory))
        top_threads = summarize_top_threads(sample, args.top_limit)
        if top_threads:
            lines.append("  top_threads: " + " | ".join(top_threads))

        matches.append("\n".join(lines))

    print(f"Log directory: {log_dir}")
    print(f"Window: {window_start.isoformat()} -> {window_end.isoformat()}")
    print("")
    if not matches:
        print("No samples found in the requested window.")
        return 0

    print("\n\n".join(matches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
