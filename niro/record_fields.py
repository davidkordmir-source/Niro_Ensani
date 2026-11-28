"""Catalog fields and date controls for personnel forms."""

from __future__ import annotations

from dataclasses import dataclass
import re

from PySide6.QtCore import QCalendar, QDate, QLocale, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableView, QToolButton, QVBoxLayout, QWidget,
)

from .workbook import DIGITS, label_key


CATALOGS = (
    (
        ("تاهل و فرزند", "تأهل و فرزند", "وضعیت تاهل", "وضعیت تأهل", "Marital status & children", "Marital status"),
        ("مجرد", "متأهل", "متأهل بدون فرزند", "متأهل دارای فرزند"),
        ("Single", "Married", "Married without children", "Married with children"),
    ),
    (
        ("وضعیت اشتغال", "Employment status"),
        ("شاغل", "مرخصی", "بازنشسته", "مستعفی", "قطع همکاری"),
        ("Employed", "On leave", "Retired", "Resigned", "Departed"),
    ),
    (
        ("سطح تحصیلات", "تحصیلات", "Education", "Education level"),
        ("زیر دیپلم", "دیپلم", "کاردانی", "کارشناسی", "کارشناسی ارشد", "دکتری"),
        ("Below high school", "High school", "Associate", "Bachelor's", "Master's", "Doctorate"),
    ),
    (
        ("عضویت", "وضعیت عضویت", "Membership", "Membership status"),
        ("عضو", "غیرعضو"),
        ("Member", "Non-member"),
    ),
    (
        ("وضعیت مسکن", "Housing status"),
        ("مالک", "مستأجر", "سازمانی", "سایر"),
        ("Owner", "Tenant", "Employer-provided", "Other"),
    ),
    (
        ("سطح دسترسی", "Access level"),
        ("پایه", "محدود", "کامل"),
        ("Basic", "Restricted", "Full"),
    ),
    (
        ("سطح", "Level"),
        ("سطح ۱", "سطح ۲", "سطح ۳"),
        ("Level 1", "Level 2", "Level 3"),
    ),
    (
        ("رتبه", "Rank"),
        ("رتبه ۱", "رتبه ۲", "رتبه ۳"),
        ("Rank 1", "Rank 2", "Rank 3"),
    ),
)
DEFAULT_CHOICES = {label_key(name): (fa, en) for names, fa, en in CATALOGS for name in names}
DEFAULT_COMBO_COLUMNS = frozenset(DEFAULT_CHOICES)

JALALI = QCalendar.System.Jalali
GREGORIAN = QCalendar.System.Gregorian
_DATE_TEXT = re.compile(
    r"(?P<year>[0-9]{4})(?P<sep>[-/.])(?P<month>[0-9]{1,2})(?P=sep)"
    r"(?P<day>[0-9]{1,2})(?P<time>[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?)?"
)
_COMPACT_DATE = re.compile(r"(?P<year>[0-9]{4})(?P<month>[0-9]{2})(?P<day>[0-9]{2})")
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_LATIN_DIGITS = "0123456789"


@dataclass(frozen=True, slots=True)
class _DateValue:
    day: QDate
    system: QCalendar.System
    separator: str
    time: str


def _read_date(value: str) -> _DateValue | None:
    plain = value.strip().translate(DIGITS)
    match = _DATE_TEXT.fullmatch(plain) or _COMPACT_DATE.fullmatch(plain)
    if match is None:
        return None
    year, month, day = (int(match[name]) for name in ("year", "month", "day"))
    if year == 0:
        return None
    system = JALALI if year < 1600 else GREGORIAN
    date = QCalendar(system).dateFromParts(year, month, day)
    if not date.isValid():
        return None
    fields = match.groupdict()
    return _DateValue(date, system, fields.get("sep") or "", fields.get("time") or "")


def date_from_text(value: str) -> QDate:
    """Read workbook dates with either numeral and calendar convention."""
    parsed = _read_date(value)
    return parsed.day if parsed else QDate()


def is_date_column(label: str) -> bool:
    normalized = label_key(label).replace("_", " ")
    return ("تاریخ" in normalized or "زادروز" in normalized
            or normalized in {"dob", "hiredate", "startdate", "enddate", "exitdate", "dateofbirth"}
            or bool(re.search(r"\b(date|expiry|expiration|birthday)\b", normalized)))


def catalog_values(label: str, observed) -> tuple[str, ...]:
    """Use stored personnel values and seed a catalog for an empty column."""
    values = tuple(value for value in observed if value.strip())
    if values:
        return values
    pair = DEFAULT_CHOICES.get(label_key(label))
    return pair[0 if re.search(r"[\u0600-\u06ff]", label) else 1] if pair else ()


def _date_text(day: QDate, system: QCalendar.System, previous: str) -> str:
    parsed = _read_date(previous)
    if parsed and parsed.system == system and parsed.day == day:
        return previous
    parts = QCalendar(system).partsFromDate(day)
    same_calendar = parsed and parsed.system == system
    separator = parsed.separator if same_calendar else "/" if system == JALALI else "-"
    local_digits = ""
    if any(digit in previous for digit in _PERSIAN_DIGITS):
        local_digits = _PERSIAN_DIGITS
    elif any(digit in previous for digit in _ARABIC_DIGITS):
        local_digits = _ARABIC_DIGITS
    date = separator.join((f"{parts.year:04}", f"{parts.month:02}", f"{parts.day:02}"))
    if parsed:
        date += parsed.time
    return date.translate(str.maketrans(_LATIN_DIGITS, local_digits)) if local_digits else date


class DateField(QWidget):
    """A workbook text value with an optional Jalali/Gregorian date picker."""

    def __init__(self, text: str, language: str, combo: QComboBox | None = None):
        super().__init__()
        self.fa = language == "fa"
        self.input = combo if combo is not None else QLineEdit(text)
        line = self.input.lineEdit() if combo is not None else self.input
        line.setMaxLength(32767)
        line.setClearButtonEnabled(True)
        line.setPlaceholderText("سال/ماه/روز" if self.fa else "YYYY-MM-DD or YYYY/MM/DD")
        self.setFocusProxy(line)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(self.input, 1)
        self.calendar_button = QPushButton()
        self.calendar_button.setFixedWidth(41)
        caption = "انتخاب تاریخ" if self.fa else "Choose date"
        self.calendar_button.setToolTip(caption)
        self.calendar_button.setAccessibleName(caption)
        self.calendar_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.calendar_button.setIcon(self._calendar_icon())
        self.calendar_button.setAutoDefault(False)
        layout.addWidget(self.calendar_button)
        self.calendar_button.clicked.connect(self._choose)

    def recolor(self, ink: str) -> None:
        self.calendar_button.setIcon(self._calendar_icon(ink))

    def _calendar_icon(self, ink: str | None = None) -> QIcon:
        pixmap = QPixmap(36, 36)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(2)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.calendar_button.palette()
        foreground = QColor(ink) if ink else palette.color(palette.ColorRole.ButtonText)
        painter.setPen(QPen(foreground, 1.25))
        painter.drawRoundedRect(2, 4, 14, 12, 2, 2)
        painter.drawLine(2, 8, 16, 8)
        painter.drawLine(6, 2, 6, 6)
        painter.drawLine(12, 2, 12, 6)
        painter.drawPoint(6, 11)
        painter.drawPoint(9, 11)
        painter.drawPoint(12, 11)
        painter.end()
        return QIcon(pixmap)

    def text(self) -> str:
        return self.input.currentText() if isinstance(self.input, QComboBox) else self.input.text()

    def setReadOnly(self, readonly: bool) -> None:
        if isinstance(self.input, QComboBox):
            self.input.setEnabled(not readonly)
        else:
            self.input.setReadOnly(readonly)
        self.calendar_button.setEnabled(not readonly)

    def _choose(self):
        previous = self.text()
        parsed = _read_date(previous)
        dialog = _DatePicker(
            self, self.fa, parsed.day if parsed else QDate.currentDate(),
            parsed.system if parsed else JALALI if self.fa else GREGORIAN,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            value = _date_text(dialog.calendar.selectedDate(), dialog.system(), previous)
            if isinstance(self.input, QComboBox):
                self.input.setEditText(value)
            else:
                self.input.setText(value)


class _DatePicker(QDialog):
    def __init__(self, parent, fa: bool, date: QDate, calendar: QCalendar.System):
        super().__init__(parent)
        self.fa = fa
        self.setWindowTitle("انتخاب تاریخ" if fa else "Choose date")
        self.resize(476, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)

        top = QHBoxLayout()
        detail = QLabel("نوع تقویم" if fa else "Calendar")
        self.kind = QComboBox()
        self.kind.addItem("هجری شمسی" if fa else "Solar Hijri", JALALI)
        self.kind.addItem("میلادی" if fa else "Gregorian", GREGORIAN)
        self.kind.setAccessibleName(detail.text())
        detail.setBuddy(self.kind)
        self.kind.setCurrentIndex(0 if calendar == JALALI else 1)
        top.addWidget(detail)
        top.addWidget(self.kind)
        top.addStretch(1)
        today = QPushButton("امروز" if fa else "Today")
        today.setAutoDefault(False)
        top.addWidget(today)
        layout.addLayout(top)

        self.calendar = QCalendarWidget()
        self.calendar.setStyleSheet("""
            QWidget#qt_calendar_navigationbar { background: transparent; }
            QTableView::item { padding: 0 2px; }
        """)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setMinimumDate(QDate(1, 1, 1))
        self.calendar.setSelectedDate(date)
        self._change_calendar()
        layout.addWidget(self.calendar, 1)
        self.kind.currentIndexChanged.connect(self._change_calendar)
        today.clicked.connect(lambda: self.calendar.setSelectedDate(QDate.currentDate()))

        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = actions.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("انتخاب" if fa else "Select")
        ok.setObjectName("primary")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف" if fa else "Cancel")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        self.calendar.activated.connect(self.accept)
        layout.addWidget(actions)

    def system(self) -> QCalendar.System:
        return self.kind.currentData()

    def showEvent(self, event):
        super().showEvent(event)
        rtl = self.calendar.layoutDirection() == Qt.LayoutDirection.RightToLeft
        for name, right in (("qt_calendar_prevmonth", rtl), ("qt_calendar_nextmonth", not rtl)):
            button = self.calendar.findChild(QToolButton, name)
            if button is None:
                continue
            mark = QPixmap(32, 32)
            mark.fill(Qt.GlobalColor.transparent)
            mark.setDevicePixelRatio(2)
            painter = QPainter(mark)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = button.palette().color(button.palette().ColorRole.ButtonText)
            painter.setPen(QPen(color, 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            tail, point = (6, 10) if right else (10, 6)
            painter.drawLine(tail, 4, point, 8)
            painter.drawLine(point, 8, tail, 12)
            painter.end()
            button.setIcon(QIcon(mark))

    def _change_calendar(self):
        selection = self.calendar.selectedDate()
        locale = QLocale("fa_IR" if self.fa else "en_US")
        self.calendar.setLocale(locale)
        self.calendar.setCalendar(QCalendar(self.system()))
        origin = Qt.DayOfWeek.Saturday if self.system() == JALALI else locale.firstDayOfWeek()
        self.calendar.setFirstDayOfWeek(origin)
        grid = self.calendar.findChild(QTableView) or self.calendar
        grid.ensurePolished()
        ink = grid.palette().color(grid.palette().ColorRole.Text)
        weekend = {Qt.DayOfWeek.Friday} if self.fa else {Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday}
        holiday_color = QColor("#bd404c" if ink.lightness() < 128 else "#ffaca9")
        for day in range(1, 8):
            weekday = Qt.DayOfWeek(day)
            format = QTextCharFormat()
            format.setForeground(holiday_color if weekday in weekend else ink)
            self.calendar.setWeekdayTextFormat(weekday, format)
        self.calendar.setSelectedDate(selection)
