"""High-watermark ("peak") samples: top CPU, memory, swap, and process RSS.

The gather/sort logic and the line formatting (previously trapped inside
find_peak_samples.main) are now importable so the monthly report can embed a
peaks section without capturing stdout.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from srmon.core.format import format_gib_from_bytes
from srmon.core.samples import iter_samples
from srmon.core.selection import add_log_selection_args, resolve_log_files


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


@dataclass
class PeakResult:
    cpu_rows: List[Dict[str, object]]
    mem_rows: List[Dict[str, object]]
    swap_rows: List[Dict[str, object]]
    rss_rows: List[Dict[str, object]]


def compute_peaks(log_files: Iterable[Path], process_name: Optional[str] = None) -> PeakResult:
    return compute_peaks_from_samples(
        (sample for _, _, sample in iter_samples(log_files)), process_name
    )


def compute_peaks_from_samples(
    samples: Iterable[Dict[str, object]], process_name: Optional[str] = None
) -> PeakResult:
    cpu_rows: List[Dict[str, object]] = []
    mem_rows: List[Dict[str, object]] = []
    swap_rows: List[Dict[str, object]] = []
    rss_rows: List[Dict[str, object]] = []

    for sample in samples:
        timestamp = str(sample.get("timestamp", "unknown"))
        hostname = str(sample.get("hostname", "unknown"))
        memory = sample.get("memory", {})
        cpu = sample.get("cpu", {})
        if isinstance(cpu, dict):
            cpu_used_pct = cpu.get("used_pct")
            if cpu_used_pct is not None:
                if not process_name or top_thread_by_cpu(sample, process_name):
                    cpu_rows.append(
                        {"timestamp": timestamp, "hostname": hostname, "value": float(cpu_used_pct), "sample": sample}
                    )

        if isinstance(memory, dict):
            mem_used_pct = memory.get("mem_used_pct")
            if mem_used_pct is not None:
                if not process_name or top_process_by_rss(sample, process_name):
                    mem_rows.append(
                        {"timestamp": timestamp, "hostname": hostname, "value": float(mem_used_pct), "sample": sample}
                    )
            swap_used_pct = memory.get("swap_used_pct")
            if swap_used_pct is not None:
                if not process_name or top_process_by_rss(sample, process_name):
                    swap_rows.append(
                        {"timestamp": timestamp, "hostname": hostname, "value": float(swap_used_pct), "sample": sample}
                    )

        process = top_process_by_rss(sample, process_name)
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
    return PeakResult(cpu_rows=cpu_rows, mem_rows=mem_rows, swap_rows=swap_rows, rss_rows=rss_rows)


def cpu_line(row: Dict[str, object], process_name: Optional[str]) -> str:
    thread = top_thread_by_cpu(row["sample"], process_name)
    thread_desc = "no hot thread captured"
    if thread:
        thread_desc = (
            f"{thread.get('process_name')}/{thread.get('thread_name')} "
            f"cpu={float(thread.get('cpu_pct', 0.0)):.2f}%"
        )
    return f"  {row['timestamp']} host={row['hostname']} cpu_used={float(row['value']):.2f}% {thread_desc}"


def mem_line(row: Dict[str, object], process_name: Optional[str]) -> str:
    process = top_process_by_rss(row["sample"], process_name)
    process_desc = "no memory process captured"
    if process:
        process_desc = (
            f"{process.get('process_name')} pid={process.get('pid')} "
            f"rss={format_gib_from_bytes(float(process.get('rss_bytes', 0.0)))}"
        )
    return f"  {row['timestamp']} host={row['hostname']} mem_used={float(row['value']):.2f}% {process_desc}"


def swap_line(row: Dict[str, object], process_name: Optional[str]) -> str:
    process = top_process_by_rss(row["sample"], process_name)
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


def build_peak_report(log_files: Iterable[Path], limit: int, process_name: Optional[str] = None) -> str:
    return build_peak_report_from_samples(
        (sample for _, _, sample in iter_samples(log_files)), limit, process_name
    )


def build_peak_report_from_samples(
    samples: Iterable[Dict[str, object]], limit: int, process_name: Optional[str] = None
) -> str:
    result = compute_peaks_from_samples(samples, process_name)
    sections = [
        format_metric_section("Top CPU Samples", [cpu_line(row, process_name) for row in result.cpu_rows[:limit]]),
        format_metric_section("Top Memory Samples", [mem_line(row, process_name) for row in result.mem_rows[:limit]]),
        format_metric_section("Top Swap Samples", [swap_line(row, process_name) for row in result.swap_rows[:limit]]),
        format_metric_section("Top Process RSS Samples", [rss_line(row) for row in result.rss_rows[:limit]]),
    ]
    return "\n\n".join(sections)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_log_selection_args(parser)
    parser.add_argument("--limit", type=int, default=5, help="Number of rows to show per section. Default: 5")
    parser.add_argument(
        "--process-name",
        default=None,
        help="Only consider rows where this process is present in top_memory_processes or top_cpu_threads.",
    )


def run(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        raise SystemExit("--limit must be greater than 0")

    log_dir, log_files = resolve_log_files(args)
    if not log_files:
        print(f"No log files found in {log_dir}")
        return 0

    report = build_peak_report(log_files, args.limit, args.process_name)
    print(f"Log directory: {log_dir}")
    print(f"Log files: {len(log_files)}")
    if args.process_name:
        print(f"Process filter: {args.process_name}")
    print("")
    print(report)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="List high-watermark samples from resource monitor logs.")
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
