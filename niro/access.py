"""Offline accounts and workbook projections for the active session."""

from dataclasses import dataclass, replace
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from os import urandom

from .schema import FIELDS
from .workbook import WorkbookError, normalize


ROLES = ("admin", "editor", "reader")
_PRIVATE = {"کد ملی", "تاریخ تولد", "محل تولد", "محل سکونت", "نام پدر", "تاهل و فرزند", "شماره موبایل",
            "شماره موبایل داخلی", "شماره تماس ضروری", "شماره گذرنامه", "تاریخ انقضاء گذرنامه", "هزینه اضافه کاری",
            "هزینه ماموریت", "هزینه شیفت", "شماره حساب", "شماره شبا", "سطح دسترسی", "تاریخ سطح دسترسی",
            "وضعیت مسکن", "پروژه ملکی", "بیماری خاص", "توضیحات"}
SENSITIVE = frozenset(normalize(label) for fa, en in FIELDS if fa in _PRIVATE for label in (fa, en)) | frozenset(
    map(normalize, ("حقوق", "آدرس", "نشانی", "کد پستی", "موبایل", "national number", "salary", "address",
                    "personal email", "bank account", "health", "medical", "رضایت شغلی")))


@dataclass(frozen=True, slots=True)
class Account:
    key: str = ""
    name: str = ""
    role: str = "admin"
    sensitive: bool = True
    export: bool = True

    @property
    def manages(self):
        return self.role == "admin"

    @property
    def edits(self):
        return self.role in ("admin", "editor")

    @property
    def sees_sensitive(self):
        return self.manages or self.sensitive

    @property
    def exports(self):
        return self.manages or self.export


class Access:
    def __init__(self, store):
        self.store = store
        with store._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS accounts (
                key TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, sensitive INTEGER NOT NULL,
                exports INTEGER NOT NULL, salt BLOB NOT NULL, verifier BLOB NOT NULL
            ) WITHOUT ROWID""")

    def accounts(self):
        with self.store._connection() as db:
            return tuple(Account(*row) for row in db.execute(
                "SELECT key, name, role, sensitive, exports FROM accounts ORDER BY role, name"))

    @staticmethod
    def _verifier(password, salt):
        return pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)

    def authenticate(self, username, password):
        with self.store._connection() as db:
            row = db.execute("""SELECT key, name, role, sensitive, exports, salt, verifier
                FROM accounts WHERE key = ?""", (normalize(username),)).fetchone()
        if row and compare_digest(self._verifier(password, row[5]), row[6]):
            return Account(*row[:5])
        return None

    def save(self, actor, name, password, role, sensitive, export, *, key=None):
        name = name.strip()
        login = normalize(name)
        if not login or role not in ROLES or (key is None or password) and len(password) < 8:
            raise WorkbookError("invalid_account")
        salt = urandom(24) if password else None
        verifier = self._verifier(password, salt) if salt else None
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            accounts = {row[0]: row[1] for row in db.execute("SELECT key, role FROM accounts")}
            if (accounts and accounts.get(actor.key) != "admin") or (not accounts and not actor.manages):
                raise WorkbookError("access_denied")
            if not accounts:
                role = "admin"
            if key is not None:
                if login != key or key not in accounts:
                    raise WorkbookError("invalid_account")
                if accounts[key] == "admin" and role != "admin" and list(accounts.values()).count("admin") == 1:
                    raise WorkbookError("last_admin")
                db.execute("UPDATE accounts SET name=?, role=?, sensitive=?, exports=? WHERE key=?",
                           (name, role, bool(sensitive), bool(export), key))
                if salt:
                    db.execute("UPDATE accounts SET salt=?, verifier=? WHERE key=?", (salt, verifier, key))
            else:
                if login in accounts:
                    raise WorkbookError("duplicate_account")
                db.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (login, name, role, bool(sensitive), bool(export), salt, verifier))
            self.store.write_audit(db, actor.name or name, "", "account_saved", name)
        return Account(login, name, role, sensitive, export)

    def remove(self, actor, account):
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT role FROM accounts WHERE key=?", (actor.key,)).fetchone()
            if active != ("admin",) or actor.key == account.key:
                raise WorkbookError("access_denied")
            db.execute("DELETE FROM accounts WHERE key=?", (account.key,))
            self.store.write_audit(db, actor.name, "", "account_removed", account.name)


def visible_view(view, account, private_columns=()):
    """Keep private fields out of data models, exported records and search catalogs."""
    if view is None or account.sees_sensitive:
        return view
    private = set(private_columns) | SENSITIVE
    snapshot = view.snapshot
    indices = tuple(index for index, column in enumerate(snapshot.columns)
                    if column.key not in private and normalize(column.label) not in private)
    columns = tuple(snapshot.columns[index] for index in indices)
    keys = {column.key for column in columns}

    def values(row):
        return tuple(row[index] for index in indices)

    rows = {row.number: replace(row, values=values(row.values), formulas=row.formulas & keys) for row in snapshot.rows}
    def overlay(record):
        item = record.overlay
        return replace(item, anchor={key: text for key, text in item.anchor.items() if key in keys},
                       changes={key: text for key, text in item.changes.items() if key in keys},
                       unique_ids={key: text for key, text in item.unique_ids.items() if key in keys}) if item else None

    records = tuple(replace(person, values=values(person.values), source=rows[person.source.number] if person.source else None,
                            overlay=overlay(person)) for person in view.records)
    counts = type(view.identity_counts)({item: count for item, count in view.identity_counts.items() if item[0] in keys})
    return replace(view, snapshot=replace(snapshot, columns=columns, rows=tuple(rows.values())), records=records, identity_counts=counts)
