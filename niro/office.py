"""Local personnel dossiers, deadline queries and workbook quality checks."""

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
import json
from uuid import uuid4

from PySide6.QtCore import QDate

from .record_fields import date_from_text, is_date_column
from .state import _protect
from .workbook import WorkbookError, normalize


EVENT_TYPES = ("transfer", "promotion", "training", "contract", "document", "note")


def iso_day(day):
    return day.toString("yyyy-MM-dd")


def _digest(*parts):
    return sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Staff:
    ref: str
    name: str
    row: int
    tokens: tuple[str, ...]
    codes: tuple[str, ...]
    values: tuple[str, ...]

    def label(self, row_text):
        return (self.name or row_text) + (" · " + self.codes[0] if self.codes else " · " + row_text if self.name else "")


def roster(view):
    columns = view.snapshot.columns
    names = {normalize(col.title("en")): i for i, col in enumerate(columns)}
    full = next((names[name] for name in ("full name", "نام و نام خانوادگی", "employee name", "name") if name in names), None)
    parts = tuple(names[name] for name in ("first name", "last name") if name in names)
    identifiers = sorted(((i, col.key, "national" if "ملی" in col.label or "national" in normalize(col.label) else "personnel")
                          for i, col in enumerate(columns) if col.identity), key=lambda item: item[2] != "personnel")

    def fingerprint(values):
        # Column order changes preserve the reference. Duplicate rows stay distinct from stored dossiers.
        return _digest("row", *sorted((col.key, normalize(value)) for col, value in zip(columns, values) if normalize(value)))

    base = {row.number: fingerprint(row.values) for row in view.snapshot.rows}
    repeats = Counter(base.values())
    result = []
    for number, row in enumerate(view.records, 1):
        name = (row.values[full] if full is not None else "") or " ".join(row.values[i] for i in parts).strip()
        codes, keys = [], []
        if row.source:
            for i, column, kind in identifiers:
                raw = row.source.values[i].strip()
                value = normalize(raw)
                if value and view.identity_counts[column, value] == 1:
                    codes.append(raw)
                    keys.append(_digest(kind, value))
            previous = base[row.source.number]
            if repeats[previous] == 1:
                keys.append(previous)
        elif row.overlay:
            keys.append(_digest("local", row.overlay.uid))
        result.append(Staff(row.token, name, row.source.number if row.source else number,
                            tuple(dict.fromkeys(keys)), tuple(dict.fromkeys(codes)), row.values))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Entry:
    kind: str
    subject: str
    day: str
    data: dict
    key: str = field(default_factory=lambda: uuid4().hex)


class OfficeStore:
    def __init__(self, store):
        self.store = store
        with store._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS office (
                scope TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL, entry_key TEXT NOT NULL,
                day TEXT NOT NULL, data BLOB NOT NULL, attachment BLOB,
                PRIMARY KEY (scope, kind, subject, entry_key)
            ) WITHOUT ROWID""")
            db.execute("CREATE INDEX IF NOT EXISTS office_dates ON office(scope, kind, day)")

    def entries(self, scope, kind, people=None, start="", end="9999-12-31"):
        with self.store._connection() as db:
            cursor = db.execute("""SELECT subject, entry_key, day, data FROM office
                WHERE scope=? AND kind=? AND day BETWEEN ? AND ? ORDER BY day DESC, entry_key DESC""",
                                (scope, kind, start, end))
            return tuple(Entry(kind, subject, day, json.loads(_protect(data, decrypt=True)), key)
                         for subject, key, day, data in cursor if people is None or subject in people)

    def save(self, scope, entries, actor, action, detail="", *, attachment=None):
        if not entries:
            return
        content = _protect(b"\x01" + attachment) if attachment is not None else None
        records = ((scope, item.kind, item.subject, item.key, item.day,
                    _protect(json.dumps(item.data, ensure_ascii=False).encode()), content) for item in entries)
        with self.store._connection() as db:
            db.executemany("""INSERT INTO office VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(scope,kind,subject,entry_key) DO UPDATE SET
                day=excluded.day, data=excluded.data, attachment=coalesce(excluded.attachment,office.attachment)""", records)
            self.store.write_audit(db, actor, scope, action, detail)

    def remove(self, scope, entry, actor, action):
        with self.store._connection() as db:
            db.execute("DELETE FROM office WHERE scope=? AND kind=? AND subject=? AND entry_key=?",
                       (scope, entry.kind, entry.subject, entry.key))
            self.store.write_audit(db, actor, scope, action, entry.day)

    def attachment(self, scope, entry):
        with self.store._connection() as db:
            row = db.execute("SELECT attachment FROM office WHERE scope=? AND kind=? AND subject=? AND entry_key=?",
                             (scope, entry.kind, entry.subject, entry.key)).fetchone()
        if not row or row[0] is None:
            raise WorkbookError("missing_attachment")
        return _protect(row[0], decrypt=True)[1:]


def deadline_column(column):
    caption = normalize(column.label)
    return (any(word in caption for word in ("پایان قرارداد", "اتمام قرارداد", "انقضا", "سررسید", "معتبر تا",
                                              "expiry", "expiration", "contract end", "due date", "valid until"))
            or "اعتبار" in caption and is_date_column(caption))


@dataclass(frozen=True, slots=True)
class Reminder:
    person: Staff
    title: str
    due: str


def reminders(view, staff, events, today, lead, date_columns, required):
    limit = today.addDays(lead)
    since = tuple((i, column) for i, column in enumerate(view.snapshot.columns) if column.key in date_columns)
    mandatory = tuple((i, column) for i, column in enumerate(view.snapshot.columns) if column.key in required)
    people = {key: person for person in staff for key in person.tokens}
    notices = []
    for person in staff:
        for i, column in since:
            day = date_from_text(person.values[i])
            if day.isValid() and day <= limit:
                notices.append(Reminder(person, column.label, iso_day(day)))
        for i, column in mandatory:
            if not person.values[i].strip():
                notices.append(Reminder(person, column.label, ""))
    for event in events:
        due = date_from_text(event.data.get("due", ""))
        person = people.get(event.subject)
        if person and due.isValid() and due <= limit:
            notices.append(Reminder(person, event.data["title"], iso_day(due)))
    return tuple(sorted(notices, key=lambda item: (not item.due, item.due, item.person.name, item.title)))


def workbook_issues(view):
    """Inspect raw workbook data while keeping row numbers useful in Excel."""
    snapshot = view.snapshot
    identities = tuple((i, field) for i, field in enumerate(snapshot.columns) if field.identity)
    dates = tuple((i, field) for i, field in enumerate(snapshot.columns) if is_date_column(field.label) or deadline_column(field))
    same_rows = Counter(tuple(map(normalize, row.values)) for row in snapshot.rows)
    issues = []
    for row in snapshot.rows:
        if identities and not any(row.values[i].strip() for i, _ in identities):
            issues.append((row.number, "missing_id", "", ""))
        for i, field in identities:
            value = normalize(row.values[i])
            if value and view.identity_counts[field.key, value] > 1:
                issues.append((row.number, "duplicate_id", field.label, row.values[i]))
        for i, field in dates:
            value = row.values[i].strip()
            if value and not date_from_text(value).isValid():
                issues.append((row.number, "bad_date", field.label, value))
        if same_rows[tuple(map(normalize, row.values))] > 1:
            issues.append((row.number, "duplicate_row", "", ""))
    return tuple(issues)


def preview_edits(snapshot, field, old, new, *, trim=False):
    found = next(((i, col.key) for i, col in enumerate(snapshot.columns) if col.key == field), None)
    if found is None:
        return ()
    index, key = found
    changes = []
    for item in snapshot.rows:
        current = item.values[index]
        if key in item.formulas or (not trim and normalize(current) != normalize(old)):
            continue
        planned = current.strip() if trim else new
        if planned != current:
            changes.append((item.number, current, planned))
    return tuple(changes)
