#!/usr/bin/env python3

import argparse
import csv
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
CPU_COUNT = os.cpu_count() or 1
TERMINATE = False


@dataclass
class ProcessEntry:
    pid: int
    starttime: int
    name: str
    rss_bytes: int


@dataclass
class ThreadEntry:
    pid: int
    tid: int
    starttime: int
    process_name: str
    thread_name: str
    state: str
    total_time: int
    process_rss_bytes: int


@dataclass
class Snapshot:
    collected_at_monotonic: float
    cpu_total: int
    cpu_idle: int
    memory: Dict[str, float]
    loadavg: Tuple[float, float, float]
    uptime_seconds: float
    process_count: int
    thread_count: int
    skipped_processes: int
    skipped_threads: int
    top_memory_processes: List[Dict[str, object]]
    threads: Dict[Tuple[int, int, int], ThreadEntry]


def handle_signal(_signum, _frame) -> None:
    global TERMINATE
    TERMINATE = True


def read_text(path: Path, binary: bool = False) -> str:
    mode = "rb" if binary else "r"
    with path.open(mode) as handle:
        data = handle.read()
    if binary:
        return data.decode("utf-8", errors="replace")
    return data


def parse_stat_file(path: Path) -> Dict[str, object]:
    raw = read_text(path).strip()
    left = raw.find("(")
    right = raw.rfind(")")
    if left == -1 or right == -1 or right <= left:
        raise ValueError(f"Malformed stat file: {path}")

    pid = int(raw[:left].strip())
    comm = raw[left + 1 : right]
    fields = raw[right + 2 :].split()
    if len(fields) < 20:
        raise ValueError(f"Incomplete stat file: {path}")

    state = fields[0]
    utime = int(fields[11])
    stime = int(fields[12])
    starttime = int(fields[19])

    return {
        "pid": pid,
        "comm": comm,
        "state": state,
        "total_time": utime + stime,
        "starttime": starttime,
    }


def read_process_rss_bytes(path: Path) -> int:
    fields = read_text(path).split()
    if len(fields) < 2:
        raise ValueError(f"Incomplete statm file: {path}")
    return int(fields[1]) * PAGE_SIZE


def read_cpu_totals() -> Tuple[int, int]:
    with Path("/proc/stat").open() as handle:
        first_line = handle.readline().strip()

    parts = first_line.split()
    if not parts or parts[0] != "cpu":
        raise RuntimeError("Unable to read aggregate CPU counters from /proc/stat")

    values = [int(value) for value in parts[1:]]
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def read_memory() -> Dict[str, float]:
    data: Dict[str, int] = {}
    with Path("/proc/meminfo").open() as handle:
        for line in handle:
            key, raw_value = line.split(":", 1)
            value = raw_value.strip().split()[0]
            data[key] = int(value) * 1024

    mem_total = data.get("MemTotal", 0)
    mem_available = data.get("MemAvailable", data.get("MemFree", 0))
    mem_used = max(mem_total - mem_available, 0)
    swap_total = data.get("SwapTotal", 0)
    swap_free = data.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)

    return {
        "mem_total_bytes": mem_total,
        "mem_available_bytes": mem_available,
        "mem_used_bytes": mem_used,
        "mem_used_pct": round((mem_used / mem_total) * 100, 2) if mem_total else 0.0,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_used,
        "swap_used_pct": round((swap_used / swap_total) * 100, 2) if swap_total else 0.0,
    }


def read_uptime_seconds() -> float:
    raw = read_text(Path("/proc/uptime")).split()
    return float(raw[0])


def read_boot_id() -> str:
    try:
        return read_text(Path("/proc/sys/kernel/random/boot_id")).strip()
    except OSError:
        return "unknown"


def top_memory_processes(entries: List[ProcessEntry], top_n: int) -> List[Dict[str, object]]:
    ranked = sorted(entries, key=lambda item: item.rss_bytes, reverse=True)[:top_n]
    return [
        {
            "pid": item.pid,
            "process_name": item.name,
            "rss_bytes": item.rss_bytes,
        }
        for item in ranked
    ]


def collect_snapshot(top_n: int) -> Snapshot:
    cpu_total, cpu_idle = read_cpu_totals()
    loadavg = os.getloadavg()
    memory = read_memory()
    uptime_seconds = read_uptime_seconds()

    processes: List[ProcessEntry] = []
    threads: Dict[Tuple[int, int, int], ThreadEntry] = {}
    skipped_processes = 0
    skipped_threads = 0

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue

        try:
            process_stat = parse_stat_file(entry / "stat")
            rss_bytes = read_process_rss_bytes(entry / "statm")
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, OSError):
            skipped_processes += 1
            continue

        process_name = str(process_stat["comm"])
        pid = int(process_stat["pid"])
        process_starttime = int(process_stat["starttime"])

        processes.append(
            ProcessEntry(
                pid=pid,
                starttime=process_starttime,
                name=process_name,
                rss_bytes=rss_bytes,
            )
        )

        task_dir = entry / "task"
        try:
            task_entries = list(task_dir.iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            skipped_threads += 1
            continue

        for task_entry in task_entries:
            try:
                thread_stat = parse_stat_file(task_entry / "stat")
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, OSError):
                skipped_threads += 1
                continue

            tid = int(thread_stat["pid"])
            thread_starttime = int(thread_stat["starttime"])
            key = (pid, tid, thread_starttime)
            threads[key] = ThreadEntry(
                pid=pid,
                tid=tid,
                starttime=thread_starttime,
                process_name=process_name,
                thread_name=str(thread_stat["comm"]),
                state=str(thread_stat["state"]),
                total_time=int(thread_stat["total_time"]),
                process_rss_bytes=rss_bytes,
            )

    return Snapshot(
        collected_at_monotonic=time.monotonic(),
        cpu_total=cpu_total,
        cpu_idle=cpu_idle,
        memory=memory,
        loadavg=loadavg,
        uptime_seconds=uptime_seconds,
        process_count=len(processes),
        thread_count=len(threads),
        skipped_processes=skipped_processes,
        skipped_threads=skipped_threads,
        top_memory_processes=top_memory_processes(processes, top_n),
        threads=threads,
    )


def parse_numeric(value: str) -> Optional[float]:
    cleaned = value.strip()
    if not cleaned or cleaned in {"N/A", "[N/A]", "Not Supported", "[Not Supported]"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def read_process_name(pid: int) -> str:
    try:
        return read_text(Path("/proc") / str(pid) / "comm").strip()
    except OSError:
        return "unknown"


def query_nvidia_gpu() -> Dict[str, object]:
    if shutil.which("nvidia-smi") is None:
        return {
            "backend": "nvidia",
            "detected": False,
            "devices": [],
            "processes": [],
            "probe_error": "nvidia-smi not found",
        }

    device_fields = [
        "index",
        "uuid",
        "name",
        "utilization.gpu",
        "utilization.memory",
        "memory.total",
        "memory.used",
        "temperature.gpu",
        "power.draw",
    ]
    device_cmd = [
        "nvidia-smi",
        f"--query-gpu={','.join(device_fields)}",
        "--format=csv,noheader,nounits",
    ]

    try:
        device_result = subprocess.run(
            device_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "backend": "nvidia",
            "detected": False,
            "devices": [],
            "processes": [],
            "probe_error": str(exc),
        }

    if device_result.returncode != 0:
        return {
            "backend": "nvidia",
            "detected": False,
            "devices": [],
            "processes": [],
            "probe_error": device_result.stderr.strip() or device_result.stdout.strip() or "nvidia-smi failed",
        }

    devices: List[Dict[str, object]] = []
    for row in csv.reader(device_result.stdout.splitlines(), skipinitialspace=True):
        if len(row) < len(device_fields):
            continue
        device = {
            "index": int(row[0]),
            "uuid": row[1],
            "name": row[2],
            "utilization_gpu_pct": parse_numeric(row[3]),
            "utilization_memory_pct": parse_numeric(row[4]),
            "memory_total_mib": parse_numeric(row[5]),
            "memory_used_mib": parse_numeric(row[6]),
            "temperature_c": parse_numeric(row[7]),
            "power_draw_w": parse_numeric(row[8]),
        }
        total = device["memory_total_mib"]
        used = device["memory_used_mib"]
        device["memory_used_pct"] = round((used / total) * 100, 2) if total and used is not None else None
        devices.append(device)

    process_fields = ["pid", "gpu_uuid", "used_gpu_memory"]
    process_cmd = [
        "nvidia-smi",
        f"--query-compute-apps={','.join(process_fields)}",
        "--format=csv,noheader,nounits",
    ]

    processes: List[Dict[str, object]] = []
    try:
        process_result = subprocess.run(
            process_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        process_result = None

    if process_result and process_result.returncode == 0:
        for row in csv.reader(process_result.stdout.splitlines(), skipinitialspace=True):
            if len(row) < len(process_fields) or not row[0].strip().isdigit():
                continue
            pid = int(row[0])
            processes.append(
                {
                    "pid": pid,
                    "process_name": read_process_name(pid),
                    "gpu_uuid": row[1],
                    "used_gpu_memory_mib": parse_numeric(row[2]),
                }
            )

    processes.sort(key=lambda item: item.get("used_gpu_memory_mib") or 0, reverse=True)

    return {
        "backend": "nvidia",
        "detected": bool(devices),
        "devices": devices,
        "processes": processes,
    }


def build_top_cpu_threads(
    previous: Snapshot,
    current: Snapshot,
    interval_seconds: float,
    top_n: int,
) -> List[Dict[str, object]]:
    total_delta = current.cpu_total - previous.cpu_total
    if total_delta <= 0:
        return []

    previous_uptime_ticks = int(previous.uptime_seconds * CLK_TCK)
    ranked: List[Dict[str, object]] = []
    for key, thread in current.threads.items():
        previous_thread = previous.threads.get(key)
        if previous_thread is None:
            if thread.starttime < previous_uptime_ticks:
                continue
            thread_delta = thread.total_time
        else:
            thread_delta = thread.total_time - previous_thread.total_time
        if thread_delta <= 0:
            continue

        cpu_pct = (thread_delta / total_delta) * CPU_COUNT * 100
        ranked.append(
            {
                "pid": thread.pid,
                "tid": thread.tid,
                "process_name": thread.process_name,
                "thread_name": thread.thread_name,
                "state": thread.state,
                "cpu_pct": round(cpu_pct, 2),
                "cpu_time_seconds": round(thread_delta / CLK_TCK, 4),
                "process_rss_bytes": thread.process_rss_bytes,
                "interval_seconds": round(interval_seconds, 3),
            }
        )

    ranked.sort(key=lambda item: item["cpu_pct"], reverse=True)
    return ranked[:top_n]


def build_sample(
    previous: Snapshot,
    current: Snapshot,
    *,
    top_n: int,
    boot_id: str,
    host: str,
) -> Dict[str, object]:
    total_delta = current.cpu_total - previous.cpu_total
    idle_delta = current.cpu_idle - previous.cpu_idle
    busy_delta = max(total_delta - idle_delta, 0)
    interval_seconds = max(current.collected_at_monotonic - previous.collected_at_monotonic, 0.0)
    cpu_used_pct = round((busy_delta / total_delta) * 100, 2) if total_delta > 0 else None

    now = datetime.now(timezone.utc)
    top_cpu_threads = build_top_cpu_threads(previous, current, interval_seconds, top_n)

    return {
        "schema_version": 1,
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "hostname": host,
        "boot_id": boot_id,
        "sample_interval_seconds": round(interval_seconds, 3),
        "cpu": {
            "logical_cpu_count": CPU_COUNT,
            "used_pct": cpu_used_pct,
            "loadavg_1m": round(current.loadavg[0], 3),
            "loadavg_5m": round(current.loadavg[1], 3),
            "loadavg_15m": round(current.loadavg[2], 3),
        },
        "memory": current.memory,
        "uptime_seconds": round(current.uptime_seconds, 3),
        "scan": {
            "process_count": current.process_count,
            "thread_count": current.thread_count,
            "skipped_processes": current.skipped_processes,
            "skipped_threads": current.skipped_threads,
        },
        "top_cpu_threads": top_cpu_threads,
        "top_memory_processes": current.top_memory_processes,
        "gpu": query_nvidia_gpu(),
    }


class JsonlLogger:
    def __init__(self, log_dir: Path, retain_days: int) -> None:
        self.log_dir = log_dir
        self.retain_days = retain_days
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._last_prune_date: Optional[str] = None

    def write(self, sample: Dict[str, object]) -> Path:
        timestamp = datetime.fromisoformat(sample["timestamp"].replace("Z", "+00:00"))
        log_path = self.log_dir / f"metrics-{timestamp.date().isoformat()}.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            json.dump(sample, handle, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
        self._prune_if_needed(timestamp.date().isoformat())
        return log_path

    def _prune_if_needed(self, current_date: str) -> None:
        if self.retain_days <= 0 or self._last_prune_date == current_date:
            return

        cutoff = datetime.now(timezone.utc).date() - timedelta(days=self.retain_days)
        for path in self.log_dir.glob("metrics-*.jsonl"):
            suffix = path.stem.replace("metrics-", "", 1)
            try:
                file_date = datetime.strptime(suffix, "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

        self._last_prune_date = current_date


def run_monitor(interval_seconds: float, top_n: int, logger: JsonlLogger) -> None:
    boot_id = read_boot_id()
    host = socket.gethostname()
    previous = collect_snapshot(top_n)
    next_deadline = time.monotonic() + interval_seconds

    while not TERMINATE:
        sleep_for = max(next_deadline - time.monotonic(), 0.0)
        if sleep_for > 0:
            time.sleep(sleep_for)
        if TERMINATE:
            break

        current = collect_snapshot(top_n)
        sample = build_sample(previous, current, top_n=top_n, boot_id=boot_id, host=host)
        logger.write(sample)
        previous = current
        next_deadline += interval_seconds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Low-overhead Linux resource monitor for CPU, memory, GPU, and hot threads."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Sampling interval in seconds. Default: 10",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of top CPU threads and top memory processes to record. Default: 5",
    )
    parser.add_argument(
        "--retain-days",
        type=int,
        default=30,
        help="Delete daily log files older than this many days. Use 0 to disable pruning. Default: 30",
    )
    parser.add_argument(
        "--log-dir",
        default="/var/log/system-resource-monitor",
        help="Directory for daily JSONL logs. Default: /var/log/system-resource-monitor",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect a single interval sample, write it, then exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if sys.platform != "linux":
        sys.stderr.write("This monitor only supports Linux.\n")
        return 2
    if args.interval <= 0:
        sys.stderr.write("--interval must be greater than 0.\n")
        return 2
    if args.top_n <= 0:
        sys.stderr.write("--top-n must be greater than 0.\n")
        return 2

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger = JsonlLogger(Path(args.log_dir), args.retain_days)

    if args.once:
        previous = collect_snapshot(args.top_n)
        time.sleep(args.interval)
        current = collect_snapshot(args.top_n)
        sample = build_sample(
            previous,
            current,
            top_n=args.top_n,
            boot_id=read_boot_id(),
            host=socket.gethostname(),
        )
        logger.write(sample)
        return 0

    run_monitor(args.interval, args.top_n, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
