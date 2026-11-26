from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from docx import Document
from lxml import etree as ET
from pptx import Presentation
import xlsxwriter

from niro.filters import Rule, filter_records
from niro.reports import Report, export
from niro.state import Store
from niro.workbook import MAIN, WorkbookError, make_template, read_workbook, update_workbook


def write_people(path, headers, rows, table=False):
    with xlsxwriter.Workbook(path) as workbook:
        sheet = workbook.add_worksheet("People")
        if table:
            sheet.add_table(0, 0, max(1, len(rows)), len(headers) - 1, {
                "name": "Directory", "columns": [{"header": name} for name in headers],
            })
        else:
            sheet.write_row(0, 0, headers)
        for index, values in enumerate(rows, 1):
            for column, value in enumerate(values):
                sheet.write_string(index, column, value)


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "بانک آزمون.xlsx"
        self.store = Store(self.root / "profile")

    def write(self, snapshot, operation, number, values):
        return update_workbook(snapshot, operation, number, values, lambda raw: self.store.backup(self.path, raw))

    def test_add_edit_delete_and_backup(self):
        make_template(self.path)
        initial = read_workbook(self.path)
        self.assertEqual(len(initial.columns), 36)
        columns = {column.index: column.key for column in initial.columns}
        self.assertFalse(initial.rows)
        first = self.write(initial, "add", None, {columns[1]: "روشن", columns[5]: "۰۰۱۲"})
        second = self.write(first, "add", None, {columns[1]: "=not_a_formula()", columns[5]: "0013"})
        self.assertEqual(second.rows[1].values[0], "=not_a_formula()")
        self.assertFalse(second.rows[1].formulas)
        self.assertEqual(second.rows[0].values[4], "۰۰۱۲")
        with ZipFile(self.path) as workbook:
            table = ET.fromstring(workbook.read("xl/tables/table1.xml"))
            self.assertEqual(table.get("ref"), "A1:AJ3")
            self.assertEqual(table.find(f"{{{MAIN}}}autoFilter").get("ref"), "A1:AJ3")
        after = self.write(second, "edit", second.rows[1].number, {columns[1]: "✨ _x0041_\nنام"})
        self.assertEqual(after.rows[1].values[0], "✨ _x0041_\nنام")
        after = self.write(after, "delete", after.rows[0].number, {})
        self.assertEqual(len(after.rows), 1)
        self.store.copy_backup(self.path, self.root / "recovered.xlsx")
        self.assertEqual(len(read_workbook(self.root / "recovered.xlsx").rows), 2)
        reused = self.write(after, "add", None, {columns[1]: "جدید"})
        self.assertEqual([row.values[0] for row in reused.rows], ["جدید", "✨ _x0041_\nنام"])

    def test_saved_schema_reorder_and_local_precedence(self):
        write_people(self.path, ("Name", "Personnel ID", "Branch", "Pay"),
                     [("A", "001", "Alpha", "500"), ("B", "002", "Beta", "800")])
        first = self.store.view(read_workbook(self.path))
        view = self.store.change(first, "edit", first.records[0], {"branch": "Local", "pay": "600"})
        view = self.store.change(view, "delete", view.records[1], {})
        view = self.store.change(view, "add", None, {"name": "Offline", "personnel id": "003"})
        self.assertEqual(read_workbook(self.path).rows[0].values[2], "Alpha")
        self.assertEqual([row.values[0] for row in view.records], ["A", "Offline"])
        write_people(self.path, ("Branch", "Personnel ID", "Name", "Pay", "Team"),
                     [("Beta", "002", "B", "900", "T1"), ("External", "001", "A", "500", "T2")])
        updated = self.store.view(read_workbook(self.path))
        self.assertEqual(updated.hidden, 1)
        self.assertEqual(updated.records[0].values, ("External", "001", "A", "600", "T2"))
        self.assertEqual(updated.records[1].values[2], "Offline")
        write_people(self.path, ("Branch", "Personnel ID", "Name", "Pay", "Team"),
                     [("Beta", "002", "B", "900", "T1")])
        after_delete = self.store.view(read_workbook(self.path))
        self.assertEqual([row.values[2] for row in after_delete.records], ["Offline"])
        self.assertEqual(after_delete.pending, 1)
        reset = self.store.clear(after_delete.snapshot)
        self.assertEqual(reset.records[0].values[2], "B")

    def test_ambiguous_duplicates_and_conflicting_sources(self):
        fields = ("Name", "Branch")
        write_people(self.path, fields, [("A", "North"), ("A", "North")])
        snapshot = read_workbook(self.path)
        original = self.store.view(snapshot)
        self.assertEqual(self.store.change(original, "delete", original.records[0], {}).hidden, 1)
        write_people(self.path, fields, [("A", "North")])
        with self.assertRaisesRegex(WorkbookError, "source_changed"):
            self.write(snapshot, "edit", snapshot.rows[0].number, {"branch": "East"})
        after = self.store.view(read_workbook(self.path))
        self.assertEqual(after.pending, 1)
        self.assertEqual(after.records[0].values[0], "A")
        lock = self.path.with_name("~$" + self.path.name)
        lock.touch()
        with self.assertRaisesRegex(WorkbookError, "excel_open"):
            self.write(after.snapshot, "delete", after.records[0].source.number, {})

    def test_external_query_parts_are_untouched(self):
        write_people(self.path, ("Name", "Branch"), [("A", "North")], table=True)
        parts = {
            "xl/connections.xml": b"<standIn>connection content</standIn>",
            "xl/queryTables/queryTable1.xml": b"<standIn>query metadata</standIn>",
            "customXml/item1.xml": b"<standIn>custom metadata</standIn>",
        }
        with ZipFile(self.path, "a") as workbook:
            for name, blob in parts.items():
                workbook.writestr(name, blob)
        snapshot = read_workbook(self.path)
        self.write(snapshot, "add", None, {"name": "B", "branch": "South"})
        with ZipFile(self.path) as result:
            for name, blob in parts.items():
                self.assertEqual(result.read(name), blob)

    def test_formula_cells_and_search_across_thousands(self):
        columns = ("Name", "Personnel ID", "Pay", "Date", "Calculated")
        with xlsxwriter.Workbook(self.path, {"constant_memory": True}) as workbook:
            sheet = workbook.add_worksheet()
            sheet.write_row(0, 0, columns)
            for i in range(1, 12_001):
                sheet.write_row(i, 0, (f"پرسنل {i}", f"{i:06}", i, "1405/09/05"))
            sheet.write_formula(12_000, 4, "=C12001+2", None, 12002)
        source = read_workbook(self.path)
        records = self.store.view(source).records
        self.assertEqual(len(records), 12_000)
        hit = filter_records(records, source.columns, "۰۱۲۰۰۰", "personnel id")
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].values[4], "12002")
        rules = (Rule("pay", "between", "۱۱۹۹۹", "12,000"), Rule("date", "at_least", "1405-9-5"))
        self.assertEqual(len(filter_records(records, source.columns, rules=rules)), 2)
        self.assertEqual(len(filter_records(records, source.columns, rules=rules, any_rule=True)), 12_000)
        with self.assertRaisesRegex(WorkbookError, "formula_read_only"):
            self.write(source, "edit", 12001, {"calculated": "3"})


class ReportTests(unittest.TestCase):
    def test_editable_outputs_and_formula_text(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            data = Report("گزارش آزمایشی", ("نام", "کد پرسنلی", "متن"), (
                ("آوا & همکار", "000123", "=@do_not_run()"),
                ("کیان", "000124", "ردیف دوم\nسطر دوم"),
            ), "fa", "modern")
            for kind in ("xlsx", "docx", "pptx", "csv"):
                export(data, root / f"summary.{kind}")
            with ZipFile(root / "summary.xlsx") as book:
                sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
                self.assertIsNone(sheet.find(f".//{{{MAIN}}}f"))
                self.assertIn(b"=@do_not_run()", book.read("xl/worksheets/sheet1.xml"))
            word = Document(root / "summary.docx")
            self.assertEqual(word.tables[0].cell(1, 1).text, "آوا & همکار")
            self.assertEqual(word.tables[0].cell(1, 2).text, "000123")
            slides = Presentation(root / "summary.pptx")
            table = [shape.table for shape in slides.slides[0].shapes if shape.has_table][0]
            self.assertEqual(table.cell(1, 2).text, "آوا & همکار")
            with (root / "summary.csv").open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.reader(handle))[1][2], "'=@do_not_run()")
