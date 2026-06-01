# System Resource Monitor

Lightweight Ubuntu resource monitoring for long-run server sizing, plus a local
toolkit (`srmon`) for downloading logs and producing summaries, peaks, plots, and
**one-command monthly per-host reports**.

## Two roles

- **On each server** — a `systemd` service samples CPU, memory, swap, disk,
  network, and GPU every 10 seconds into `/var/log/system-resource-monitor/`.
  Standard library only; no `pip`, no third-party packages.
- **On your workstation** — the `srmon` command downloads those logs and turns
  them into summaries, peak lists, time-series plots, CSVs, and monthly reports.

## Repository layout

- `src/srmon/` — the Python package
  - `collect/` the on-host sampler · `ingest/` SSH download · `core/` shared log
    selection/parsing/formatting · `analysis/` summary, peaks, window ·
    `export/` CSV · `plotting/` matplotlib · `report/` the monthly report ·
    `cli/` the `srmon` entry point
- `scripts/` — the systemd installer and uninstaller
  (`install-system-resource-monitor.sh`, `uninstall-system-resource-monitor.sh`)
- `docs/system-resource-monitor.md` — full operating manual
- `hosts.example.toml` — copy to `hosts.toml` for the monthly report inventory

## Install on a server

```bash
sudo sh scripts/install-system-resource-monitor.sh
```

This installs the collector to `/usr/local/bin/system-resource-monitor`, the
`srmon` package to `/usr/local/lib/system-resource-monitor/`, a
`system-resource-monitor-summary` wrapper, `/etc/default/system-resource-monitor`,
and the `system-resource-monitor.service` unit, then starts logging. The runtime
uses only the Python standard library.

On the server:

```bash
systemctl status system-resource-monitor.service --no-pager
tail -n 5 /var/log/system-resource-monitor/metrics-$(date +%F).jsonl
system-resource-monitor-summary
```

## Set up the workstation toolkit (Python ≥ 3.11)

Recommended — install `srmon` as a global tool so it is on your `PATH` everywhere,
with no virtualenv to activate (and it tracks your local code via `--editable`):

```bash
uv tool install --editable . --with matplotlib
```

`srmon` then lives in `~/.local/bin/srmon`. Upgrade with `uv tool upgrade srmon`,
remove with `uv tool uninstall srmon`. (Ensure `~/.local/bin` is on your `PATH`.)

Alternatives:

- `pipx install -e ".[report]"` — same idea, isolated global CLI.
- A virtualenv, **without activating it** — call the binary directly:
  `python3 -m venv .venv && .venv/bin/pip install -e ".[report]"`, then run
  `.venv/bin/srmon ...`, or `uv run srmon ...` from the project directory.

## Monthly report — the one command

Copy `hosts.example.toml` to `hosts.toml` and list your servers. A `hosts.toml`
in the current directory or the repo root is picked up automatically, so the
everyday command is just:

```bash
srmon report --month 2026-05      # all hosts in hosts.toml; omit --month for the current month
```

Point at a different inventory with `--inventory PATH`, or run a single host
directly without any inventory:

```bash
srmon report --inventory other-hosts.toml --month 2026-05
srmon report robotruck@100.64.0.6 --month 2026-05
```

For each host this **downloads** the logs, **plots** CPU/memory/swap, computes the
**peaks**, builds the **summary**, and bundles everything into:

```text
reports/<host>/<YYYY-MM>/
  report.md          # open this: the plot is embedded, with peaks and summary below
  cpu-mem-swap.png
  summary.txt        # raw summary (grep / pbcopy)
  peaks.txt
  spreadsheet.tsv    # one-column values for Excel / Google Sheets
  manifest.json      # what data the report was built from
```

Behavior:

- Defaults to the **current** month; pass `--month YYYY-MM` for a finished month.
- **Incremental**: a host with no in-month data is skipped (no empty report), and a
  host already reported with no new data is skipped. Pass `--force` to regenerate
  anyway, or `--skip-download` to reuse already-downloaded logs without SSH.
- **Coverage is factual.** Hosts that don't record every day simply show fewer
  "days with data" in the report header — that is expected, not an error.
- With more than one host, a `reports/index-<YYYY-MM>.md` roll-up links them all.

## Individual commands

```bash
srmon download user@host                 # SSH-pull logs into data/
srmon summary  --hostname H002           # p50/p95/p99/max + spreadsheet values
srmon peaks    --hostname H002           # top CPU / memory / swap / process-RSS samples
srmon plot     --hostname H002           # 3-panel CPU/MEM/Swap PNG
srmon export   --hostname H002           # 18-column CSV
srmon window   --hostname H002 --timestamp 2026-05-09T01:45:24Z
```

Shared selection flags for the analysis commands: `--mode {auto,server,local}`
(default `auto`: server logs when present, else `data/`), `--hostname`,
`--start-date` / `--end-date`, `--days`, `--log-dir`.

Spreadsheet paste on macOS:

```bash
srmon summary --hostname H002 --spreadsheet-values-only | pbcopy
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

Logs are written as newline-delimited JSON under `/var/log/system-resource-monitor`:

- one file per UTC day, named `metrics-YYYY-MM-DD.jsonl`
- one JSON object per line
- old daily log files are pruned according to `RETAIN_DAYS`

Downloaded logs are merged per host into `data/<hostname>_<start>_to_<end>.jsonl`
(both `data/` and `reports/` are gitignored).

## Install Details

The installer writes this default config to `/etc/default/system-resource-monitor`:

```bash
INTERVAL_SECONDS=10
TOP_N=5
RETAIN_DAYS=30
LOG_DIR=/var/log/system-resource-monitor
```

To remove the service and binaries while keeping logs and config:

```bash
sudo sh scripts/uninstall-system-resource-monitor.sh
```

Add `--purge` to also remove the config and logs.

## Notes

- Designed for Ubuntu/Linux with `systemd`. The collector and the summary path use only `/proc`, `nvidia-smi`, and the Python standard library.
- matplotlib is required **only** on the workstation, and **only** for `srmon plot` and `srmon report`; it is imported lazily, so `srmon summary` and the on-server summary never need it.
- Full operating notes are in `docs/system-resource-monitor.md`.
```
