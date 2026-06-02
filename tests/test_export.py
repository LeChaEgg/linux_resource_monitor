import contextlib
import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from srmon.export import csv_export as MODULE  # noqa: E402

# Patching MODULE.DEFAULT_DOWNLOADED_LOG_DIR works because run()/default_output_path
# look the name up as a module global of srmon.export.csv_export at call time.


class ExportMetricsCsvTests(unittest.TestCase):
    def test_default_output_path_uses_host_and_date_range(self) -> None:
        path = MODULE.default_output_path({"server/a:01"}, {date(2026, 4, 20), date(2026, 4, 22)})

        self.assertEqual(path.name, "resource-monitor_server_a_01_2026-04-20_to_2026-04-22.csv")

    def test_default_export_writes_to_local_debug_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            output_dir = Path(tmpdir) / "data"
            log_dir.mkdir()
            (log_dir / "server-a_2026-04-20_to_2026-04-20.jsonl").write_text(
                '{"timestamp":"2026-04-20T00:00:00Z","hostname":"server-a","cpu":{"used_pct":10},"memory":{"mem_used_pct":20,"swap_used_pct":0},"disk":{"read_bytes_per_sec":1048576,"write_bytes_per_sec":0},"network":{"rx_bytes_per_sec":0,"tx_bytes_per_sec":0},"top_cpu_threads":[],"top_memory_processes":[]}\n',
                encoding="utf-8",
            )

            argv = [
                "srmon-export",
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
