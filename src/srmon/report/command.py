"""CLI glue for the monthly ``srmon report`` command."""

import argparse
from datetime import date
from pathlib import Path
from typing import List, Optional, Sequence

from srmon.core.selection import DEFAULT_DOWNLOADED_LOG_DIR
from srmon.core.timerange import current_month, month_bounds
from srmon.report.builder import build_reports
from srmon.report.inventory import HostSpec, host_spec_from_target, load_inventory
from srmon.report.models import HostReportOutcome


REPO_ROOT = DEFAULT_DOWNLOADED_LOG_DIR.parent
DEFAULT_REPORTS_DIR = REPO_ROOT / "reports"
DEFAULT_INVENTORY_NAME = "hosts.toml"


def find_default_inventory(search_dirs=None) -> "Optional[Path]":
    """Return the first hosts.toml found in the current dir, then the repo root."""
    if search_dirs is None:
        search_dirs = [Path.cwd(), REPO_ROOT]
    for directory in search_dirs:
        candidate = Path(directory) / DEFAULT_INVENTORY_NAME
        if candidate.exists():
            return candidate
    return None


_STATUS_MARKERS = {
    "generated": "[+]",
    "skipped_unchanged": "[=]",
    "skipped_no_data": "[.]",
    "error": "[x]",
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "servers",
        nargs="*",
        help="SSH targets, for example robotruck@100.64.0.6. Omit when using --inventory.",
    )
    parser.add_argument(
        "--inventory",
        default=None,
        help=(
            "Path to a hosts.toml inventory listing all hosts. If omitted and no "
            "user@host is given, hosts.toml in the current directory or repo root is used."
        ),
    )
    parser.add_argument(
        "--month",
        default=None,
        help="Report month in YYYY-MM format. Default: the current month.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_REPORTS_DIR),
        help=f"Root directory for report output. Default: {DEFAULT_REPORTS_DIR}",
    )
    parser.add_argument(
        "--downloaded-log-dir",
        default=str(DEFAULT_DOWNLOADED_LOG_DIR),
        help=f"Directory holding downloaded combined logs. Default: {DEFAULT_DOWNLOADED_LOG_DIR}",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Reuse already-downloaded logs instead of connecting over SSH.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate reports even when a host has no new in-month data.",
    )
    parser.add_argument("--peak-limit", type=int, default=5, help="Rows to show per peak section. Default: 5")
    # SSH options applied to positional user@host targets (inventory entries carry their own).
    parser.add_argument("--remote-log-dir", default=None, help="Remote log directory for positional targets.")
    parser.add_argument("--port", type=int, default=None, help="SSH port for positional targets.")
    parser.add_argument("--identity-file", default=None, help="SSH private key for positional targets.")
    parser.add_argument(
        "--ssh-option",
        action="append",
        default=[],
        help="Extra ssh -o option for positional targets. Repeatable.",
    )


def _collect_specs(args: argparse.Namespace, inventory_path: "Optional[Path]") -> List[HostSpec]:
    specs: List[HostSpec] = []
    if inventory_path:
        specs.extend(load_inventory(Path(inventory_path)))
    for target in args.servers:
        specs.append(
            host_spec_from_target(
                target,
                identity_file=args.identity_file,
                port=args.port,
                remote_log_dir=args.remote_log_dir,
                ssh_options=args.ssh_option,
            )
        )
    return specs


def run(args: argparse.Namespace) -> int:
    month = args.month or current_month(date.today())
    try:
        month_bounds(month)
    except ValueError as exc:
        raise SystemExit(str(exc))

    if args.peak_limit <= 0:
        raise SystemExit("--peak-limit must be greater than 0")

    inventory_path = Path(args.inventory) if args.inventory else None
    if inventory_path is None and not args.servers:
        inventory_path = find_default_inventory()

    specs = _collect_specs(args, inventory_path)
    if not specs:
        raise SystemExit(
            "No hosts to report on. Pass one or more user@host targets, --inventory PATH, "
            f"or create {DEFAULT_INVENTORY_NAME} in the current directory or {REPO_ROOT}."
        )

    if inventory_path and not args.servers:
        print(f"Inventory: {inventory_path}")

    outcomes = build_reports(
        specs,
        month=month,
        out_dir=Path(args.out_dir),
        download=not args.skip_download,
        force=args.force,
        peak_limit=args.peak_limit,
        downloaded_log_dir=Path(args.downloaded_log_dir),
    )

    print(f"Monthly report — {month}")
    for outcome in outcomes:
        marker = _STATUS_MARKERS.get(outcome.status, "[?]")
        print(f"  {marker} {outcome.label}: {outcome.status} — {outcome.detail}")
        if outcome.status == "generated" and outcome.report_path is not None:
            print(f"        {outcome.report_path}")

    if outcomes and all(outcome.status == "error" for outcome in outcomes):
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a monthly per-host resource report (capture, plot, peaks, summary)."
    )
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
