from __future__ import annotations

import unittest

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from niro.record_fields import _DatePicker, _date_text, date_from_text, DateField, GREGORIAN, JALALI
from niro.widgets import DistinctValuesCombo, RecordDialog
from niro.workbook import Column, label_key


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_solar_hijri_leap_day_and_persian_digits(self):
        self.assertEqual(date_from_text("۱۴۰۳/۰۱/۰۱"), QDate(2024, 3, 20))
        self.assertEqual(date_from_text("۱۴۰۳۰۱۰۱"), QDate(2024, 3, 20))
        self.assertEqual(date_from_text("١٤٠٣/١٢/٣٠"), QDate(2025, 3, 20))
        self.assertFalse(date_from_text("1402/12/30").isValid())
        self.assertEqual(date_from_text("2024-02-29 04:05:06"), QDate(2024, 2, 29))

    def test_calendar_switch_preserves_absolute_day_and_today(self):
        original = QDate(2024, 3, 20)
        picker = _DatePicker(None, True, original, JALALI)
        self.assertEqual((picker.calendar.yearShown(), picker.calendar.monthShown()), (1403, 1))
        picker.kind.setCurrentIndex(1)
        self.assertEqual((picker.calendar.yearShown(), picker.calendar.monthShown()), (2024, 3))
        self.assertEqual(picker.calendar.selectedDate(), original)
        picker.kind.setCurrentIndex(0)
        self.assertEqual(picker.calendar.selectedDate(), original)
        today = next(btn for btn in picker.findChildren(QPushButton) if btn.text() == "امروز")
        today.click()
        self.assertEqual(picker.calendar.selectedDate(), QDate.currentDate())

    def test_uses_original_representation_until_changed(self):
        day = QDate(2024, 3, 20)
        self.assertEqual(_date_text(day, JALALI, " ۱۴۰۳.۱.۱ "), " ۱۴۰۳.۱.۱ ")
        self.assertEqual(_date_text(day.addDays(1), JALALI, "۱۴۰۳.۰۱.۰۱"), "۱۴۰۳.۰۱.۰۲")
        self.assertEqual(_date_text(day, GREGORIAN, "1403/01/01"), "2024-03-20")
        self.assertEqual(_date_text(day.addDays(1), GREGORIAN, "2024-03-20 09:30"), "2024-03-21 09:30")


class RecordFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dynamic_catalogs_calendar_and_window_controls(self):
        labels = ("وضعیت مسکن", "تاریخ تولد", "محل سکونت", "رتبه")
        columns = tuple(Column(label_key(label), label, index) for index, label in enumerate(labels, start=1))
        dialog = RecordDialog(None, "fa", columns, None, "add", {
            "وضعیت مسکن": (), "محل سکونت": ("اهواز", "شیراز"), "تاریخ تولد": ("1403/01/01",)
        })
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint)
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)
        self.assertIsInstance(dialog.editors["رتبه"], QLineEdit)
        birth = dialog.editors["تاریخ تولد"]
        self.assertIsInstance(birth, DateField)
        self.assertIsInstance(birth.input, DistinctValuesCombo)
        birth.input.setCurrentIndex(0)
        housing = dialog.editors["وضعیت مسکن"]
        self.assertIn("مالک", housing.value_model.values)
        housing.setEditText("تعاونی")
        residence = dialog.editors["محل سکونت"]
        residence.setCurrentIndex(1)
        sent = []
        dialog.submitted.connect(sent.append)
        dialog.save.click()
        self.assertEqual(sent, [("local", {"وضعیت مسکن": "تعاونی", "تاریخ تولد": "1403/01/01", "محل سکونت": "شیراز"})])
