import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "download_server_logs.py"
SPEC = importlib.util.spec_from_file_location("download_server_logs", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DownloadServerLogsTests(unittest.TestCase):
    def test_merge_creates_hostname_range_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = MODULE.merge_lines_into_host_log(
                hostname="server-a",
                remote_lines=[
                    '{"timestamp":"2026-04-20T00:00:00Z","hostname":"server-a"}\n',
                    '{"timestamp":"2026-04-21T00:00:00Z","hostname":"server-a"}\n',
                ],
                output_dir=Path(tmpdir),
            )

            self.assertEqual(result.path.name, "server-a_2026-04-20_to_2026-04-21.jsonl")
            self.assertEqual(result.downloaded_rows, 2)
            self.assertEqual(result.appended_rows, 2)
            self.assertEqual(result.duplicate_rows, 0)
            self.assertEqual(result.path.read_text(encoding="utf-8").count("\n"), 2)

    def test_merge_appends_existing_host_file_and_renames_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            existing = output_dir / "server-a_2026-04-20_to_2026-04-20.jsonl"
            existing.write_text(
                '{"timestamp":"2026-04-20T00:00:00Z","hostname":"server-a"}\n',
                encoding="utf-8",
            )

            result = MODULE.merge_lines_into_host_log(
                hostname="server-a",
                remote_lines=[
                    '{"timestamp":"2026-04-20T00:00:00Z","hostname":"server-a"}\n',
                    '{"timestamp":"2026-04-22T00:00:00Z","hostname":"server-a"}\n',
                ],
                output_dir=output_dir,
            )

            self.assertEqual(result.path.name, "server-a_2026-04-20_to_2026-04-22.jsonl")
            self.assertFalse(existing.exists())
            self.assertEqual(result.existing_rows, 1)
            self.assertEqual(result.downloaded_rows, 2)
            self.assertEqual(result.appended_rows, 1)
            self.assertEqual(result.duplicate_rows, 1)
            self.assertEqual(result.path.read_text(encoding="utf-8").count("\n"), 2)

    def test_sanitize_hostname_replaces_unsafe_characters(self) -> None:
        self.assertEqual(MODULE.sanitize_hostname(" server/name:01 "), "server_name_01")


if __name__ == "__main__":
    unittest.main()
