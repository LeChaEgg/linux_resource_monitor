import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "log_analysis_utils.py"
SPEC = importlib.util.spec_from_file_location("log_analysis_utils", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class LogAnalysisUtilsTests(unittest.TestCase):
    def test_resolve_log_dir_uses_downloaded_dir_by_source(self) -> None:
        resolved = MODULE.resolve_log_dir(None, "downloaded")
        expected = Path(__file__).resolve().parents[1] / "local-debug-logs"
        self.assertEqual(resolved, expected)

    def test_resolve_log_files_uses_recent_recorded_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            for day in ("2026-04-10", "2026-04-12", "2026-04-20", "2026-04-21"):
                (log_dir / f"metrics-{day}.jsonl").write_text("{}\n", encoding="utf-8")

            args = argparse.Namespace(source="server", log_dir=str(log_dir), days=2)
            _, files = MODULE.resolve_log_files(args)

        self.assertEqual(
            [path.name for path in files],
            ["metrics-2026-04-20.jsonl", "metrics-2026-04-21.jsonl"],
        )

    def test_iter_samples_skips_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "metrics-2026-04-20.jsonl"
            path.write_text('{"timestamp":"2026-04-20T00:00:00Z"}\n{"timestamp" 1}\n', encoding="utf-8")

            rows = list(MODULE.iter_samples([path]))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2]["timestamp"], "2026-04-20T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
