from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication
import xlsxwriter

from niro.catalog import distinct_values
from niro.dashboard_tools import export_pack, pack_charts, prepare_pack
from niro.filters import filter_records
from niro.office import Entry, OfficeStore, reminders, roster, workbook_issues
from niro.query import suggest
from niro.state import Store, namespace
from niro.theme import THEMES
from niro.timekeeping import SHIFT_DEFAULT, attendance, leave_days, read_clock
from niro.workbook import read_workbook


class OfficeWorkflows(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root / "data")
        self.office = OfficeStore(self.store)
        self.today = QDate(2026, 11, 28)
        self.book = self.root / "sample.xlsx"
        with xlsxwriter.Workbook(self.book) as file:
            page = file.add_worksheet("Roster")
            page.write_row(0, 0, ("نام", "کد پرسنلی", "مدیریت", "عضویت", "تاریخ پایان قرارداد", "تاریخ ورود به سازمان"))
            page.write_row(1, 0, ("فرد نمونه الف", "0012", "اداره مالی", "قراردادی", "1405/09/15", "2026-11-15"))
            page.write_row(2, 0, ("فرد نمونه ب", "789", "تدارکات", "رسمی", "2027-07-05", "2025-11-15"))
            page.write_row(3, 0, ("فرد نمونه پ", "", "اداره مالی", "قراردادی", "1405/14/12", "2026-01-01"))
        self.view = self.store.view(read_workbook(self.book))
        self.scope = namespace(self.view.snapshot)
        self.people = roster(self.view)

    def test_dossier_attachment_and_workbook_deadlines(self):
        person, _, no_code = self.people
        self.assertTrue(no_code.tokens, "A unique unchanged row can have a local dossier")
        entry = Entry("profile", person.tokens[0], "2026-11-27", {"type": "document", "title": "نمونه", "due": "2026-12-05"})
        self.office.save(self.scope, (entry,), "local", "dossier_saved", attachment=b"Example attachment")
        all_events = self.office.entries(self.scope, "profile")
        self.assertEqual(all_events, (entry,))
        self.assertEqual(self.office.attachment(self.scope, entry), b"Example attachment")
        _, identity, _, _, deadline, _ = self.view.snapshot.columns
        result = reminders(self.view, self.people, all_events, self.today, 30, {deadline.key}, {identity.key})
        self.assertEqual(len(result), 3)
        self.assertIn("bad_date", [issue for _, issue, _, _ in workbook_issues(self.view)])
        self.office.remove(self.scope, entry, "local", "dossier_removed")
        self.assertFalse(self.office.entries(self.scope, "profile"))
        self.assertEqual([event[2] for event in self.store.audit_entries(self.scope)], ["dossier_removed", "dossier_saved"])

    def test_clock_import_leave_and_midnight_shift(self):
        source = self.root / "clock.csv"
        source.write_text("Personnel ID,Date,In,Out\n12,2026-11-28,08:22,17:05\n789,2026-11-28,22:10,06:30\n0,2026-11-28,08:00,16:00\n",
                          encoding="utf-8")
        entries, errors = read_clock(source, self.people)
        self.assertEqual(len(entries), 2)
        self.assertEqual(errors[0][:2], (4, "clock_code"))
        lookup = {item.subject: item for item in entries}
        self.assertEqual(attendance(lookup[self.people[0].tokens[0]], SHIFT_DEFAULT), (523, 22, 65))
        night = {**SHIFT_DEFAULT, "start": 1320, "end": 360, "grace": 10}
        self.assertEqual(attendance(lookup[self.people[1].tokens[0]], night), (500, 0, 30))
        leave = Entry("leave", self.people[0].tokens[0], "2026-11-27", {"end": "2026-11-29", "minutes": 0})
        result = leave_days((leave,), self.today.addDays(-1), self.today.addDays(1),
                            {**SHIFT_DEFAULT, "holidays": ["2026-11-29"]})
        self.assertEqual(result, {"2026-11-28": 480})
        source.write_text("Code,Timestamp\n12,2026-11-28 08:22:00\n12,2026-11-28 17:05:00\n", encoding="utf-8")
        punches, errors = read_clock(source, self.people)
        self.assertEqual(punches[0].data, {"in": 502, "out": 1025})
        self.assertFalse(errors)

    def test_persian_search_drives_filtered_meeting_archive(self):
        question = "قراردادی‌های مالی که قراردادشان تا ۳۰ روز دیگر تمام می‌شود"
        result = suggest(question, self.view.snapshot.columns, distinct_values(self.view), self.today)
        self.assertEqual(len(result.rules), 3)
        self.assertIn("calendar_between", tuple(c.operator for c in result.rules))
        self.assertFalse(result.notices)
        selected = filter_records(self.view.records, self.view.snapshot.columns, rules=result.rules)
        self.assertEqual([row.values[1] for row in selected], ["0012"])
        start = self.today.addDays(-29)
        columns = self.view.snapshot.columns
        summary, rows = prepare_pack(self.view, {columns[2].key}, result.rules, start, self.today, "")
        self.assertEqual(summary.total, len(rows))
        self.assertEqual(sum(summary.periods[0].hires), 1)
        pictures = pack_charts(summary, "fa", THEMES["plum_hour"])
        self.assertGreaterEqual(len(pictures), 2)
        session = self.root / "meeting.zip"
        export_pack(session, columns, rows, summary, pictures, "fa", start, self.today, "", result.rules)
        with ZipFile(session) as bundle:
            self.assertIn("Summary.docx", bundle.namelist())
            self.assertTrue(bundle.read("Charts/Chart-01.png").startswith(b"\x89PNG"))
            sheet = self.root / "personnel.xlsx"
            sheet.write_bytes(bundle.read("Personnel.xlsx"))
        self.assertEqual(read_workbook(sheet).rows[0].values[1], "0012")
