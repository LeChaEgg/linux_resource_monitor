#!/usr/bin/env python3

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return values[0]

    ranked = sorted(values)
    position = (len(ranked) - 1) * (pct / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ranked) - 1)
    weight = position - lower
    return ranked[lower] * (1 - weight) + ranked[upper] * weight


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def format_gib(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value / (1024 ** 3):.2f} GiB"


def format_mib_per_sec(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value / (1024 ** 2):.2f} MiB/s"


def parse_sample(line: str) -> Optional[Dict[str, object]]:
    line = line.strip()
    if not line:
        return None
    return json.loads(line)


def warn_invalid_sample(path: Path, line_number: int, exc: JSONDecodeError) -> None:
    sys.stderr.write(
        f"Skipping invalid JSON sample in {path} line {line_number}: {exc.msg} at column {exc.colno}\n"
    )


def parse_log_file_date(path: Path) -> Optional[datetime.date]:
    suffix = path.stem.replace("metrics-", "", 1)
    try:
        return datetime.strptime(suffix, "%Y-%m-%d").date()
    except ValueError:
        return None


def list_log_files(log_dir: Path, days: Optional[int]) -> List[Path]:
    dated_files: List[Tuple[datetime.date, Path]] = []
    for path in log_dir.glob("metrics-*.jsonl"):
        file_date = parse_log_file_date(path)
        if file_date is None:
            continue
        dated_files.append((file_date, path))

    dated_files.sort()
    if days is None:
        return [path for _, path in dated_files]

    return [path for _, path in dated_files[-days:]]


def detect_parallelism_bottleneck(cpu_p95: Optional[float], hottest_thread: Optional[Dict[str, object]]) -> Optional[str]:
    if cpu_p95 is None or hottest_thread is None:
        return None
    hottest_cpu = float(hottest_thread["cpu_pct"])
    if hottest_cpu >= 90 and cpu_p95 < 60:
        return (
            "A single thread reached near-core saturation while aggregate CPU stayed moderate. "
            "This points to a parallelism bottleneck more than a total-core shortage."
        )
    return None


def build_report(log_files: Iterable[Path]) -> str:
    timestamps: List[str] = []
    cpu_used: List[float] = []
    mem_used: List[float] = []
    swap_used: List[float] = []
    disk_read_bps: List[float] = []
    disk_write_bps: List[float] = []
    network_rx_bps: List[float] = []
    network_tx_bps: List[float] = []
    gpu_util_by_device: Dict[str, List[float]] = defaultdict(list)
    gpu_mem_by_device: Dict[str, List[float]] = defaultdict(list)
    top_thread_observations: List[Dict[str, object]] = []
    top_memory_observations: List[Dict[str, object]] = []
    hostnames = set()
    sample_count = 0
    skipped_invalid_samples = 0

    for path in log_files:
        with path.open(encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                try:
                    sample = parse_sample(raw_line)
                except JSONDecodeError as exc:
                    skipped_invalid_samples += 1
                    warn_invalid_sample(path, line_number, exc)
                    continue
                if sample is None:
                    continue

                sample_count += 1
                timestamp = str(sample.get("timestamp", "unknown"))
                timestamps.append(timestamp)
                hostname = str(sample.get("hostname", "unknown"))
                hostnames.add(hostname)

                cpu = sample.get("cpu", {})
                if isinstance(cpu, dict) and cpu.get("used_pct") is not None:
                    cpu_used.append(float(cpu["used_pct"]))

                memory = sample.get("memory", {})
                if isinstance(memory, dict):
                    if memory.get("mem_used_pct") is not None:
                        mem_used.append(float(memory["mem_used_pct"]))
                    if memory.get("swap_used_pct") is not None:
                        swap_used.append(float(memory["swap_used_pct"]))

                disk = sample.get("disk", {})
                if isinstance(disk, dict):
                    if disk.get("read_bytes_per_sec") is not None:
                        disk_read_bps.append(float(disk["read_bytes_per_sec"]))
                    if disk.get("write_bytes_per_sec") is not None:
                        disk_write_bps.append(float(disk["write_bytes_per_sec"]))

                network = sample.get("network", {})
                if isinstance(network, dict):
                    if network.get("rx_bytes_per_sec") is not None:
                        network_rx_bps.append(float(network["rx_bytes_per_sec"]))
                    if network.get("tx_bytes_per_sec") is not None:
                        network_tx_bps.append(float(network["tx_bytes_per_sec"]))

                gpu = sample.get("gpu", {})
                if isinstance(gpu, dict):
                    for device in gpu.get("devices", []):
                        if not isinstance(device, dict):
                            continue
                        device_key = f"{hostname}:{device.get('index', '?')}:{device.get('name', 'unknown')}"
                        if device.get("utilization_gpu_pct") is not None:
                            gpu_util_by_device[device_key].append(float(device["utilization_gpu_pct"]))
                        if device.get("memory_used_pct") is not None:
                            gpu_mem_by_device[device_key].append(float(device["memory_used_pct"]))

                for thread in sample.get("top_cpu_threads", []):
                    if not isinstance(thread, dict):
                        continue
                    top_thread_observations.append(
                        {
                            "timestamp": timestamp,
                            "hostname": hostname,
                            "pid": thread.get("pid"),
                            "tid": thread.get("tid"),
                            "process_name": thread.get("process_name"),
                            "thread_name": thread.get("thread_name"),
                            "cpu_pct": float(thread.get("cpu_pct", 0.0)),
                        }
                    )

                for process in sample.get("top_memory_processes", []):
                    if not isinstance(process, dict):
                        continue
                    top_memory_observations.append(
                        {
                            "timestamp": timestamp,
                            "hostname": hostname,
                            "pid": process.get("pid"),
                            "process_name": process.get("process_name"),
                            "rss_bytes": float(process.get("rss_bytes", 0.0)),
                        }
                    )

    if sample_count == 0:
        return "No samples found."

    top_thread_observations.sort(key=lambda item: item["cpu_pct"], reverse=True)
    top_memory_observations.sort(key=lambda item: item["rss_bytes"], reverse=True)

    cpu_p50 = percentile(cpu_used, 50)
    cpu_p95 = percentile(cpu_used, 95)
    cpu_p99 = percentile(cpu_used, 99)
    mem_p50 = percentile(mem_used, 50)
    mem_p95 = percentile(mem_used, 95)
    mem_p99 = percentile(mem_used, 99)
    swap_max = max(swap_used) if swap_used else None
    hottest_thread = top_thread_observations[0] if top_thread_observations else None
    fattest_process = top_memory_observations[0] if top_memory_observations else None
    bottleneck_note = detect_parallelism_bottleneck(cpu_p95, hottest_thread)

    lines: List[str] = []
    lines.append("Resource Monitor Summary")
    lines.append(f"Hosts: {', '.join(sorted(hostnames))}")
    lines.append(f"Samples: {sample_count}")
    if skipped_invalid_samples:
        lines.append(f"Skipped invalid samples: {skipped_invalid_samples}")
    lines.append(f"Time range: {min(timestamps)} -> {max(timestamps)}")
    lines.append("")
    lines.append("CPU")
    lines.append(f"  p50: {format_pct(cpu_p50)}")
    lines.append(f"  p95: {format_pct(cpu_p95)}")
    lines.append(f"  p99: {format_pct(cpu_p99)}")
    lines.append(f"  max: {format_pct(max(cpu_used) if cpu_used else None)}")
    lines.append("")
    lines.append("Memory")
    lines.append(f"  p50: {format_pct(mem_p50)}")
    lines.append(f"  p95: {format_pct(mem_p95)}")
    lines.append(f"  p99: {format_pct(mem_p99)}")
    lines.append(f"  max: {format_pct(max(mem_used) if mem_used else None)}")
    lines.append(f"  swap max: {format_pct(swap_max)}")

    if disk_read_bps or disk_write_bps:
        lines.append("")
        lines.append("Disk Throughput")
        lines.append(f"  read p95: {format_mib_per_sec(percentile(disk_read_bps, 95))}")
        lines.append(f"  read max: {format_mib_per_sec(max(disk_read_bps) if disk_read_bps else None)}")
        lines.append(f"  write p95: {format_mib_per_sec(percentile(disk_write_bps, 95))}")
        lines.append(f"  write max: {format_mib_per_sec(max(disk_write_bps) if disk_write_bps else None)}")

    if network_rx_bps or network_tx_bps:
        lines.append("")
        lines.append("Network Throughput")
        lines.append(f"  rx p95: {format_mib_per_sec(percentile(network_rx_bps, 95))}")
        lines.append(f"  rx max: {format_mib_per_sec(max(network_rx_bps) if network_rx_bps else None)}")
        lines.append(f"  tx p95: {format_mib_per_sec(percentile(network_tx_bps, 95))}")
        lines.append(f"  tx max: {format_mib_per_sec(max(network_tx_bps) if network_tx_bps else None)}")

    if gpu_util_by_device:
        lines.append("")
        lines.append("GPU")
        for device_key in sorted(gpu_util_by_device):
            util_values = gpu_util_by_device.get(device_key, [])
            mem_values = gpu_mem_by_device.get(device_key, [])
            lines.append(f"  {device_key}")
            lines.append(f"    util p95: {format_pct(percentile(util_values, 95))}")
            lines.append(f"    util max: {format_pct(max(util_values) if util_values else None)}")
            lines.append(f"    mem p95: {format_pct(percentile(mem_values, 95))}")
            lines.append(f"    mem max: {format_pct(max(mem_values) if mem_values else None)}")

    if hottest_thread:
        lines.append("")
        lines.append("Hot Thread")
        lines.append(
            "  "
            f"{hottest_thread['timestamp']} host={hottest_thread['hostname']} "
            f"pid={hottest_thread['pid']} tid={hottest_thread['tid']} "
            f"{hottest_thread['process_name']}/{hottest_thread['thread_name']} "
            f"cpu={format_pct(hottest_thread['cpu_pct'])}"
        )

    if fattest_process:
        lines.append("")
        lines.append("Heavy Process")
        lines.append(
            "  "
            f"{fattest_process['timestamp']} host={fattest_process['hostname']} "
            f"pid={fattest_process['pid']} "
            f"{fattest_process['process_name']} rss={format_gib(fattest_process['rss_bytes'])}"
        )

    if bottleneck_note:
        lines.append("")
        lines.append("Observation")
        lines.append(f"  {bottleneck_note}")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize system resource monitor JSONL logs.")
    parser.add_argument(
        "--log-dir",
        default="/var/log/system-resource-monitor",
        help="Directory containing metrics-YYYY-MM-DD.jsonl files.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of recent log days with records to include. Use 0 to include all files. Default: 7",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        raise SystemExit(f"Log directory does not exist: {log_dir}")
    if args.days < 0:
        raise SystemExit("--days must be 0 or greater")

    days = None if args.days == 0 else args.days
    log_files = list_log_files(log_dir, days)
    print(build_report(log_files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
