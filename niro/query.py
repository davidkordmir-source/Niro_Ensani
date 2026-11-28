"""Turn supported Persian and English phrases into inspectable offline report rules."""

from dataclasses import dataclass
import re

from .filters import Rule
from .office import deadline_column, iso_day
from .record_fields import date_from_text
from .workbook import group_key


RELATIVE = re.compile(r"(?:تا\s+|\bnext\s+)(\d{1,4})\s*(?:روز\s*(?:آینده|دیگر)?|days\b)")
DATE_PART = r"\d{4}[./-]\d{1,2}[./-]\d{1,2}"
DATE_RANGE = re.compile(rf"(?:از|from)\s+({DATE_PART})\s+(?:تا|to)\s+({DATE_PART})")
OPERATIONS = (("not_blank", ("پر است", "دارای مقدار", "not empty")), ("blank", ("خالی", "بدون مقدار", "empty")),
              ("at_least", ("حداقل", "at least")), ("at_most", ("حداکثر", "at most")),
              ("greater", ("بیشتر از", "more than")), ("less", ("کمتر از", "less than")))
ALIASES = ("اداره ", "بخش ", "واحد ", "مدیریت ", "department ", "team ")


@dataclass(frozen=True, slots=True)
class Suggestion:
    rules: tuple[Rule, ...]
    notices: tuple[str, ...]


def _words(text):
    return " ".join(re.sub(r"[^\w./\- ]+", " ", group_key(text)).split())


def suggest(question, columns, catalog, today):
    sentence = _words(question)
    padded = " " + sentence + " "
    names = {col.key: {_words(col.label), _words(col.title("en"))} - {""} for col in columns}
    explicit = {key for key, labels in names.items() if any(" " + name + " " in padded for name in labels)}
    rules, notices = {}, []

    relative, interval = RELATIVE.search(sentence), DATE_RANGE.search(sentence)
    if relative or interval:
        deadlines = tuple(col for col in columns if deadline_column(col))
        specific = tuple(col for col in deadlines if col.key in explicit)
        if not specific:
            aliases = (("قرارداد", "contract"), ("مدرک", "certificate"), ("گذرنامه", "passport"))
            concepts = [words for words in aliases if any(term in padded for term in words)]
            specific = tuple(col for col in deadlines if any(any(term in group_key(col.label) for term in words) for words in concepts))
        wanted = specific or tuple(col for col in columns if col.key in explicit and any("تاریخ" in name or "date" in name for name in names[col.key]))
        wanted = wanted or deadlines
        low, high = (tuple(map(date_from_text, interval.groups())) if interval
                     else (today, today.addDays(int(relative[1]))))
        if len(wanted) == 1 and low.isValid() and high.isValid() and low <= high:
            key = wanted[0].key
            rules[key] = Rule(key, "calendar_between", iso_day(low), iso_day(high))
        else:
            notices.append("query_date")

    for col in columns:
        if col.key not in explicit or col.key in rules:
            continue
        for name in sorted(names[col.key], key=len, reverse=True):
            for operator, phrases in OPERATIONS:
                for phrase in phrases:
                    match = re.search(rf"(?:^| )({re.escape(name)}\s+{re.escape(phrase)}|{re.escape(phrase)}\s+{re.escape(name)})(?:\s+(-?\d+(?:\.\d+)?))?\b", sentence)
                    if match and (operator in ("blank", "not_blank") or match[2]):
                        rules[col.key] = Rule(col.key, operator, match[2] or "")
                        break
                if col.key in rules:
                    break
            if col.key in rules:
                break

    hits = []
    for col in columns:
        if col.key in rules:
            continue
        for value in catalog.get(col.key, ()):
            phrase = _words(value)
            if len(phrase) < 2 or phrase.isdigit() or phrase in names[col.key]:
                continue
            variants = (phrase, *(phrase[len(prefix):] for prefix in ALIASES if phrase.startswith(prefix)))
            for candidate in variants:
                pos = padded.find(" " + candidate + " ")
                if len(candidate) > 1 and pos >= 0:
                    hits.append((pos, pos + len(candidate) + 1, col.key, value))
                    break

    taken = []
    for start, end, key, value in sorted(hits, key=lambda hit: (-(hit[1] - hit[0]), hit[0], hit[2] not in explicit)):
        if any(start < consumed_end and consumed_start < end for consumed_start, consumed_end in taken):
            continue
        candidates = {(column, value) for a, b, column, value in hits if (a, b) == (start, end)}
        named = {(column, value) for column, value in candidates if column in explicit}
        candidates = named or candidates
        taken.append((start, end))
        if len(candidates) != 1:
            if "query_ambiguous" not in notices:
                notices.append("query_ambiguous")
            continue
        key, value = next(iter(candidates))
        if key in rules and rules[key].value != value:
            if "query_ambiguous" not in notices:
                notices.append("query_ambiguous")
        else:
            rules[key] = Rule(key, "group", value)
    if not rules and not notices:
        notices.append("query_empty")
    return Suggestion(tuple(rules.values()), tuple(notices))
