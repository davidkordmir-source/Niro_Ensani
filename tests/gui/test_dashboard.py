"""Verify workforce totals against workbook dates and statuses without opening a window."""

from types import SimpleNamespace
import unittest

from PySide6.QtCore import QDate

from niro.dashboard import summarize
from niro.state import Record
from niro.workbook import Column


def view(columns, rows):
    snapshot = SimpleNamespace(columns=tuple(Column(str(index), title, index)
                                             for index, title in enumerate(columns)))
    return SimpleNamespace(snapshot=snapshot, records=tuple(Record(row) for row in rows))


class WorkforceTests(unittest.TestCase):
    today = QDate(2026, 11, 26)

    def test_uses_jalali_or_gregorian_start_and_exit_dates(self):
        data = view(("تاریخ ورود به سازمان", "تاریخ خروج", "وضعیت اشتغال"), (
            ("۱۴۰۵/۰۹/۰۵", "", "شاغل"),
            ("2026-10-28", "2026-11-09", "قطع همکاری"),
            ("2026-10-27", "نامشخص", "بازنشسته"),
            ("2026-11-27", "", "شاغل"),
        ))
        result = summarize(data, self.today)
        self.assertEqual(result.total, 4)
        self.assertEqual(sum(result.periods[0].hires), 2)
        self.assertEqual(sum(result.periods[1].hires), 3)
        self.assertEqual(sum(result.periods[0].departures), 1)
        self.assertEqual(result.undated_departures, 1)

    def test_status_departures_do_not_imply_dated_events(self):
        data = view(("تاریخ ورود به یگان", "وضعیت اشتغال"), (
            ("۱۴۰۵/۰۹/۰۱", " قطع‌همکاری "),
            ("۱۴۰۵/۰۹/۰۱", "قطع همکاری"),
            ("۱۴۰۵/۰۹/۰۲", "مامور به"),
            ("۱۴۰۵/۰۹/۰۲", ""),
        ))
        result = summarize(data, self.today)
        self.assertEqual(result.departures_by_status, 2)
        self.assertEqual(result.undated_departures, 2)
        self.assertIsNone(result.periods[0].departures)
        self.assertIsNone(result.periods[0].hires)
        self.assertIn((None, 1), result.statuses)

    def test_empty_sheet_and_unlinked_dashboard(self):
        result = summarize(view(("Hire Date", "Exit Date", "Employment Status"), ()), self.today)
        self.assertEqual(result.total, 0)
        self.assertEqual(sum(result.periods[0].hires), 0)
        self.assertEqual(sum(result.periods[0].departures), 0)
        self.assertEqual(result.departures_by_status, 0)
        self.assertIsNone(summarize(None, self.today).total)
