import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from srmon.analysis import peaks  # noqa: E402


def write_log(directory: Path, rows: list) -> list:
    path = directory / "metrics-2026-05-10.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return [path]


SAMPLES = [
    {
        "timestamp": "2026-05-10T00:00:00Z", "hostname": "h",
        "cpu": {"used_pct": 10}, "memory": {"mem_used_pct": 20, "swap_used_pct": 0},
        "top_cpu_threads": [], "top_memory_processes": [{"process_name": "p", "pid": 1, "rss_bytes": 100}],
    },
    {
        "timestamp": "2026-05-10T00:00:10Z", "hostname": "h",
        "cpu": {"used_pct": 80}, "memory": {"mem_used_pct": 90, "swap_used_pct": 5},
        "top_cpu_threads": [], "top_memory_processes": [{"process_name": "p", "pid": 1, "rss_bytes": 900}],
    },
    {
        "timestamp": "2026-05-10T00:00:20Z", "hostname": "h",
        "cpu": {"used_pct": 50}, "memory": {"mem_used_pct": 40, "swap_used_pct": 2},
        "top_cpu_threads": [], "top_memory_processes": [],
    },
]


class ComputePeaksTests(unittest.TestCase):
    def test_rows_sorted_descending_by_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            files = write_log(Path(tmpdir), SAMPLES)
            result = peaks.compute_peaks(files)

        self.assertEqual([row["value"] for row in result.cpu_rows], [80.0, 50.0, 10.0])
        self.assertEqual([row["value"] for row in result.mem_rows], [90.0, 40.0, 20.0])
        self.assertEqual([row["value"] for row in result.swap_rows], [5.0, 2.0, 0.0])
        # Only the two samples with a memory process contribute RSS rows.
        self.assertEqual([row["value"] for row in result.rss_rows], [900.0, 100.0])

    def test_build_peak_report_limits_and_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            files = write_log(Path(tmpdir), SAMPLES)
            report = peaks.build_peak_report(files, limit=2)

        self.assertIn("Top CPU Samples", report)
        self.assertIn("Top Process RSS Samples", report)
        self.assertIn("cpu_used=80.00%", report)
        # limit=2 keeps two CPU rows (80, 50), not the 10 one.
        self.assertNotIn("cpu_used=10.00%", report)

    def test_process_name_filter_yields_no_matching_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            files = write_log(Path(tmpdir), SAMPLES)
            report = peaks.build_peak_report(files, limit=5, process_name="does-not-exist")

        # No threads or matching processes -> every section reports no matches.
        self.assertEqual(report.count("No matching samples."), 4)


if __name__ == "__main__":
    unittest.main()
