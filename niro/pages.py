"""Directory, reporting, and settings pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .filters import NO_VALUE
from .locale import number, translate
from .theme import FONT_NAMES, THEMES
from .widgets import (
    ColumnsDialog, ElidedLabel, Hero, Metric, RuleEditor, TablePanel, button, card, field,
    fill_options, icon, label, row_layout, section_header,
)


def _page_layout(page):
    page.setObjectName("page")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(27, 11, 27, 17)
    layout.setSpacing(13)
    return layout


def _scroll(parent_layout):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area = QWidget()
    area.setObjectName("scrollContent")
    body = QVBoxLayout(area)
    body.setContentsMargins(27, 11, 27, 18)
    body.setSpacing(14)
    scroll.setWidget(area)
    parent_layout.addWidget(scroll)
    return body


class PeoplePage(QWidget):
    connect_source = Signal()
    add_person = Signal()
    edit_person = Signal()
    remove_person = Signal()
    restore = Signal()
    search_changed = Signal()
    sort_changed = Signal()

    def __init__(self, language, palette):
        super().__init__()
        self.language = language
        self.t = lambda key, **values: translate(language, key, **values)
        self.current_sort = ""
        self.sort_reverse = False
        self.columns = ()
        layout = _page_layout(self)
        self.add_button = button(self.t("add"), "primary")
        self.add_button.setEnabled(False)
        layout.addWidget(section_header(self.t("people"), self.t("people_lead"), self.add_button))
        self.hero = Hero(palette)
        layout.addWidget(self.hero)
        stats = row_layout(11)
        self.metrics = (
            Metric(self.t("records_metric"), "people", language),
            Metric(self.t("columns_metric"), "excel", language),
            Metric(self.t("local_metric"), "shield", language),
        )
        for metric in self.metrics:
            stats.addWidget(metric, 1)
        layout.addLayout(stats)
        self.pending = label("", "warning", True)
        self.pending.hide()
        layout.addWidget(self.pending)

        pane, panel = card()
        pane.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        top = row_layout(8)
        top.addWidget(label(self.t("directory"), "sectionTitle"))
        self.count = label(self.t("record_count", count=number(language, 0)), "chip")
        top.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch()
        self.edit_button = button(self.t("edit"))
        self.delete_button = button(self.t("delete"), "danger")
        self.restore_button = button(self.t("restore_row"))
        self.restore_button.hide()
        top.addWidget(self.restore_button)
        top.addWidget(self.edit_button)
        top.addWidget(self.delete_button)
        panel.addLayout(top)

        search = row_layout(8)
        self.term = QLineEdit()
        self.term.setPlaceholderText(self.t("search"))
        self.term.setAccessibleName(self.t("search"))
        self.term.setClearButtonEnabled(True)
        self.search_icon = self.term.addAction(icon("search", palette.muted, 17), QLineEdit.ActionPosition.LeadingPosition)
        self.search_column = QComboBox()
        self.search_column.setAccessibleName(self.t("any_column"))
        self.search_column.setMinimumWidth(145)
        self.search_column.setMaximumWidth(274)
        fill_options(self.search_column, [("", self.t("any_column"))])
        search.addWidget(self.term, 1)
        search.addWidget(self.search_column)
        panel.addLayout(search)
        self.table = TablePanel()
        self.table.view.setAccessibleName(self.t("directory"))
        self.table.view.horizontalHeader().setSectionsClickable(True)
        panel.addWidget(self.table, 1)
        layout.addWidget(pane, 1)

        self.add_button.clicked.connect(self.add_person)
        self.edit_button.clicked.connect(self.edit_person)
        self.delete_button.clicked.connect(self.remove_person)
        self.restore_button.clicked.connect(self.restore)
        self.table.empty_action.clicked.connect(self.connect_source)
        self.table.view.doubleClicked.connect(self.edit_person)
        self.term.textChanged.connect(self.search_changed)
        self.search_column.currentIndexChanged.connect(self.search_changed)
        self.table.view.horizontalHeader().sectionClicked.connect(self._sort)
        self.table.view.selectionModel().selectionChanged.connect(self._selection)
        self._selection()
        self.recolor(palette)

    def _selection(self):
        record = self.selected()
        self.edit_button.setEnabled(record is not None)
        self.delete_button.setEnabled(record is not None)
        self.restore_button.setVisible(bool(record and record.overlay and record.source))

    def selected(self):
        selected = self.table.view.selectionModel().selectedRows()
        if selected and selected[0].row() < len(self.table.model.rows):
            return self.table.model.rows[selected[0].row()]
        return None

    def _sort(self, section):
        if not 0 <= section < len(self.columns):
            return
        key = self.columns[section].key
        self.sort_reverse = not self.sort_reverse if self.current_sort == key else False
        self.current_sort = key
        self.sort_changed.emit()

    def set_columns(self, columns):
        keep = self.search_column.currentData()
        self.columns = columns
        fill_options(self.search_column, [("", self.t("any_column")), *((column.key, column.title(self.language)) for column in columns)], keep)
        self.add_button.setEnabled(bool(columns))
        self.search_column.setEnabled(bool(columns))
        self.term.setEnabled(bool(columns))

    def present(self, records, connected, has_records):
        selected = self.selected()
        selected_token = selected.token if selected else None
        empty = "no_source" if not connected else "no_headers" if not self.columns else "no_match" if has_records else "no_rows"
        self.table.present(self.columns, records, self.language, self.t(empty), not connected)
        self.count.setText(self.t("record_count", count=number(self.language, len(records))))
        header = self.table.view.horizontalHeader()
        position = next((index for index, column in enumerate(self.columns) if column.key == self.current_sort), -1)
        header.setSortIndicatorShown(position >= 0)
        if position >= 0:
            order = Qt.SortOrder.DescendingOrder if self.sort_reverse else Qt.SortOrder.AscendingOrder
            header.setSortIndicator(position, order)
        if selected_token:
            new_index = next((index for index, record in enumerate(records) if record.token == selected_token and record.values == selected.values), -1)
            if new_index >= 0:
                self.table.view.selectRow(new_index)
        self._selection()

    def recolor(self, palette):
        self.hero.palette = palette
        self.hero.update()
        self.search_icon.setIcon(icon("search", palette.muted, 17))
        self.table.recolor(palette)
        for item, name, color in (
            (self.add_button, "plus", "#ffffff"), (self.edit_button, "edit", palette.accent),
            (self.delete_button, "delete", palette.warning), (self.restore_button, "check", palette.accent),
        ):
            item.setIcon(icon(name, color, 17))
        for metric in self.metrics:
            metric.recolor(palette)


class ReportPage(QWidget):
    rules_changed = Signal()
    export_requested = Signal()

    def __init__(self, language, palette):
        super().__init__()
        self.language = language
        self.palette = palette
        self.t = lambda key, **values: translate(language, key, **values)
        self.columns = ()
        self.selected_keys = None
        self.editors = []
        self.export_busy = False
        self.report_ready = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        layout = _scroll(outer)
        self.export_button = button(self.t("export"), "primary")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_requested)
        layout.addWidget(section_header(self.t("reports"), self.t("reports_lead"), self.export_button))

        filters, body = card()
        top = row_layout(10)
        self.title = QLineEdit()
        self.title.setMaxLength(160)
        self.title.setPlaceholderText(self.t("report_default"))
        self.column_button = button(self.t("choose_columns"))
        self.column_button.clicked.connect(self.choose_columns)
        self.style = QComboBox()
        fill_options(self.style, [(key, self.t(key)) for key in ("formal", "modern")])
        self.format = QComboBox()
        fill_options(self.format, [("." + key, self.t(key)) for key in ("xlsx", "docx", "pptx", "csv")])
        field(top, self.t("report_name"), self.title, 2)
        field(top, self.t("report_columns"), self.column_button, 1)
        field(top, self.t("style"), self.style, 1)
        field(top, self.t("format"), self.format, 1)
        body.addLayout(top)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        body.addWidget(divider)
        controls = row_layout()
        controls.addWidget(label(self.t("rules"), "sectionTitle"))
        controls.addStretch()
        self.combine = QComboBox()
        fill_options(self.combine, [(False, self.t("rule_all")), (True, self.t("rule_any"))])
        self.combine.currentIndexChanged.connect(self.rules_changed)
        self.add_rule_button = button(self.t("add_rule"))
        self.add_rule_button.clicked.connect(lambda: self.add_rule())
        controls.addWidget(self.combine)
        controls.addWidget(self.add_rule_button)
        body.addLayout(controls)
        self.rule_list = QScrollArea()
        self.rule_list.setWidgetResizable(True)
        self.rule_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rule_container = QWidget()
        self.rule_stack = QVBoxLayout(self.rule_container)
        self.rule_stack.setContentsMargins(0, 0, 0, 0)
        self.rule_stack.setSpacing(5)
        self.rule_stack.addStretch()
        self.rule_list.setWidget(self.rule_container)
        self.rule_list.hide()
        body.addWidget(self.rule_list)
        self.hint = label("", "warning", True)
        self.hint.hide()
        body.addWidget(self.hint)
        layout.addWidget(filters)

        preview, inside = card()
        heading = row_layout()
        heading.addWidget(label(self.t("preview"), "sectionTitle"))
        heading.addStretch()
        self.total = label("", "chip")
        heading.addWidget(self.total)
        inside.addLayout(heading)
        self.table = TablePanel()
        self.table.setMinimumHeight(300)
        self.table.view.setAccessibleName(self.t("preview"))
        inside.addWidget(self.table, 1)
        layout.addWidget(preview, 1)
        self.recolor(palette)
        self.update_column_button()

    def _rule_height(self):
        self.rule_list.setVisible(bool(self.editors))
        if self.editors:
            row = self.editors[0].sizeHint().height()
            self.rule_list.setFixedHeight(min(218, len(self.editors) * (max(row, 44) + 5)))

    def add_rule(self, rule=None, notify=True):
        if not self.columns:
            return
        item = RuleEditor(self.language, self.columns, rule)
        item.recolor(self.palette)
        item.changed.connect(self.rules_changed)
        item.removed.connect(lambda: self.remove_rule(item))
        self.editors.append(item)
        self.rule_stack.insertWidget(self.rule_stack.count() - 1, item)
        self._rule_height()
        if notify:
            self.rules_changed.emit()

    def remove_rule(self, item, notify=True):
        self.editors.remove(item)
        self.rule_stack.removeWidget(item)
        item.hide()
        item.deleteLater()
        self._rule_height()
        if notify:
            self.rules_changed.emit()

    def rule_values(self):
        return tuple(item.rule() for item in self.editors)

    def rules_error(self):
        keys = {column.key for column in self.columns}
        for rule in self.rule_values():
            if rule.key not in keys:
                return "missing_rule_column"
            if rule.operator not in NO_VALUE and (not rule.value.strip() or (rule.operator == "between" and not rule.upper.strip())):
                return "need_filter"
        return ""

    def complete_rules(self):
        return not self.rules_error()

    def set_columns(self, columns):
        self.columns = columns
        for item in self.editors:
            item.set_columns(columns, self.language)
        self.update_column_button()

    def update_column_button(self):
        self.column_button.setEnabled(bool(self.columns))
        self.add_rule_button.setEnabled(bool(self.columns))
        self.column_button.setText(
            self.t("selected_columns", count=number(self.language, len(self.choices()))) if self.columns else self.t("choose_columns")
        )

    def choices(self):
        columns = {column.key for column in self.columns}
        return columns if self.selected_keys is None else columns & self.selected_keys

    def projection(self):
        chosen = self.choices()
        indexes = tuple(index for index, column in enumerate(self.columns) if column.key in chosen)
        return tuple(self.columns[i] for i in indexes), indexes

    def choose_columns(self):
        dialog = ColumnsDialog(self, self.language, self.columns, self.choices())
        if dialog.exec() == dialog.DialogCode.Accepted:
            chosen = dialog.keys()
            self.selected_keys = None if chosen == {column.key for column in self.columns} else chosen
            self.update_column_button()
            self.rules_changed.emit()

    def set_busy(self, busy):
        self.export_busy = busy
        self.export_button.setEnabled(self.report_ready and not busy)
        self.export_button.setText(self.t("working_export" if busy else "export"))

    def present(self, records, connected, preview_limit=120):
        chosen, positions = self.projection()
        error = self.rules_error()
        self.hint.setVisible(connected and (not chosen or bool(error)))
        self.hint.setText(self.t(error or "select_columns_error"))
        self.report_ready = connected and bool(chosen) and not error
        self.export_button.setEnabled(self.report_ready and not self.export_busy)
        empty = "no_source" if not connected else "select_columns_error" if not chosen else "no_report"
        self.table.present(chosen, records[:preview_limit], self.language, self.t(empty), positions=positions)
        self.total.setText(self.t("preview_count", shown=number(self.language, min(len(records), preview_limit)),
                                  total=number(self.language, len(records))))

    def recolor(self, palette):
        self.palette = palette
        self.export_button.setIcon(icon("download", "#ffffff", 17))
        self.add_rule_button.setIcon(icon("plus", palette.accent, 17))
        for rule in self.editors:
            rule.recolor(palette)
        self.table.recolor(palette)


class SettingsPage(QWidget):
    connect_source = Signal()
    create_template = Signal()
    sheet_changed = Signal()
    appearance_changed = Signal()
    save_name = Signal()
    copy_backup = Signal()
    reset_local = Signal()

    def __init__(self, language, palette, font, theme, name):
        super().__init__()
        self.language = language
        self.t = lambda key, **values: translate(language, key, **values)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        layout = _scroll(outer)
        layout.addWidget(section_header(self.t("settings"), self.t("settings_lead")))

        source, panel = card()
        panel.addWidget(label(self.t("excel_source"), "sectionTitle"))
        self.path = ElidedLabel("muted")
        panel.addWidget(self.path)
        workbook = row_layout()
        self.sheet = QComboBox()
        field(workbook, self.t("sheet"), self.sheet, 2)
        connect = button(self.t("change_source"))
        blank = button(self.t("blank_workbook"))
        workbook.addWidget(connect, 0, Qt.AlignmentFlag.AlignBottom)
        workbook.addWidget(blank, 0, Qt.AlignmentFlag.AlignBottom)
        panel.addLayout(workbook)
        layout.addWidget(source)

        appearance, panel = card()
        panel.addWidget(label(self.t("appearance"), "sectionTitle"))
        first = row_layout()
        self.name = QLineEdit(name)
        self.name.setPlaceholderText(self.t("name_placeholder"))
        self.name.setMaxLength(42)
        save = button(self.t("save_settings"))
        field(first, self.t("app_name"), self.name, 2)
        first.addWidget(save, 0, Qt.AlignmentFlag.AlignBottom)
        self.language_choice = QComboBox()
        fill_options(self.language_choice, (("fa", "فارسی"), ("en", "English")), language)
        field(first, self.t("language"), self.language_choice)
        self.font_choice = QComboBox()
        fill_options(self.font_choice, ((key, self.t("font_" + key)) for key in FONT_NAMES), font)
        field(first, self.t("font"), self.font_choice)
        panel.addLayout(first)
        panel.addWidget(label(self.t("theme"), "smallMuted"))
        swatches = row_layout()
        self.theme_group = QButtonGroup(self)
        self.themes = {}
        for key, colors in THEMES.items():
            pick = button(self.t(key), "swatch")
            pick.setCheckable(True)
            pick.setProperty("themeKey", key)
            pick.setChecked(key == theme)
            pick.setIcon(icon("offline", colors.accent, 20))
            self.theme_group.addButton(pick)
            swatches.addWidget(pick, 1)
            self.themes[key] = pick
        panel.addLayout(swatches)
        layout.addWidget(appearance)

        security, panel = card()
        panel.addWidget(label(self.t("local_data"), "sectionTitle"))
        panel.addWidget(label(self.t("backup_lead"), "muted", True))
        self.local_state = label("", "smallMuted", True)
        panel.addWidget(self.local_state)
        actions = row_layout()
        self.backup_button = button(self.t("backup"))
        self.reset_button = button(self.t("reset_local"), "danger")
        actions.addWidget(self.backup_button)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        panel.addLayout(actions)
        layout.addWidget(security)
        layout.addStretch()

        connect.clicked.connect(self.connect_source)
        blank.clicked.connect(self.create_template)
        self.sheet.currentIndexChanged.connect(self.sheet_changed)
        save.clicked.connect(self.save_name)
        self.name.returnPressed.connect(self.save_name)
        self.language_choice.currentIndexChanged.connect(self.appearance_changed)
        self.font_choice.currentIndexChanged.connect(self.appearance_changed)
        self.theme_group.buttonClicked.connect(self.appearance_changed)
        self.backup_button.clicked.connect(self.copy_backup)
        self.reset_button.clicked.connect(self.reset_local)
        self.recolor(palette)

    def recolor(self, palette):
        self.backup_button.setIcon(icon("download", palette.accent, 17))

    def update_source(self, view, backup_exists):
        self.backup_button.setEnabled(backup_exists)
        self.reset_button.setEnabled(bool(view and view.local_count))
        if not view:
            self.path.setText(self.t("disconnected"))
            self.sheet.setEnabled(False)
            self.local_state.clear()
            return
        snapshot = view.snapshot
        self.path.setText(str(snapshot.path))
        self.sheet.setEnabled(bool(snapshot.sheets))
        fill_options(self.sheet, ((sheet.key, sheet.label) for sheet in snapshot.sheets), snapshot.sheet.key)
        self.local_state.setText(self.t("local_status", hidden=number(self.language, view.hidden), pending=number(self.language, view.pending)))
