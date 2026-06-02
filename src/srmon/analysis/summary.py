"""Percentile-based summary of resource-monitor JSONL logs.

``build_report`` is the reusable entry point (also used by the monthly report).
Formatters come from srmon.core.format; they are aliased to their historical
names here so the summary text and spreadsheet values stay byte-for-byte stable.
"""

import argparse
from collections import defaultdict
from json import JSONDecodeError
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from srmon.core.format import format_pct
from srmon.core.format import format_gib_from_bytes as format_gib
from srmon.core.format import format_mib_per_sec_from_bytes as format_mib_per_sec
from srmon.core.samples import iter_samples
from srmon.core.selection import add_log_selection_args, resolve_log_files


SPREADSHEET_ROW_SEPARATOR = "\r\n"


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


def parse_gpu_index(device: Dict[str, object]) -> Optional[int]:
    index = device.get("index")
    try:
        return int(str(index))
    except (TypeError, ValueError):
        return None


def build_spreadsheet_values(
    cpu_used: List[float],
    mem_used: List[float],
    swap_used: List[float],
    network_rx_bps: List[float],
    network_tx_bps: List[float],
    gpu_util_by_index: Dict[int, List[float]],
    gpu_mem_by_index: Dict[int, List[float]],
) -> List[str]:
    values = [
        format_pct(percentile(cpu_used, 50)),
        format_pct(percentile(cpu_used, 95)),
        format_pct(percentile(cpu_used, 99)),
        format_pct(max(cpu_used) if cpu_used else None),
        format_pct(percentile(mem_used, 50)),
        format_pct(percentile(mem_used, 95)),
        format_pct(percentile(mem_used, 99)),
        format_pct(max(mem_used) if mem_used else None),
        format_pct(max(swap_used) if swap_used else None),
        format_mib_per_sec(percentile(network_rx_bps, 95)),
        format_mib_per_sec(max(network_rx_bps) if network_rx_bps else None),
        format_mib_per_sec(percentile(network_tx_bps, 95)),
        format_mib_per_sec(max(network_tx_bps) if network_tx_bps else None),
    ]

    for gpu_index in range(3):
        util_values = gpu_util_by_index.get(gpu_index, [])
        mem_values = gpu_mem_by_index.get(gpu_index, [])
        values.extend(
            [
                format_pct(percentile(util_values, 95)),
                format_pct(max(util_values) if util_values else None),
                format_pct(percentile(mem_values, 95)),
                format_pct(max(mem_values) if mem_values else None),
            ]
        )

    return values


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


def build_report(log_files: Iterable[Path], spreadsheet_values_only: bool = False) -> str:
    """Summarize the given log files. Thin wrapper over build_report_from_samples."""
    skipped_invalid_samples = 0

    def count_invalid(_path: Path, _line_number: int, _exc: JSONDecodeError) -> None:
        nonlocal skipped_invalid_samples
        skipped_invalid_samples += 1

    samples = [sample for _, _, sample in iter_samples(log_files, on_invalid=count_invalid)]
    return build_report_from_samples(
        samples, spreadsheet_values_only=spreadsheet_values_only, skipped_invalid_samples=skipped_invalid_samples
    )


def build_report_from_samples(
    samples: Iterable[Dict[str, object]],
    spreadsheet_values_only: bool = False,
    skipped_invalid_samples: int = 0,
) -> str:
    """Build the summary text from already-parsed samples.

    Lets the monthly report parse a host's month once and feed the same samples to
    the summary, peaks, and plot instead of re-reading the file for each.
    """
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
    gpu_util_by_index: Dict[int, List[float]] = defaultdict(list)
    gpu_mem_by_index: Dict[int, List[float]] = defaultdict(list)
    top_thread_observations: List[Dict[str, object]] = []
    top_memory_observations: List[Dict[str, object]] = []
    hostnames = set()
    sample_count = 0

    for sample in samples:
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
                    util_pct = float(device["utilization_gpu_pct"])
                    gpu_util_by_device[device_key].append(util_pct)
                    device_index = parse_gpu_index(device)
                    if device_index is not None:
                        gpu_util_by_index[device_index].append(util_pct)
                if device.get("memory_used_pct") is not None:
                    mem_pct = float(device["memory_used_pct"])
                    gpu_mem_by_device[device_key].append(mem_pct)
                    device_index = parse_gpu_index(device)
                    if device_index is not None:
                        gpu_mem_by_index[device_index].append(mem_pct)

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
    spreadsheet_values = build_spreadsheet_values(
        cpu_used,
        mem_used,
        swap_used,
        network_rx_bps,
        network_tx_bps,
        gpu_util_by_index,
        gpu_mem_by_index,
    )

    if spreadsheet_values_only:
        return SPREADSHEET_ROW_SEPARATOR.join(spreadsheet_values)

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

    lines.append("")
    lines.append("Spreadsheet Values")
    lines.extend(spreadsheet_values)

    return "\n".join(lines)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_log_selection_args(parser)
    parser.add_argument(
        "--spreadsheet-values-only",
        action="store_true",
        help="Print only the one-column spreadsheet value list, without the full summary.",
    )


def run(args: argparse.Namespace) -> int:
    _, log_files = resolve_log_files(args)
    print(build_report(log_files, spreadsheet_values_only=args.spreadsheet_values_only))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize system resource monitor JSONL logs.")
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
