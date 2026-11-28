"""Excel-backed workforce metrics, workbook identities, and sport attendance."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from math import ceil, isfinite
import re

from PySide6.QtCore import QCalendar, QDate

from .dashboard import _DEPARTED, _EXIT_COLUMNS, _START_COLUMNS, _STATUS_COLUMNS, _key
from .locale import PERSIAN_DIGITS, translate
from .record_fields import date_from_text
from .schema import PROFILE_HEADERS, PROFILE_SHEET, SPORT_HEADERS, SPORT_SHEET
from .state import View
from .workbook import (
    Snapshot, WorkbookError, companion_snapshots, create_companion_sheet, label_key, normalize, read_workbook, update_workbook,
)


FIELD_NAMES = {
    "department": ("مدیریت", "بخش", "مدیریت سازمانی", "واحد سازمانی", "واحد", "بخش مدیریت", "دپارتمان", "department", "division", "business unit", "team"),
    "satisfaction": ("رضایت شغلی (%)", "نرخ رضایت شغلی", "درصد رضایت شغلی", "رضایت شغلی", "امتیاز رضایت شغلی", "job satisfaction", "satisfaction score", "satisfaction"),
    "tenure": ("سابقه کار (سال)", "میانگین سابقه کار", "سابقه کار", "سابقه کاری", "سابقه خدمت", "سنوات", "work experience", "tenure", "years of service", "tenure (years)", "experience (years)"),
    "start": _START_COLUMNS + ("تاریخ ورود",),
    "exit": _EXIT_COLUMNS,
    "membership": ("عضویت", "نوع عضویت", "وضعیت عضویت", "membership", "membership type"),
    "birth": ("تاریخ تولد", "تولد", "date of birth", "birth date", "dob"),
    "age": ("سن", "سن کارکنان", "age", "age (years)"),
    "leave": ("تعداد مرخصی", "تعداد مرخصی های رفته", "مرخصی رفته", "تعداد دفعات مرخصی", "leave count", "leave occurrences", "leaves taken"),
    "delay": ("تأخیر (دقیقه)", "تاخیر (دقیقه)", "میزان تأخیر", "میزان تاخیر", "مجموع تاخیر", "تأخیر", "تاخیر", "دقایق تاخیر", "delay minutes", "late minutes", "lateness (minutes)", "lateness in minutes", "lateness", "delay"),
    "education": ("سطح تحصیلات", "مدرک تحصیلی", "تحصیلات", "education level", "education", "qualification"),
    "employment": _STATUS_COLUMNS,
}
METRICS = ("total", "hires", "movement", "satisfaction", "tenure", "membership", "department", "age", "leave", "delay", "education", "sport")
GROUPS = ("overall", "membership", "department", "education", "age", "employment", "person")
SPORT_STATES = {"present": "حاضر", "absent": "غایب", "excused": "غیبت موجه"}
_SPORT_NAMES = {
    _key(text): key for key, words in {
        "present": ("حاضر", "حضور", "present"),
        "absent": ("غایب", "غیبت", "عدم حضور", "absent"),
        "excused": ("غیبت موجه", "موجه", "excused", "excused absence"),
    }.items() for text in words
}
_PERSONNEL = ("کد پرسنلی", "شماره پرسنلی", "شناسه پرسنلی", "personnel id", "personnel number", "employee id", "employee code", "employee number", "staff id", "id", "uid")
_NATIONAL = ("کد ملی", "شماره ملی", "national id", "national number")
_FIRST = ("نام", "first name", "given name")
_LAST = ("نام خانوادگی", "last name", "family name", "surname")
_FULL = ("نام و نام خانوادگی", "نام کامل", "full name", "name")
_SCOPE = ("شناسه شیت پرسنل", "personnel sheet id", "sheet id")
_AGES = ("age_under25", "age_25_34", "age_35_44", "age_45_54", "age_55")
_SATISFACTION = {
    _key(text): score for score, words in (
        (100, ("بسیار راضی", "کاملاً راضی", "very satisfied")),
        (75, ("راضی", "satisfied")),
        (50, ("متوسط", "خنثی", "neutral")),
        (25, ("ناراضی", "dissatisfied")),
        (0, ("بسیار ناراضی", "کاملاً ناراضی", "very dissatisfied")),
    ) for text in words
}


def tr(lang, key, **kwargs):
    return translate(lang, "ins_" + key, **kwargs)


def _header(text):
    return re.sub(r"[()\u060c%٪]+", " ", _key(text)).strip()


def _position(columns, accepted):
    positions = {_header(col.label): i for i, col in reversed(tuple(enumerate(columns)))}
    return next((positions[_header(value)] for value in accepted if _header(value) in positions), None)


def _text(values, index):
    return values[index].strip() if index is not None else ""


def _binding_positions(columns, overrides):
    by_key = {col.key: i for i, col in enumerate(columns)}
    return {field: by_key.get(overrides.get(field), _position(columns, aliases)) for field, aliases in FIELD_NAMES.items()}


@lru_cache(maxsize=4096)
def _calendar_date(value: str) -> QDate:
    if iso := re.fullmatch(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?", normalize(value)):
        value = iso[1]
    return date_from_text(value) if value else QDate()


def date_label(day: QDate, language: str, *, full=True) -> str:
    if not day.isValid():
        return ""
    if language == "fa":
        parts = QCalendar(QCalendar.System.Jalali).partsFromDate(day)
        value = f"{parts.year:04d}/{parts.month:02d}/{parts.day:02d}" if full else f"{parts.month:02d}/{parts.day:02d}"
        return value.translate(PERSIAN_DIGITS)
    return day.toString("yyyy-MM-dd" if full else "MM-dd")


def format_value(value, language: str, unit: str = "") -> str:
    if value is None:
        return "—"
    digits = f"{value:,.1f}".removesuffix(".0")
    if language == "fa":
        digits = digits.translate(PERSIAN_DIGITS).replace(".", "٫")
    if unit == "percent":
        return digits + ("٪" if language == "fa" else "%")
    if language == "en" and value == 1 and unit in ("person", "year", "occurrence"):
        return digits + " " + {"person": "person", "year": "year", "occurrence": "entry"}[unit]
    return digits + (" " + tr(language, "unit_" + unit) if unit else "")


def _number(text):
    value = normalize(text).replace("٬", "").replace(",", "").replace("٫", ".").replace("−", "-")
    if not re.fullmatch(r"[+]?\d+(?:\.\d+)?", value):
        return None
    parsed = float(value)
    return parsed if isfinite(parsed) and parsed <= 1_000_000_000 else None


def _delay(text):
    value = normalize(text)
    clock = re.fullmatch(r"(\d{1,8}):([0-5]\d)(?::([0-5]\d))?", value)
    if clock:
        return int(clock[1]) * 60 + int(clock[2]) + int(clock[3] or 0) / 60
    return _number(re.sub(r"\s*(دقیقه|minutes?)$", "", value))


def _experience(text):
    text = normalize(text).replace("٫", ".").replace("٬", "").replace(",", "")
    if plain := re.fullmatch(r"([\d.+]+)\s*(سال|years?|ماه|months?)?", text):
        value = _number(plain[1])
        return value / 12 if value is not None and plain[2] in ("ماه", "month", "months") else value
    english_or_persian = re.fullmatch(r"(\d+)\s*(سال|years?)\s*(?:و|and)?\s*(\d+)\s*(ماه|months?)", text)
    if english_or_persian:
        value, months = _number(english_or_persian[1]), _number(english_or_persian[3])
        if value is not None and months is not None:
            return value + months / 12
    return None


def _satisfaction(text, scale):
    key = _key(text)
    if key in _SATISFACTION:
        return _SATISFACTION[key]
    number = _number(key.rstrip("%٪").strip())
    if number is None:
        return None
    if key.endswith(("%", "٪")) or scale == "percent":
        return number if number <= 100 else None
    return 20 * number if 1 <= number <= 5 else None


@dataclass(frozen=True, slots=True)
class Person:
    ref: str
    name: str
    row: int
    personnel: str
    national: str
    fields: dict[str, str]

    @property
    def stable(self) -> bool:
        return bool(self.personnel or self.national)


def person_label(person: Person, language, *, distinct=False):
    row = tr(language, "row_person", n=format_value(person.row, language))
    name = person.name or row
    identifier = person.personnel or person.national
    if not identifier and distinct and person.name:
        identifier = row
    return name + (" · " + identifier if identifier else "")


@dataclass(frozen=True, slots=True)
class Attendance:
    row: int
    ref: str | None
    personnel: str
    national: str
    name: str
    day: QDate
    session: str
    status: str
    search_text: str
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class Dataset:
    view: View
    people: tuple[Person, ...]
    profiles: Snapshot | None
    attendance_sheet: Snapshot | None
    attendance: tuple[Attendance, ...]
    invalid_attendance: int
    duplicate_attendance: int
    profile_issues: int


def _matching_row(values, positions, sheet_key, lookup):
    bound_sheet = _text(values, positions[0])
    if bound_sheet and bound_sheet != sheet_key:
        return False, None
    keys = {lookup.get((kind, value)) for kind, index in zip(("p", "n"), positions[1:]) if (value := normalize(_text(values, index)))}
    known = keys - {None}
    return True, next(iter(known)) if len(known) == 1 else None


def load_dataset(view: View | None, overrides: dict[str, str] | None = None) -> Dataset | None:
    if view is None:
        return None
    snapshot = view.snapshot
    companion = companion_snapshots(snapshot)
    columns = snapshot.columns
    bindings = overrides or {}
    indices = _binding_positions(columns, bindings)
    column_keys = {col.key for col in columns}
    manual_fields = {field for field, key in bindings.items() if key in column_keys}
    personal = _position(columns, _PERSONNEL)
    national = _position(columns, _NATIONAL)
    names = tuple(_position(columns, names) for names in (_FULL, _FIRST, _LAST))
    id_counts = [
        Counter(value for row in snapshot.rows if (value := normalize(_text(row.values, index))))
        for index in (personal, national)
    ]
    lookup: dict[tuple[str, str], str] = {}
    refs = []
    for record in view.records:
        source = record.source
        raw_ids = tuple(_text(source.values, index) if source else "" for index in (personal, national))
        ids = tuple(item if counts[normalize(item)] == 1 else "" for item, counts in zip(raw_ids, id_counts))
        aliases = tuple((kind, normalize(value)) for kind, value in zip(("p", "n"), ids) if value)
        ref = f"{aliases[0][0]}:{aliases[0][1]}" if aliases else f"{snapshot.revision}:{record.token}"
        for alias in aliases:
            lookup[alias] = ref
        refs.append((ref, ids))

    profile = companion.get(PROFILE_SHEET)
    supplements, issues = {}, 0
    if profile:
        sheet_fields = _binding_positions(profile.columns, {})
        identity = tuple(_position(profile.columns, field) for field in (_SCOPE, _PERSONNEL, _NATIONAL))
        for entry in profile.rows:
            applicable, ref = _matching_row(entry.values, identity, snapshot.sheet.key, lookup)
            if not applicable:
                continue
            if ref is None:
                issues += 1
                continue
            if ref in supplements:
                supplements[ref] = None
                issues += 1
                continue
            supplements[ref] = {key: _text(entry.values, index) for key, index in sheet_fields.items() if index is not None}

    people = []
    for seq, (record, (ref, ids)) in enumerate(zip(view.records, refs), 1):
        labels = record.values
        primary = {key: _text(labels, position) for key, position in indices.items()}
        for field, value in (supplements.get(ref) or {}).items():
            if not primary[field] and field not in manual_fields:
                primary[field] = value
        name = _text(labels, names[0]) or " ".join(part for index in names[1:] if (part := _text(labels, index)))
        people.append(Person(ref, name, record.source.number if record.source else seq, *ids, primary))

    sports = companion.get(SPORT_SHEET)
    events, invalid = [], 0
    if sports:
        fields = sports.columns
        identity = tuple(_position(fields, accepted) for accepted in (_SCOPE, _PERSONNEL, _NATIONAL))
        date_index = _position(fields, ("تاریخ جلسه", "تاریخ", "date", "session date"))
        title_index = _position(fields, ("عنوان جلسه", "session", "session title"))
        state_index = _position(fields, ("وضعیت حضور", "attendance", "status"))
        name_index = _position(fields, _FULL)
        directory_names = {person.ref: person.name for person in people}
        dates = {}
        for row in sports.rows:
            applicable, ref = _matching_row(row.values, identity, snapshot.sheet.key, lookup)
            if not applicable:
                continue
            day = _calendar_date(_text(row.values, date_index))
            status = _SPORT_NAMES.get(_key(_text(row.values, state_index)))
            if not status or not day.isValid():
                invalid += 1
                continue
            personnel, national, name, title = (_text(row.values, index)
                                                for index in (identity[1], identity[2], name_index, title_index))
            date_id = day.toJulianDay()
            if date_id not in dates:
                dates[date_id] = date_label(day, "fa") + " " + day.toString("yyyy-MM-dd")
            search = normalize(" ".join((personnel, national, name, directory_names.get(ref, ""), title, dates[date_id])))
            events.append(Attendance(row.number, ref, personnel, national, name, day, title, status, search))
        events.sort(key=lambda e: (e.day.toJulianDay(), e.row), reverse=True)
    def attendance_key(event):
        identity = event.ref or ("p:" + normalize(event.personnel) if event.personnel else "n:" + normalize(event.national) if event.national else None)
        return (identity, event.day.toJulianDay(), normalize(event.session)) if identity else None
    repeats = Counter(key for event in events if (key := attendance_key(event)) is not None)
    events = tuple(replace(event, duplicate=True) if repeats[attendance_key(event)] > 1 else event for event in events)
    return Dataset(view, tuple(people), profile, sports, events, invalid, sum(event.duplicate for event in events), issues)


def prepare_profile(data: Dataset, backup):
    snapshot = data.view.snapshot
    rows = tuple((snapshot.sheet.key, item.personnel, item.national, item.name, "", "", "", "", "", "")
                 for item in data.people if item.stable)
    return create_companion_sheet(snapshot, PROFILE_SHEET, PROFILE_HEADERS, rows, backup)


def save_attendance(data: Dataset, person_ref: str | None, day: QDate, session: str, status: str,
                    row: int | None, deleting: bool, backup) -> Snapshot:
    primary = data.view.snapshot
    existing = next((event for event in data.attendance if event.row == row), None) if row else None
    if row is not None and existing is None:
        raise WorkbookError("source_changed")
    if deleting and existing is None:
        raise WorkbookError("source_changed")
    person = next((person for person in data.people if person.ref == person_ref and person.stable), None)
    if not deleting:
        if not day.isValid() or status not in SPORT_STATES or not session.strip():
            raise WorkbookError("attendance_values")
        if person is None and (not existing or person_ref is not None):
            raise WorkbookError("attendance_identity")
        personnel = person.personnel if person else existing.personnel
        national = person.national if person else existing.national
        name = person.name if person else existing.name
        duplicates = [
            event for event in data.attendance
            if event.row != row and event.day == day and normalize(event.session) == normalize(session)
            and ((person and event.ref == person.ref) or (
                event.ref is None and not person and (
                    (personnel and normalize(event.personnel) == normalize(personnel))
                    or (national and normalize(event.national) == normalize(national))
                )
            ))
        ]
        if duplicates:
            raise WorkbookError("attendance_duplicate")
        values = (primary.sheet.key, personnel, national, name, day.toString("yyyy-MM-dd"), session.strip(), SPORT_STATES[status])
    if data.attendance_sheet is None:
        if deleting or row is not None:
            raise WorkbookError("source_changed")
        return create_companion_sheet(primary, SPORT_SHEET, SPORT_HEADERS, (values,), backup)

    target = data.attendance_sheet
    names = {label_key(col.label): col.key for col in reversed(target.columns)}
    if not deleting and any(label_key(title) not in names for title in SPORT_HEADERS):
        raise WorkbookError("attendance_columns")
    updates = {} if deleting else {names[label_key(title)]: value for title, value in zip(SPORT_HEADERS, values)}
    update_workbook(target, "delete" if deleting else "edit" if row else "add", row, updates, backup)
    return read_workbook(primary.path, (primary.sheet.key, primary.sheet.label))


@dataclass(frozen=True, slots=True)
class Series:
    label: str
    unit: str
    values: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class Chart:
    labels: tuple[str, ...]
    series: tuple[Series, ...]
    slices: tuple[tuple[str, float], ...] = ()
    scaled: tuple[Series, ...] | None = None
    table_only: bool = False


@dataclass(frozen=True, slots=True)
class MetricResult:
    key: str
    chart: Chart
    headline: str
    note: str


@dataclass(frozen=True, slots=True)
class Analysis:
    metrics: tuple[MetricResult, ...]
    compare: Chart | None


@dataclass(frozen=True, slots=True)
class Query:
    language: str
    metrics: tuple[str, ...]
    start: QDate
    end: QDate
    today: QDate
    group: str = "membership"
    person: str | None = None
    scale: str = "percent"


@dataclass(frozen=True, slots=True)
class _Observation:
    person: Person
    age: float | None
    start: QDate
    exit: QDate
    tenure: float | None
    tenure_derived: bool
    satisfaction: float | None
    leave: float | None
    delay: float | None


def _observe(person: Person, query: Query):
    raw = person.fields
    birth, start, exit = (_calendar_date(raw[field]) for field in ("birth", "start", "exit"))
    age = _number(raw["age"])
    if age is None and birth.isValid() and birth <= query.today:
        age = query.today.year() - birth.year() - ((query.today.month(), query.today.day()) < (birth.month(), birth.day()))
    if age is not None and not 0 <= age <= 125:
        age = None
    tenure = _experience(raw["tenure"])
    derived = False
    tenure_end = exit if exit.isValid() and exit <= query.today else query.today
    if tenure is None and start.isValid() and start <= tenure_end:
        tenure = start.daysTo(tenure_end) / 365.2425
        derived = True
    return _Observation(person, age, start, exit, tenure, derived,
                        _satisfaction(raw["satisfaction"], query.scale), _number(raw["leave"]), _delay(raw["delay"]))


def _age_band(value):
    if value is None:
        return None
    return _AGES[sum(value >= boundary for boundary in (25, 35, 45, 55))]


def _label(language, key):
    return tr(language, key or "unspecified")


def _groups(observations, query):
    if query.group == "overall":
        return [(tr(query.language, "all"), list(observations))]
    if query.group == "person":
        return [(person_label(item.person, query.language, distinct=True), [item]) for item in observations]
    groups = {}
    for item in observations:
        raw = _age_band(item.age) if query.group == "age" else item.person.fields[query.group]
        group = _label(query.language, raw) if query.group == "age" else raw or tr(query.language, "unspecified")
        groups.setdefault(_key(group), (group, []))[1].append(item)
    if query.group == "age":
        order = [_label(query.language, key) for key in (*_AGES, None)]
        return [groups[key] for name in order if (key := _key(name)) in groups]
    return sorted(groups.values(), key=lambda group: (-len(group[1]), normalize(group[0])))


def _average(values):
    present = [number for number in values if number is not None]
    return sum(present) / len(present) if present else None


def _sum(values):
    present = [number for number in values if number is not None]
    return sum(present) if present else None


def _events(rows, query, kind):
    values = [getattr(row, kind) for row in rows]
    known = [value for value in values if value.isValid()]
    return sum(query.start <= day <= query.end for day in known) if known else None


def _in_period(events, query):
    return [event for event in events if query.start <= event.day <= query.end]


def _attendance_rate(events):
    return 100 * sum(event.status == "present" for event in events) / len(events) if events else None


def _undated_departures(rows):
    return sum(_key(row.person.fields["employment"]) in _DEPARTED and not row.exit.isValid() for row in rows)


# The distribution cards have dedicated count breakdowns. Their comparison lines
# summarize recorded categories and average age, all over the same cohorts.
def _measures(rows, events, query):
    attendance_in_period = _in_period(events, query)
    return {
        "total": len(rows), "hires": _events(rows, query, "start"), "entered": _events(rows, query, "start"),
        "left": _events(rows, query, "exit"),
        "undated": _undated_departures(rows),
        "satisfaction": _average(item.satisfaction for item in rows),
        "tenure": _average(item.tenure for item in rows),
        "membership": sum(bool(item.person.fields["membership"]) for item in rows),
        "department": sum(bool(item.person.fields["department"]) for item in rows),
        "education": sum(bool(item.person.fields["education"]) for item in rows),
        "age": _average(item.age for item in rows),
        "leave": _sum(item.leave for item in rows), "delay": _sum(item.delay for item in rows),
        "sport": _attendance_rate(attendance_in_period),
    }


UNITS = {
    "total": "person", "hires": "person", "entered": "person", "left": "person", "undated": "person", "membership": "person",
    "department": "person", "education": "person", "satisfaction": "percent", "tenure": "year", "age": "year",
    "leave": "occurrence", "delay": "minute", "sport": "percent",
}


def _series_name(language, key):
    return tr(language, "measure_" + key)


def _comparison(groups, stats, query):
    movements = ("entered", "left", "undated") if any(row["undated"] for row in stats) else ("entered", "left")
    keys = tuple(sub for key in query.metrics for sub in (movements if key == "movement" else (key,)))
    labels = tuple(label for label, _ in groups)
    raw = tuple(Series(_series_name(query.language, key), UNITS[key], tuple(row[key] for row in stats)) for key in keys)
    if len({item.unit for item in raw if any(v is not None for v in item.values)}) <= 1:
        return Chart(labels, raw)
    if len(labels) < 2:
        return Chart(labels, raw, table_only=True)
    scaled = []
    for item in raw:
        peak = max((value for value in item.values if value is not None), default=0)
        scaled.append(Series(item.label, "percent", tuple(None if value is None else 100 * value / peak if peak else 0 for value in item.values)))
    return Chart(labels, raw, scaled=tuple(scaled))


def _timeline(observations, query, keys):
    size = query.start.daysTo(query.end) + 1
    span = next((step for step in (1, 7, 14, 30, 60, 90, 180, 365) if ceil(size / step) <= 18), ceil(size / 18))
    dates = tuple(query.start.addDays(offset) for offset in range(0, size, span))
    labels = tuple(date_label(value, query.language, full=size > 365) for value in dates)
    series = []
    for attribute, key in keys:
        valid = [getattr(row, attribute) for row in observations if getattr(row, attribute).isValid()]
        bucket = Counter(query.start.daysTo(day) // span for day in valid if query.start <= day <= query.end)
        values = tuple(bucket[index] if valid else None for index in range(len(labels)))
        series.append(Series(_series_name(query.language, key), "person", values))
    slices = tuple((s.label, sum(v for v in s.values if v is not None)) for s in series) if len(series) > 1 else tuple(
        (label, count) for label, count in zip(labels, series[0].values) if count is not None)
    return Chart(labels, tuple(series), slices)


def _distribution(rows, key, language):
    if key == "age":
        values = Counter(_age_band(item.age) for item in rows)
        items = [(_label(language, band), values[band]) for band in (*_AGES, None) if values[band]]
        defined = len(rows) - values[None]
    else:
        displays = {}
        values = Counter()
        for item in rows:
            label = item.person.fields[key]
            name = _key(label)
            displays.setdefault(name, label or tr(language, "unspecified"))
            values[name] += 1
        items = sorted(((displays[label], count) for label, count in values.items()),
                       key=lambda item: (-item[1], normalize(item[0])))
        defined = len(rows) - values[""]
    return Chart(tuple(name for name, _ in items),
                 (Series(tr(language, "measure_total"), "person", tuple(value for _, value in items)),),
                 tuple(items)), defined


def evaluate(data: Dataset, query: Query) -> Analysis:
    language = query.language
    selected = tuple(item for item in data.people if query.person is None or item.ref == query.person)
    observations = tuple(_observe(person, query) for person in selected)
    events = tuple(item for item in data.attendance if not item.duplicate and (query.person is None or item.ref == query.person))
    by_ref = defaultdict(list)
    for event in events:
        by_ref[event.ref].append(event)
    groups = _groups(observations, query)
    if not groups:
        groups = [(tr(language, "all"), [])]
    stats = []
    for _, rows in groups:
        group_events = events if query.group == "overall" else [event for row in rows for event in by_ref[row.person.ref]]
        stats.append(_measures(rows, group_events, query))
    if query.group != "overall" and "sport" in query.metrics and _in_period(by_ref[None], query):
        groups.append((tr(language, "unmatched_person"), []))
        values = {key: None for key in stats[0]}
        values["sport"] = _attendance_rate(_in_period(by_ref[None], query))
        stats.append(values)
    overall = _measures(observations, events, query)
    eligible = _in_period(events, query)
    metrics = []

    for key in query.metrics:
        note = ""
        if key in ("hires", "movement"):
            keys = (("start", "hires"),) if key == "hires" else (("start", "entered"), ("exit", "left"))
            chart = _timeline(observations, query, keys)
            if key == "movement":
                headline = tr(language, "in_out", entered=format_value(overall["entered"], language), left=format_value(overall["left"], language))
                undated = overall["undated"]
                note = tr(language, "undated_exit", count=format_value(undated, language)) if undated else tr(language, "info_movement")
            else:
                headline = format_value(overall["hires"], language, "person")
                note = tr(language, "info_hires")
        elif key in ("membership", "department", "age", "education"):
            chart, defined = _distribution(observations, key, language)
            headline = format_value(defined, language, "person") if defined or not observations else "—"
            note = tr(language, "coverage", count=format_value(defined, language), total=format_value(len(observations), language))
        elif key == "sport":
            counts = Counter(event.status for event in eligible)
            items = tuple((tr(language, state), counts[state]) for state in SPORT_STATES)
            chart = Chart(tuple(name for name, _ in items),
                          (Series(tr(language, "recorded_attendance"), "occurrence", tuple(value for _, value in items)),), items)
            headline = format_value(overall[key], language, "percent")
            note = tr(language, "attendance_basis", count=format_value(len(eligible), language))
        else:
            unit = UNITS[key]
            values = tuple(row[key] for row in stats)
            labels = tuple(label for label, _ in groups)
            value = overall[key]
            slices = tuple((label, value) for label, value in zip(labels, values) if value is not None) if key in ("total", "leave", "delay") else ()
            if key == "satisfaction" and value is not None:
                slices = ((tr(language, "measure_satisfaction"), value), (tr(language, "difference"), 100 - value))
            chart = Chart(labels, (Series(_series_name(language, key), unit, values),), slices)
            headline = format_value(value, language, unit)
            if key == "tenure":
                derived = sum(item.tenure_derived for item in observations)
                note = tr(language, "tenure_mix", count=format_value(derived, language))
            elif key == "satisfaction":
                note = tr(language, "satisfaction_basis", count=format_value(sum(item.satisfaction is not None for item in observations), language))
            elif key in ("leave", "delay"):
                count = sum(getattr(item, key) is not None for item in observations)
                note = tr(language, "coverage", count=format_value(count, language), total=format_value(len(observations), language))
        metrics.append(MetricResult(key, chart, headline, note))
    return Analysis(tuple(metrics), _comparison(groups, stats, query) if len(query.metrics) > 1 else None)
