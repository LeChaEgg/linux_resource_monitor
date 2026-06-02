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
from typing import Callable, Dict, List, Optional, Tuple

from srmon.analysis.peaks import build_peak_report_from_samples
from srmon.analysis.summary import build_report_from_samples
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
from srmon.plotting.timeseries import render_timeseries_from_samples
from srmon.report.inventory import HostSpec
from srmon.report.models import HostReportOutcome, ReportBundle
from srmon.report.render_md import write_report_dir


PNG_NAME = "cpu-mem-swap.png"
MANIFEST_NAME = "manifest.json"


def _noop_progress(message: str) -> None:
    pass


def host_part(ssh: str) -> str:
    return ssh.split("@", 1)[-1]


def local_store_has_full_month(
    downloaded_log_dir: Path, sanitized_hostname: str, month_start: date, month_end: date
) -> bool:
    """True if the local store already holds this host's data through month_end.

    Requires the store to actually contain in-month data (its range overlaps the
    month) AND to extend to or past month_end — i.e. there is no newer in-month data
    to fetch and nothing to backfill. Only then is connecting over SSH pointless and
    the report identical offline. A store that ends in a later month but has *no*
    data inside this month (so the month must be backfilled) does NOT qualify.
    """
    for path in find_existing_host_logs(Path(downloaded_log_dir), sanitized_hostname):
        date_range = parse_combined_log_file_date_range(path)
        if date_range is None:
            continue
        local_start, local_end = date_range
        if ranges_overlap(local_start, local_end, month_start, month_end) and local_end >= month_end:
            return True
    return False


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


def load_month_samples(log_files: LogFiles) -> Tuple[List[Dict[str, object]], int]:
    """Parse the in-month samples once, returning (samples, skipped_invalid_count).

    This is the single read of the (potentially huge) per-host log; the summary,
    peaks, and plot are all built from the returned list instead of each re-reading
    and re-parsing the file. Invalid lines are warned about exactly once, here.
    """
    samples: List[Dict[str, object]] = []
    skipped_invalid = 0

    def count_invalid(_path, _line_number, _exc) -> None:
        nonlocal skipped_invalid
        skipped_invalid += 1

    for _, _, sample in iter_samples(log_files, on_invalid=count_invalid):
        samples.append(sample)
    return samples, skipped_invalid


def month_data_signature(samples: List[Dict[str, object]]) -> Tuple[int, Optional[str], List[str]]:
    """(sample_count, max_timestamp, sorted in-month date strings) from parsed samples."""
    max_ts: Optional[str] = None
    dates = set()
    for sample in samples:
        timestamp = sample.get("timestamp")
        if isinstance(timestamp, str) and (max_ts is None or timestamp > max_ts):
            max_ts = timestamp
        sample_date = parse_sample_timestamp_date(sample)
        if sample_date is not None:
            dates.add(sample_date)
    return len(samples), max_ts, [d.isoformat() for d in sorted(dates)]


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
    progress: Optional[Callable[[str], None]] = None,
) -> HostReportOutcome:
    progress = progress or _noop_progress
    month_start, month_end = month_bounds(month)
    downloaded_log_dir = Path(downloaded_log_dir)
    display = spec.name or host_part(spec.ssh)

    resolved_hostname: Optional[str] = None
    download_attempted = False
    remote_earliest: Optional[date] = None
    if download:
        # When the host label is known up front (inventory entries carry one) we can
        # tell, without any SSH, whether the local store already holds this month and
        # skip the network round-trip entirely.
        local_key = sanitize_hostname(spec.name) if spec.name else None
        if local_key is not None and local_store_has_full_month(
            downloaded_log_dir, local_key, month_start, month_end
        ):
            progress(f"{display}: local data already covers {month}; skipping download")
        else:
            download_attempted = True
            progress(f"{display}: downloading {month} data from {spec.ssh} …")
            # Scope the fetch to the reported month so an older month is backfilled
            # (not ignored) and a forward run does not pull unrelated newer data.
            result = download_host_logs(
                spec.ssh,
                output_dir=downloaded_log_dir,
                remote_log_dir=spec.remote_log_dir or DEFAULT_REMOTE_LOG_DIR,
                hostname=spec.name,
                port=spec.port,
                identity_file=spec.identity_file,
                ssh_options=spec.ssh_options,
                progress=lambda n: progress(f"{display}: downloaded {n:,} rows so far …"),
                want_start=month_start,
                want_end=month_end,
            )
            if result is None:
                progress(
                    f"{display}: no {month} data on {spec.ssh} "
                    "(those days were likely pruned server-side)"
                )
            else:
                resolved_hostname = result.hostname
                remote_earliest = result.remote_earliest_date
                progress(
                    f"{display}: download done — {result.downloaded_rows:,} rows, "
                    f"{result.appended_rows:,} new"
                )
    if resolved_hostname is None:
        resolved_hostname = sanitize_hostname(spec.name or host_part(spec.ssh))

    label = spec.name or resolved_hostname

    progress(f"{label}: scanning local samples for {month} …")
    log_files = select_month_log_files(downloaded_log_dir, resolved_hostname, month_start, month_end)
    samples, skipped_invalid = load_month_samples(log_files)
    sample_count, max_ts, covered_dates = month_data_signature(samples)

    if sample_count == 0:
        if download_attempted and remote_earliest is not None:
            detail = (
                f"no {month} data — {spec.ssh} only has data from "
                f"{remote_earliest.isoformat()} on (older days pruned by the server's retention)"
            )
        elif download_attempted:
            detail = (
                f"no {month} data found locally or on {spec.ssh} "
                "(older days were likely pruned by the server's retention)"
            )
        else:
            detail = f"no samples for {month} under {downloaded_log_dir}"
        return HostReportOutcome(label=label, status="skipped_no_data", detail=detail)

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

    progress(f"{label}: rendering plot …")
    rendered_png = render_timeseries_from_samples(samples, rdir / PNG_NAME, title=None)
    png_name = PNG_NAME if rendered_png is not None else None

    progress(f"{label}: building summary & peaks …")
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
        summary_text=build_report_from_samples(samples, skipped_invalid_samples=skipped_invalid),
        peaks_text=build_peak_report_from_samples(samples, peak_limit),
        spreadsheet_text=build_report_from_samples(samples, spreadsheet_values_only=True),
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
    progress: Optional[Callable[[str], None]] = None,
) -> List[HostReportOutcome]:
    progress = progress or _noop_progress
    total = len(specs)
    outcomes: List[HostReportOutcome] = []
    for index, spec in enumerate(specs, start=1):
        prefix = f"[{index}/{total}] "
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
                    progress=lambda message, prefix=prefix: progress(prefix + message),
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
