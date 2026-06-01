"""Render a ReportBundle to Markdown plus copy-paste sidecar files."""

from pathlib import Path

from srmon.report.models import ReportBundle


def render_markdown(bundle: ReportBundle) -> str:
    lines = []
    lines.append(f"# Monthly Resource Report — {bundle.label} — {bundle.month}")
    lines.append("")
    lines.append(f"- Server: `{bundle.ssh}`")
    lines.append(f"- Month: {bundle.month_start.isoformat()} → {bundle.month_end.isoformat()}")
    if bundle.covered_start and bundle.covered_end:
        lines.append(
            f"- Covered: {bundle.covered_start.isoformat()} → {bundle.covered_end.isoformat()} "
            f"({bundle.days_with_data} days with data, {bundle.sample_count} samples)"
        )
    else:
        lines.append(f"- Covered: none ({bundle.sample_count} samples)")
    lines.append(f"- Generated: {bundle.generated_at}")
    lines.append("")

    lines.append("## CPU / Memory / Swap")
    lines.append("")
    if bundle.png_name:
        lines.append(f"![CPU / memory / swap over time]({bundle.png_name})")
    else:
        lines.append("_No plottable samples for this month._")
    lines.append("")

    lines.append(f"## Peaks (top {bundle.peak_limit})")
    lines.append("")
    lines.append("```")
    lines.append(bundle.peaks_text)
    lines.append("```")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("```")
    lines.append(bundle.summary_text)
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_report_dir(report_dir: Path, bundle: ReportBundle) -> Path:
    """Write report.md plus summary.txt / peaks.txt / spreadsheet.tsv sidecars."""
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / "report.md"
    report_path.write_text(render_markdown(bundle), encoding="utf-8")
    (report_dir / "summary.txt").write_text(bundle.summary_text + "\n", encoding="utf-8")
    (report_dir / "peaks.txt").write_text(bundle.peaks_text + "\n", encoding="utf-8")
    # spreadsheet_text already uses \r\n separators for spreadsheet paste; keep verbatim.
    (report_dir / "spreadsheet.tsv").write_text(bundle.spreadsheet_text, encoding="utf-8")
    return report_path
