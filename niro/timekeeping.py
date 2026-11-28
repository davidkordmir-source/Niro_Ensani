"""Import clocks and calculate attendance against locally configured shifts."""

from collections import defaultdict
import csv
from dataclasses import dataclass
from decimal import Decimal
from io import StringIO
import re

from PySide6.QtCore import QDate

from .office import Entry, iso_day
from .record_fields import date_from_text
from .workbook import WorkbookError, normalize, read_workbook


SHIFT_DEFAULT = {"start": 8 * 60, "end": 16 * 60, "grace": 0, "weekdays": [1, 2, 3, 4, 6, 7], "holidays": []}
TIME = re.compile(r"(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$")
CSV_FIELDS = {
    "code": ("کد پرسنلی", "شماره پرسنلی", "کد ملی", "شناسه", "کد", "personnel id", "employee id", "employee code", "code", "id", "user id"),
    "date": ("تاریخ", "تاریخ تردد", "روز", "date", "day", "attendance date"),
    "in": ("ورود", "ساعت ورود", "اولین تردد", "in", "entry", "clock in", "check in", "start"),
    "out": ("خروج", "ساعت خروج", "آخرین تردد", "out", "exit", "clock out", "check out", "end"),
    "stamp": ("زمان تردد", "تاریخ و ساعت", "تاریخ و زمان", "timestamp", "punch time", "datetime", "date time"),
}


def minute_value(text):
    value = normalize(text)
    match = TIME.fullmatch(value)
    if match:
        hour, minute = map(int, match.groups())
        return 60 * hour + minute if hour < 24 and minute < 60 else None
    if re.fullmatch(r"0(?:\.\d+)?", value):
        minutes = round(Decimal(value) * 1440)
        return minutes if minutes < 1440 else None
    return None


def time_label(minutes):
    if minutes is None:
        return "—"
    hour, minute = divmod(minutes % 1440, 60)
    return f"{hour:02}:{minute:02}"


def _table(path):
    if path.suffix.lower() == ".xlsx":
        sheet = read_workbook(path)
        return tuple(col.label for col in sheet.columns), tuple((row.number, row.values) for row in sheet.rows)
    if path.suffix.lower() not in (".csv", ".tsv", ".txt"):
        raise WorkbookError("bad_clock_format")
    content = path.read_bytes()
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = content.decode("utf-16")
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1256")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
    rows = csv.reader(StringIO(text, newline=""), dialect)
    return next(rows, ()), tuple((line, row) for line, row in enumerate(rows, 2) if any(cell.strip() for cell in row))


def _code_map(people):
    exact, numeric = defaultdict(set), defaultdict(set)
    for person in people:
        if not person.tokens:
            continue
        for code in map(normalize, person.codes):
            exact[code].add(person.tokens[0])
            if code.isascii() and code.isdigit():
                numeric[code.lstrip("0") or "0"].add(person.tokens[0])
    return exact, numeric


def read_clock(path, people, existing=()):
    """Return a preview with one daily record per person and line-level errors."""
    headings, rows = _table(path)
    names = {normalize(title.replace("_", " ").replace("-", " ")): index for index, title in enumerate(headings)}
    fields = {field: next((names[normalize(alias)] for alias in aliases if normalize(alias) in names), None)
              for field, aliases in CSV_FIELDS.items()}
    raw = fields["stamp"] is not None and fields["in"] is None
    if fields["code"] is None or not raw and (fields["date"] is None or fields["in"] is None or fields["out"] is None):
        raise WorkbookError("bad_clock_headers")
    exact, numeric = _code_map(people)
    punches = defaultdict(list)
    errors = []

    for line, values in rows:
        def text(field):
            slot = fields[field]
            return values[slot].strip() if slot is not None and slot < len(values) else ""

        code = normalize(text("code"))
        matches = exact.get(code, ()) or numeric.get(code.lstrip("0") or "0", ()) if code else ()
        if len(matches) != 1:
            errors.append((line, "clock_code", text("code")))
            continue
        time_stamp = text("stamp") if raw else ""
        date, time = (re.split(r"[ T]", time_stamp, maxsplit=1) + [""])[:2] if raw else (text("date"), text("in"))
        day = date_from_text(date)
        start = minute_value(time)
        end = None if raw else minute_value(text("out"))
        if not day.isValid() or start is None or not raw and text("out") and end is None:
            errors.append((line, "clock_value", time_stamp or text("date")))
            continue
        if end is not None and end < start:
            end += 1440
        punches[next(iter(matches)), iso_day(day)].append((start, end))

    old = {(entry.subject, entry.day): entry for entry in existing}
    entries = []
    for (subject, day), values in sorted(punches.items()):
        start = min(value[0] for value in values)
        finishes = [value[0] if raw else value[1] for value in values]
        end = max((value for value in finishes if value is not None and value > start), default=None)
        if raw and len(values) == 1 and (saved := old.get((subject, day))):
            start, end = (min(start, saved.data["in"]), max(start, saved.data["out"] or saved.data["in"]))
            if end == start:
                end = None
        entries.append(Entry("punch", subject, day, {"in": start, "out": end}, day))
    return tuple(entries), tuple(errors)


def shift_length(shift):
    return (shift["end"] - shift["start"]) % 1440


def workday(day, shift):
    return day.dayOfWeek() in shift["weekdays"] and iso_day(day) not in shift["holidays"]


def attendance(punch, shift):
    start, end = punch.data["in"], punch.data["out"]
    if end is None or end <= start:
        return None, None, None
    worked = end - start
    if not workday(date_from_text(punch.day), shift):
        return worked, 0, worked
    offset = (start - shift["start"] + 720) % 1440 - 720
    return worked, max(0, offset - shift["grace"]), max(0, offset + worked - shift_length(shift))


def leave_days(leaves, first, last, shift):
    used = defaultdict(int)
    allowance = shift_length(shift)
    for entry in leaves:
        day = max(first, date_from_text(entry.day))
        until = min(last, date_from_text(entry.data["end"]))
        while day <= until:
            if workday(day, shift):
                key = iso_day(day)
                used[key] = min(allowance, used[key] + (entry.data.get("minutes", 0) or allowance))
            day = day.addDays(1)
    return dict(used)
