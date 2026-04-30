import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "summarize_resource_monitor.py"
sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location("summarize_resource_monitor", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SummarizeResourceMonitorTests(unittest.TestCase):
    def test_resolve_log_files_uses_recent_recorded_days_instead_of_calendar_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            for day in ("2026-04-10", "2026-04-12", "2026-04-20", "2026-04-21"):
                (log_dir / f"metrics-{day}.jsonl").write_text("{}", encoding="utf-8")

            args = argparse.Namespace(mode="server", log_dir=str(log_dir), days=3)
            _, files = MODULE.resolve_log_files(args)

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

            args = argparse.Namespace(mode="server", log_dir=str(log_dir), days=3)
            _, files = MODULE.resolve_log_files(args)
            report = MODULE.build_report(files)

        self.assertIn("Samples: 2", report)
        self.assertIn("Skipped invalid samples: 1", report)
        self.assertIn("Time range: 2026-04-20T00:00:00Z -> 2026-04-21T00:00:00Z", report)

    def test_build_report_returns_no_samples_when_only_invalid_rows_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "metrics-2026-04-20.jsonl"
            log_path.write_text('{"timestamp":"2026-04-20T00:00:00Z"\n', encoding="utf-8")

            report = MODULE.build_report([log_path])

        self.assertEqual(report, "No samples found.")

    def test_build_report_appends_spreadsheet_values_in_copy_order(self) -> None:
        mib = 1024 ** 2
        samples = [
            {
                "timestamp": "2026-04-20T00:00:00Z",
                "hostname": "host-a",
                "cpu": {"used_pct": 10},
                "memory": {"mem_used_pct": 20, "swap_used_pct": 1},
                "network": {"rx_bytes_per_sec": 1 * mib, "tx_bytes_per_sec": 2 * mib},
                "gpu": {
                    "devices": [
                        {"index": 0, "name": "gpu-a", "utilization_gpu_pct": 40, "memory_used_pct": 10},
                        {"index": 2, "name": "gpu-c", "utilization_gpu_pct": 5, "memory_used_pct": 15},
                    ]
                },
                "top_cpu_threads": [],
                "top_memory_processes": [],
            },
            {
                "timestamp": "2026-04-20T00:00:10Z",
                "hostname": "host-a",
                "cpu": {"used_pct": 30},
                "memory": {"mem_used_pct": 80, "swap_used_pct": 5},
                "network": {"rx_bytes_per_sec": 3 * mib, "tx_bytes_per_sec": 6 * mib},
                "gpu": {
                    "devices": [
                        {"index": 0, "name": "gpu-a", "utilization_gpu_pct": 80, "memory_used_pct": 30},
                        {"index": 2, "name": "gpu-c", "utilization_gpu_pct": 15, "memory_used_pct": 25},
                    ]
                },
                "top_cpu_threads": [],
                "top_memory_processes": [],
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "metrics-2026-04-20.jsonl"
            log_path.write_text("\n".join(json.dumps(sample) for sample in samples) + "\n", encoding="utf-8")

            report = MODULE.build_report([log_path])
            spreadsheet_only = MODULE.build_report([log_path], spreadsheet_values_only=True)

        expected_values = [
            "20.00%",
            "29.00%",
            "29.80%",
            "30.00%",
            "50.00%",
            "77.00%",
            "79.40%",
            "80.00%",
            "5.00%",
            "2.90 MiB/s",
            "3.00 MiB/s",
            "5.80 MiB/s",
            "6.00 MiB/s",
            "78.00%",
            "80.00%",
            "29.00%",
            "30.00%",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "14.50%",
            "15.00%",
            "24.50%",
            "25.00%",
        ]

        self.assertEqual(spreadsheet_only.splitlines(), expected_values)
        self.assertEqual(spreadsheet_only.split("\r\n"), expected_values)
        self.assertIn("Spreadsheet Values\n" + "\n".join(expected_values), report)


if __name__ == "__main__":
    unittest.main()
