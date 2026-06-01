import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from srmon.ingest.download import (  # noqa: E402
    build_remote_cat_command,
    merge_lines_into_host_log,
    read_local_cutoff_date,
    repair_partial_tail,
    sanitize_hostname,
)


def line(ts: str, host: str = "server-a") -> str:
    return json.dumps({"timestamp": ts, "hostname": host}) + "\n"


class DownloadMergeTests(unittest.TestCase):
    def test_merge_creates_hostname_range_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = merge_lines_into_host_log(
                hostname="server-a",
                remote_lines=[line("2026-04-20T00:00:00Z"), line("2026-04-21T00:00:00Z")],
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
            existing.write_text(line("2026-04-20T00:00:00Z"), encoding="utf-8")

            result = merge_lines_into_host_log(
                hostname="server-a",
                remote_lines=[line("2026-04-20T00:00:00Z"), line("2026-04-22T00:00:00Z")],
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
        self.assertEqual(sanitize_hostname(" server/name:01 "), "server_name_01")

    def test_remote_command_filters_by_since_date(self) -> None:
        cmd = build_remote_cat_command("/var/log/x", date(2026, 5, 2))
        self.assertIn("since=2026-05-02", cmd)
        self.assertIn("metrics-*.jsonl", cmd)
        self.assertIn("sort | head", cmd)  # the >= since filter
        # No cutoff -> fetch everything (empty since).
        self.assertIn("since=''", build_remote_cat_command("/var/log/x", None))

    def test_incremental_append_is_in_place_and_bumps_end_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            existing = output_dir / "server-a_2026-05-01_to_2026-05-02.jsonl"
            existing.write_text(line("2026-05-01T00:00:00Z") + line("2026-05-02T00:00:00Z"), encoding="utf-8")
            inode = existing.stat().st_ino
            size = existing.stat().st_size

            new_line = line("2026-05-03T00:00:00Z")
            result = merge_lines_into_host_log(
                hostname="server-a",
                remote_lines=[line("2026-05-02T00:00:00Z"), new_line],  # boundary dup + one new day
                output_dir=output_dir,
            )

            self.assertEqual(result.path.name, "server-a_2026-05-01_to_2026-05-03.jsonl")
            self.assertEqual(result.appended_rows, 1)
            self.assertEqual(result.duplicate_rows, 1)
            # Appended in place (rename keeps the inode); body not rewritten.
            self.assertEqual(result.path.stat().st_ino, inode)
            self.assertEqual(result.path.stat().st_size, size + len(new_line.encode("utf-8")))
            self.assertEqual(result.path.read_text(encoding="utf-8").count("\n"), 3)

    def test_future_local_data_results_in_no_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            existing = output_dir / "server-a_2026-05-01_to_2026-05-05.jsonl"
            content = line("2026-05-01T00:00:00Z") + line("2026-05-05T00:00:00Z")
            existing.write_text(content, encoding="utf-8")

            result = merge_lines_into_host_log(
                hostname="server-a",
                remote_lines=[line("2026-05-05T00:00:00Z")],  # only data we already have
                output_dir=output_dir,
            )

            self.assertEqual(result.appended_rows, 0)
            self.assertEqual(result.path.name, "server-a_2026-05-01_to_2026-05-05.jsonl")  # no rename
            self.assertEqual(result.path.read_text(encoding="utf-8"), content)  # untouched

    def test_recovers_from_interrupted_partial_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            existing = output_dir / "server-a_2026-05-01_to_2026-05-02.jsonl"
            # Last line is a half-written fragment from an interrupted run.
            existing.write_text(
                line("2026-05-01T00:00:00Z") + line("2026-05-02T00:00:00Z") + '{"timestamp":"2026-05-02T00:00:10',
                encoding="utf-8",
            )

            result = merge_lines_into_host_log(
                hostname="server-a",
                remote_lines=[
                    line("2026-05-02T00:00:00Z"),  # boundary dup
                    line("2026-05-02T00:00:10Z"),  # the sample that was being written
                    line("2026-05-03T00:00:00Z"),  # a new day
                ],
                output_dir=output_dir,
            )

            lines = result.path.read_text(encoding="utf-8").splitlines()
            # Every stored line is valid JSON (the fragment was repaired away).
            timestamps = [json.loads(row)["timestamp"] for row in lines]
            self.assertEqual(
                timestamps,
                [
                    "2026-05-01T00:00:00Z",
                    "2026-05-02T00:00:00Z",
                    "2026-05-02T00:00:10Z",
                    "2026-05-03T00:00:00Z",
                ],
            )


class TailHelperTests(unittest.TestCase):
    def test_read_local_cutoff_date_reads_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "x.jsonl"
            path.write_text(line("2026-05-01T00:00:00Z") + line("2026-05-03T12:00:00Z"), encoding="utf-8")
            self.assertEqual(read_local_cutoff_date(path), date(2026, 5, 3))

    def test_read_local_cutoff_date_none_for_truncated_or_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "x.jsonl"
            path.write_text(line("2026-05-01T00:00:00Z") + '{"timestamp":"2026-05-03', encoding="utf-8")
            self.assertIsNone(read_local_cutoff_date(path))
            path.write_text("", encoding="utf-8")
            self.assertIsNone(read_local_cutoff_date(path))

    def test_repair_partial_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "x.jsonl"
            good = line("2026-05-01T00:00:00Z")
            path.write_text(good + '{"timestamp":"2026-05-02', encoding="utf-8")
            repair_partial_tail(path)
            self.assertEqual(path.read_text(encoding="utf-8"), good)
            # A clean file is left untouched.
            healthy = good + line("2026-05-02T00:00:00Z")
            path.write_text(healthy, encoding="utf-8")
            repair_partial_tail(path)
            self.assertEqual(path.read_text(encoding="utf-8"), healthy)


if __name__ == "__main__":
    unittest.main()
