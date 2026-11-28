"""Workbook-backed sport attendance entry and history."""

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, QTimer, Qt, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QStackedWidget, QTableView, QVBoxLayout, QWidget

from .insights import SPORT_STATES, date_label, format_value, tr
from .insights_page import person_options
from .record_fields import DateField, date_from_text
from .widgets import button, card, escaped_tooltip, field, fill_options, icon, label, row_layout, section_header
from .workbook import normalize


class AttendanceDialog(QDialog):
    submitted = Signal(object)

    def __init__(self, parent, language, palette, dataset, entry=None):
        super().__init__(parent)
        self.t = lambda key: tr(language, key)
        self.entry = entry
        self.busy = False
        title = self.t("attendance_edit" if entry else "attendance_add")
        self.setWindowTitle(title)
        self.resize(525, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(label(title, "pageTitle", True))
        layout.addWidget(label(self.t("attendance_lead"), "muted", True))
        self.person = QComboBox()
        self.person.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        empty = entry.name or self.t("unmatched_person") if entry and entry.ref is None else self.t("choose_person")
        fill_options(self.person, [(None, empty), *person_options(dataset, language, stable_only=True)],
                     entry.ref if entry else None)
        field(layout, self.t("person"), self.person)
        details = row_layout()
        self.day = DateField(date_label(entry.day if entry else QDate.currentDate(), language), language)
        self.day.recolor(palette.text)
        field(details, self.t("session_date"), self.day, 2)
        self.status = QComboBox()
        fill_options(self.status, ((state, self.t(state)) for state in SPORT_STATES), entry.status if entry else "present")
        field(details, self.t("status"), self.status)
        layout.addLayout(details)
        self.session = QLineEdit(entry.session if entry and entry.session else self.t("session_default"))
        self.session.setMaxLength(140)
        field(layout, self.t("session"), self.session)
        self.error = label("", "warning", True)
        self.error.hide()
        layout.addWidget(self.error)
        layout.addStretch(1)
        actions = row_layout()
        actions.addStretch()
        self.save = button("ذخیره" if language == "fa" else "Save", "primary")
        self.cancel = button("انصراف" if language == "fa" else "Cancel")
        self.save.clicked.connect(self._submit)
        self.cancel.clicked.connect(self.reject)
        actions.addWidget(self.cancel)
        actions.addWidget(self.save)
        layout.addLayout(actions)

    def _submit(self):
        if self.busy:
            return
        person = self.person.currentData()
        day = date_from_text(self.day.text())
        session = self.session.text().strip()
        if not person and (self.entry is None or self.entry.ref is not None):
            self.failed("یک نفر با شناسهٔ یکتا انتخاب کنید." if self.day.fa else "Choose a person with a unique ID.")
            return
        if not day.isValid() or not session:
            self.failed("تاریخ معتبر و عنوان جلسه را وارد کنید." if self.day.fa else "Enter a valid date and a session name.")
            return
        self.error.hide()
        self.set_busy(True)
        self.submitted.emit((person, day, session, self.status.currentData()))

    def set_busy(self, enabled):
        self.busy = enabled
        for control in (self.person, self.day, self.session, self.status, self.save, self.cancel):
            control.setEnabled(not enabled)

    def failed(self, message):
        self.set_busy(False)
        self.error.setText(message)
        self.error.show()

    def reject(self):
        if not self.busy:
            super().reject()


class _AttendanceModel(QAbstractTableModel):
    def __init__(self, language, parent):
        super().__init__(parent)
        self.language = language
        self.rows = ()
        self.names = {}

    def present(self, rows, names):
        self.beginResetModel()
        self.rows, self.names = tuple(rows), names
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, column, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and 0 <= column < 4:
            return tr(self.language, ("person", "session_date", "session", "status")[column])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        item = self.rows[index.row()]
        name = self.names.get(item.ref) or item.name or item.personnel or item.national or tr(self.language, "unmatched_person")
        text = (name, date_label(item.day, self.language), item.session, tr(self.language, item.status))[index.column()]
        return escaped_tooltip(text) if role == Qt.ItemDataRole.ToolTipRole else text


class AttendancePage(QWidget):
    add = Signal()
    edit = Signal()
    remove = Signal()

    def __init__(self, language, palette):
        super().__init__()
        self.setObjectName("page")
        self.language = language
        self.t = lambda key, **values: tr(language, key, **values)
        self.data = None
        self.names = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(160)
        self.timer.timeout.connect(self.refresh)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(27, 11, 27, 17)
        layout.setSpacing(13)
        self.add_button = button(self.t("attendance_add"), "primary")
        self.add_button.clicked.connect(self.add)
        layout.addWidget(section_header(self.t("attendance_title"), self.t("attendance_lead"), self.add_button))
        self.notice = label("", "smallMuted", True)
        layout.addWidget(self.notice)
        frame, body = card()
        header = row_layout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.t("search_attendance"))
        self.search.setAccessibleName(self.t("search_attendance"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self.timer.start())
        self.status = QComboBox()
        fill_options(self.status, [("", self.t("status_all")), *((valid, self.t(valid)) for valid in SPORT_STATES)])
        self.status.currentIndexChanged.connect(self.refresh)
        header.addWidget(self.search, 2)
        header.addWidget(self.status, 1)
        body.addLayout(header)

        actions = row_layout()
        self.count = label("", "chip")
        actions.addWidget(self.count)
        actions.addStretch(1)
        self.edit_button = button("ویرایش" if language == "fa" else "Edit")
        self.del_button = button("حذف" if language == "fa" else "Delete", "danger")
        self.edit_button.clicked.connect(self.edit)
        self.del_button.clicked.connect(self.remove)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.del_button)
        body.addLayout(actions)
        self.stack = QStackedWidget()
        self.table = QTableView()
        self.model = _AttendanceModel(language, self.table)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setDefaultSectionSize(157)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._selection)
        self.table.doubleClicked.connect(self.edit)
        self.stack.addWidget(self.table)
        self.empty = label(self.t("no_attendance"), "muted", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.empty)
        body.addWidget(self.stack, 1)
        layout.addWidget(frame, 1)
        self.set_dataset(None)
        self.recolor(palette)

    def set_dataset(self, dataset):
        self.data = dataset
        self.names = {entry.ref: entry.name for entry in dataset.people} if dataset else {}
        available = bool(dataset and any(person.stable for person in dataset.people))
        self.add_button.setEnabled(available)
        notice = self.t("passive")
        if dataset and not available:
            notice = self.t("attendance_no_ids")
        if dataset and dataset.invalid_attendance:
            notice += " " + self.t("attendance_bad_rows", count=format_value(dataset.invalid_attendance, self.language))
        if dataset and dataset.duplicate_attendance:
            notice += " " + self.t("attendance_duplicate_rows", count=format_value(dataset.duplicate_attendance, self.language))
        self.notice.setText(notice if dataset else "")
        self.refresh()

    def refresh(self):
        source = self.data.attendance if self.data else ()
        query = normalize(self.search.text()).split()
        status = self.status.currentData()
        rows = []
        for item in source:
            if status and status != item.status:
                continue
            if query and not all(word in item.search_text for word in query):
                continue
            rows.append(item)
        self.model.present(rows, self.names)
        self.count.setText(self.t("records", n=format_value(len(rows), self.language)))
        self.stack.setCurrentWidget(self.table if rows else self.empty)
        self._selection()

    def _selection(self):
        selected = self.selected() is not None
        self.edit_button.setEnabled(selected)
        self.del_button.setEnabled(selected)

    def selected(self):
        selection = self.table.selectionModel().selectedRows()
        if selection and selection[0].row() < len(self.model.rows):
            return self.model.rows[selection[0].row()]
        return None

    def recolor(self, palette):
        self.add_button.setIcon(icon("plus", "#ffffff", 18))
        self.edit_button.setIcon(icon("edit", palette.accent, 16))
        self.del_button.setIcon(icon("delete", palette.warning, 16))
