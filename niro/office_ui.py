"""Native dialogs for personnel dossiers, reminders and workbook maintenance."""

from datetime import datetime, timezone
import json
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QGridLayout, QHeaderView, QLineEdit, QMessageBox,
    QPlainTextEdit, QSpinBox, QTabWidget, QTableView, QVBoxLayout, QWidget,
)

from .insights import date_label
from .locale import number, translate
from .office import EVENT_TYPES, Entry, deadline_column, iso_day, preview_edits, reminders, roster, workbook_issues
from .pages import _scroll
from .record_fields import DateField, date_from_text
from .state import atomic_file, namespace
from .widgets import ColumnsDialog, ComboBox, button, card, escaped_tooltip, field, fill_options, icon, label, row_layout, section_header
from .workbook import WorkbookError, update_workbook_cells


def popup(parent, title, width=780, height=600):
    box = QDialog(parent)
    box.setWindowTitle(title)
    box.resize(width, height)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(9)
    return box, layout


def actions(layout, *buttons):
    row = row_layout(7)
    for item in buttons:
        row.addWidget(item)
    row.addStretch(1)
    layout.addLayout(row)


def form_field(layout, title, control):
    row = row_layout()
    field(row, title, control)
    layout.addLayout(row)
    return control


def confirm(parent, title, message):
    return QMessageBox.question(parent, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


class _Rows(QAbstractTableModel):
    def __init__(self, headings, parent):
        super().__init__(parent)
        self.headings, self.rows = headings, ()

    def replace(self, rows):
        self.beginResetModel()
        self.rows = tuple(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headings)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and 0 <= section < len(self.headings):
            return self.headings[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            value = str(self.rows[index.row()][index.column()])
            return escaped_tooltip(value) if role == Qt.ItemDataRole.ToolTipRole else value
        return None


class RowsTable(QTableView):
    def __init__(self, headings):
        super().__init__()
        self.data_model = _Rows(tuple(headings), self)
        self.setModel(self.data_model)
        self.setSelectionBehavior(self.SelectionBehavior.SelectRows)
        self.setSelectionMode(self.SelectionMode.SingleSelection)
        self.setEditTriggers(self.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setHorizontalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.verticalHeader().hide()
        self.horizontalHeader().setDefaultSectionSize(130)
        self.horizontalHeader().setStretchLastSection(True)

    def replace(self, rows):
        self.data_model.replace(rows)

    def chosen(self, items):
        selected = self.selectionModel().selectedRows()
        return items[selected[0].row()] if selected and selected[0].row() < len(items) else None


class OfficeDialog(QDialog):
    def __init__(self, window, key, size=(850, 650)):
        super().__init__(window)
        self.window = window
        self.language = window.language
        self.t = window.t
        self.view = window.view
        self.scope = namespace(self.view.snapshot) if self.view else ""
        self.account = window.account
        self.office = window.office_store
        self.setWindowTitle(self.t(key))
        self.resize(*size)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(9)
        self.content = QVBoxLayout()
        self.body.addLayout(self.content, 1)
        self.banner = label("", "warning", True)
        self.banner.hide()
        self.body.addWidget(self.banner)
        self.close_button = button(self.t("close"))
        self.close_button.clicked.connect(self.reject)
        actions(self.body, self.close_button)
        self.busy = False

    @property
    def actor(self):
        return self.account.name or self.t("local_guest")

    def message(self, text):
        self.banner.setText(text)
        self.banner.setVisible(bool(text))

    def problem(self, key):
        self.message(self.t(key))

    def current(self):
        if self.window.view is self.view:
            return True
        self.problem("source_changed")
        return False

    def reject(self):
        if not self.busy:
            super().reject()

    def run(self, work, done):
        if self.busy:
            return
        self.busy = True
        self.setEnabled(False)
        self.problem("working")

        def ready(value, error):
            self.setEnabled(True)
            self.busy = False
            if error:
                self.message(self.window._reason(error))
            else:
                self.message("")
                done(value)

        self.window.bus.submit(work, ready)

    def num(self, value):
        return number(self.language, value)

    def day_label(self, value):
        day = date_from_text(value)
        return date_label(day, self.language) if day.isValid() else "—"

    def staff_label(self, person):
        return person.label(self.t("worksheet_row") + " " + self.num(person.row))


class OfficePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.setObjectName("page")
        self.window = window
        t = window.t
        stack = QVBoxLayout(self)
        stack.setContentsMargins(0, 0, 0, 0)
        layout = _scroll(stack)
        layout.addWidget(section_header(t("office"), t("office_lead")))
        self.notice = label("", "chip", True)
        self.notice.hide()
        layout.addWidget(self.notice)
        tiles = QGridLayout()
        tiles.setSpacing(12)
        self.actions = {}
        choices = [
            ("dossiers", "people", window.open_dossier), ("reminders", "attendance", window.open_reminders),
            ("timekeeping", "attendance", window.open_timekeeping), ("health", "excel", window.open_quality),
            ("query", "search", window.open_phrase),
            ("access", "shield", window.open_access), ("audit", "reports", window.open_audit),
        ]
        account = window.account
        visible = ("dossiers", "reminders", "timekeeping", "health", "query") if account.sees_sensitive else ("query",)
        if account.manages:
            visible = (*visible, "access", "audit")
        for index, (key, mark, go) in enumerate(item for item in choices if item[0] in visible):
            surface, panel = card()
            panel.addWidget(label(t(key), "sectionTitle", True))
            panel.addWidget(label(t("query_card_lead" if key == "query" else key + "_lead"), "smallMuted", True))
            launch = button(t("open_module"))
            launch.setAccessibleName(t(key))
            launch.clicked.connect(go)
            self.actions[key] = (launch, mark)
            panel.addStretch(1)
            panel.addWidget(launch, 0, Qt.AlignmentFlag.AlignLeading)
            tiles.addWidget(surface, index // 2, index % 2)
        tiles.setColumnStretch(0, 1)
        tiles.setColumnStretch(1, 1)
        layout.addLayout(tiles)
        if account.key:
            exit_session = button(t("signout"))
            exit_session.clicked.connect(window.close)
            actions(layout, label(account.name, "muted", True), exit_session)
        layout.addStretch(1)

    def set_connected(self, enabled):
        for name, pair in self.actions.items():
            pair[0].setEnabled(enabled or name in ("access", "audit"))

    def set_reminders(self, count):
        self.notice.setText(self.window.t("next_deadlines", number=self.window.n(count)))
        self.notice.setVisible(bool(count))

    def recolor(self, palette):
        for button_, mark in self.actions.values():
            button_.setIcon(icon(mark, palette.accent, 17))


class DossierDialog(OfficeDialog):
    def __init__(self, window, selected=None):
        super().__init__(window, "dossiers")
        self.people = roster(self.view)
        self.entries = ()
        self.person = ComboBox()
        fill_options(self.person, ((item.ref, self.staff_label(item)) for item in self.people), selected)
        self.person.setSizeAdjustPolicy(ComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        form_field(self.content, self.t("pick_person"), self.person)
        self.fields = RowsTable(map(self.t, ("value_name", "data_value")))
        self.timeline = RowsTable(map(self.t, ("form_date", "field_kind", "field_title", "expiry", "attachment", "field_note")))
        tabs = QTabWidget()
        tabs.addTab(self.timeline, self.t("timeline"))
        tabs.addTab(self.fields, self.t("contact_sheet"))
        self.content.addWidget(tabs, 1)
        self.add_entry = button(self.t("add"), "primary")
        self.edit_entry = button(self.t("edit"))
        self.delete_entry = button(self.t("delete"), "danger")
        self.copy_attachment = button(self.t("save_attachment"))
        actions(self.content, self.add_entry, self.edit_entry, self.delete_entry, self.copy_attachment)
        self.person.currentIndexChanged.connect(self.refresh)
        self.timeline.selectionModel().selectionChanged.connect(self.selection)
        self.add_entry.clicked.connect(lambda: self.edit(None))
        self.edit_entry.clicked.connect(lambda: self.edit(self.timeline.chosen(self.entries)))
        self.timeline.doubleClicked.connect(lambda: self.edit(self.timeline.chosen(self.entries)))
        self.delete_entry.clicked.connect(self.remove)
        self.copy_attachment.clicked.connect(self.download)
        QTimer.singleShot(0, self.refresh)

    def selected(self):
        return next((item for item in self.people if item.ref == self.person.currentData()), None)

    def selection(self):
        item = self.timeline.chosen(self.entries)
        self.edit_entry.setEnabled(bool(item and self.account.edits))
        self.delete_entry.setEnabled(bool(item and self.account.edits))
        self.copy_attachment.setEnabled(bool(item and item.data.get("file") and self.account.exports))

    def refresh(self):
        person = self.selected()
        self.entries = ()
        self.timeline.replace(())
        values = zip(self.view.snapshot.columns, person.values) if person else ()
        self.fields.replace((col.title(self.language), value) for col, value in values if value)
        self.add_entry.setEnabled(bool(person and person.tokens and self.account.edits))
        self.selection()
        if person and not person.tokens:
            self.problem("unmatched_staff")
        elif person:
            self.run(lambda: self.office.entries(self.scope, "profile", set(person.tokens)), self.show_entries)

    def show_entries(self, entries):
        self.entries = entries
        self.timeline.replace((self.day_label(entry.day), self.t("event_" + entry.data["type"]), entry.data["title"],
                               self.day_label(entry.data.get("due", "")), entry.data.get("file", ""), entry.data.get("notes", ""))
                              for entry in entries)

    def edit(self, old):
        person = self.selected()
        if not self.account.edits or not person or not person.tokens or not self.current():
            return
        old_values = old.data if old else {}
        box, layout = popup(self, self.t("dossiers"), 530, 550)
        title = form_field(layout, self.t("field_title"), QLineEdit(old_values.get("title", "")))
        kind = ComboBox()
        fill_options(kind, ((key, self.t("event_" + key)) for key in EVENT_TYPES), old_values.get("type"))
        form_field(layout, self.t("field_kind"), kind)
        day = form_field(layout, self.t("form_date"), DateField(old.day if old else iso_day(QDate.currentDate()), self.language))
        due = form_field(layout, self.t("expiry"), DateField(old_values.get("due", ""), self.language))
        notes = QPlainTextEdit(old_values.get("notes", ""))
        layout.addWidget(label(self.t("field_note"), "smallMuted"))
        layout.addWidget(notes, 1)
        attachment = label(old_values.get("file", ""), "muted", True)
        pick = button(self.t("attach_file"))
        actions(layout, pick, attachment)
        chosen = None

        def attach():
            nonlocal chosen
            filename, _ = QFileDialog.getOpenFileName(box, self.t("attach_file"))
            if filename:
                chosen = Path(filename)
                attachment.setText(chosen.name)

        pick.clicked.connect(attach)
        notice = label("", "warning", True)
        layout.addWidget(notice)
        save = button(self.t("save"), "primary")
        cancel = button(self.t("cancel"))
        actions(layout, save, cancel)
        cancel.clicked.connect(box.reject)
        request = None

        def check():
            nonlocal request
            name, start, deadline = title.text().strip(), date_from_text(day.text()), date_from_text(due.text())
            if not name or not start.isValid() or due.text().strip() and not deadline.isValid():
                notice.setText(self.t("event_input_error"))
                return
            data = {"title": name, "type": kind.currentData(), "notes": notes.toPlainText(),
                    "due": iso_day(deadline) if deadline.isValid() else "", "file": attachment.text()}
            request = Entry("profile", old.subject, iso_day(start), data, old.key) if old else Entry(
                "profile", person.tokens[0], iso_day(start), data)
            box.accept()

        save.clicked.connect(check)
        if box.exec() == box.DialogCode.Accepted and self.current():
            def persist():
                content = chosen.read_bytes() if chosen else None
                self.office.save(self.scope, (request,), self.actor, "dossier_saved", request.day, attachment=content)

            self.run(persist, self.changed)

    def remove(self):
        entry = self.timeline.chosen(self.entries)
        if entry and self.account.edits and self.current() and confirm(self, self.t("delete"), self.t("confirm_remove")):
            self.run(lambda: self.office.remove(self.scope, entry, self.actor, "dossier_removed"), self.changed)

    def download(self):
        entry = self.timeline.chosen(self.entries)
        if not entry or not self.account.exports or not entry.data.get("file"):
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t("save_attachment"), entry.data["file"])
        if path and self.window._destination_allowed(Path(path)):
            def export():
                atomic_file(Path(path), self.office.attachment(self.scope, entry))
                self.office.store.audit(self.actor, self.scope, "attachment_saved", entry.day)

            self.run(export, lambda _: self.window.notify(self.t("exported")))

    def changed(self, result):
        self.refresh()
        self.window.schedule_reminders()


def reminder_settings(window, view):
    columns = view.snapshot.columns
    name = "office/reminders/" + namespace(view.snapshot) + window._session_suffix()
    try:
        values = json.loads(window.settings.value(name, "{}", str))
        lead = min(3660, max(0, int(values.get("lead", 30))))
        dates = set(values.get("dates", [col.key for col in columns if deadline_column(col)]))
        fields = set(values.get("required", [col.key for col in columns if col.identity and "ملی" not in col.label and "national" not in col.key]))
    except (ValueError, AttributeError, TypeError):
        lead, dates, fields = 30, {col.key for col in columns if deadline_column(col)}, set()
    return name, lead, dates, fields


class ReminderDialog(OfficeDialog):
    def __init__(self, window):
        super().__init__(window, "reminders")
        self.setting, lead, self.dates, self.required = reminder_settings(window, self.view)
        self.lead = QSpinBox()
        self.lead.setRange(0, 3660)
        self.lead.setValue(lead)
        form_field(self.content, self.t("lead_days"), self.lead)
        dates = button(self.t("deadline_fields"))
        required = button(self.t("required_fields"))
        refresh = button(self.t("refresh"), "primary")
        actions(self.content, dates, required, refresh)
        self.table = RowsTable(map(self.t, ("pick_person", "field_title", "expiry", "status")))
        self.content.addWidget(self.table, 1)
        dates.clicked.connect(lambda: self.choose("deadline_fields"))
        required.clicked.connect(lambda: self.choose("required_fields"))
        refresh.clicked.connect(self.refresh)
        self.lead.editingFinished.connect(self.refresh)
        self.table.doubleClicked.connect(self.dossier)
        self.items = ()
        QTimer.singleShot(0, self.refresh)

    def choose(self, key):
        cols = self.view.snapshot.columns
        is_deadline = key == "deadline_fields"
        dialog = ColumnsDialog(self, self.language, cols, self.dates if is_deadline else self.required,
                               title=self.t(key), search_placeholder=self.t("search_columns"))
        if dialog.exec() == dialog.DialogCode.Accepted:
            if is_deadline:
                self.dates = dialog.keys()
            else:
                self.required = dialog.keys()
            self.refresh()

    def refresh(self):
        if self.busy or not self.current():
            return
        lead, dates, required = self.lead.value(), frozenset(self.dates), frozenset(self.required)
        self.window.settings.setValue(self.setting, json.dumps({"lead": lead, "dates": sorted(dates), "required": sorted(required)}))
        today = QDate.currentDate()
        self.run(lambda: reminders(self.view, roster(self.view), self.office.entries(self.scope, "profile"),
                                   today, lead, dates, required), lambda values: self.present(values, today))

    def present(self, values, today):
        self.items = values
        self.table.replace((self.staff_label(item.person), item.title, self.day_label(item.due),
                            self.t("missing_value" if not item.due else "overdue" if item.due < iso_day(today) else "upcoming"))
                           for item in values)
        self.window.reminder_count = len(values)
        self.window.office_page.set_reminders(len(values))

    def dossier(self):
        selected = self.table.chosen(self.items)
        if selected and self.current():
            DossierDialog(self.window, selected.person.ref).exec()


class QualityDialog(OfficeDialog):
    def __init__(self, window):
        super().__init__(window, "health")
        self.total = label("", "chip", True)
        self.content.addWidget(self.total)
        self.table = RowsTable(map(self.t, ("worksheet_row", "field_kind", "value_name", "data_value")))
        self.content.addWidget(self.table, 1)
        preview = button(self.t("batch_edit"))
        backup = button(self.t("dated_backups"))
        actions(self.content, preview, backup)
        preview.setEnabled(self.account.edits)
        backup.setEnabled(self.account.manages)
        preview.clicked.connect(self.batch)
        backup.clicked.connect(self.backups)
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        self.run(lambda: workbook_issues(self.view), self.show_issues)

    def show_issues(self, values):
        self.table.replace((self.num(row), self.t(kind), col, value) for row, kind, col, value in values)
        self.total.setText(self.t("issues_count", count=self.num(len(values)), pending=self.num(self.view.pending)))

    def batch(self):
        if not self.account.edits or not self.current():
            return
        edit = BatchDialog(self.window)
        edit.exec()
        if self.window.view is not self.view:
            self.view = self.window.view
            if self.view:
                self.refresh()

    def backups(self):
        if self.account.manages:
            RecoveryDialog(self.window).exec()


class BatchDialog(OfficeDialog):
    def __init__(self, window):
        super().__init__(window, "batch_edit")
        columns = self.view.snapshot.columns
        self.column = ComboBox()
        fill_options(self.column, ((col.key, col.title(self.language)) for col in columns))
        form_field(self.content, self.t("value_name"), self.column)
        pair = row_layout()
        self.find = QLineEdit()
        self.replace = QLineEdit()
        field(pair, self.t("batch_find"), self.find)
        field(pair, self.t("batch_replace"), self.replace)
        self.content.addLayout(pair)
        self.trim = QCheckBox(self.t("batch_trim"))
        self.content.addWidget(self.trim)
        self.table = RowsTable(map(self.t, ("worksheet_row", "batch_find", "batch_replace")))
        self.content.addWidget(self.table, 1)
        preview = button(self.t("preview_edits"))
        self.save = button(self.t("batch_commit"), "primary")
        self.save.setEnabled(False)
        actions(self.content, preview, self.save)
        self.pending = ()
        preview.clicked.connect(self.preview)
        self.save.clicked.connect(self.commit)
        for signal in (self.column.currentIndexChanged, self.find.textChanged, self.replace.textChanged, self.trim.toggled):
            signal.connect(self.clear)

    def clear(self):
        self.pending = ()
        self.table.replace(())
        self.save.setEnabled(False)
        self.find.setEnabled(not self.trim.isChecked())
        self.replace.setEnabled(not self.trim.isChecked())

    def preview(self):
        if not self.current():
            return
        key, find, replacement, trim = self.column.currentData(), self.find.text(), self.replace.text(), self.trim.isChecked()
        self.run(lambda: preview_edits(self.view.snapshot, key, find, replacement, trim=trim), self.present)

    def present(self, values):
        self.pending = values
        self.table.replace((self.num(row), old, new) for row, old, new in values)
        self.message(self.t("batch_count", count=self.num(len(values))))
        self.save.setEnabled(bool(values and self.account.edits))

    def commit(self):
        if not self.pending or not self.account.edits or not self.current():
            return
        if not confirm(self, self.t("batch_edit"), self.t("batch_count", count=self.num(len(self.pending)))):
            return
        column = self.column.currentData()
        edits = {row: {column: replacement} for row, before, replacement in self.pending}
        snapshot = self.view.snapshot

        def write():
            updated = update_workbook_cells(snapshot, edits, lambda payload: self.window.store.backup(snapshot.path, payload))
            return self.window.store.view(updated)

        self.busy = True
        self.setEnabled(False)

        def saved(view):
            self.busy = False
            self.window._apply(view)
            self.accept()

        def failed(error):
            self.busy = False
            self.setEnabled(True)
            self.message(error)

        self.window._run_write(self.window.with_audit(write, "batch_saved", self.view, str(len(edits))),
                               saved, failed, expected=self.view)


class RecoveryDialog(OfficeDialog):
    def __init__(self, window):
        super().__init__(window, "dated_backups", (580, 440))
        self.path = self.view.snapshot.path
        self.versions = window.store.versions(self.path)
        self.table = RowsTable((self.t("form_date"),))
        self.table.replace((datetime.strptime(value, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
                            .astimezone().isoformat(sep=" ", timespec="seconds"),) for value in self.versions)
        self.content.addWidget(self.table, 1)
        choose = button(self.t("save_copy"), "primary")
        choose.setEnabled(bool(self.versions))
        actions(self.content, choose)
        choose.clicked.connect(self.copy)

    def copy(self):
        version = self.table.chosen(self.versions)
        if not version or not self.account.manages:
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t("save_copy"), self.path.stem + "-recovery.xlsx", "Excel (*.xlsx)")
        destination = self.window._extension(Path(path), ".xlsx") if path else None
        if destination and self.window._destination_allowed(destination):
            self.run(lambda: self.window.store.copy_version(self.path, version, destination),
                     lambda _: self.window.notify(self.t("recovery_saved")))
