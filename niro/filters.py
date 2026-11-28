"""Search and combine filters over dynamically discovered workbook columns."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from .state import Record
from .workbook import Column, group_key, normalize


OPERATORS = (
    "contains", "not_contains", "equals", "not_equals", "starts", "ends",
    "greater", "at_least", "less", "at_most", "between", "calendar_between", "group", "blank", "not_blank",
)
NO_VALUE = {"blank", "not_blank"}
DATE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


@dataclass(frozen=True, slots=True)
class Rule:
    key: str
    operator: str
    value: str = ""
    upper: str = ""


def comparable(value: str) -> tuple:
    match = DATE.fullmatch(value)
    if match:
        return 1, tuple(map(int, match.groups()))
    cleaned = value.replace("٬", "").replace(",", "").replace("٫", ".").replace(" ", "")
    if NUMBER.fullmatch(cleaned):
        try:
            return 0, Decimal(cleaned)
        except InvalidOperation:
            pass
    return 2, value


def _match(value: str, operator: str, query: str, upper: str) -> bool:
    if operator == "blank":
        return not value
    if operator == "not_blank":
        return bool(value)
    if operator == "contains":
        return query in value
    if operator == "not_contains":
        return query not in value
    if operator == "equals":
        return value == query
    if operator == "group":
        return group_key(value) == group_key(query)
    if operator == "not_equals":
        return value != query
    if operator == "starts":
        return value.startswith(query)
    if operator == "ends":
        return value.endswith(query)
    if not value:
        return False
    if operator == "calendar_between":
        from .record_fields import date_from_text

        current, start, end = map(date_from_text, (value, query, upper))
        return current.isValid() and start.isValid() and end.isValid() and start <= current <= end
    left, right = comparable(value), comparable(query)
    if left[0] != right[0]:
        return False
    if operator == "greater":
        return left > right
    if operator == "at_least":
        return left >= right
    if operator == "less":
        return left < right
    if operator == "at_most":
        return left <= right
    high = comparable(upper)
    return left[0] == high[0] and right <= left <= high


def filter_records(records: tuple[Record, ...], columns: tuple[Column, ...], term: str = "",
                   search_key: str = "", rules: tuple[Rule, ...] = (), any_rule: bool = False,
                   sort_key: str = "", reverse: bool = False) -> list[Record]:
    indexes = {column.key: i for i, column in enumerate(columns)}
    search_index = indexes.get(search_key)
    queries = tuple(normalize(term).split())
    report_rules = [(indexes[rule.key], rule.operator, normalize(rule.value), normalize(rule.upper))
                    for rule in rules if rule.key in indexes and (rule.operator in NO_VALUE or rule.value.strip())]
    combine = any if any_rule else all
    results = []
    for record in records:
        if queries:
            search_values = record.values if search_index is None else (record.values[search_index],)
            haystack = " ".join(map(normalize, search_values))
            if not all(word in haystack for word in queries):
                continue
        if report_rules and not combine(_match(normalize(record.values[idx]), op, val, high) for idx, op, val, high in report_rules):
            continue
        results.append(record)
    if sort_key in indexes:
        position = indexes[sort_key]
        results.sort(key=lambda item: comparable(normalize(item.values[position])), reverse=reverse)
    return results
