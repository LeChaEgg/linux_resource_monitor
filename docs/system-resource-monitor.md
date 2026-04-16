# Ubuntu Resource Monitor

## Goal

This tool is for long-run capacity sizing on Ubuntu servers:

- record CPU, memory, and GPU usage every 10 seconds
- start automatically at boot
- keep daily log files for later analysis
- capture the hottest threads and memory-heavy processes

The implementation is in [resource_monitor.py](/path/to/system-resource-monitor/scripts/resource_monitor.py#L1) and the installer is [install-system-resource-monitor.sh](/path/to/system-resource-monitor/scripts/install-system-resource-monitor.sh#L1).
The sizing summary helper is [summarize_resource_monitor.py](/path/to/system-resource-monitor/scripts/summarize_resource_monitor.py#L1).

## Why this design

The monitor stays lightweight by using:

- `/proc` for CPU, memory, process RSS, and thread CPU counters
- `nvidia-smi` only when an NVIDIA GPU is present
- `systemd` for boot-time startup and restart handling
- JSONL logs for low-friction later analysis with Python, `jq`, or pandas

It does **not** depend on `psutil`, Prometheus, Docker, or a database.

## What it records every sample

- aggregate CPU usage
- 1m / 5m / 15m load average
- memory used / available
- swap used
- top `N` CPU-consuming threads
- top `N` memory-consuming processes
- NVIDIA GPU utilization and memory usage
- NVIDIA GPU processes by GPU memory usage, when available

Important limit:

- memory is a process-level metric, not a true thread-level metric on Linux
- because of that, the monitor records top CPU threads and top memory processes

This is the correct tradeoff if the goal is server sizing without misleading data.

## Install on Ubuntu

Run as root:

```bash
sudo sh /path/to/system-resource-monitor/scripts/install-system-resource-monitor.sh
```

That will:

1. install the executable to `/usr/local/bin/system-resource-monitor`
2. install the summary helper to `/usr/local/bin/system-resource-monitor-summary`
3. create `/etc/default/system-resource-monitor`
4. create and enable `system-resource-monitor.service`
5. start logging immediately and on every future boot

## Default config

The installer writes:

```bash
INTERVAL_SECONDS=10
TOP_N=5
RETAIN_DAYS=30
LOG_DIR=/var/log/system-resource-monitor
```

If you need a different interval or retention:

```bash
sudoedit /etc/default/system-resource-monitor
sudo systemctl restart system-resource-monitor.service
```

## Validate after install

Check the service:

```bash
systemctl status system-resource-monitor.service --no-pager
```

Tail recent samples:

```bash
tail -n 3 /var/log/system-resource-monitor/metrics-$(date +%F).jsonl
```

Run one manual sample with a shorter interval:

```bash
sudo /usr/local/bin/system-resource-monitor --once --interval 1 --top-n 5 --log-dir /tmp/system-resource-monitor
```

Summarize the last 7 days for sizing:

```bash
system-resource-monitor-summary --log-dir /var/log/system-resource-monitor --days 7
```

## Sample log shape

Each line is one JSON object:

```json
{
  "timestamp": "2026-04-16T01:23:45Z",
  "hostname": "server-a",
  "cpu": {
    "logical_cpu_count": 32,
    "used_pct": 61.7,
    "loadavg_1m": 18.2
  },
  "memory": {
    "mem_total_bytes": 270582939648,
    "mem_used_bytes": 182314950656,
    "mem_used_pct": 67.38
  },
  "top_cpu_threads": [
    {
      "pid": 1821,
      "tid": 1830,
      "process_name": "python3",
      "thread_name": "DataLoader",
      "cpu_pct": 92.4
    }
  ],
  "top_memory_processes": [
    {
      "pid": 1821,
      "process_name": "python3",
      "rss_bytes": 6249807872
    }
  ],
  "gpu": {
    "backend": "nvidia",
    "detected": true
  }
}
```

## Suggestions to improve this further

- Keep `TOP_N=5`. More than that adds noise without helping server sizing much.
- Keep `INTERVAL_SECONDS=10` for normal workloads. Use `2-5` seconds only during short stress tests.
- Leave `RETAIN_DAYS` at `30-90` days so you can compare quiet days against peak days.
- If your workloads are containerized, add `container_id` or `cgroup` tags in a v2 iteration.
- If you use AMD GPUs, add a second backend for `rocm-smi` instead of forcing everything through `nvidia-smi`.
- Use `system-resource-monitor-summary` weekly and keep those reports with your capacity-planning notes.

## Recommended sizing workflow

Use the monitor for at least:

- one normal working day
- one peak-load test
- one worst-case multi-job window

Then size the next server with headroom based on:

- CPU: sustained utilization and hot-thread concentration
- memory: peak RSS plus page-cache pressure plus swap behavior
- GPU: peak utilization, peak GPU memory, and how many GPU processes overlap

If a single thread is repeatedly near 100 percent while total CPU is still moderate, you have a parallelism bottleneck, not a raw-core-count bottleneck.
