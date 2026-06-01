"""The unified ``srmon`` command-line entry point.

Subcommand modules are imported here, but none import matplotlib at module load
(plotting imports it lazily inside its render functions), so building the parser
and running ``srmon summary`` stays dependency-free — important for the on-host
summary path, which has no third-party packages installed.
"""

import argparse
from typing import Optional, Sequence

from srmon import __version__
from srmon.analysis import peaks, summary, window
from srmon.export import csv_export
from srmon.ingest import download
from srmon.plotting import timeseries
from srmon.report import command as report_command


_COMMANDS = [
    ("download", download, "Download server logs over SSH into local-debug-logs/."),
    ("summary", summary, "Percentile summary of CPU / memory / swap / disk / network / GPU."),
    ("peaks", peaks, "List high-watermark CPU / memory / swap / process-RSS samples."),
    ("plot", timeseries, "Plot CPU / memory / swap over time to a PNG."),
    ("export", csv_export, "Export samples to CSV for spreadsheet plotting."),
    ("window", window, "Inspect samples in a time window around a timestamp."),
    ("report", report_command, "Monthly per-host report: capture, plot, peaks, summary."),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="srmon", description="System resource monitor toolkit.")
    parser.add_argument("--version", action="version", version=f"srmon {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for name, module, help_text in _COMMANDS:
        subparser = subparsers.add_parser(name, help=help_text, description=help_text)
        module.add_arguments(subparser)
        subparser.set_defaults(_run=module.run)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args._run(args)


if __name__ == "__main__":
    raise SystemExit(main())
