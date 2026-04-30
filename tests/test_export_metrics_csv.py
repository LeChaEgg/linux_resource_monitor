import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "export_metrics_csv.py"
sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location("export_metrics_csv", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ExportMetricsCsvTests(unittest.TestCase):
    def test_default_output_path_uses_host_and_date_range(self) -> None:
        path = MODULE.default_output_path({"server/a:01"}, {date(2026, 4, 20), date(2026, 4, 22)})

        self.assertEqual(path.name, "resource-monitor_server_a_01_2026-04-20_to_2026-04-22.csv")

    def test_default_export_writes_to_local_debug_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            output_dir = Path(tmpdir) / "local-debug-logs"
            log_dir.mkdir()
            (log_dir / "server-a_2026-04-20_to_2026-04-20.jsonl").write_text(
                '{"timestamp":"2026-04-20T00:00:00Z","hostname":"server-a","cpu":{"used_pct":10},"memory":{"mem_used_pct":20,"swap_used_pct":0},"disk":{"read_bytes_per_sec":1048576,"write_bytes_per_sec":0},"network":{"rx_bytes_per_sec":0,"tx_bytes_per_sec":0},"top_cpu_threads":[],"top_memory_processes":[]}\n',
                encoding="utf-8",
            )

            argv = [
                "export_metrics_csv.py",
                "--mode",
                "local",
                "--log-dir",
                str(log_dir),
                "--hostname",
                "server-a",
            ]
            with patch.object(sys, "argv", argv), patch.object(MODULE, "DEFAULT_DOWNLOADED_LOG_DIR", output_dir):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    result = MODULE.main()

            output_path = output_dir / "resource-monitor_server-a_2026-04-20_to_2026-04-20.csv"
            self.assertEqual(result, 0)
            self.assertTrue(output_path.exists())
            self.assertIn(f"Wrote CSV: {output_path}", stdout.getvalue())
            self.assertIn("cpu_used_pct", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
