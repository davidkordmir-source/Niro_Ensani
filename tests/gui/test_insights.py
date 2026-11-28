from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import xlsxwriter
from PySide6.QtCore import QDate

from niro.insights import METRICS, Query, evaluate, load_dataset, prepare_profile, save_attendance
from niro.schema import PROFILE_SHEET, SPORT_SHEET
from niro.state import Store
from niro.workbook import WorkbookError, companion_snapshots, label_key, read_workbook, update_workbook


class WorkbookInsights(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "metrics.xlsx"
        self.store = Store(Path(folder.name) / "profile")
        self.today = QDate(2026, 11, 28)
        columns = (
            "Name", "Personnel ID", "Membership", "Education", "Date of birth", "Start date", "Exit date",
            "Job satisfaction", "Work experience", "Leave count", "Lateness in minutes", "Team code",
        )
        with xlsxwriter.Workbook(self.path) as book:
            sheet = book.add_worksheet("Roster")
            sheet.write_row(0, 0, columns)
            sheet.write_row(1, 0, ("Ada", "001", "Permanent", "Bachelor", "1992-02-12", "2014-01-01", "",
                                  "", 10, 2, "", "Finance"))
            sheet.write_row(2, 0, ("Sam", "002", "Contract", "Master", "1380/08/10", "2025-04-01", "2026-07-01",
                                  "۶۰٪", "", 4, "0:25", "Support"))
            sheet.write_row(3, 0, ("Alex", "003", "Permanent", "Bachelor", "", "2024-08-01", "",
                                  "", "", "", "", ""))
            sheet.write_number(1, 7, 0.8, book.add_format({"num_format": "0%"}))
            sheet.write_number(1, 10, 90 / 1440, book.add_format({"num_format": "[h]:mm"}))
        self.snap = read_workbook(self.path)

    def data(self, snapshot=None, overrides=None):
        return load_dataset(self.store.view(snapshot or read_workbook(self.path)), overrides)

    def test_metrics_and_stable_group_comparison(self):
        dataset = self.data(overrides={"department": "team code"})
        self.assertEqual(dataset.people[0].fields["delay"], "01:30:00")
        query = Query("en", METRICS, self.today.addYears(-2), self.today, self.today)
        result = evaluate(dataset, query)
        cards = {card.key: card for card in result.metrics}
        self.assertEqual(cards["total"].headline, "3 people")
        self.assertEqual(cards["hires"].headline, "1 person")
        self.assertEqual(cards["movement"].headline, "1 in · 1 out")
        self.assertEqual(cards["satisfaction"].headline, "70%")
        self.assertEqual(cards["leave"].headline, "6 entries")
        self.assertEqual(cards["delay"].headline, "115 min")
        self.assertEqual(cards["department"].headline, "2 people")
        self.assertEqual(cards["education"].chart.slices[0], ("Bachelor", 2))
        self.assertIn(("Not recorded", 1), cards["age"].chart.slices)
        self.assertEqual(result.compare.labels, ("Permanent", "Contract"))
        self.assertIsNotNone(result.compare.scaled)
        raw = {series.label: series.values for series in result.compare.series}
        self.assertEqual(raw["Job satisfaction"], (80., 60.))
        self.assertEqual(raw["Leaves taken"], (2., 4.))
        only = evaluate(dataset, Query("en", ("leave", "delay"), self.today.addDays(-7), self.today, self.today, person="p:002"))
        self.assertEqual(only.metrics[0].headline, "4 entries")
        self.assertEqual(only.metrics[1].headline, "25 min")
        self.assertTrue(only.compare.table_only)

    def test_companion_inputs_and_attendance_save_edit_delete(self):
        backups = []
        original = self.data()
        new = prepare_profile(original, backups.append)
        self.assertTrue(backups)
        profile = companion_snapshots(new)[PROFILE_SHEET]
        self.assertEqual(len(profile.rows), 3)
        self.assertEqual(profile.rows[0].values[1], "001")
        update_workbook(profile, "edit", profile.rows[0].number,
                        {label_key("مدیریت"): "Finance", label_key("تعداد مرخصی"): "9"}, backups.append)
        data = self.data()
        self.assertEqual(data.people[0].fields["department"], "Finance")
        self.assertEqual(data.people[0].fields["leave"], "2")
        first = save_attendance(data, "p:001", self.today, "Practice", "present", None, False, backups.append)
        self.assertIn(SPORT_SHEET, companion_snapshots(first))
        data = self.data(first)
        self.assertEqual(data.attendance[0].ref, "p:001")
        with self.assertRaisesRegex(WorkbookError, "attendance_duplicate"):
            save_attendance(data, "p:001", self.today, "practice", "absent", None, False, backups.append)
        with self.assertRaisesRegex(WorkbookError, "source_changed"):
            save_attendance(original, "p:001",
                            self.today, "Other practice", "present", None, False, backups.append)
        old_row = data.attendance[0].row
        second = save_attendance(data, "p:002", self.today, "Practice", "absent", None, False, backups.append)
        data = self.data(second)
        result = evaluate(data, Query("en", ("sport",), self.today.addDays(-1), self.today, self.today))
        self.assertEqual(result.metrics[0].headline, "50%")
        third = save_attendance(data, "p:001", self.today, "Practice", "excused", old_row, False, backups.append)
        data = self.data(third)
        self.assertEqual({event.status for event in data.attendance}, {"excused", "absent"})
        deleted = save_attendance(data, None, None, "", "", old_row, True, backups.append)
        final = self.data(deleted)
        self.assertEqual([event.status for event in final.attendance], ["absent"])
        self.assertEqual(final.view.snapshot.sheet.label, "Roster")

    def test_missing_workbook_data_is_not_reported_as_observed(self):
        dataset = self.data()
        start = self.today.addDays(-30)
        result = evaluate(dataset, Query("fa", ("department", "sport", "hires"), start, self.today, self.today))
        self.assertEqual(result.metrics[0].headline, "—")
        self.assertEqual(result.metrics[1].headline, "—")
        self.assertEqual(result.metrics[2].headline, "۰ نفر")
