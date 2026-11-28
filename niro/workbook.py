"""Read OOXML personnel tables and save narrow, atomic changes to their source."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import os
import posixpath
import re
import stat
from tempfile import mkstemp
import unicodedata
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from lxml import etree as ET

from .schema import COMPANION_SHEETS, FIELDS, PRIMARY_SHEET


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
BOOK = "xl/workbook.xml"
BOOK_RELS = "xl/_rels/workbook.xml.rels"
MAX_ROW = 1_048_576
MAX_COL = 16_384
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يكۀة", "01234567890123456789یکهه")
ADDRESS = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")
INVALID_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]")
EXCEL_ESCAPE = re.compile(r"_x([0-9a-fA-F]{4})_")
COLUMN_KEY = "\x1f"


class WorkbookError(Exception):
    """A stable error code the interface can translate."""


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).translate(DIGITS)
    value = value.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    return " ".join(value.casefold().split())


def label_key(label: str) -> str:
    return normalize(label)


def is_companion(label: str) -> bool:
    return label_key(label) in _COMPANION_KEYS


_COMPANION_KEYS = frozenset(map(label_key, COMPANION_SHEETS))


TRANSLATED_LABELS = {label_key(fa): en for fa, en in FIELDS}
ID_LABELS = {
    "کد ملی", "کد پرسنلی", "شماره پرسنلی", "شناسه پرسنلی",
    "national id", "personnel id", "employee id", "employee number", "employee code",
    "personnel number", "staff id", "id", "uid",
}
TEXT_ID_LABELS = ID_LABELS | {
    "شماره موبایل", "شماره تلفن داخلی", "شماره موبایل داخلی", "شماره تماس ضروری",
    "شماره گذرنامه", "شماره حساب", "شماره شبا", "mobile phone", "bank account", "iban",
    "passport number", "office extension", "emergency phone", "internal mobile phone",
}


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    label: str
    index: int

    def title(self, language: str) -> str:
        return TRANSLATED_LABELS.get(label_key(self.label), self.label) if language == "en" else self.label

    @property
    def identity(self) -> bool:
        return label_key(self.label) in ID_LABELS


@dataclass(frozen=True, slots=True)
class SourceRow:
    number: int
    values: tuple[str, ...]
    formulas: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Sheet:
    key: str
    label: str
    part: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    path: Path
    stamp: tuple[int, ...]
    revision: str
    sheet: Sheet
    sheets: tuple[Sheet, ...]
    columns: tuple[Column, ...]
    rows: tuple[SourceRow, ...]


def stamp(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino, info.st_dev


def _read_bytes(path: Path) -> tuple[bytes, tuple[int, ...]]:
    if path.suffix.lower() != ".xlsx":
        raise WorkbookError("xlsx_only")
    before = stamp(path)
    with path.open("rb") as handle:
        content = handle.read()
    if before != stamp(path):
        raise WorkbookError("file_changing")
    return content, before


def _tag(name: str) -> str:
    return f"{{{MAIN}}}{name}"


def _xml(archive: ZipFile, member: str) -> ET._Element:
    if archive.getinfo(member).file_size > 384 * 1024 * 1024:
        raise WorkbookError("workbook_too_large")
    parser = ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = ET.fromstring(archive.read(member), parser=parser)
    if root.getroottree().docinfo.doctype:
        raise WorkbookError("invalid_workbook")
    return root


def _xml_bytes(root: ET._Element) -> bytes:
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _relations(archive: ZipFile, owner: str) -> list:
    directory, name = posixpath.split(owner)
    member = posixpath.join(directory, "_rels", name + ".rels")
    if member not in archive.namelist():
        return []
    return list(_xml(archive, member))


def _target(owner: str, relationship: ET._Element) -> str:
    path = relationship.get("Target", "")
    if path.startswith("/"):
        return path.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(owner), path))


def _parts(archive: ZipFile, owner: str, kind: str) -> dict[str, str]:
    return {
        link.get("Id"): _target(owner, link)
        for link in _relations(archive, owner)
        if link.get("Type", "").endswith("/" + kind) and link.get("TargetMode") != "External"
    }


def _column_index(address: str) -> int:
    result = 0
    for letter in address:
        result = result * 26 + ord(letter) - ord("A") + 1
    return result


def _letter(index: int) -> str:
    result = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _bounds(value: str) -> tuple[int, int, int, int]:
    start, _, end = value.replace("$", "").partition(":")
    first = ADDRESS.fullmatch(start)
    last = ADDRESS.fullmatch(end or start)
    if not first or not last:
        raise WorkbookError("invalid_workbook")
    return _column_index(first[1]), int(first[2]), _column_index(last[1]), int(last[2])


def _string(text: str) -> str:
    decoded = EXCEL_ESCAPE.sub(lambda match: chr(int(match[1], 16)), text)
    if re.search(r"[\ud800-\udfff]", decoded):
        decoded = decoded.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le", errors="replace")
    return INVALID_XML.sub("", decoded)


def _rich_text(parent: ET._Element | None) -> str:
    if parent is None:
        return ""
    texts = parent.findall(_tag("t")) + parent.findall(f"{_tag('r')}/{_tag('t')}")
    return _string("".join(item.text or "" for item in texts))


BUILTIN_DATES = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))


def _date_value(raw: str, format_id: str, date1904: bool) -> str:
    try:
        day = float(raw)
        if format_id in ("18", "19", "20", "21", "45", "46", "47"):
            seconds = round(day * 86400)
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02}:{minutes:02}:{seconds:02}"
        epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
        if not date1904 and 0 < day < 60:
            day += 1
        value = epoch + timedelta(days=day)
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ", timespec="seconds")
    except (OverflowError, ValueError):
        return raw


class _Reader:
    def __init__(self, archive: ZipFile):
        self.archive = archive
        self.book = _xml(archive, BOOK)
        sheet_parts = _parts(archive, BOOK, "worksheet")
        self.sheets = tuple(
            Sheet(node.get("sheetId") or node.get(f"{{{OFFICE_REL}}}id"), node.get("name", ""),
                  sheet_parts[node.get(f"{{{OFFICE_REL}}}id")])
            for node in self.book.findall(f"{_tag('sheets')}/{_tag('sheet')}")
            if node.get("state", "visible") == "visible" and node.get(f"{{{OFFICE_REL}}}id") in sheet_parts
        )
        if not self.sheets:
            raise WorkbookError("no_headers")
        shared = next(iter(_parts(archive, BOOK, "sharedStrings").values()), None)
        self.strings = tuple(_rich_text(node) for node in _xml(archive, shared)) if shared else ()
        styles = next(iter(_parts(archive, BOOK, "styles").values()), None)
        self.formats: list[tuple[int, str]] = []
        if styles:
            root = _xml(archive, styles)
            custom = {int(node.get("numFmtId")): node.get("formatCode", "") for node in root.findall(f"{_tag('numFmts')}/{_tag('numFmt')}")}
            for node in root.findall(f"{_tag('cellXfs')}/{_tag('xf')}"):
                number = int(node.get("numFmtId", "0"))
                self.formats.append((number, custom.get(number, "")))
        props = self.book.find(_tag("workbookPr"))
        self.date1904 = props is not None and props.get("date1904") in ("1", "true")

    def _style(self, cell: ET._Element) -> tuple[int, str]:
        style = int(cell.get("s", "0"))
        return self.formats[style] if 0 <= style < len(self.formats) else (0, "")

    def _value(self, cell: ET._Element | None) -> str:
        if cell is None:
            return ""
        cell_type = cell.get("t", "n")
        if cell_type == "inlineStr":
            return _rich_text(cell.find(_tag("is")))
        raw = cell.findtext(_tag("v"), "")
        if not raw:
            return ""
        if cell_type == "s":
            return self.strings[int(raw)]
        if cell_type == "b":
            return "TRUE" if raw == "1" else "FALSE"
        if cell_type != "n":
            return _string(raw)
        format_id, custom = self._style(cell)
        tokens = re.sub(r'"[^"]*"|\\.|\[[^\]]+\]', "", custom.split(";")[0]).lower()
        elapsed = re.search(r"\[[hms]+\]", custom, re.IGNORECASE)
        if elapsed or format_id in BUILTIN_DATES or re.search(r"[ydhs]|m{2,}", tokens):
            time_only = format_id not in BUILTIN_DATES and not re.search(r"[yd]", tokens) and (
                "h" in tokens or elapsed
            )
            return _date_value(raw, "46" if time_only else str(format_id), self.date1904)
        if format_id in (9, 10) or "%" in tokens:
            try:
                percent = Decimal(raw) * 100
                if percent.is_finite() and -100 <= percent.adjusted() <= 100:
                    return format(percent.normalize(), "f") + "%"
            except InvalidOperation:
                pass
        if re.fullmatch(r"0{2,}", tokens):
            try:
                return f"{int(Decimal(raw)):0{len(tokens)}d}"
            except (InvalidOperation, OverflowError, ValueError):
                return raw
        if "e" in raw.lower():
            try:
                number = Decimal(raw)
                if number.is_finite() and -100 <= number.adjusted() <= 100:
                    return format(number, "f")
            except InvalidOperation:
                pass
        return raw

    def select(self, preferred: str | tuple[str, str] | None) -> "_SheetData":
        key, label = preferred if isinstance(preferred, tuple) else (preferred, preferred)

        def priority(sheet):
            if sheet.label == label:
                return 0
            if key in (sheet.key, sheet.part):
                return 1
            return 2 if sheet.label == PRIMARY_SHEET else 3

        candidates = sorted(
            (sheet for sheet in self.sheets if priority(sheet) < 2 or not is_companion(sheet.label)),
            key=priority,
        )
        if not candidates:
            raise WorkbookError("no_headers")
        for sheet in candidates:
            loaded = _SheetData(self, sheet)
            if loaded.columns or priority(sheet) < 2:
                return loaded
        return _SheetData(self, candidates[0])


def _row_cells(row: ET._Element) -> dict[int, ET._Element]:
    result = {}
    following = 1
    for cell in row.iterchildren(_tag("c")):
        address = ADDRESS.fullmatch(cell.get("r", ""))
        index = _column_index(address[1]) if address else following
        result[index] = cell
        following = index + 1
    return result


class _SheetData:
    def __init__(self, reader: _Reader, sheet: Sheet):
        self.reader = reader
        self.sheet = sheet
        self.root = _xml(reader.archive, sheet.part)
        self.data = self.root.find(_tag("sheetData"))
        if self.data is None:
            raise WorkbookError("invalid_workbook")
        self.row_nodes: dict[int, ET._Element] = {}
        next_number = 1
        for row in self.data.iterchildren(_tag("row")):
            number = int(row.get("r", str(next_number)))
            self.row_nodes[number] = row
            next_number = number + 1
        self.table_part: str | None = None
        self.table: ET._Element | None = None
        self.table_end = 0
        self.table_start_col = 1
        self.table_end_col = MAX_COL
        self.totals = 0
        self.header_row = 0
        table_parts = _parts(reader.archive, sheet.part, "table")
        tables = []
        for part in self.root.findall(f"{_tag('tableParts')}/{_tag('tablePart')}"):
            target = table_parts.get(part.get(f"{{{OFFICE_REL}}}id"))
            if target:
                root = _xml(reader.archive, target)
                bound = _bounds(root.get("ref", ""))
                if root.get("headerRowCount", "1") != "0":
                    tables.append((bound, target, root))
        if tables:
            bounds, self.table_part, self.table = min(tables, key=lambda table: (table[0][1], table[0][0]))
            self.table_start_col, self.header_row, self.table_end_col, self.table_end = bounds
            self.totals = int(self.table.get("totalsRowCount", "0"))
        found_headers: list[tuple[int, str]] = []
        for number, node in self.row_nodes.items():
            if self.table is not None and number != self.header_row:
                continue
            found_headers = [
                (index, reader._value(cell).strip())
                for index, cell in _row_cells(node).items()
                if self.table_start_col <= index <= self.table_end_col
            ]
            found_headers = [(index, title) for index, title in found_headers if title]
            if found_headers:
                self.header_row = number
                break
        counts: Counter[str] = Counter()
        columns = []
        for index, label in sorted(found_headers):
            name = label_key(label)
            counts[name] += 1
            key = name if counts[name] == 1 else f"{name}{COLUMN_KEY}{counts[name]}"
            columns.append(Column(key, label, index))
        self.columns = tuple(columns)
        self.by_key = {column.key: column for column in self.columns}

    def scan_rows(self) -> tuple[SourceRow, ...]:
        rows = []
        for number, node in self.row_nodes.items():
            if number <= self.header_row:
                continue
            if self.table is not None and number > self.table_end - self.totals:
                continue
            cells = _row_cells(node)
            values = []
            formulas = set()
            for column in self.columns:
                cell = cells.get(column.index)
                value = self.reader._value(cell)
                if cell is not None and cell.find(_tag("f")) is not None:
                    formulas.add(column.key)
                values.append(value)
            if any(value.strip() for value in values) or formulas:
                rows.append(SourceRow(number, tuple(values), frozenset(formulas)))
        return tuple(rows)

    def _row(self, number: int) -> ET._Element:
        existing = self.row_nodes.get(number)
        if existing is not None:
            return existing
        row = ET.Element(_tag("row"), r=str(number))
        following = next((n for n in sorted(self.row_nodes) if n > number), None)
        if following:
            self.row_nodes[following].addprevious(row)
        else:
            self.data.append(row)
        self.row_nodes[number] = row
        return row

    def _cell(self, row: ET._Element, number: int, index: int, style: str | None) -> ET._Element:
        cells = _row_cells(row)
        if index in cells:
            return cells[index]
        cell = ET.Element(_tag("c"), r=_letter(index) + str(number))
        if style is not None:
            cell.set("s", style)
        following = next((i for i in sorted(cells) if i > index), None)
        if following:
            cells[following].addprevious(cell)
        elif cells:
            cells[max(cells)].addnext(cell)
        else:
            row.insert(0, cell)
        return cell

    @staticmethod
    def _has_contents(cell: ET._Element) -> bool:
        return any(child.tag in {_tag("v"), _tag("is"), _tag("f")} for child in cell)

    @staticmethod
    def _clear(cell: ET._Element) -> None:
        for child in list(cell):
            if child.tag in {_tag("v"), _tag("is"), _tag("f")}:
                cell.remove(child)
        cell.attrib.pop("t", None)

    def _set_value(self, cell: ET._Element, column: Column, value: str, template: ET._Element | None) -> None:
        previous = cell.findtext(_tag("v"), "")
        style_cell = template if not previous and template is not None else cell
        format_id, custom = self.reader._style(style_cell)
        original_numeric = bool(previous) and cell.get("t", "n") == "n"
        numeric = value.translate(DIGITS).replace("٫", ".")
        numeric_column = label_key(column.label).startswith(("هزینه ", "cost ", "amount ", "salary"))
        as_number = (
            (original_numeric or numeric_column) and label_key(column.label) not in TEXT_ID_LABELS
            and format_id not in BUILTIN_DATES and format_id < 164 and not custom
            and re.fullmatch(r"-?(?:0|[1-9]\d{0,14})(?:\.\d+)?", numeric, re.ASCII)
            and len(re.sub(r"[-.]", "", numeric)) <= 15
        )
        self._clear(cell)
        if not value:
            return
        if as_number:
            ET.SubElement(cell, _tag("v")).text = numeric
            return
        cell.set("t", "inlineStr")
        text = ET.SubElement(ET.SubElement(cell, _tag("is")), _tag("t"))
        text.set(XML_SPACE, "preserve")
        text.text = EXCEL_ESCAPE.sub(lambda match: "_x005F_" + match[0][1:], value)

    def _append_row(self) -> tuple[int, bool]:
        if self.table is None:
            occupied = [number for number, row in self.row_nodes.items() if any(map(self._has_contents, _row_cells(row).values()))]
            return max([self.header_row, *occupied]) + 1, False
        for number in range(self.header_row + 1, self.table_end - self.totals + 1):
            row = self.row_nodes.get(number)
            cells = _row_cells(row) if row is not None else {}
            if not any(self._has_contents(cell) for index, cell in cells.items() if self.table_start_col <= index <= self.table_end_col):
                return number, False
        if self.totals or self.table.find(f".//{_tag('calculatedColumnFormula')}") is not None:
            raise WorkbookError("add_in_excel")
        number = self.table_end + 1
        row = self.row_nodes.get(number)
        if row is not None and any(self._has_contents(cell) for index, cell in _row_cells(row).items() if self.table_start_col <= index <= self.table_end_col):
            raise WorkbookError("add_in_excel")
        return number, True

    def change(self, operation: str, number: int | None, values: dict[str, str]) -> dict[str, bytes]:
        if not self.columns:
            raise WorkbookError("no_headers")
        if self.root.find(_tag("sheetProtection")) is not None or any(
            part.lower().startswith("_xmlsignatures/") for part in self.reader.archive.namelist()
        ):
            raise WorkbookError("protected_workbook")
        if values.keys() - self.by_key.keys():
            raise WorkbookError("source_changed")
        if any(INVALID_XML.search(value) or len(value.encode("utf-16-le")) // 2 > 32767 for value in values.values()):
            raise WorkbookError("cell_too_long")
        grow = False
        if operation == "add":
            number, grow = self._append_row()
        elif number not in self.row_nodes or number <= self.header_row:
            raise WorkbookError("source_changed")
        if not number or number > MAX_ROW:
            raise WorkbookError("add_in_excel")
        row = self._row(number)
        existing_cells = _row_cells(row)
        parts = {}
        if operation == "delete":
            for column in self.columns:
                cell = existing_cells.get(column.index)
                if cell is not None:
                    formula = cell.find(_tag("f"))
                    if formula is not None and formula.get("ref") and formula.get("t") in ("shared", "array", "dataTable"):
                        raise WorkbookError("formula_read_only")
                    self._clear(cell)
        else:
            previous_row = self.row_nodes.get(number - 1) if number > self.header_row + 1 else None
            template = _row_cells(previous_row) if previous_row is not None and operation == "add" else {}
            for key, value in values.items():
                column = self.by_key[key]
                example = template.get(column.index)
                cell = self._cell(row, number, column.index, example.get("s") if example is not None else None)
                if cell.find(_tag("f")) is not None:
                    raise WorkbookError("formula_read_only")
                self._set_value(cell, column, value, example)
        if grow and self.table is not None:
            new_range = f"{_letter(self.table_start_col)}{self.header_row}:{_letter(self.table_end_col)}{number}"
            old_range = self.table.get("ref")
            self.table.set("ref", new_range)
            for element in self.table.iter():
                if element is not self.table and element.get("ref") == old_range:
                    element.set("ref", new_range)
            for defined_name in self.reader.book.findall(f"{_tag('definedNames')}/{_tag('definedName')}"):
                if defined_name.get("name") != "_xlnm._FilterDatabase":
                    continue
                sheet_label, separator, span = (defined_name.text or "").rpartition("!")
                if separator and sheet_label.strip("'").replace("''", "'") == self.sheet.label and span.replace("$", "") == old_range:
                    left, top, right, bottom = _bounds(new_range)
                    defined_name.text = f"{sheet_label}!${_letter(left)}${top}:${_letter(right)}${bottom}"
            parts[self.table_part] = _xml_bytes(self.table)
        if operation == "add":
            dimension = self.root.find(_tag("dimension"))
            if dimension is not None:
                left, top, right, bottom = _bounds(dimension.get("ref", "A1"))
                touched = [self.by_key[key].index for key in values]
                dimension.set("ref", f"{_letter(min([left, *touched]))}{min(top, number)}:{_letter(max([right, *touched]))}{max(bottom, number)}")
        parts[self.sheet.part] = _xml_bytes(self.root)
        return parts


def read_workbook(path: Path, preferred: str | tuple[str, str] | None = None) -> Snapshot:
    path = path.resolve()
    raw, signature = _read_bytes(path)
    return _snapshot(path, raw, signature, preferred)


def _snapshot(path: Path, raw: bytes, signature: tuple[int, ...], preferred: str | tuple[str, str] | None) -> Snapshot:
    try:
        with ZipFile(BytesIO(raw)) as archive:
            reader = _Reader(archive)
            sheet = reader.select(preferred)
            return _sheet_snapshot(path, signature, sha256(raw).hexdigest(), reader, sheet)
    except (BadZipFile, KeyError, IndexError, ValueError, ET.XMLSyntaxError, RuntimeError) as issue:
        raise WorkbookError("invalid_workbook") from issue


def _sheet_snapshot(path, signature, revision, reader, data) -> Snapshot:
    return Snapshot(path, signature, revision, data.sheet, reader.sheets, data.columns, data.scan_rows())


def companion_snapshots(snapshot: Snapshot) -> dict[str, Snapshot]:
    if not any(is_companion(sheet.label) for sheet in snapshot.sheets):
        return {}
    raw, signature = _read_bytes(snapshot.path)
    revision = sha256(raw).hexdigest()
    if revision != snapshot.revision:
        raise WorkbookError("source_changed")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            reader = _Reader(archive)
            names = {label_key(sheet.label): sheet for sheet in reader.sheets}
            return {
                name: _sheet_snapshot(snapshot.path, signature, revision, reader, _SheetData(reader, names[label_key(name)]))
                for name in COMPANION_SHEETS if label_key(name) in names
            }
    except (BadZipFile, KeyError, IndexError, ValueError, ET.XMLSyntaxError, RuntimeError) as issue:
        raise WorkbookError("invalid_workbook") from issue


def _check_excel_lock(path: Path) -> None:
    if path.with_name("~$" + path.name).exists():
        raise WorkbookError("excel_open")


def _replace_source(temp: Path, path: Path) -> None:
    if os.name != "nt":
        os.replace(temp, path)
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    replace.restype = wintypes.BOOL
    if not replace(str(path), str(temp), None, 0, None, None):
        error = ctypes.get_last_error()
        if error in (5, 32, 33):
            raise WorkbookError("excel_open")
        raise ctypes.WinError(error)


def update_workbook(snapshot: Snapshot, operation: str, row_number: int | None, values: dict[str, str], backup) -> Snapshot:
    if operation not in ("add", "edit", "delete"):
        raise ValueError(operation)

    def edit(original):
        reader = _Reader(original)
        sheet = reader.select((snapshot.sheet.key, snapshot.sheet.label))
        parts = sheet.change(operation, row_number, values)
        calc = reader.book.find(_tag("calcPr"))
        if calc is not None:
            calc.set("fullCalcOnLoad", "1")
        removed: set[str] = set()
        chain = _parts(original, BOOK, "calcChain")
        if operation == "delete" and chain:
            removed.update(chain.values())
            rels = _xml(original, BOOK_RELS)
            for relationship in list(rels):
                if relationship.get("Id") in chain:
                    rels.remove(relationship)
            content = _xml(original, "[Content_Types].xml")
            for member in list(content):
                if member.get("PartName", "").lstrip("/") in removed:
                    content.remove(member)
            parts[BOOK_RELS] = _xml_bytes(rels)
            parts["[Content_Types].xml"] = _xml_bytes(content)
        parts[BOOK] = _xml_bytes(reader.book)
        return parts, removed

    return _edit_package(snapshot, edit, backup)


def _edit_package(snapshot, edit, backup):
    path = snapshot.path
    _check_excel_lock(path)
    raw, signature = _read_bytes(path)
    if sha256(raw).hexdigest() != snapshot.revision:
        raise WorkbookError("source_changed")
    fd, name = mkstemp(dir=path.parent, prefix=".niro-", suffix=".tmp")
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, ZipFile(BytesIO(raw)) as original:
            parts, removed = edit(original)
            with ZipFile(output, "w", ZIP_DEFLATED) as updated:
                for member in original.infolist():
                    if member.filename not in removed:
                        item = parts.pop(member.filename) if member.filename in parts else original.read(member)
                        updated.writestr(member, item)
                for part, item in parts.items():
                    updated.writestr(part, item)
                updated.comment = original.comment
            output.flush()
            os.fsync(output.fileno())
        if os.name != "nt":
            temp.chmod(stat.S_IMODE(path.stat().st_mode))
        new_contents = temp.read_bytes()
        result = _snapshot(path, new_contents, stamp(temp), (snapshot.sheet.key, snapshot.sheet.label))
        _check_excel_lock(path)
        if stamp(path) != signature:
            raise WorkbookError("source_changed")
        backup(raw)
        _check_excel_lock(path)
        if stamp(path) != signature:
            raise WorkbookError("source_changed")
        _replace_source(temp, path)
        return Snapshot(result.path, stamp(path), result.revision, result.sheet, result.sheets, result.columns, result.rows)
    finally:
        temp.unlink(missing_ok=True)


def create_companion_sheet(snapshot: Snapshot, title: str, headings: tuple[str, ...],
                           rows: tuple[tuple[str, ...], ...], backup) -> Snapshot:
    if title not in COMPANION_SHEETS or not headings:
        raise ValueError(title)
    if len(rows) >= MAX_ROW:
        raise WorkbookError("add_in_excel")

    def edit(archive):
        book = _xml(archive, BOOK)
        sheets = book.find(_tag("sheets"))
        protection = book.find(_tag("workbookProtection"))
        if any(name.lower().startswith("_xmlsignatures/") for name in archive.namelist()) or (
            protection is not None and protection.get("lockStructure", "0").lower() in ("1", "true")
        ):
            raise WorkbookError("protected_workbook")
        if any(label_key(item.get("name", "")) == label_key(title) for item in sheets):
            raise WorkbookError("companion_exists")
        sheet_id = max((int(item.get("sheetId", "0")) for item in sheets), default=0) + 1
        rels = _xml(archive, BOOK_RELS)
        rel_ids = {item.get("Id") for item in rels}
        filenames = set(archive.namelist())
        index = sheet_id
        while f"xl/worksheets/sheet{index}.xml" in filenames or f"rIdNiro{index}" in rel_ids:
            index += 1
        target, relation = f"worksheets/sheet{index}.xml", f"rIdNiro{index}"
        ET.SubElement(sheets, _tag("sheet"), name=title, sheetId=str(sheet_id), attrib={f"{{{OFFICE_REL}}}id": relation})
        ET.SubElement(rels, f"{{{PACKAGE_REL}}}Relationship", Id=relation, Type=OFFICE_REL + "/worksheet", Target=target)
        content = _xml(archive, "[Content_Types].xml")
        ET.SubElement(content, f"{{{CONTENT}}}Override", PartName="/xl/" + target,
                      ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
        sheet = ET.Element(_tag("worksheet"), nsmap={None: MAIN})
        ref = f"A1:{_letter(len(headings))}{1 + len(rows)}"
        ET.SubElement(sheet, _tag("dimension"), ref=ref)
        views = ET.SubElement(sheet, _tag("sheetViews"))
        view = ET.SubElement(views, _tag("sheetView"), rightToLeft="1", workbookViewId="0")
        ET.SubElement(view, _tag("pane"), ySplit="1", topLeftCell="A2", activePane="bottomLeft", state="frozen")
        ET.SubElement(sheet, _tag("sheetFormatPr"), defaultRowHeight="17")
        columns = ET.SubElement(sheet, _tag("cols"))
        ET.SubElement(columns, _tag("col"), min="1", max=str(len(headings)), width="22", customWidth="1")
        data = ET.SubElement(sheet, _tag("sheetData"))
        for number, values in enumerate((headings, *rows), 1):
            if len(values) != len(headings):
                raise ValueError("Sheet row has incorrect width")
            row = ET.SubElement(data, _tag("row"), r=str(number))
            for position, value in enumerate(values, 1):
                if not value:
                    continue
                if INVALID_XML.search(value) or len(value.encode("utf-16-le")) // 2 > 32767:
                    raise WorkbookError("cell_too_long")
                cell = ET.SubElement(row, _tag("c"), r=f"{_letter(position)}{number}", t="inlineStr")
                text = ET.SubElement(ET.SubElement(cell, _tag("is")), _tag("t"), attrib={XML_SPACE: "preserve"})
                text.text = EXCEL_ESCAPE.sub(lambda match: "_x005F_" + match[0][1:], value)
        if rows:
            ET.SubElement(sheet, _tag("autoFilter"), ref=ref)
        return {
            BOOK: _xml_bytes(book), BOOK_RELS: _xml_bytes(rels),
            "[Content_Types].xml": _xml_bytes(content), "xl/" + target: _xml_bytes(sheet),
        }, set()

    return _edit_package(snapshot, edit, backup)


def make_template(path: Path) -> None:
    import xlsxwriter

    with xlsxwriter.Workbook(path) as book:
        sheet = book.add_worksheet(PRIMARY_SHEET)
        sheet.right_to_left()
        sheet.freeze_panes(1, 0)
        sheet.set_column(0, len(FIELDS) - 1, 18)
        sheet.add_table(0, 0, 1, len(FIELDS) - 1, {
            "name": "Personnel",
            "style": "Table Style Medium 5",
            "columns": [{"header": name} for name, _ in FIELDS],
        })
