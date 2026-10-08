# System Resource Monitor

This project monitors the resources of Ubuntu servers for a long period. Use
the data to find the correct size for a server.

The project has two parts:

- A collector. It runs on each server.
- The `srmon` toolkit. It runs on your local machine. It downloads the logs and
  makes summaries, peak lists, plots, and monthly reports for each host.

## Two roles

- **Server:** A `systemd` service records CPU, memory, swap, disk, network,
  and GPU data every 10 seconds. It writes the data to
  `/var/log/system-resource-monitor/`. The service uses only the Python
  standard library. You do not need `pip` or third-party packages.
- **Local machine:** The `srmon` command downloads the logs from the servers.
  It makes summaries, peak lists, time-series plots, CSV files, and monthly
  reports from the logs.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/srmon/collect/` | The collector that runs on the server |
| `src/srmon/ingest/` | The SSH download |
| `src/srmon/core/` | Shared log selection, parse, and format code |
| `src/srmon/analysis/` | The `summary`, `peaks`, and `window` analysis |
| `src/srmon/export/` | The CSV export |
| `src/srmon/plotting/` | The matplotlib plots |
| `src/srmon/report/` | The monthly report |
| `src/srmon/cli/` | The `srmon` entry point |
| `scripts/` | The `systemd` install and uninstall scripts |
| `docs/system-resource-monitor.md` | The full operation manual |
| `hosts.example.toml` | An example host inventory for the monthly report |

## Install the collector on a server

1. Copy the repository to the server.
2. Run the install script:

   ```bash
   sudo sh scripts/install-system-resource-monitor.sh
   ```

The install script installs these items:

- The collector, at `/usr/local/bin/system-resource-monitor`
- The `srmon` package, at `/usr/local/lib/system-resource-monitor/`
- The `system-resource-monitor-summary` wrapper
- The configuration file, at `/etc/default/system-resource-monitor`
- The `system-resource-monitor.service` unit

The script then starts the service. The service uses only the Python standard
library.

To make sure that the service operates correctly, do these steps on the
server:

1. Examine the service status:

   ```bash
   systemctl status system-resource-monitor.service --no-pager
   ```

2. Examine the last 5 records in the log of today:

   ```bash
   tail -n 5 /var/log/system-resource-monitor/metrics-$(date +%F).jsonl
   ```

3. Show a summary of the data:

   ```bash
   system-resource-monitor-summary
   ```

## Install the toolkit on your local machine

You must have Python 3.11 or later.

We recommend that you install `srmon` as a global tool with `uv`. Then the
`srmon` command is on your `PATH`, and you do not need a virtual environment.
The `--editable` option makes `srmon` use your local code.

1. Install `srmon`:

   ```bash
   uv tool install --editable . --with matplotlib
   ```

2. Make sure that `~/.local/bin` is on your `PATH`. The `srmon` command is at
   `~/.local/bin/srmon`.

To upgrade `srmon`, run `uv tool upgrade srmon`.
To remove `srmon`, run `uv tool uninstall srmon`.

You can also use one of these methods:

- **pipx:** Run `pipx install -e ".[report]"`. This method also gives an
  isolated global command.
- **Virtual environment:** You do not have to activate the virtual
  environment. Run these commands:

  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -e ".[report]"
  ```

  Then run `.venv/bin/srmon ...`. Or, in the project directory, run
  `uv run srmon ...`.

## Make the monthly report

1. Copy `hosts.example.toml` to `hosts.toml`.
2. Add your servers to `hosts.toml`.
3. Run the report command:

   ```bash
   srmon report --month 2026-05
   ```

`srmon` finds `hosts.toml` automatically. It looks first in the current
directory, then in the repository root. If you do not give `--month`, `srmon`
uses the current month.

To use a different inventory file, add `--inventory PATH`. To make a report
for one host without an inventory file, give the host in the command:

```bash
srmon report --inventory other-hosts.toml --month 2026-05
srmon report robotruck@100.64.0.6 --month 2026-05
```

For each host, the report command does these steps:

1. It downloads the logs.
2. It plots the CPU, memory, and swap data.
3. It calculates the peaks.
4. It makes the summary.
5. It writes all the files to one directory:

```text
reports/<host>/<YYYY-MM>/
  report.md          # Open this file. It shows the plot, the peaks, and the summary.
  cpu-mem-swap.png
  summary.txt        # The raw summary, for grep or pbcopy
  peaks.txt
  spreadsheet.tsv    # One column of values for Excel or Google Sheets
  manifest.json      # The data that the report uses
```

### Report behavior

- **Month:** The default is the current month. To make a report for a
  completed month, use `--month YYYY-MM`.
- **Incremental reports:** `srmon` does not make a report for a host in these
  conditions:
  - The host has no data for the month.
  - A report for the host exists, and the host has no new data.
- **Options:** To make the report again, use `--force`. To use the logs that
  you downloaded before, and not use SSH, use `--skip-download`.
- **Coverage:** Some hosts do not record data every day. For these hosts, the
  report header shows fewer "days with data". This is not an error.
- **Index:** If you have more than one host, `srmon` also writes
  `reports/index-<YYYY-MM>.md`. This file has links to all the host reports.

## Individual commands

```bash
srmon download user@host                 # Download the logs with SSH into data/
srmon summary  --hostname H002           # Show p50, p95, p99, max, and spreadsheet values
srmon peaks    --hostname H002           # Show the top CPU, memory, swap, and process RSS samples
srmon plot     --hostname H002           # Make a PNG with 3 panels: CPU, memory, and swap
srmon export   --hostname H002           # Write a CSV file with 18 columns
srmon window   --hostname H002 --timestamp 2026-05-09T01:45:24Z
```

The analysis commands use these selection options:

| Option | Function |
| --- | --- |
| `--mode {auto,server,local}` | Select the log source. The default is `auto`: `srmon` uses the server logs if they exist. If not, it uses `data/`. |
| `--hostname` | Select the host. |
| `--start-date`, `--end-date` | Select the date range. |
| `--days` | Select the number of days. |
| `--log-dir` | Select the log directory. |

To copy the spreadsheet values to the clipboard on macOS, run this command:

```bash
srmon summary --hostname H002 --spreadsheet-values-only | pbcopy
```

## Monitored data

Each sample records these values:

- The total CPU usage, and the 1-minute, 5-minute, and 15-minute load average
- The used memory, the available memory, and the used swap
- The total disk read and write throughput of all monitored block devices
- The total network receive and transmit throughput of all interfaces,
  but not the loopback interface
- The top `N` threads by CPU usage in the sample interval
- The top `N` processes by memory usage
- The NVIDIA GPU usage and GPU memory usage, if `nvidia-smi` is available
- The NVIDIA compute processes by GPU memory usage, if this data is available

Read the data correctly:

- **Thread CPU:** The collector calculates thread CPU from the difference
  between two samples. Thus, `top_cpu_threads` shows the threads with the
  highest CPU usage in that interval.
- **Memory:** The collector records memory for each process, not for each
  thread. Linux does not give a useful RSS value for each thread.
- **Disk and network:** The values are the total throughput of the host. They
  are not the I/O of each process.

## Data storage

The collector writes the logs as newline-delimited JSON to
`/var/log/system-resource-monitor`:

- There is one file for each UTC day. The file name is
  `metrics-YYYY-MM-DD.jsonl`.
- Each line contains one JSON object.
- The collector deletes old log files. The `RETAIN_DAYS` setting controls the
  number of days that it keeps.

`srmon` merges the downloaded logs for each host into
`data/<hostname>_<start>_to_<end>.jsonl`. Git ignores the `data/` and
`reports/` directories.

## Installation details

The install script writes this default configuration to
`/etc/default/system-resource-monitor`:

```bash
INTERVAL_SECONDS=10
TOP_N=5
RETAIN_DAYS=30
LOG_DIR=/var/log/system-resource-monitor
```

To remove the service and the programs, run the uninstall script. This script
keeps the logs and the configuration file:

```bash
sudo sh scripts/uninstall-system-resource-monitor.sh
```

To also remove the configuration file and the logs, add `--purge`.

## Notes

- The collector operates on Ubuntu and other Linux systems with `systemd`.
  The collector and the summary use only `/proc`, `nvidia-smi`, and the
  Python standard library.
- matplotlib is necessary only on the local machine, and only for `srmon plot`
  and `srmon report`. `srmon` loads matplotlib only when it is necessary.
  Thus, `srmon summary` and the server summary do not need matplotlib.
- For the full operation instructions, refer to
  `docs/system-resource-monitor.md`.
