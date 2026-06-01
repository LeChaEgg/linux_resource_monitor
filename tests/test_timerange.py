import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from srmon.core import timerange  # noqa: E402


class MonthBoundsTests(unittest.TestCase):
    def test_regular_month(self) -> None:
        self.assertEqual(timerange.month_bounds("2026-05"), (date(2026, 5, 1), date(2026, 5, 31)))

    def test_february_non_leap(self) -> None:
        self.assertEqual(timerange.month_bounds("2026-02"), (date(2026, 2, 1), date(2026, 2, 28)))

    def test_february_leap(self) -> None:
        self.assertEqual(timerange.month_bounds("2024-02"), (date(2024, 2, 1), date(2024, 2, 29)))

    def test_december_wraps_to_next_year(self) -> None:
        self.assertEqual(timerange.month_bounds("2026-12"), (date(2026, 12, 1), date(2026, 12, 31)))

    def test_january(self) -> None:
        self.assertEqual(timerange.month_bounds("2026-01"), (date(2026, 1, 1), date(2026, 1, 31)))

    def test_rejects_out_of_range_month(self) -> None:
        for bad in ("2026-13", "2026-00"):
            with self.assertRaises(ValueError):
                timerange.month_bounds(bad)

    def test_rejects_malformed(self) -> None:
        for bad in ("2026", "2026-05-01", "not-a-month", "", "2026/05"):
            with self.assertRaises(ValueError):
                timerange.month_bounds(bad)


class CurrentPreviousMonthTests(unittest.TestCase):
    def test_current_month(self) -> None:
        self.assertEqual(timerange.current_month(date(2026, 6, 15)), "2026-06")

    def test_previous_month_mid_year(self) -> None:
        self.assertEqual(timerange.previous_month(date(2026, 6, 1)), "2026-05")

    def test_previous_month_january_wraps(self) -> None:
        self.assertEqual(timerange.previous_month(date(2026, 1, 10)), "2025-12")


if __name__ == "__main__":
    unittest.main()
