"""Workbook-derived lists used by the directory and report filters."""

import re

from .workbook import normalize

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{device}{number}" for device in ("COM", "LPT") for number in "123456789¹²³"
}


def distinct_values(view) -> dict[str, tuple[str, ...]]:
    if not view:
        return {}
    fields = view.snapshot.columns
    values = [set() for _ in fields]
    for record in view.records:
        for choices, value in zip(values, record.values):
            if value.strip():
                choices.add(value.strip())
    result = {}
    for column, choices in zip(fields, values):
        ordered = sorted((normalize(value), value) for value in choices)
        unique = {}
        for key, label in ordered:
            unique.setdefault(key, label)
        result[column.key] = tuple(unique.values())
    return result


def report_filename(title: str, extension: str, fallback: str) -> str:
    stem = _INVALID_FILENAME.sub("_", title.strip()).rstrip(". ")
    if stem.lower().endswith(extension):
        stem = stem[:-len(extension)].rstrip(". ")
    while len(stem.encode("utf-16-le")) // 2 > 180:
        stem = stem[:-1]
    stem = stem.rstrip(". ") or fallback
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    return stem + extension
