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

After the service has collected data, summarize it with:

```bash
system-resource-monitor-summary --log-dir /var/log/system-resource-monitor --days 7
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
