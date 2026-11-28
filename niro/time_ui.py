"""Attendance imports, calendars, leave entries and period balances."""

from pathlib import Path

from PySide6.QtCore import QDate, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFileDialog, QLineEdit, QPlainTextEdit, QSpinBox, QTabWidget, QTimeEdit,
)

from .locale import PERSIAN_DIGITS
from .office import Entry, iso_day, roster
from .office_ui import OfficeDialog, RowsTable, actions, confirm, form_field, popup
from .record_fields import DateField, date_from_text
from .timekeeping import SHIFT_DEFAULT, attendance, leave_days, read_clock, time_label
from .widgets import ComboBox, button, field, fill_options, label, row_layout


_WEEKDAYS = (("دوشنبه", "Mon"), ("سه‌شنبه", "Tue"), ("چهارشنبه", "Wed"), ("پنجشنبه", "Thu"),
             ("جمعه", "Fri"), ("شنبه", "Sat"), ("یکشنبه", "Sun"))


class TimekeepingDialog(OfficeDialog):
    def __init__(self, window):
        super().__init__(window, "timekeeping", (1020, 710))
        self.people = roster(self.view)
        self.person = ComboBox()
        fill_options(self.person, ((person.ref, self.staff_label(person)) for person in self.people))
        self.person.setSizeAdjustPolicy(ComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        form_field(self.content, self.t("pick_person"), self.person)
        today = QDate.currentDate()
        dates = row_layout()
        self.first = DateField(iso_day(today.addDays(-29)), self.language)
        self.last = DateField(iso_day(today), self.language)
        field(dates, self.t("date_first"), self.first)
        field(dates, self.t("date_last"), self.last)
        refresh = button(self.t("refresh"))
        dates.addWidget(refresh)
        self.content.addLayout(dates)
        self.totals = label("", "chip", True)
        self.content.addWidget(self.totals)
        self.table = RowsTable(map(self.t, ("form_date", "clock_in", "clock_out", "worked", "lateness", "overtime", "leave")))
        self.leave_table = RowsTable(map(self.t, ("date_first", "date_last", "leave", "field_note")))
        tabs = QTabWidget()
        tabs.addTab(self.table, self.t("timekeeping"))
        tabs.addTab(self.leave_table, self.t("leave"))
        self.content.addWidget(tabs, 1)
        imported = button(self.t("clock_import"), "primary")
        calendar = button(self.t("shift_calendar"))
        personal = button(self.t("shift_personal"))
        leave = button(self.t("leave_add"))
        allowance = button(self.t("allowance"))
        remove = button(self.t("delete"), "danger")
        actions(self.content, imported, calendar, personal)
        actions(self.content, leave, allowance, remove)
        for control in (imported, calendar, personal, leave, allowance, remove):
            control.setEnabled(self.account.edits)
        refresh.clicked.connect(self.refresh)
        self.person.currentIndexChanged.connect(self.refresh)
        imported.clicked.connect(self.import_clock)
        calendar.clicked.connect(lambda: self.edit_shift(False))
        personal.clicked.connect(lambda: self.edit_shift(True))
        leave.clicked.connect(self.add_leave)
        allowance.clicked.connect(self.edit_allowance)
        remove.clicked.connect(self.remove_leave)
        self.saved_leaves = ()
        self.saved_allowance = None
        QTimer.singleShot(0, self.refresh)

    def selected(self):
        return next((person for person in self.people if person.ref == self.person.currentData()), None)

    def dates(self):
        start, end = date_from_text(self.first.text()), date_from_text(self.last.text())
        if not start.isValid() or not end.isValid() or start > end:
            self.problem("invalid_range")
            return None
        return start, end

    def _shift(self, person=None):
        common = self.office.entries(self.scope, "shift", {""})
        shift = {**SHIFT_DEFAULT, **common[0].data} if common else SHIFT_DEFAULT.copy()
        if person:
            override = self.office.entries(self.scope, "shift", set(person.tokens))
            if override:
                shift.update(override[0].data)
        return shift

    def _hours(self, minutes):
        if minutes is None:
            return "—"
        amount = abs(minutes)
        text = ("−" if minutes < 0 else "") + f"{amount // 60:02}:{amount % 60:02}"
        return text.translate(PERSIAN_DIGITS) if self.language == "fa" else text

    def refresh(self):
        if self.busy:
            return
        person, days = self.selected(), self.dates()
        if not person or not days:
            self.table.replace(())
            self.leave_table.replace(())
            return
        if not person.tokens:
            self.problem("unmatched_staff")
            self.table.replace(())
            self.leave_table.replace(())
            return
        since, until = days

        def compute():
            start, end = iso_day(since), iso_day(until)
            keys = set(person.tokens)
            shift = self._shift(person)
            punches = self.office.entries(self.scope, "punch", keys, start, end)
            order = {key: index for index, key in enumerate(person.tokens)}
            punch_days = {entry.day: entry for entry in sorted(punches, key=lambda item: order[item.subject], reverse=True)}
            leaves = tuple(entry for entry in self.office.entries(self.scope, "leave", keys, end=end) if entry.data["end"] >= start)
            taken = leave_days(leaves, since, until, shift)
            allowances = self.office.entries(self.scope, "allowance", keys, start, start)
            allowance = next((item for item in allowances if item.data["end"] == end), None)
            rows, totals = [], [0, 0, 0]
            for day in sorted(punch_days.keys() | taken.keys(), reverse=True):
                entry = punch_days.get(day)
                hours = attendance(entry, shift) if entry else (None,) * 3
                totals = [old + (new or 0) for old, new in zip(totals, hours)]
                rows.append((day, entry.data["in"] if entry else None, entry.data["out"] if entry else None, *hours, taken.get(day, 0)))
            return rows, totals, leaves, sum(taken.values()), allowance

        self.run(compute, self.show_results)

    def show_results(self, result):
        rows, (worked, late, overtime), leaves, used, allowance = result
        self.saved_leaves, self.saved_allowance = leaves, allowance
        self.table.replace((self.day_label(day), time_label(start), time_label(end), *(self._hours(value) for value in values))
                           for day, start, end, *values in rows)
        self.leave_table.replace((self.day_label(item.day), self.day_label(item.data["end"]),
                                 self._hours(item.data["minutes"]) if item.data["minutes"] else self.t("leave_day"), item.data.get("note", ""))
                                 for item in leaves)
        self.totals.setText(self.t("time_totals", worked=self._hours(worked), late=self._hours(late),
                                   overtime=self._hours(overtime), used=self._hours(used),
                                   balance=self._hours(allowance.data["minutes"] - used) if allowance else self.t("missing_allowance")))

    def import_clock(self):
        if not self.account.edits or not self.current():
            return
        path, _ = QFileDialog.getOpenFileName(self, self.t("clock_import"), "", "Excel / CSV (*.xlsx *.csv *.tsv *.txt)")
        if not path:
            self.problem("clock_format_help")
            return
        self.run(lambda: read_clock(Path(path), self.people, self.office.entries(self.scope, "punch")), self.preview_import)

    def preview_import(self, result):
        entries, errors = result
        box, layout = popup(self, self.t("clock_import"), 810, 590)
        layout.addWidget(label(self.t("clock_preview", valid=self.num(len(entries)), skipped=self.num(len(errors))), "muted", True))
        people = {key: person for person in self.people for key in person.tokens}
        accepted = RowsTable(map(self.t, ("pick_person", "form_date", "clock_in", "clock_out")))
        accepted.replace((self.staff_label(people[entry.subject]), self.day_label(entry.day),
                          time_label(entry.data["in"]), time_label(entry.data["out"])) for entry in entries)
        skipped = RowsTable(map(self.t, ("worksheet_row", "field_kind", "data_value")))
        skipped.replace((self.num(row), self.t(problem), value) for row, problem, value in errors)
        tabs = QTabWidget()
        tabs.addTab(accepted, self.t("preview_edits"))
        tabs.addTab(skipped, self.t("health"))
        layout.addWidget(tabs, 1)
        apply, cancel = button(self.t("save"), "primary"), button(self.t("cancel"))
        apply.setEnabled(bool(entries))
        actions(layout, apply, cancel)
        apply.clicked.connect(box.accept)
        cancel.clicked.connect(box.reject)
        if box.exec() == box.DialogCode.Accepted and self.current():
            self.run(lambda: self.office.save(self.scope, entries, self.actor, "clock_saved", str(len(entries))),
                     lambda _: self.refresh())

    def edit_shift(self, personal):
        if not self.account.edits or not self.current():
            return
        person = self.selected() if personal else None
        if personal and (not person or not person.tokens):
            self.problem("unmatched_staff")
            return
        self.run(lambda: self._shift(person), lambda shift: self.shift_form(person, shift))

    def shift_form(self, person, shift):
        box, layout = popup(self, self.t("shift_personal" if person else "shift_calendar"), 615, 555)
        line = row_layout()
        start = QTimeEdit(QTime(shift["start"] // 60, shift["start"] % 60))
        end = QTimeEdit(QTime(shift["end"] // 60, shift["end"] % 60))
        for field_, key in ((start, "shift_start"), (end, "shift_end")):
            field_.setDisplayFormat("HH:mm")
            field(line, self.t(key), field_)
        layout.addLayout(line)
        grace = QSpinBox()
        grace.setRange(0, 1440)
        grace.setValue(shift["grace"])
        form_field(layout, self.t("grace"), grace)
        days = []
        holiday = QPlainTextEdit("\n".join(shift["holidays"]))
        if not person:
            layout.addWidget(label(self.t("working_days"), "smallMuted"))
            for indices in ((5, 6, 0, 1), (2, 3, 4)):
                row = row_layout(6)
                for index in indices:
                    item = QCheckBox(_WEEKDAYS[index][self.language == "en"])
                    item.setChecked(index + 1 in shift["weekdays"])
                    row.addWidget(item)
                    days.append((index + 1, item))
                layout.addLayout(row)
            layout.addWidget(label(self.t("holidays"), "smallMuted"))
            layout.addWidget(holiday, 1)
        notice = label("", "warning", True)
        layout.addWidget(notice)
        result = None

        def take():
            nonlocal result
            starts, ends = start.time(), end.time()
            times = {"start": starts.hour() * 60 + starts.minute(), "end": ends.hour() * 60 + ends.minute(), "grace": grace.value()}
            weekly = [idx for idx, item in days if item.isChecked()]
            parsed = [date_from_text(edit) for edit in holiday.toPlainText().splitlines() if edit.strip()]
            if starts == ends or not person and (not weekly or any(not date.isValid() for date in parsed)):
                notice.setText(self.t("bad_shift"))
                return
            if not person:
                times.update(weekdays=weekly, holidays=sorted({iso_day(date) for date in parsed}))
            result = Entry("shift", person.tokens[0] if person else "", "", times, "shift")
            box.accept()

        commit = button(self.t("save"), "primary")
        commit.clicked.connect(take)
        actions(layout, commit)
        if box.exec() == box.DialogCode.Accepted and self.current():
            self.run(lambda: self.office.save(self.scope, (result,), self.actor, "shift_saved"), lambda _: self.refresh())

    def add_leave(self):
        person = self.selected()
        if not self.account.edits or not person or not person.tokens or not self.current():
            self.problem("unmatched_staff")
            return
        box, layout = popup(self, self.t("leave_add"), 585, 400)
        today = iso_day(QDate.currentDate())
        start = form_field(layout, self.t("date_first"), DateField(today, self.language))
        end = form_field(layout, self.t("date_last"), DateField(today, self.language))
        full = QCheckBox(self.t("leave_day"))
        full.setChecked(True)
        layout.addWidget(full)
        minutes = QSpinBox()
        minutes.setRange(1, 1440)
        minutes.setValue(60)
        minutes.setEnabled(False)
        full.toggled.connect(lambda value: minutes.setEnabled(not value))
        form_field(layout, self.t("leave_minutes"), minutes)
        note = form_field(layout, self.t("field_note"), QLineEdit())
        notice = label("", "warning", True)
        layout.addWidget(notice)
        result = None

        def take():
            nonlocal result
            first, last = date_from_text(start.text()), date_from_text(end.text())
            if not first.isValid() or not last.isValid() or first > last:
                notice.setText(self.t("invalid_range"))
                return
            result = Entry("leave", person.tokens[0], iso_day(first),
                           {"end": iso_day(last), "minutes": 0 if full.isChecked() else minutes.value(), "note": note.text().strip()})
            box.accept()

        save = button(self.t("save"), "primary")
        save.clicked.connect(take)
        actions(layout, save)
        if box.exec() == box.DialogCode.Accepted and self.current():
            self.run(lambda: self.office.save(self.scope, (result,), self.actor, "leave_saved", result.day), lambda _: self.refresh())

    def remove_leave(self):
        item = self.leave_table.chosen(self.saved_leaves)
        if item and self.account.edits and self.current() and confirm(self, self.t("leave"), self.t("confirm_remove")):
            self.run(lambda: self.office.remove(self.scope, item, self.actor, "leave_removed"), lambda _: self.refresh())

    def edit_allowance(self):
        person, days = self.selected(), self.dates()
        if not self.account.edits or not person or not person.tokens or not days or not self.current():
            return
        start, end = map(iso_day, days)
        box, layout = popup(self, self.t("allowance"), 425, 180)
        layout.addWidget(label(f"{self.day_label(start)} — {self.day_label(end)}", "muted", True))
        hours = QDoubleSpinBox()
        hours.setRange(0, 100_000)
        hours.setDecimals(2)
        old = self.saved_allowance
        if old and not (old.day == start and old.data["end"] == end and old.subject in person.tokens):
            old = None
        if old:
            hours.setValue(old.data["minutes"] / 60)
        form_field(layout, self.t("hours"), hours)
        save = button(self.t("save"), "primary")
        save.clicked.connect(box.accept)
        actions(layout, save)
        if box.exec() == box.DialogCode.Accepted and self.current():
            entry = Entry("allowance", old.subject if old else person.tokens[0], start,
                          {"end": end, "minutes": round(hours.value() * 60)}, f"{start}/{end}")
            self.run(lambda: self.office.save(self.scope, (entry,), self.actor, "allowance_saved", start),
                     lambda _: self.refresh())
