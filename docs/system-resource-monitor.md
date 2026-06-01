# Ubuntu Resource Monitor

## Goal

This tool is for long-run capacity sizing on Ubuntu servers:

- record CPU, memory, disk, network, and GPU usage every 10 seconds
- start automatically at boot
- keep daily log files for later analysis
- capture the hottest threads and memory-heavy processes

The collector is `src/srmon/collect/monitor.py`; the installer is
`scripts/install-system-resource-monitor.sh` and the uninstaller is
`scripts/uninstall-system-resource-monitor.sh`. The local toolkit is the `srmon`
package under `src/srmon/`: summaries in `srmon/analysis/summary.py`, peaks in
`srmon/analysis/peaks.py`, plotting in `srmon/plotting/timeseries.py`, the SSH
downloader in `srmon/ingest/download.py`, the monthly report in `srmon/report/`,
and shared log selection in `srmon/core/`. Every command is reachable via the
unified `srmon` CLI (or `python3 -m srmon <command>`).

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
- aggregate disk read / write throughput across monitored block devices
- aggregate network receive / transmit throughput across non-loopback interfaces
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
2. install the summary command to `/usr/local/bin/system-resource-monitor-summary`
3. install the `srmon` package to `/usr/local/lib/system-resource-monitor/` for the summary command (standard library only)
4. create `/etc/default/system-resource-monitor`
5. create and enable `system-resource-monitor.service`
6. start logging immediately and on every future boot

## Uninstall or roll back

Remove the service and installed binaries but keep config and logs:

```bash
sudo sh /path/to/system-resource-monitor/scripts/uninstall-system-resource-monitor.sh
```

Fully remove the installed state, including `/etc/default/system-resource-monitor` and `/var/log/system-resource-monitor`:

```bash
sudo sh /path/to/system-resource-monitor/scripts/uninstall-system-resource-monitor.sh --purge
```

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

## Local log analysis workflow

Install the workstation toolkit once (Python >= 3.11). The recommended path puts
`srmon` on your `PATH` with no virtualenv to activate:

```bash
uv tool install --editable . --with matplotlib
```

(Alternatives: `pipx install -e ".[report]"`, or a venv used without activating it
via `.venv/bin/srmon` / `uv run srmon`.) matplotlib is needed only for plotting and
the monthly report.

### Monthly report (single command)

The fastest path downloads, plots, finds peaks, and summarizes a host in one step.
A `hosts.toml` in the current directory or repo root is used automatically:

```bash
srmon report --month 2026-05                       # every host in hosts.toml
srmon report --inventory other.toml --month 2026-05 # a different inventory
srmon report robotruck@100.64.0.6 --month 2026-05   # a single host, no inventory
```

Output lands in `reports/<host>/<YYYY-MM>/report.md` with the CPU/memory/swap plot
embedded above the peaks and summary. It defaults to the current month and is
incremental: hosts with no new in-month data are skipped (`--force` overrides,
`--skip-download` reuses already-downloaded logs). Hosts that do not record every
day simply show fewer "days with data" — coverage is reported as-is.

### Individual steps

When you need to investigate an incident, download the remote logs into the
repo-local `local-debug-logs/` directory (ignored by git):

```bash
srmon download robotruck@100.64.0.6
```

The downloader reads all remote `metrics-YYYY-MM-DD.jsonl` files from
`/var/log/system-resource-monitor`, detects the hostname, and writes a combined
local file named like `local-debug-logs/server-a_2026-04-20_to_2026-04-30.jsonl`,
merging into any existing file for that host, skipping duplicate rows, and renaming
when the date range expands. Use `--remote-log-dir`, `--port`, `--identity-file`,
or repeated `--ssh-option` values for connection details.

The analysis commands default to `--mode auto` and the most recent 30 recorded
days. Auto reads `/var/log/system-resource-monitor` when present; otherwise the
newest combined file in `local-debug-logs/`. Use `--mode server` or `--mode local`
to force one, and narrow local logs by hostname and date range:

```bash
srmon summary --hostname server-a
srmon peaks   --mode local --hostname server-a --start-date 2026-04-20 --end-date 2026-04-30
srmon window  --mode local --hostname server-a --timestamp 2026-04-20T01:45:24Z --minutes-before 15 --minutes-after 15
srmon export  --mode local --hostname server-a --start-date 2026-04-20 --end-date 2026-04-30
srmon plot    --mode local --hostname server-a
```

`srmon export` writes `local-debug-logs/resource-monitor_<host>_<start>_to_<end>.csv`
by default (`--output FILE` for a custom path, `--output -` for stdout). Point any
command at a custom directory with `--log-dir /path/to/logs`.

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

Summarize the most recent 30 recorded days for sizing:

```bash
system-resource-monitor-summary
```

The normal summary also ends with a `Spreadsheet Values` block. To print only that one-column value list for Excel or Google Sheets:

```bash
system-resource-monitor-summary --spreadsheet-values-only
```

The values-only mode uses CRLF row separators for spreadsheet clipboard compatibility. On macOS, this copies the values directly:

```bash
system-resource-monitor-summary --spreadsheet-values-only | pbcopy
```

For a specific downloaded host on your workstation, put `--hostname` before the pipe:

```bash
srmon summary --mode local --hostname server-a --spreadsheet-values-only | pbcopy
```

The same command run from a source checkout without installing `srmon`:

```bash
uv run srmon summary --hostname server-a
```

`--days 30` selects the most recent 30 log days that actually exist under the selected log directory, so gaps on days when the system was off are ignored.

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
  "disk": {
    "read_bytes_per_sec": 1048576.0,
    "write_bytes_per_sec": 524288.0
  },
  "network": {
    "rx_bytes_per_sec": 262144.0,
    "tx_bytes_per_sec": 131072.0
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
