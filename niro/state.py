"""Per-user offline changes and conservative matching across Excel revisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import re
from tempfile import NamedTemporaryFile
from uuid import uuid4

from .workbook import Snapshot, SourceRow, normalize


def _protect(payload: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        return payload

    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    incoming, outgoing = Blob(len(payload), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    method = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    method.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
    ]
    method.restype = wintypes.BOOL
    if not method(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(outgoing.data)


def atomic_file(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".niro-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def namespace(snapshot: Snapshot) -> str:
    return sha256((_path_key(snapshot.path) + "\n" + snapshot.sheet.key).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Overlay:
    uid: str
    kind: str
    revision: str
    number: int
    anchor: dict[str, str]
    unique_ids: dict[str, str]
    changes: dict[str, str]
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class Record:
    values: tuple[str, ...]
    source: SourceRow | None = None
    overlay: Overlay | None = None

    @property
    def token(self) -> str:
        return f"row:{self.source.number}" if self.source else f"local:{self.overlay.uid}"


@dataclass(frozen=True, slots=True)
class View:
    snapshot: Snapshot
    records: tuple[Record, ...]
    hidden: int
    pending: int
    local_count: int
    identity_counts: Counter


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "local.sqlite"
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with self._connection() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS overlays (
                    workbook TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (workbook, uid)
                ) WITHOUT ROWID
            """)
            db.execute("""CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, created TEXT NOT NULL, actor TEXT NOT NULL, scope TEXT NOT NULL,
                operation TEXT NOT NULL, detail BLOB NOT NULL
            )""")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _load(self, key: str) -> list[Overlay]:
        with self._connection() as db:
            return [
                Overlay(uid, **json.loads(_protect(value, decrypt=True)))
                for uid, value in db.execute("SELECT uid, payload FROM overlays WHERE workbook = ?", (key,))
            ]

    def _write(self, key: str, updates: list[Overlay], removals: list[str]) -> None:
        if not updates and not removals:
            return
        with self._connection() as db:
            db.executemany("DELETE FROM overlays WHERE workbook = ? AND uid = ?", ((key, uid) for uid in removals))
            for item in updates:
                packed = json.dumps({
                    "kind": item.kind, "revision": item.revision, "number": item.number,
                    "anchor": item.anchor, "unique_ids": item.unique_ids, "changes": item.changes,
                    "ambiguous": item.ambiguous,
                }, ensure_ascii=False, separators=(",", ":")).encode()
                db.execute("INSERT OR REPLACE INTO overlays VALUES (?, ?, ?)", (key, item.uid, _protect(packed)))

    def backup_path(self, path: Path) -> Path:
        key = sha256(_path_key(path).encode()).hexdigest()
        return self.directory / (key + ".previous")

    def backup(self, path: Path, payload: bytes) -> None:
        protected = _protect(payload)
        folder = self.directory / "backups" / self.backup_path(path).stem
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        atomic_file(folder / (timestamp + ".xlsx"), protected)
        atomic_file(self.backup_path(path), protected)

    def versions(self, path: Path) -> tuple[str, ...]:
        folder = self.directory / "backups" / self.backup_path(path).stem
        return tuple(sorted((entry.stem for entry in folder.glob("*.xlsx")
                             if re.fullmatch(r"\d{8}T\d{12}Z", entry.stem)), reverse=True))

    def copy_version(self, path: Path, revision: str, destination: Path) -> None:
        if not re.fullmatch(r"\d{8}T\d{12}Z", revision):
            raise ValueError("Invalid backup version")
        source = self.directory / "backups" / self.backup_path(path).stem / (revision + ".xlsx")
        atomic_file(destination, _protect(source.read_bytes(), decrypt=True))

    def copy_backup(self, path: Path, destination: Path) -> None:
        atomic_file(destination, _protect(self.backup_path(path).read_bytes(), decrypt=True))

    def write_audit(self, db, actor, scope, operation, detail=""):
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = _protect(json.dumps(detail, ensure_ascii=False).encode("utf-8"))
        db.execute("INSERT INTO audit (created,actor,scope,operation,detail) VALUES (?,?,?,?,?)",
                   (created, actor, scope, operation, payload))

    def audit(self, actor, scope, operation, detail=""):
        with self._connection() as db:
            self.write_audit(db, actor, scope, operation, detail)

    def audit_entries(self, scope):
        with self._connection() as db:
            return tuple((when, actor, action, json.loads(_protect(detail, decrypt=True)))
                         for when, actor, action, detail in db.execute("""SELECT created,actor,operation,detail
                             FROM audit WHERE scope IN (?, '') ORDER BY id DESC""", (scope,)))

    def clear(self, snapshot: Snapshot) -> View:
        with self._connection() as db:
            db.execute("DELETE FROM overlays WHERE workbook = ?", (namespace(snapshot),))
        return self.view(snapshot)

    def clear_record(self, snapshot: Snapshot, record: Record) -> View:
        if record.overlay:
            self._write(namespace(snapshot), [], [record.overlay.uid])
        return self.view(snapshot)

    def finish_source_change(self, snapshot: Snapshot, record: Record | None,
                             operation: str, changed: dict[str, str]) -> View:
        if record is not None and record.overlay:
            item = record.overlay
            remaining = {key: value for key, value in item.changes.items() if key not in changed}
            if operation != "edit" or not remaining:
                self._write(namespace(snapshot), [], [item.uid])
            else:
                self._write(namespace(snapshot), [replace(item, changes=remaining)], [])
        return self.view(snapshot)

    def change(self, view: View, operation: str, record: Record | None, values: dict[str, str]) -> View:
        snapshot = view.snapshot
        key = namespace(snapshot)
        if operation == "add":
            item = Overlay(uuid4().hex, "addition", "", 0, {}, {}, values)
        elif record is not None and record.source is None:
            if operation == "delete":
                self._write(key, [], [record.overlay.uid])
                return self.view(snapshot)
            item = replace(record.overlay, changes={**record.overlay.changes, **values})
        else:
            source = record.source
            anchor = _row_dict(snapshot, source)
            changes = {**(record.overlay.changes if record.overlay else {}), **values}
            changes = {name: value for name, value in changes.items() if value != anchor.get(name, "")}
            kind = "hide" if operation == "delete" else "edit"
            if kind == "edit" and not changes:
                if record.overlay:
                    self._write(key, [], [record.overlay.uid])
                return self.view(snapshot)
            unique_ids = _unique_ids(snapshot, anchor, view.identity_counts)
            ambiguous = False
            if not unique_ids:
                key_values = tuple(map(normalize, source.values))
                ambiguous = any(row.number != source.number and tuple(map(normalize, row.values)) == key_values
                                for row in snapshot.rows)
            item = Overlay(
                record.overlay.uid if record.overlay else uuid4().hex, kind, snapshot.revision,
                source.number, anchor, unique_ids, changes, ambiguous,
            )
        self._write(key, [item], [])
        return self.view(snapshot)

    def view(self, snapshot: Snapshot) -> View:
        key = namespace(snapshot)
        overlays = self._load(key)
        id_positions = [(index, column.key) for index, column in enumerate(snapshot.columns) if column.identity]
        id_counts = Counter(
            (name, value)
            for row in snapshot.rows
            for index, name in id_positions
            if (value := normalize(row.values[index]))
        )
        by_row: dict[int, list[Overlay]] = defaultdict(list)
        rows = {row.number: row for row in snapshot.rows}
        pending = 0
        additions = [overlay for overlay in overlays if overlay.kind == "addition"]
        outdated = [overlay for overlay in overlays if overlay.kind != "addition" and overlay.revision != snapshot.revision]
        matcher = _Match(snapshot, outdated, id_counts) if outdated else None
        for item in overlays:
            if item.kind == "addition":
                continue
            number = item.number if item.revision == snapshot.revision else matcher.find(item)
            if number in rows:
                by_row[number].append(item)
            else:
                pending += 1
        updates, removals, visible = [], [], []
        hidden = 0
        for row in snapshot.rows:
            attached = by_row.get(row.number, ())
            item = attached[0] if len(attached) == 1 else None
            if len(attached) > 1:
                pending += len(attached)
            if item is not None and item.revision != snapshot.revision:
                anchor = _row_dict(snapshot, row)
                changes = {
                    name: value for name, value in item.changes.items()
                    if name not in anchor or anchor[name] == item.anchor.get(name, "")
                }
                if item.kind == "edit" and not changes:
                    removals.append(item.uid)
                    item = None
                else:
                    carried = {**item.anchor, **anchor}
                    item = replace(
                        item, revision=snapshot.revision, number=row.number, anchor=carried,
                        unique_ids=_unique_ids(snapshot, anchor, id_counts), changes=changes,
                    )
                    updates.append(item)
            if item and item.kind == "hide":
                hidden += 1
                continue
            values = tuple(item.changes.get(col.key, value) for col, value in zip(snapshot.columns, row.values)) if item else row.values
            visible.append(Record(values, row, item))
        for item in additions:
            values = tuple(item.changes.get(column.key, "") for column in snapshot.columns)
            if not snapshot.columns or not any(value.strip() for value in values):
                pending += 1
            else:
                visible.append(Record(values, overlay=item))
        self._write(key, updates, removals)
        return View(snapshot, tuple(visible), hidden, pending, len(overlays) - len(removals), id_counts)


def _row_dict(snapshot: Snapshot, row: SourceRow) -> dict[str, str]:
    return {column.key: value for column, value in zip(snapshot.columns, row.values)}


def _unique_ids(snapshot: Snapshot, values: dict[str, str], counts: Counter) -> dict[str, str]:
    return {
        column.key: value for column in snapshot.columns
        if column.identity and (value := normalize(values[column.key])) and counts[column.key, value] == 1
    }


class _Match:
    """Keep local changes attached only when repeated workbook data identifies a row."""

    def __init__(self, snapshot: Snapshot, overlays: list[Overlay], counts: Counter):
        self.keys = tuple(column.key for column in snapshot.columns)
        self.counts = counts
        self.pos = {name: index for index, name in enumerate(self.keys)}
        needed = {(name, value) for item in overlays for name, text in item.anchor.items()
                  if name in self.pos and (value := normalize(text))}
        self.rows, self.index, self.exact = {}, defaultdict(list), {}
        for row in snapshot.rows:
            values = tuple(map(normalize, row.values))
            self.rows[row.number] = values
            for name, value in zip(self.keys, values):
                if (name, value) in needed:
                    self.index[name, value].append(row.number)

    def _same_ids(self, item: Overlay, number: int) -> bool:
        return all(
            self.rows[number][self.pos[name]] == value
            for name, value in item.unique_ids.items() if name in self.pos
        )

    def find(self, item: Overlay) -> int | None:
        strong = {
            self.index[name, value][0] for name, value in item.unique_ids.items()
            if self.counts[name, value] == 1 and self.index[name, value]
        }
        if len(strong) == 1:
            return next(iter(strong))
        if len(strong) > 1 or item.ambiguous:
            return None
        overlap = tuple(name for name in self.keys if name in item.anchor)
        if not overlap:
            return None
        values = tuple(normalize(item.anchor[name]) for name in overlap)
        if not any(values):
            return None
        if overlap not in self.exact:
            positions = [self.pos[name] for name in overlap]
            records = defaultdict(list)
            for number, row in self.rows.items():
                records[tuple(row[pos] for pos in positions)].append(number)
            self.exact[overlap] = records
        exact = self.exact[overlap].get(values, [])
        if len(exact) == 1 and self._same_ids(item, exact[0]):
            return exact[0]
        if exact:
            return None
        pairs = [(name, value) for name, value in zip(overlap, values) if value]
        if len(pairs) < 3:
            return None
        matches = Counter(number for pair in pairs for number in self.index[pair])
        eligible = {number: hits for number, hits in matches.items() if self._same_ids(item, number)}
        best = Counter(eligible).most_common(2)
        if not best:
            return None
        number, hits = best[0]
        distinctive = sum(
            len(self.index[pair]) == 1 and self.index[pair][0] == number for pair in pairs
        )
        if hits < 2 or hits * 4 < len(pairs) * 3 or not distinctive or (len(best) > 1 and best[1][1] >= hits - 1):
            return None
        return number
