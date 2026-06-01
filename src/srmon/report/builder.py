"""Build the monthly per-host report: capture -> plot -> peak -> summary.

Incremental by design:
- a host with no in-month samples is skipped (no empty report);
- a host whose in-month data signature matches the existing manifest is skipped
  ("already reported, no new data");
- otherwise the report is regenerated. --force overrides the skip.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from srmon.analysis.peaks import build_peak_report
from srmon.analysis.summary import build_report
from srmon.core.naming import parse_combined_log_file_date_range
from srmon.core.samples import iter_samples, parse_sample_timestamp_date
from srmon.core.selection import DEFAULT_DOWNLOADED_LOG_DIR, LogFiles
from srmon.core.timerange import date_range_set, month_bounds, ranges_overlap
from srmon.ingest.download import (
    DEFAULT_REMOTE_LOG_DIR,
    download_host_logs,
    find_existing_host_logs,
    sanitize_hostname,
)
from srmon.plotting.timeseries import render_timeseries_figure
from srmon.report.inventory import HostSpec
from srmon.report.models import HostReportOutcome, ReportBundle
from srmon.report.render_md import write_report_dir


PNG_NAME = "cpu-mem-swap.png"
MANIFEST_NAME = "manifest.json"


def host_part(ssh: str) -> str:
    return ssh.split("@", 1)[-1]


def report_dir_for(out_dir: Path, label: str, month: str) -> Path:
    return Path(out_dir) / sanitize_hostname(label) / month


def select_month_log_files(
    downloaded_log_dir: Path, sanitized_hostname: str, month_start: date, month_end: date
) -> LogFiles:
    """The host's combined log file(s) overlapping the month, scoped to in-month dates.

    Selects by filename (host-specific) and applies an in-month date filter via
    LogFiles.selected_dates_by_path. No sample-level hostname filter is set, so a
    raw-vs-sanitized hostname mismatch can never silently drop every sample.
    """
    selected_dates = date_range_set(month_start, month_end)
    chosen: List[Path] = []
    dates_by_path: Dict[Path, object] = {}
    for path in find_existing_host_logs(Path(downloaded_log_dir), sanitized_hostname):
        date_range = parse_combined_log_file_date_range(path)
        if date_range is None:
            continue
        if not ranges_overlap(date_range[0], date_range[1], month_start, month_end):
            continue
        chosen.append(path)
        dates_by_path[path] = selected_dates
    return LogFiles(chosen, dates_by_path, hostname_filter=None)


def month_data_signature(log_files: LogFiles) -> Tuple[int, Optional[str], List[str]]:
    """(sample_count, max_timestamp, sorted in-month date strings)."""
    count = 0
    max_ts: Optional[str] = None
    dates = set()
    for _, _, sample in iter_samples(log_files):
        count += 1
        timestamp = sample.get("timestamp")
        if isinstance(timestamp, str) and (max_ts is None or timestamp > max_ts):
            max_ts = timestamp
        sample_date = parse_sample_timestamp_date(sample)
        if sample_date is not None:
            dates.add(sample_date)
    return count, max_ts, [d.isoformat() for d in sorted(dates)]


def read_manifest(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def signature_matches(manifest: dict, sample_count: int, max_ts: Optional[str], covered_dates: List[str]) -> bool:
    return (
        manifest.get("sample_count") == sample_count
        and manifest.get("max_timestamp") == max_ts
        and manifest.get("covered_dates") == covered_dates
    )


def _utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def build_host_report(
    spec: HostSpec,
    *,
    month: str,
    out_dir: Path,
    download: bool = True,
    force: bool = False,
    peak_limit: int = 5,
    downloaded_log_dir: Path = DEFAULT_DOWNLOADED_LOG_DIR,
    generated_at: Optional[str] = None,
) -> HostReportOutcome:
    month_start, month_end = month_bounds(month)
    downloaded_log_dir = Path(downloaded_log_dir)

    resolved_hostname: Optional[str] = None
    if download:
        result = download_host_logs(
            spec.ssh,
            output_dir=downloaded_log_dir,
            remote_log_dir=spec.remote_log_dir or DEFAULT_REMOTE_LOG_DIR,
            hostname=spec.name,
            port=spec.port,
            identity_file=spec.identity_file,
            ssh_options=spec.ssh_options,
        )
        resolved_hostname = result.hostname
    if resolved_hostname is None:
        resolved_hostname = sanitize_hostname(spec.name or host_part(spec.ssh))

    label = spec.name or resolved_hostname

    log_files = select_month_log_files(downloaded_log_dir, resolved_hostname, month_start, month_end)
    sample_count, max_ts, covered_dates = month_data_signature(log_files)

    if sample_count == 0:
        return HostReportOutcome(
            label=label,
            status="skipped_no_data",
            detail=f"no samples for {month} under {downloaded_log_dir}",
        )

    rdir = report_dir_for(out_dir, label, month)
    manifest_path = rdir / MANIFEST_NAME
    if not force and manifest_path.exists():
        existing = read_manifest(manifest_path)
        if existing is not None and signature_matches(existing, sample_count, max_ts, covered_dates):
            return HostReportOutcome(
                label=label,
                status="skipped_unchanged",
                detail=f"already reported ({sample_count} samples, no new data)",
                report_path=rdir / "report.md",
            )

    rdir.mkdir(parents=True, exist_ok=True)

    rendered_png = render_timeseries_figure(log_files, rdir / PNG_NAME, title=None)
    png_name = PNG_NAME if rendered_png is not None else None

    bundle = ReportBundle(
        label=label,
        ssh=spec.ssh,
        month=month,
        month_start=month_start,
        month_end=month_end,
        covered_start=date.fromisoformat(covered_dates[0]) if covered_dates else None,
        covered_end=date.fromisoformat(covered_dates[-1]) if covered_dates else None,
        days_with_data=len(covered_dates),
        sample_count=sample_count,
        peak_limit=peak_limit,
        summary_text=build_report(log_files),
        peaks_text=build_peak_report(log_files, peak_limit),
        spreadsheet_text=build_report(log_files, spreadsheet_values_only=True),
        png_name=png_name,
        generated_at=generated_at or _utc_today_iso(),
    )
    report_path = write_report_dir(rdir, bundle)

    manifest = {
        "host": label,
        "month": month,
        "sample_count": sample_count,
        "max_timestamp": max_ts,
        "covered_dates": covered_dates,
        "generated_at": bundle.generated_at,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return HostReportOutcome(
        label=label,
        status="generated",
        detail=f"{sample_count} samples across {len(covered_dates)} days",
        report_path=report_path,
    )


def write_month_index(out_dir: Path, month: str, outcomes: List[HostReportOutcome]) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# Monthly Resource Reports — {month}", ""]
    for outcome in outcomes:
        if outcome.report_path is not None and outcome.status in {"generated", "skipped_unchanged"}:
            rel = outcome.report_path.relative_to(out_dir)
            lines.append(f"- [{outcome.label}]({rel}) — {outcome.detail}")
        else:
            lines.append(f"- {outcome.label} — {outcome.status}: {outcome.detail}")
    lines.append("")
    index_path = out_dir / f"index-{month}.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def build_reports(
    specs: List[HostSpec],
    *,
    month: str,
    out_dir: Path,
    download: bool = True,
    force: bool = False,
    peak_limit: int = 5,
    downloaded_log_dir: Path = DEFAULT_DOWNLOADED_LOG_DIR,
) -> List[HostReportOutcome]:
    outcomes: List[HostReportOutcome] = []
    for spec in specs:
        try:
            outcomes.append(
                build_host_report(
                    spec,
                    month=month,
                    out_dir=out_dir,
                    download=download,
                    force=force,
                    peak_limit=peak_limit,
                    downloaded_log_dir=downloaded_log_dir,
                )
            )
        except Exception as exc:  # isolate per-host failures
            outcomes.append(
                HostReportOutcome(label=spec.name or host_part(spec.ssh), status="error", detail=str(exc))
            )

    written = [o for o in outcomes if o.report_path is not None]
    if len(written) > 1:
        write_month_index(out_dir, month, outcomes)
    return outcomes
