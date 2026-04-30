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
    def test_resolve_log_dir_uses_local_debug_dir_by_mode(self) -> None:
        resolved = MODULE.resolve_log_dir(None, "local")
        expected = Path(__file__).resolve().parents[1] / "local-debug-logs"
        self.assertEqual(resolved, expected)

    def test_resolve_log_files_uses_recent_recorded_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            for day in ("2026-04-10", "2026-04-12", "2026-04-20", "2026-04-21"):
                (log_dir / f"metrics-{day}.jsonl").write_text("{}\n", encoding="utf-8")

            args = argparse.Namespace(mode="server", log_dir=str(log_dir), days=2)
            _, files = MODULE.resolve_log_files(args)

        self.assertEqual(
            [path.name for path in files],
            ["metrics-2026-04-20.jsonl", "metrics-2026-04-21.jsonl"],
        )

    def test_add_log_selection_args_defaults_to_auto_and_30_days(self) -> None:
        parser = argparse.ArgumentParser()
        MODULE.add_log_selection_args(parser)
        args = parser.parse_args([])

        self.assertEqual(args.mode, "auto")
        self.assertEqual(args.days, 30)

    def test_iter_samples_skips_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "metrics-2026-04-20.jsonl"
            path.write_text('{"timestamp":"2026-04-20T00:00:00Z"}\n{"timestamp" 1}\n', encoding="utf-8")

            rows = list(MODULE.iter_samples([path]))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2]["timestamp"], "2026-04-20T00:00:00Z")

    def test_local_mode_filters_combined_logs_by_hostname_and_date_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / "server-a_2026-04-10_to_2026-04-12.jsonl").write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-04-10T00:00:00Z","hostname":"server-a"}',
                        '{"timestamp":"2026-04-12T00:00:00Z","hostname":"server-a"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (log_dir / "server-b_2026-04-12_to_2026-04-12.jsonl").write_text(
                '{"timestamp":"2026-04-12T00:00:00Z","hostname":"server-b"}\n',
                encoding="utf-8",
            )

            args = argparse.Namespace(
                mode="local",
                log_dir=str(log_dir),
                days=7,
                hostname="server-a",
                start_date="2026-04-12",
                end_date="2026-04-12",
            )
            _, files = MODULE.resolve_log_files(args)
            rows = list(MODULE.iter_samples(files))

        self.assertEqual([path.name for path in files], ["server-a_2026-04-10_to_2026-04-12.jsonl"])
        self.assertEqual([row[2]["timestamp"] for row in rows], ["2026-04-12T00:00:00Z"])

    def test_local_mode_defaults_to_newest_combined_log_file_and_recent_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / "server-a_2026-04-10_to_2026-04-10.jsonl").write_text(
                '{"timestamp":"2026-04-10T00:00:00Z","hostname":"server-a"}\n',
                encoding="utf-8",
            )
            (log_dir / "server-b_2026-04-11_to_2026-04-13.jsonl").write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-04-11T00:00:00Z","hostname":"server-b"}',
                        '{"timestamp":"2026-04-12T00:00:00Z","hostname":"server-b"}',
                        '{"timestamp":"2026-04-13T00:00:00Z","hostname":"server-b"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            args = argparse.Namespace(
                mode="local",
                log_dir=str(log_dir),
                days=2,
                hostname=None,
                start_date=None,
                end_date=None,
            )
            _, files = MODULE.resolve_log_files(args)
            rows = list(MODULE.iter_samples(files))

        self.assertEqual([path.name for path in files], ["server-b_2026-04-11_to_2026-04-13.jsonl"])
        self.assertEqual([row[2]["timestamp"] for row in rows], ["2026-04-12T00:00:00Z", "2026-04-13T00:00:00Z"])

    def test_auto_mode_uses_local_combined_logs_in_custom_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / "server-a_2026-04-12_to_2026-04-12.jsonl").write_text(
                '{"timestamp":"2026-04-12T00:00:00Z","hostname":"server-a"}\n',
                encoding="utf-8",
            )

            args = argparse.Namespace(
                mode="auto",
                log_dir=str(log_dir),
                days=30,
                hostname=None,
                start_date=None,
                end_date=None,
            )
            _, files = MODULE.resolve_log_files(args)
            rows = list(MODULE.iter_samples(files))

        self.assertEqual([path.name for path in files], ["server-a_2026-04-12_to_2026-04-12.jsonl"])
        self.assertEqual(len(rows), 1)

    def test_local_mode_ignores_manually_copied_daily_metric_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / "metrics-2026-04-12.jsonl").write_text(
                '{"timestamp":"2026-04-12T00:00:00Z","hostname":"server-a"}\n',
                encoding="utf-8",
            )

            args = argparse.Namespace(
                mode="local",
                log_dir=str(log_dir),
                days=7,
                hostname="server-a",
                start_date="2026-04-12",
                end_date="2026-04-12",
            )
            _, files = MODULE.resolve_log_files(args)

        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
