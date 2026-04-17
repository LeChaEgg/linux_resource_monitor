# System Resource Monitor

Lightweight Ubuntu resource monitoring for long-run server sizing.

## Contents

- `scripts/resource_monitor.py`: CPU, memory, swap, disk, network, GPU, and top hot-thread sampler
- `scripts/install-system-resource-monitor.sh`: systemd installer for Ubuntu
- `scripts/uninstall-system-resource-monitor.sh`: systemd uninstaller with optional purge
- `scripts/summarize_resource_monitor.py`: percentile-based log summarizer
- `docs/system-resource-monitor.md`: install and operating notes

## Quick Start

```bash
git clone <your-remote-url>
cd system-resource-monitor
sudo sh scripts/install-system-resource-monitor.sh
```

The installer will:

- install `system-resource-monitor` to `/usr/local/bin/system-resource-monitor`
- install `system-resource-monitor-summary` to `/usr/local/bin/system-resource-monitor-summary`
- create `/etc/default/system-resource-monitor`
- create and enable `system-resource-monitor.service`
- start the service immediately and again on future boots

After the service has collected data, summarize it with:

```bash
system-resource-monitor-summary --log-dir /var/log/system-resource-monitor --days 7
```

## What It Monitors

Each sample records:

- aggregate CPU usage and 1m / 5m / 15m load average
- memory used / available and swap used
- aggregate disk read / write throughput across monitored block devices
- aggregate network receive / transmit throughput across non-loopback interfaces
- top `N` CPU-consuming threads for the sample interval
- top `N` memory-consuming processes
- NVIDIA GPU overall utilization and memory usage when `nvidia-smi` is available
- NVIDIA compute processes by GPU memory usage when available

Important interpretation notes:

- thread CPU is sampled by interval delta, so `top_cpu_threads` reflects what was hottest during that window
- memory is recorded at process level, not true thread-level memory, because Linux does not expose thread RSS meaningfully
- disk and network are aggregate host-level throughput, not per-process I/O

## Data Storage

Logs are written as newline-delimited JSON under:

```bash
/var/log/system-resource-monitor
```

File layout:

- one file per UTC day
- filename format: `metrics-YYYY-MM-DD.jsonl`
- one JSON object per line
- old daily log files are pruned according to `RETAIN_DAYS`

Default config written at install time:

```bash
INTERVAL_SECONDS=10
TOP_N=5
RETAIN_DAYS=30
LOG_DIR=/var/log/system-resource-monitor
```

## Log Shape

Each line in `metrics-YYYY-MM-DD.jsonl` looks like:

```json
{
  "schema_version": 2,
  "timestamp": "2026-04-16T01:23:45Z",
  "hostname": "server-a",
  "boot_id": "0c74c0e8-4f7d-4be4-8f30-aaaaaaaaaaaa",
  "sample_interval_seconds": 10.0,
  "cpu": {
    "logical_cpu_count": 32,
    "used_pct": 61.7,
    "loadavg_1m": 18.2,
    "loadavg_5m": 15.4,
    "loadavg_15m": 12.8
  },
  "memory": {
    "mem_total_bytes": 270582939648,
    "mem_available_bytes": 88267988992,
    "mem_used_bytes": 182314950656,
    "mem_used_pct": 67.38,
    "swap_total_bytes": 34359734272,
    "swap_used_bytes": 0,
    "swap_used_pct": 0.0
  },
  "disk": {
    "device_count": 3,
    "read_bytes": 1234567890,
    "write_bytes": 987654321,
    "read_bytes_per_sec": 1048576.0,
    "write_bytes_per_sec": 524288.0
  },
  "network": {
    "interface_count": 2,
    "rx_bytes": 7777777,
    "tx_bytes": 3333333,
    "rx_bytes_per_sec": 262144.0,
    "tx_bytes_per_sec": 131072.0
  },
  "top_cpu_threads": [
    {
      "pid": 1821,
      "tid": 1830,
      "process_name": "python3",
      "thread_name": "DataLoader",
      "state": "R",
      "cpu_pct": 92.4,
      "cpu_time_seconds": 9.24,
      "process_rss_bytes": 6249807872,
      "interval_seconds": 10.0
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
    "detected": true,
    "devices": [
      {
        "index": 0,
        "uuid": "GPU-xxxxxxxx",
        "name": "NVIDIA GeForce RTX 3090",
        "utilization_gpu_pct": 73.0,
        "utilization_memory_pct": 41.0,
        "memory_total_mib": 24268.0,
        "memory_used_mib": 4096.0,
        "memory_used_pct": 16.88,
        "temperature_c": 61.0,
        "power_draw_w": 248.0
      }
    ],
    "processes": [
      {
        "pid": 1821,
        "process_name": "python3",
        "gpu_uuid": "GPU-xxxxxxxx",
        "used_gpu_memory_mib": 4096.0
      }
    ]
  }
}
```

## Useful Commands

Check service state:

```bash
systemctl status system-resource-monitor.service --no-pager
```

Tail today's raw samples:

```bash
tail -n 5 /var/log/system-resource-monitor/metrics-$(date +%F).jsonl
```

Summarize the last 7 days:

```bash
system-resource-monitor-summary --log-dir /var/log/system-resource-monitor --days 7
```

Take one manual sample into a temp directory:

```bash
sudo /usr/local/bin/system-resource-monitor --once --interval 1 --top-n 5 --log-dir /tmp/system-resource-monitor
```

To remove the service and binaries while keeping logs and config:

```bash
sudo sh scripts/uninstall-system-resource-monitor.sh
```

To fully roll back and delete logs and config too:

```bash
sudo sh scripts/uninstall-system-resource-monitor.sh --purge
```

## Notes

- Designed for Ubuntu/Linux with `systemd`
- Uses `/proc` and `nvidia-smi`; no Python third-party dependencies
- Tracks top CPU threads, top memory processes, plus aggregate disk and network throughput
- Full operating notes are in `docs/system-resource-monitor.md`
