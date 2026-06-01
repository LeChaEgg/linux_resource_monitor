import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from srmon.ingest.download import MergeResult  # noqa: E402
from srmon.report import builder, command  # noqa: E402
from srmon.report.inventory import HostSpec  # noqa: E402


def sample(ts: str, host: str, cpu: float, mem: float, swap: float = 0.0) -> dict:
    return {
        "timestamp": ts, "hostname": host,
        "cpu": {"used_pct": cpu, "loadavg_1m": 1.0},
        "memory": {"mem_used_pct": mem, "swap_used_pct": swap},
        "disk": {"read_bytes_per_sec": 0, "write_bytes_per_sec": 0},
        "network": {"rx_bytes_per_sec": 0, "tx_bytes_per_sec": 0},
        "top_cpu_threads": [], "top_memory_processes": [],
    }


def seed_log(log_dir: Path, name: str, rows: list) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    dates = [r["timestamp"][:10] for r in rows]
    filename = f"{name}_{min(dates)}_to_{max(dates)}.jsonl"
    (log_dir / filename).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# Spans a month boundary: two April samples, two May samples.
CROSS_BOUNDARY_ROWS = [
    sample("2026-04-28T00:00:00Z", "H002", 10, 20),
    sample("2026-04-30T00:00:00Z", "H002", 12, 22),
    sample("2026-05-02T00:00:00Z", "H002", 50, 60, 1),
    sample("2026-05-09T00:00:00Z", "H002", 80, 70, 3),
]


class ReportBuilderTests(unittest.TestCase):
    def test_month_scoping_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            reports = Path(tmp) / "reports"
            seed_log(logs, "H002", CROSS_BOUNDARY_ROWS)

            outcome = builder.build_host_report(
                HostSpec(ssh="me@host", name="H002"),
                month="2026-05", out_dir=reports, download=False, downloaded_log_dir=logs,
            )

            rdir = reports / "H002" / "2026-05"
            self.assertEqual(outcome.status, "generated")
            for name in ("report.md", "cpu-mem-swap.png", "manifest.json", "summary.txt", "peaks.txt", "spreadsheet.tsv"):
                self.assertTrue((rdir / name).exists(), f"missing {name}")

            report_md = (rdir / "report.md").read_text(encoding="utf-8")
            self.assertIn("![CPU / memory / swap over time](cpu-mem-swap.png)", report_md)
            self.assertIn("Covered:", report_md)

            manifest = json.loads((rdir / "manifest.json").read_text(encoding="utf-8"))
            # Only the two May samples are in scope; April is excluded.
            self.assertEqual(manifest["sample_count"], 2)
            self.assertEqual(manifest["covered_dates"], ["2026-05-02", "2026-05-09"])

    def test_incremental_skip_regen_and_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            reports = Path(tmp) / "reports"
            seed_log(logs, "H002", CROSS_BOUNDARY_ROWS)
            spec = HostSpec(ssh="me@host", name="H002")

            first = builder.build_host_report(spec, month="2026-05", out_dir=reports, download=False, downloaded_log_dir=logs)
            self.assertEqual(first.status, "generated")

            again = builder.build_host_report(spec, month="2026-05", out_dir=reports, download=False, downloaded_log_dir=logs)
            self.assertEqual(again.status, "skipped_unchanged")

            # New in-month data -> regenerate.
            seed_log(logs, "H002", CROSS_BOUNDARY_ROWS + [sample("2026-05-11T00:00:00Z", "H002", 90, 85, 5)])
            after_new = builder.build_host_report(spec, month="2026-05", out_dir=reports, download=False, downloaded_log_dir=logs)
            self.assertEqual(after_new.status, "generated")

            # No new data, but --force regenerates.
            forced = builder.build_host_report(spec, month="2026-05", out_dir=reports, download=False, force=True, downloaded_log_dir=logs)
            self.assertEqual(forced.status, "generated")

    def test_no_in_month_data_is_skipped_without_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            reports = Path(tmp) / "reports"
            # Only April data; ask for May.
            seed_log(logs, "H002", CROSS_BOUNDARY_ROWS[:2])

            outcome = builder.build_host_report(
                HostSpec(ssh="me@host", name="H002"),
                month="2026-05", out_dir=reports, download=False, downloaded_log_dir=logs,
            )
            self.assertEqual(outcome.status, "skipped_no_data")
            self.assertFalse((reports / "H002" / "2026-05").exists())

    def test_skip_download_never_calls_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            reports = Path(tmp) / "reports"
            seed_log(logs, "H002", CROSS_BOUNDARY_ROWS)

            with patch.object(builder, "download_host_logs") as mock_download:
                builder.build_host_report(
                    HostSpec(ssh="me@host", name="H002"),
                    month="2026-05", out_dir=reports, download=False, downloaded_log_dir=logs,
                )
            mock_download.assert_not_called()

    def test_download_path_invokes_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            reports = Path(tmp) / "reports"
            seed_log(logs, "H002", CROSS_BOUNDARY_ROWS)
            fake = MergeResult(
                path=logs / "H002_2026-04-28_to_2026-05-09.jsonl", hostname="H002",
                start_date=date(2026, 4, 28), end_date=date(2026, 5, 9),
                existing_rows=0, downloaded_rows=0, appended_rows=0, duplicate_rows=0,
            )
            with patch.object(builder, "download_host_logs", return_value=fake) as mock_download:
                outcome = builder.build_host_report(
                    HostSpec(ssh="me@host", name="H002"),
                    month="2026-05", out_dir=reports, download=True, downloaded_log_dir=logs,
                )
            mock_download.assert_called_once()
            self.assertEqual(outcome.status, "generated")

    def test_build_reports_writes_index_for_multiple_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            reports = Path(tmp) / "reports"
            seed_log(logs, "H002", CROSS_BOUNDARY_ROWS)
            seed_log(logs, "V001", [sample("2026-05-03T00:00:00Z", "V001", 33, 44)])

            outcomes = builder.build_reports(
                [HostSpec(ssh="me@h1", name="H002"), HostSpec(ssh="me@h2", name="V001")],
                month="2026-05", out_dir=reports, download=False, downloaded_log_dir=logs,
            )
            self.assertEqual({o.status for o in outcomes}, {"generated"})
            index = reports / "index-2026-05.md"
            self.assertTrue(index.exists())
            index_text = index.read_text(encoding="utf-8")
            self.assertIn("H002", index_text)
            self.assertIn("V001", index_text)


class DefaultInventoryTests(unittest.TestCase):
    def test_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(command.find_default_inventory([Path(tmp)]))

    def test_finds_hosts_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "hosts.toml").write_text('[[host]]\nssh = "me@h"\n', encoding="utf-8")
            self.assertEqual(command.find_default_inventory([Path(tmp)]), Path(tmp) / "hosts.toml")

    def test_prefers_first_search_dir(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            (Path(a) / "hosts.toml").write_text('[[host]]\nssh = "me@a"\n', encoding="utf-8")
            (Path(b) / "hosts.toml").write_text('[[host]]\nssh = "me@b"\n', encoding="utf-8")
            self.assertEqual(command.find_default_inventory([Path(a), Path(b)]), Path(a) / "hosts.toml")


if __name__ == "__main__":
    unittest.main()
