import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "summarize_resource_monitor.py"
SPEC = importlib.util.spec_from_file_location("summarize_resource_monitor", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SummarizeResourceMonitorTests(unittest.TestCase):
    def test_list_log_files_uses_recent_recorded_days_instead_of_calendar_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            for day in ("2026-04-10", "2026-04-12", "2026-04-20", "2026-04-21"):
                (log_dir / f"metrics-{day}.jsonl").write_text("{}", encoding="utf-8")

            files = MODULE.list_log_files(log_dir, days=3)

        self.assertEqual(
            [path.name for path in files],
            [
                "metrics-2026-04-12.jsonl",
                "metrics-2026-04-20.jsonl",
                "metrics-2026-04-21.jsonl",
            ],
        )

    def test_build_report_skips_invalid_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / "metrics-2026-04-20.jsonl").write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-04-20T00:00:00Z","hostname":"host-a","cpu":{"used_pct":10},"memory":{"mem_used_pct":20,"swap_used_pct":0},"disk":{"read_bytes_per_sec":1048576,"write_bytes_per_sec":0},"network":{"rx_bytes_per_sec":0,"tx_bytes_per_sec":0},"gpu":{"devices":[]},"top_cpu_threads":[],"top_memory_processes":[]}',
                        '{"timestamp":"2026-04-20T00:00:10Z","hostname":"host-a","cpu":{"used_pct" 10},"memory":{"mem_used_pct":20,"swap_used_pct":0},"disk":{"read_bytes_per_sec":0,"write_bytes_per_sec":0},"network":{"rx_bytes_per_sec":0,"tx_bytes_per_sec":0},"gpu":{"devices":[]},"top_cpu_threads":[],"top_memory_processes":[]}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (log_dir / "metrics-2026-04-21.jsonl").write_text(
                '{"timestamp":"2026-04-21T00:00:00Z","hostname":"host-a","cpu":{"used_pct":30},"memory":{"mem_used_pct":40,"swap_used_pct":0},"disk":{"read_bytes_per_sec":2097152,"write_bytes_per_sec":0},"network":{"rx_bytes_per_sec":0,"tx_bytes_per_sec":0},"gpu":{"devices":[]},"top_cpu_threads":[],"top_memory_processes":[]}\n',
                encoding="utf-8",
            )

            report = MODULE.build_report(MODULE.list_log_files(log_dir, days=3))

        self.assertIn("Samples: 2", report)
        self.assertIn("Skipped invalid samples: 1", report)
        self.assertIn("Time range: 2026-04-20T00:00:00Z -> 2026-04-21T00:00:00Z", report)

    def test_build_report_returns_no_samples_when_only_invalid_rows_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "metrics-2026-04-20.jsonl"
            log_path.write_text('{"timestamp":"2026-04-20T00:00:00Z"\n', encoding="utf-8")

            report = MODULE.build_report([log_path])

        self.assertEqual(report, "No samples found.")


if __name__ == "__main__":
    unittest.main()
