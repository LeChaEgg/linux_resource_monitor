"""Plot CPU / memory / swap utilization over time from resource-monitor logs.

matplotlib is imported lazily inside the render functions so that importing this
module (e.g. while building the srmon CLI parser, or on the dependency-free
server summary path) never requires matplotlib to be installed.
"""

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from srmon.core.samples import iter_samples, parse_iso_timestamp
from srmon.core.selection import add_log_selection_args, resolve_log_files


SeriesByHost = Dict[str, List[Tuple[datetime, float]]]


def collect_series(log_files: Iterable[Path]) -> Tuple[SeriesByHost, SeriesByHost, SeriesByHost]:
    return collect_series_from_samples(sample for _, _, sample in iter_samples(log_files))


def collect_series_from_samples(
    samples: Iterable[Dict[str, object]],
) -> Tuple[SeriesByHost, SeriesByHost, SeriesByHost]:
    cpu_by_host: SeriesByHost = defaultdict(list)
    mem_by_host: SeriesByHost = defaultdict(list)
    swap_by_host: SeriesByHost = defaultdict(list)

    for sample in samples:
        timestamp_raw = sample.get("timestamp")
        if not isinstance(timestamp_raw, str):
            continue
        try:
            sample_time = parse_iso_timestamp(timestamp_raw)
        except ValueError:
            continue
        host = str(sample.get("hostname", "unknown"))

        cpu = sample.get("cpu", {})
        if isinstance(cpu, dict) and cpu.get("used_pct") is not None:
            cpu_by_host[host].append((sample_time, float(cpu["used_pct"])))

        memory = sample.get("memory", {})
        if isinstance(memory, dict):
            if memory.get("mem_used_pct") is not None:
                mem_by_host[host].append((sample_time, float(memory["mem_used_pct"])))
            if memory.get("swap_used_pct") is not None:
                swap_by_host[host].append((sample_time, float(memory["swap_used_pct"])))

    return cpu_by_host, mem_by_host, swap_by_host


def plot_panel(ax, title: str, series_by_host: SeriesByHost) -> None:
    ax.set_title(title)
    ax.set_ylabel("%")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)
    if not series_by_host:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        return
    for host in sorted(series_by_host):
        points = sorted(series_by_host[host])
        times = [point[0] for point in points]
        values = [point[1] for point in points]
        ax.plot(times, values, linewidth=0.8, label=host)
    ax.legend(loc="upper right", fontsize="small")


def window_label(cpu_by_host: SeriesByHost, mem_by_host: SeriesByHost, swap_by_host: SeriesByHost) -> Optional[str]:
    all_times = [
        point[0]
        for series_by_host in (cpu_by_host, mem_by_host, swap_by_host)
        for points in series_by_host.values()
        for point in points
    ]
    if not all_times:
        return None
    return f"{min(all_times).date()} -> {max(all_times).date()}"


def default_output(log_dir: Path, hosts: List[str], window: Optional[str]) -> Path:
    parts = ["resource-timeseries"]
    if hosts:
        parts.append("-".join(hosts))
    if window:
        parts.append(window.replace(" -> ", "_to_"))
    return log_dir / ("_".join(parts) + ".png")


def render_series(
    cpu_by_host: SeriesByHost,
    mem_by_host: SeriesByHost,
    swap_by_host: SeriesByHost,
    output_path: Path,
    *,
    title: Optional[str] = None,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)
    plot_panel(axes[0], "CPU used %", cpu_by_host)
    plot_panel(axes[1], "Memory used %", mem_by_host)
    plot_panel(axes[2], "Swap used %", swap_by_host)

    axes[2].set_xlabel("time")
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()

    hosts = sorted(set(cpu_by_host) | set(mem_by_host) | set(swap_by_host))
    window = window_label(cpu_by_host, mem_by_host, swap_by_host)
    label = title or window
    suptitle = "Resource utilization"
    if hosts:
        suptitle += f"  hosts={', '.join(hosts)}"
    if label:
        suptitle += f"  window={label}"
    fig.suptitle(suptitle)
    fig.tight_layout(rect=(0, 0, 1, 0.98))

    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120)
    plt.close(fig)
    return output


def render_timeseries_figure(log_files: Iterable[Path], output_path: Path, *, title: Optional[str] = None) -> Optional[Path]:
    """Collect series and render a PNG. Returns the path, or None if no samples.

    Reusable by the monthly report.
    """
    return render_timeseries_from_samples(
        (sample for _, _, sample in iter_samples(log_files)), output_path, title=title
    )


def render_timeseries_from_samples(
    samples: Iterable[Dict[str, object]], output_path: Path, *, title: Optional[str] = None
) -> Optional[Path]:
    """Render the PNG from already-parsed samples. Returns the path, or None if empty."""
    cpu_by_host, mem_by_host, swap_by_host = collect_series_from_samples(samples)
    if not (cpu_by_host or mem_by_host or swap_by_host):
        return None
    return render_series(cpu_by_host, mem_by_host, swap_by_host, output_path, title=title)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_log_selection_args(parser)
    parser.add_argument(
        "--output",
        default=None,
        help="Output image path. Defaults to resource-timeseries.png in the log directory.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Optional figure title. Defaults to the resolved date window and hosts.",
    )


def run(args: argparse.Namespace) -> int:
    log_dir, log_files = resolve_log_files(args)
    if not log_files:
        print(f"No log files found in {log_dir}")
        return 0

    cpu_by_host, mem_by_host, swap_by_host = collect_series(log_files)
    if not (cpu_by_host or mem_by_host or swap_by_host):
        print("No samples found in the requested window.")
        return 0

    hosts = sorted(set(cpu_by_host) | set(mem_by_host) | set(swap_by_host))
    window = window_label(cpu_by_host, mem_by_host, swap_by_host)
    output = Path(args.output).expanduser() if args.output else default_output(log_dir, hosts, window)
    render_series(cpu_by_host, mem_by_host, swap_by_host, output, title=args.title)
    print(f"Wrote {output}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plot CPU / memory / swap utilization over time from resource-monitor logs."
    )
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
