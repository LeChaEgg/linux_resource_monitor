"""Plain data holders shared by the report builder and renderer."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional


@dataclass
class ReportBundle:
    label: str
    ssh: str
    month: str
    month_start: date
    month_end: date
    covered_start: Optional[date]
    covered_end: Optional[date]
    days_with_data: int
    sample_count: int
    peak_limit: int
    summary_text: str
    peaks_text: str
    spreadsheet_text: str
    png_name: Optional[str]
    generated_at: str


@dataclass
class HostReportOutcome:
    label: str
    status: str  # "generated" | "skipped_no_data" | "skipped_unchanged" | "error"
    detail: str
    report_path: Optional[Path] = None
