"""Dashboard layouts, drill-through, offline search and meeting exports."""

from dataclasses import replace
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from PySide6.QtCore import QByteArray, QBuffer, QDate, QIODevice, QTimer, Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .catalog import distinct_values
from .dashboard import _DistributionCard, _Trend, _text as dashboard_text, summarize
from .filters import NO_VALUE, Rule, filter_records
from .insights import date_label
from .locale import number, translate
from .office import deadline_column, iso_day
from .office_ui import OfficeDialog, RowsTable, actions, form_field, popup
from .query import suggest
from .record_fields import DateField, date_from_text, is_date_column
from .reports import Report, export
from .widgets import ComboBox, RuleEditor, button, field, fill_options, label, row_layout


class LayoutDialog(QDialog):
    def __init__(self, window, columns, current, presets, keys):
        super().__init__(window)
        self.t = window.t
        self.language = window.language
        self.columns = {column.key: column for column in columns}
        self.presets = presets
        self.current = {**current, "order": tuple(key for key in current.get("order", ()) if key in keys)}
        self.current["order"] += tuple(column.key for column in columns if column.key in keys and column.key not in self.current["order"])
        self.output = None
        self.setWindowTitle(self.t("charts_layout"))
        self.resize(570, 585)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        self.saved = ComboBox()
        fill_options(self.saved, (("", self.t("layout_current")), *((name, name) for name in sorted(presets))))
        form_field(layout, self.t("charts_layout"), self.saved)
        layout.addWidget(label(self.t("layout_drag"), "muted", True))
        self.cards = QListWidget()
        self.cards.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.cards.setDefaultDropAction(Qt.DropAction.MoveAction)
        layout.addWidget(self.cards, 1)
        self.name = form_field(layout, self.t("layout_name"), QLineEdit())
        save, cancel = button(self.t("layout_save"), "primary"), button(self.t("cancel"))
        actions(layout, save, cancel)
        self.saved.currentIndexChanged.connect(self._choose)
        save.clicked.connect(self._take)
        cancel.clicked.connect(self.reject)
        self._choose()

    def _choose(self):
        name = self.saved.currentData()
        config = self.presets.get(name, self.current)
        wide = set(config.get("wide", ()))
        self.name.setText(name)
        self.cards.clear()
        for key in config.get("order", ()):
            if key in self.columns:
                item = QListWidgetItem(self.columns[key].title(self.language), self.cards)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setCheckState(Qt.CheckState.Checked if key in wide else Qt.CheckState.Unchecked)

    def _take(self):
        choices = tuple(self.cards.item(index) for index in range(self.cards.count()))
        self.output = (self.name.text().strip(), {
            "order": [item.data(Qt.ItemDataRole.UserRole) for item in choices],
            "wide": [item.data(Qt.ItemDataRole.UserRole) for item in choices if item.checkState() == Qt.CheckState.Checked],
        })
        self.accept()


class ResultsDialog(OfficeDialog):
    def __init__(self, window, rules):
        super().__init__(window, "drill_result", (1030, 630))
        self.rules = rules
        self.transfer = ""
        self.columns = self.view.snapshot.columns
        summaries = {column.key: column.title(self.language) for column in self.columns}
        self.content.addWidget(label("  ·  ".join(f"{summaries.get(item.key, item.key)}: {item.value or self.t('empty_cells')}"
                                                  for item in rules), "muted", True))
        self.count = label("", "chip")
        self.content.addWidget(self.count)
        self.table = RowsTable(col.title(self.language) for col in self.columns)
        self.content.addWidget(self.table, 1)
        directory, reports = button(self.t("apply_directory")), button(self.t("apply_reports"), "primary")
        actions(self.content, directory, reports)
        directory.clicked.connect(lambda: self.send("people"))
        reports.clicked.connect(lambda: self.send("reports"))
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        self.run(lambda: filter_records(self.view.records, self.columns, rules=self.rules), self.show_records)

    def show_records(self, rows):
        self.table.replace(person.values for person in rows)
        self.count.setText(self.t("record_count", count=self.num(len(rows))))

    def send(self, page):
        if self.current():
            self.transfer = page
            self.accept()


class PhraseDialog(OfficeDialog):
    def __init__(self, window):
        super().__init__(window, "query", (890, 625))
        self.columns = self.view.snapshot.columns
        self.catalog = window.catalog
        self.rules, self.transfer = (), ""
        self.editors = []
        self.content.addWidget(label(self.t("query_lead"), "muted", True))
        self.phrase = QLineEdit()
        self.phrase.setClearButtonEnabled(True)
        self.phrase.setPlaceholderText(self.t("query_example"))
        row = row_layout(7)
        row.addWidget(self.phrase, 1)
        parse = button(self.t("query_parse"), "primary")
        row.addWidget(parse)
        self.content.addLayout(row)
        parse.clicked.connect(self.parse)
        self.phrase.returnPressed.connect(self.parse)
        self.phrase.textChanged.connect(self.clear)
        self.edit_layout = QVBoxLayout()
        self.content.addLayout(self.edit_layout)
        self.content.addStretch(1)
        self.directory, self.reports = button(self.t("apply_directory")), button(self.t("apply_reports"), "primary")
        self.directory.setEnabled(False)
        self.reports.setEnabled(False)
        actions(self.content, self.directory, self.reports)
        self.directory.clicked.connect(lambda: self.send("people"))
        self.reports.clicked.connect(lambda: self.send("reports"))

    def clear(self):
        for editor in self.editors:
            self.edit_layout.removeWidget(editor)
            editor.hide()
            editor.deleteLater()
        self.editors = []
        self.directory.setEnabled(False)
        self.reports.setEnabled(False)

    def parse(self):
        if not self.current():
            return
        query, today = self.phrase.text(), QDate.currentDate()
        self.run(lambda: suggest(query, self.columns, self.catalog or distinct_values(self.view), today), self.present)

    def present(self, result):
        self.clear()
        for rule in result.rules:
            editor = RuleEditor(self.language, self.columns, rule, self.catalog)
            editor.recolor(self.window.colors)
            editor.removed.connect(lambda item=editor: self.remove(item))
            self.editors.append(editor)
            self.edit_layout.addWidget(editor)
        self.directory.setEnabled(bool(self.editors))
        self.reports.setEnabled(bool(self.editors))
        self.message(" ".join(self.t(notice) for notice in result.notices))

    def remove(self, editor):
        self.editors.remove(editor)
        self.edit_layout.removeWidget(editor)
        editor.hide()
        editor.deleteLater()
        self.directory.setEnabled(bool(self.editors))
        self.reports.setEnabled(bool(self.editors))

    def send(self, page):
        if not self.current() or not self.editors:
            return
        rules = tuple(editor.rule() for editor in self.editors)
        if any(rule.operator not in NO_VALUE and (not rule.value.strip()
               or rule.operator in ("between", "calendar_between") and not rule.upper.strip()) for rule in rules):
            self.problem("need_filter")
            return
        for rule in rules:
            if rule.operator == "calendar_between":
                first, last = map(date_from_text, (rule.value, rule.upper))
                if not first.isValid() or not last.isValid() or first > last:
                    self.problem("invalid_range")
                    return
        self.rules, self.transfer = rules, page
        self.accept()


def meeting_settings(window):
    t = window.t
    box, body = popup(window, t("meeting"), 580, 355)
    body.addWidget(label(t("meeting_lead"), "muted", True))
    today = QDate.currentDate()
    row = row_layout()
    first = DateField(iso_day(today.addDays(1 - window.dashboard.selector.currentData())), window.language)
    last = DateField(iso_day(today), window.language)
    field(row, t("date_first"), first)
    field(row, t("date_last"), last)
    body.addLayout(row)
    choice = ComboBox()
    fill_options(choice, (("", t("meeting_all")), *((column.key, column.title(window.language))
                 for column in window.view.snapshot.columns if is_date_column(column.label) or deadline_column(column))))
    form_field(body, t("meeting_period"), choice)
    warning = label("", "warning", True)
    body.addWidget(warning)
    save, cancel = button(t("export"), "primary"), button(t("cancel"))
    actions(body, save, cancel)
    cancel.clicked.connect(box.reject)
    result = None

    def accept():
        nonlocal result
        start, finish = date_from_text(first.text()), date_from_text(last.text())
        if not start.isValid() or not finish.isValid() or start > finish:
            warning.setText(t("invalid_range"))
            return
        result = start, finish, choice.currentData()
        box.accept()

    save.clicked.connect(accept)
    box.exec()
    return result


def prepare_pack(view, chart_keys, selection, start, end, date_column):
    columns = view.snapshot.columns
    if date_column:
        selection = (*selection, Rule(date_column, "calendar_between", iso_day(start), iso_day(end)))
    rows = tuple(filter_records(view.records, columns, rules=selection))
    display = replace(view, records=rows)
    summary = summarize(display, end, chart_keys, days=start.daysTo(end) + 1)
    return summary, rows


def _png(widget, width=765, height=275):
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    widget.resize(width, height)
    widget.show()
    if widget.layout():
        widget.layout().activate()
    image = widget.grab()
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = image.save(buffer, "PNG")
    buffer.close()
    widget.close()
    widget.deleteLater()
    if not ok:
        raise ValueError("Could not render a dashboard chart")
    return bytes(store)


def pack_charts(summary, language, colors):
    result = []
    status = dashboard_text(language, "statuses")
    graphs = ([(status, summary.statuses)] if summary.statuses else []) + [
        (column.title(language), counts) for column, counts in summary.charts if counts and column.key != summary.status_key
    ]
    for caption, counts in graphs:
        widget = _DistributionCard(language, colors, caption)
        widget.present(counts, summary.total or 0, "")
        result.append((caption, _png(widget)))
    trend = _Trend(language, colors)
    title = dashboard_text(language, "trend")
    trend.set_period(summary.periods[0], translate(language, "meeting_no_events"))
    result.append((title, _png(trend, 765, 290)))
    return tuple(result)


def export_pack(path, columns, rows, summary, pictures, language, start, end, selected_column, selection):
    t = lambda key: translate(language, key)
    metrics = summary.periods[0]
    n = lambda amount: number(language, amount)
    named = {col.key: col.title(language) for col in columns}
    values = [
        (t("date_first"), date_label(start, language)), (t("date_last"), date_label(end, language)),
        (t("meeting_scope"), named.get(selected_column, t("meeting_all"))), (("تعداد نفرات" if language == "fa" else "Personnel count"), n(summary.total or 0)),
        ("جذب در بازه" if language == "fa" else "New hires", n(sum(metrics.hires)) if metrics.hires is not None else "—"),
        ("خروج در بازه" if language == "fa" else "Departures", n(sum(metrics.departures)) if metrics.departures is not None else "—"),
    ]
    values.extend((named[rule.key], rule.value or t("empty_cells")) for rule in selection if rule.key in named)
    from docx import Document
    from docx.shared import Inches

    with TemporaryDirectory(prefix=".niro-meeting-", dir=path.parent) as directory:
        folder = Path(directory)
        export(Report(t("meeting"), (t("value_name"), t("data_value")), tuple(values), language), folder / "Summary.docx")
        document = Document(folder / "Summary.docx")
        for caption, picture in pictures:
            document.add_heading(caption, level=2)
            document.add_picture(BytesIO(picture), width=Inches(6.6))
        document.save(folder / "Summary.docx")
        export(Report(t("people"), tuple(col.title(language) for col in columns),
                      tuple(item.values for item in rows), language), folder / "Personnel.xlsx", data_only=True)
        draft = folder / "meeting.zip"
        with ZipFile(draft, "w", ZIP_DEFLATED) as archive:
            archive.write(folder / "Summary.docx", "Summary.docx")
            archive.write(folder / "Personnel.xlsx", "Personnel.xlsx")
            for index, (_, picture) in enumerate(pictures, 1):
                archive.writestr(f"Charts/Chart-{index:02}.png", picture)
        os.replace(draft, path)
