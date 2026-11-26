"""Main window and asynchronous coordination for sync, search, and exports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMessageBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import __version__
from .filters import filter_records
from .locale import number, translate
from .pages import PeoplePage, ReportPage, SettingsPage
from .reports import Report, export
from .state import Store
from .theme import ASSETS, FONT_NAMES, THEMES, interface_font, style
from .widgets import ElidedLabel, RecordDialog, button, icon, label, row_layout
from .workbook import WorkbookError, make_template, read_workbook, stamp, update_workbook


class _Job(QRunnable):
    def __init__(self, bus, identifier, work):
        super().__init__()
        self.bus = bus
        self.identifier = identifier
        self.work = work

    def run(self):
        try:
            result = self.work()
        except Exception as error:
            self.bus.result.emit(self.identifier, None, error)
        else:
            self.bus.result.emit(self.identifier, result, None)


class TaskBus(QObject):
    result = Signal(int, object, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.pending = {}
        self.next_id = 0
        self.result.connect(self.deliver, Qt.ConnectionType.QueuedConnection)

    def submit(self, work, done):
        self.next_id += 1
        self.pending[self.next_id] = done
        self.pool.start(_Job(self, self.next_id, work))

    @Slot(int, object, object)
    def deliver(self, identifier, data, error):
        callback = self.pending.pop(identifier)
        callback(data, error)


class Window(QMainWindow):
    def __init__(self, store: Store, settings: QSettings, system_font, initial_file: Path | None = None,
                 watch_interval=950):
        super().__init__()
        self.store = store
        self.settings = settings
        self.system_font = system_font
        self.language = settings.value("language", "fa", str)
        self.language = self.language if self.language in ("fa", "en") else "fa"
        self.theme = settings.value("theme", "violet", str)
        self.theme = self.theme if self.theme in THEMES else "violet"
        self.font_key = settings.value("font", "vazir", str)
        self.font_key = self.font_key if self.font_key in FONT_NAMES else "vazir"
        self.display_name = settings.value("name", "", str)
        self.source_path = initial_file or (Path(value) if (value := settings.value("workbook", "", str)) else None)
        self.preferred_sheet = None if initial_file else (
            settings.value("worksheet", "", str), settings.value("worksheetName", "", str))
        self.view = None
        self.last_read = None
        self.problem = ""
        self.status = "disconnected"
        self.page_key = "people"
        self.sync_busy = False
        self.write_busy = False
        self.export_busy = False
        self.filter_busy = False
        self.filter_revision = 0
        self.filter_queued = False
        self.report_records = []
        self.directory_records = []
        self.bus = TaskBus(self)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(155)
        self.filter_timer.timeout.connect(self._filter)
        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(watch_interval)
        self.sync_timer.timeout.connect(self._sync)
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self._clear_notice)
        self.setWindowIcon(QIcon(str(ASSETS / "niro.png")))
        self.resize(1320, 850)
        self.setMinimumSize(870, 663)
        self._style_app()
        self._build()
        self.sync_timer.start()
        if self.source_path:
            QTimer.singleShot(0, self._sync)

    def t(self, key, **values):
        return translate(self.language, key, **values)

    def n(self, amount):
        return number(self.language, amount)

    def _style_app(self):
        self.colors = THEMES[self.theme]
        app = QApplication.instance()
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if self.language == "fa" else Qt.LayoutDirection.LeftToRight)
        app.setFont(interface_font(self.font_key, self.system_font))
        app.setStyleSheet(style(self.colors))

    def _build(self):
        root = QWidget()
        horizontal = QHBoxLayout(root)
        horizontal.setContentsMargins(0, 0, 0, 0)
        horizontal.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(17, 21, 17, 17)
        rail.setSpacing(10)
        brand = row_layout(11)
        mark = label()
        mark.setPixmap(QPixmap(str(ASSETS / "niro.png")).scaled(49, 49, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(mark)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        self.brand = ElidedLabel("brandName")
        brand_copy.addWidget(self.brand)
        brand_copy.addWidget(label(self.t("product"), "sidebarMuted"))
        brand.addLayout(brand_copy, 1)
        rail.addLayout(brand)
        rail.addSpacing(21)

        self.navigation = QButtonGroup(sidebar)
        self.nav = {}
        for key in ("people", "reports", "settings"):
            item = button(self.t(key), "nav")
            item.setCheckable(True)
            item.setMinimumHeight(46)
            item.setIcon(icon(key, self.colors.sidebar_text))
            item.clicked.connect(lambda checked, page=key: self.set_page(page))
            self.navigation.addButton(item)
            self.nav[key] = item
            rail.addWidget(item)
        rail.addStretch()

        source = QFrame()
        source.setObjectName("sourceBox")
        source_body = QVBoxLayout(source)
        source_body.setContentsMargins(13, 13, 13, 13)
        source_body.setSpacing(7)
        source_title = row_layout(6)
        self.source_icon = label()
        source_title.addWidget(self.source_icon)
        source_title.addWidget(label(self.t("excel_source")))
        source_title.addStretch()
        source_body.addLayout(source_title)
        self.source_file = ElidedLabel("sidebarMuted")
        source_body.addWidget(self.source_file)
        self.link_button = button(self.t("open_source"), "sidebarAction")
        self.link_button.clicked.connect(self.choose_file)
        source_body.addWidget(self.link_button)
        rail.addWidget(source)
        version = label(f"Niro Desk {__version__}", "sidebarMuted")
        version.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rail.addWidget(version)
        horizontal.addWidget(sidebar)

        stage = QWidget()
        stage.setObjectName("stage")
        body = QVBoxLayout(stage)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        bar = row_layout(8)
        bar.setContentsMargins(27, 12, 27, 6)
        self.breadcrumb = label("", "smallMuted")
        bar.addWidget(self.breadcrumb, 1)
        self.offline = label()
        bar.addWidget(self.offline)
        bar.addWidget(label(self.t("offline"), "smallMuted"))
        self.lang_button = button("EN" if self.language == "fa" else "فارسی")
        self.lang_button.setAccessibleName(self.t("language"))
        self.lang_button.clicked.connect(lambda: self.set_language("en" if self.language == "fa" else "fa"))
        bar.addWidget(self.lang_button)
        body.addLayout(bar)

        self.stack = QStackedWidget()
        self.pages = {}
        self.people = PeoplePage(self.language, self.colors)
        self.reports = ReportPage(self.language, self.colors)
        self.options = SettingsPage(self.language, self.colors, self.font_key, self.theme, self.display_name)
        for key, page in (("people", self.people), ("reports", self.reports), ("settings", self.options)):
            self.pages[key] = page
            self.stack.addWidget(page)
        body.addWidget(self.stack, 1)
        bar = row_layout(6)
        bar.setContentsMargins(28, 3, 28, 10)
        self.dot = label("●")
        bar.addWidget(self.dot)
        self.status_text = label("", "smallMuted")
        bar.addWidget(self.status_text)
        self.status_detail = ElidedLabel("smallMuted")
        bar.addWidget(self.status_detail, 1)
        body.addLayout(bar)
        horizontal.addWidget(stage, 1)

        self.people.connect_source.connect(self.choose_file)
        self.people.add_person.connect(lambda: self._record("add"))
        self.people.edit_person.connect(lambda: self._record("edit"))
        self.people.remove_person.connect(lambda: self._record("delete"))
        self.people.restore.connect(self.restore_record)
        self.people.search_changed.connect(self._plan_filter)
        self.people.sort_changed.connect(self._plan_filter)
        self.reports.rules_changed.connect(self._plan_filter)
        self.reports.export_requested.connect(self.export_report)
        self.options.connect_source.connect(self.choose_file)
        self.options.create_template.connect(self.create_file)
        self.options.sheet_changed.connect(self.choose_sheet)
        self.options.appearance_changed.connect(self.update_appearance)
        self.options.save_name.connect(self.save_name)
        self.options.copy_backup.connect(self.copy_backup)
        self.options.reset_local.connect(self.reset_local)
        self.setCentralWidget(root)
        self._recolor()
        self.set_page(self.page_key)
        self._update_name()
        self._draw_source()
        self._draw_layout()
        self._clear_notice()

    def _recolor(self):
        self.source_icon.setPixmap(icon("excel", self.colors.mint, 18).pixmap(18, 18))
        self.offline.setPixmap(icon("shield", self.colors.good, 17).pixmap(17, 17))
        for key, item in self.nav.items():
            item.setIcon(icon(key, self.colors.sidebar_text))
        for page in self.pages.values():
            page.recolor(self.colors)
        self._draw_source()

    def set_page(self, key):
        self.page_key = key
        self.stack.setCurrentWidget(self.pages[key])
        self.nav[key].setChecked(True)
        self.breadcrumb.setText(self.t("workspace") + " / " + self.t(key))

    def _update_name(self):
        title = self.display_name or self.t("default_name")
        self.brand.setText(title)
        self.setWindowTitle(title + " · " + self.t("product"))
        QApplication.instance().setApplicationDisplayName(title)
        self.options.name.setPlaceholderText(title)

    def save_name(self):
        self.display_name = self.options.name.text().strip()
        self.settings.setValue("name", self.display_name)
        self._update_name()
        self.notify(self.t("settings_saved"))

    def set_language(self, value):
        if value == self.language:
            return
        search = self.people.term.text(), self.people.search_column.currentData(), self.people.current_sort, self.people.sort_reverse
        plan = (self.reports.title.text(), self.reports.style.currentData(), self.reports.format.currentData(),
                self.reports.combine.currentData(), self.reports.selected_keys, self.reports.rule_values())
        self.language = value
        self.settings.setValue("language", value)
        self._style_app()
        self._build()
        self.people.term.setText(search[0])
        self.people.search_column.setCurrentIndex(max(0, self.people.search_column.findData(search[1])))
        self.people.current_sort, self.people.sort_reverse = search[2:]
        title, layout_style, extension, any_rule, keys, rules = plan
        self.reports.title.setText(title)
        self.reports.style.setCurrentIndex(self.reports.style.findData(layout_style))
        self.reports.format.setCurrentIndex(self.reports.format.findData(extension))
        self.reports.combine.setCurrentIndex(self.reports.combine.findData(any_rule))
        self.reports.selected_keys = keys
        self.reports.update_column_button()
        for rule in rules:
            self.reports.add_rule(rule, False)
        self.reports.set_busy(self.export_busy)
        self._plan_filter(immediate=True)

    def update_appearance(self):
        self.theme = self.options.theme_group.checkedButton().property("themeKey")
        self.font_key = self.options.font_choice.currentData()
        self.settings.setValue("theme", self.theme)
        self.settings.setValue("font", self.font_key)
        language = self.options.language_choice.currentData()
        if language != self.language:
            self.set_language(language)
        else:
            self._style_app()
            self._recolor()

    def _draw_source(self):
        self.status_text.setText(self.t(self.status))
        color = self.colors.good if self.status == "linked" else self.colors.warning if self.problem else self.colors.muted
        self.dot.setStyleSheet(f"color:{color}; font-size: 11px;")
        snapshot = self.view.snapshot if self.view else None
        source_path = snapshot.path if snapshot else self.source_path
        self.source_file.setText(source_path.name if source_path else self.t("disconnected"))
        self.link_button.setText(self.t("change_source" if source_path else "open_source"))
        hero = self.people.hero
        hero.tag.setText(self.t(self.status))
        hero.title.setText(source_path.stem if source_path else self.t("source_intro"))
        if snapshot:
            when = self.last_read.strftime("%H:%M:%S") if self.last_read else ""
            if self.language == "fa":
                from .locale import PERSIAN_DIGITS
                when = when.translate(PERSIAN_DIGITS)
            sheet = snapshot.sheet.label
            hero.subtitle.setText(sheet + "   ·   " + self.t("source_updated", time=when))
        else:
            hero.subtitle.setText(self.t("source_stage"))

    def _draw_layout(self):
        cols = self.view.snapshot.columns if self.view else ()
        if self.people.columns != cols or self.reports.columns != cols:
            self.people.set_columns(cols)
            self.reports.set_columns(cols)
        counts = (len(self.view.records), len(cols), self.view.local_count) if self.view else (0, 0, 0)
        for metric, amount in zip(self.people.metrics, counts):
            metric.value.setText(self.n(amount))
        pending = self.view.pending if self.view else 0
        self.people.pending.setVisible(bool(pending))
        self.people.pending.setText(self.t("pending_notice", count=self.n(pending)))
        self.options.update_source(self.view, bool(self.view and self.store.backup_path(self.view.snapshot.path).exists()))
        self.people.present([], self.view is not None, bool(self.view and self.view.records))
        self.reports.present([], self.view is not None)

    def _apply(self, view, reading=True):
        previous_columns = self.view.snapshot.columns if self.view else ()
        self.view = view
        snapshot = view.snapshot
        self.source_path = snapshot.path
        self.preferred_sheet = snapshot.sheet.key, snapshot.sheet.label
        self.settings.setValue("workbook", str(snapshot.path))
        self.settings.setValue("worksheet", snapshot.sheet.key)
        self.settings.setValue("worksheetName", snapshot.sheet.label)
        if reading:
            self.last_read = datetime.now()
            self.problem = ""
            self.status = "linked"
            self._clear_notice()
        if previous_columns != snapshot.columns or self.people.columns != snapshot.columns:
            self._draw_layout()
        else:
            counts = (len(self.view.records), len(snapshot.columns), self.view.local_count)
            for metric, amount in zip(self.people.metrics, counts):
                metric.value.setText(self.n(amount))
            self.people.pending.setVisible(bool(view.pending))
            self.people.pending.setText(self.t("pending_notice", count=self.n(view.pending)))
            self.options.update_source(view, self.store.backup_path(snapshot.path).exists())
        self._draw_source()
        self._plan_filter(immediate=True)

    def _reason(self, error):
        if isinstance(error, WorkbookError):
            return self.t(error.args[0])
        if isinstance(error, FileNotFoundError):
            return self.t("file_missing")
        if isinstance(error, PermissionError):
            return self.t("permission_error")
        return self.t("operation_error")

    def notify(self, text, temporary=True):
        self.status_detail.setText(text)
        if temporary:
            self.notice_timer.start(6400)
        else:
            self.notice_timer.stop()

    def _clear_notice(self):
        self.status_detail.setText(self.problem)

    def _sync(self):
        if not self.source_path or self.sync_busy or self.write_busy:
            return
        active = self.view.snapshot if self.view else None
        self._load(self.source_path, self.preferred_sheet, active)

    def _load(self, path, sheet=None, previous=None, manual=False):
        if self.sync_busy or self.write_busy:
            if manual:
                QTimer.singleShot(90, lambda: self._load(path, sheet, manual=True))
            return
        self.sync_busy = True
        if manual or self.view is None:
            self.status = "loading"
            self._draw_source()

        def load():
            if previous and stamp(path) == previous.stamp:
                return False
            loaded = read_workbook(path, sheet)
            return self.store.view(loaded)

        def done(view, problem):
            self.sync_busy = False
            if problem:
                if manual:
                    self._message("select_source_error", self._reason(problem))
                    self.status = "linked" if self.view and not self.problem else "sync_problem" if self.source_path else "disconnected"
                else:
                    self.problem = self._reason(problem)
                    self.status = "sync_problem"
                    self.notify(self.problem, False)
                self._draw_source()
                return
            if view is False:
                if self.problem:
                    self.problem = ""
                    self.status = "linked"
                    self._draw_source()
                    self._clear_notice()
                return
            self._apply(view)

        self.bus.submit(load, done)

    def choose_file(self):
        selected, _ = QFileDialog.getOpenFileName(self, self.t("open_source"), str(self.source_path or ""), "Excel (*.xlsx)")
        if selected:
            self._load(Path(selected), manual=True)

    def create_file(self):
        destination, _ = QFileDialog.getSaveFileName(self, self.t("blank_workbook"), "Personnel.xlsx", "Excel (*.xlsx)")
        if not destination:
            return
        path = self._extension(Path(destination), ".xlsx")
        if not self._destination_allowed(path):
            return
        self._run_write(lambda: make_template(path), lambda _: self._load(path, manual=True))

    def choose_sheet(self):
        selected = self.options.sheet.currentData()
        chosen = selected, self.options.sheet.currentText()
        if self.source_path and selected and chosen != self.preferred_sheet:
            self._load(self.source_path, chosen, manual=True)

    def _plan_filter(self, immediate=False):
        self.filter_revision += 1
        self.filter_timer.start(0 if immediate is True else 155)

    def _filter(self):
        if self.filter_busy:
            self.filter_queued = True
            return
        self.filter_busy = True
        self.filter_queued = False
        generation = self.filter_revision
        view = self.view
        columns = view.snapshot.columns if view else ()
        records = view.records if view else ()
        term, search = self.people.term.text(), self.people.search_column.currentData()
        order, reverse = self.people.current_sort, self.people.sort_reverse
        rules, any_match = self.reports.rule_values(), self.reports.combine.currentData()

        def filter_all():
            directory = filter_records(records, columns, term, search, sort_key=order, reverse=reverse)
            reporting = filter_records(records, columns, rules=rules, any_rule=any_match)
            return directory, reporting

        def show(results, problem):
            self.filter_busy = False
            if not problem and generation == self.filter_revision:
                self.directory_records, self.report_records = results
                self.people.present(self.directory_records, view is not None, bool(records))
                self.reports.present(self.report_records, view is not None)
            elif problem:
                self.notify(self._reason(problem))
            if self.filter_queued:
                self.filter_timer.start(0)

        self.bus.submit(filter_all, show)

    def _record(self, operation):
        view = self.view
        if not view or self.write_busy or not view.snapshot.columns:
            return
        record = self.people.selected() if operation != "add" else None
        if operation != "add" and (not record or not any(item is record for item in view.records)):
            return
        dialog = RecordDialog(self, self.language, view.snapshot.columns, record, operation)
        def commit(change):
            target, values = change
            if view is not self.view:
                dialog.failed(self.t("source_changed"))
                return
            def apply():
                if target == "local":
                    return self.store.change(view, operation, record, values)
                snapshot = view.snapshot
                row = record.source.number if record and record.source else None
                verb = "add" if row is None and operation == "edit" else operation
                updated = update_workbook(snapshot, verb, row, values, lambda payload: self.store.backup(snapshot.path, payload))
                return self.store.finish_source_change(updated, record, verb, values)

            def finished(new_view):
                self._apply(new_view, reading=target == "excel")
                self.notify(self.t("saved_excel" if target == "excel" else "saved_local"))
                dialog.accept()

            self._run_write(apply, finished, dialog.failed, expected=view)
        dialog.submitted.connect(commit)
        dialog.exec()

    def _run_write(self, work, success, fail=None, expected=None):
        if self.write_busy:
            if fail:
                fail(self.t("working"))
            return
        self.write_busy = True

        def done(data, error):
            self.write_busy = False
            if error:
                text = self._reason(error)
                if fail:
                    fail(text)
                else:
                    self._message("operation_error", text)
            else:
                success(data)

        def start():
            if self.sync_busy:
                QTimer.singleShot(35, start)
            elif expected is not None and expected is not self.view:
                done(None, WorkbookError("source_changed"))
            else:
                self.bus.submit(work, done)

        start()

    def _message(self, title, text):
        box = QMessageBox(QMessageBox.Icon.Warning, self.t(title), text, QMessageBox.StandardButton.Ok, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.button(QMessageBox.StandardButton.Ok).setText(self.t("close"))
        box.exec()

    def restore_record(self):
        record = self.people.selected()
        if record and record.overlay and self.view and any(item is record for item in self.view.records):
            snapshot = self.view.snapshot
            self._run_write(lambda: self.store.clear_record(snapshot, record),
                            lambda view: self._apply(view, False), expected=self.view)

    def reset_local(self):
        if not self.view:
            return
        box = QMessageBox(QMessageBox.Icon.Question, self.t("reset_local"), self.t("reset_question"), parent=self)
        confirm = box.addButton(self.t("reset_local"), QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(self.t("cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is confirm and self.view:
            snapshot = self.view.snapshot
            def finished(view):
                self._apply(view, False)
                self.notify(self.t("local_reset"))
            self._run_write(lambda: self.store.clear(snapshot), finished, expected=self.view)

    @staticmethod
    def _extension(path, suffix):
        return path if path.suffix.lower() == suffix else path.with_name(path.name + suffix)

    def _destination_allowed(self, path):
        if self.source_path:
            try:
                same_file = path.samefile(self.source_path)
            except OSError:
                same_file = path.resolve() == self.source_path.resolve()
            if same_file:
                self._message("operation_error", self.t("cannot_overwrite_source"))
                return False
        return True

    def copy_backup(self):
        if not self.view:
            return
        path = self.view.snapshot.path
        choice, _ = QFileDialog.getSaveFileName(self, self.t("backup"), f"{path.stem}-recovery.xlsx", "Excel (*.xlsx)")
        if choice:
            destination = self._extension(Path(choice), ".xlsx")
            if self._destination_allowed(destination):
                self._run_write(lambda: self.store.copy_backup(path, destination), lambda _: self.notify(self.t("recovery_saved")))

    def export_report(self):
        view = self.view
        selected, projection = self.reports.projection()
        if not view or not selected or not self.reports.complete_rules() or self.export_busy:
            return
        extension = self.reports.format.currentData()
        title = self.reports.title.text().strip() or self.t("report_default")
        rules, any_match = self.reports.rule_values(), self.reports.combine.currentData()
        language, layout = self.language, self.reports.style.currentData()
        file_filter = self.t(extension[1:]).replace("(" + extension, "(*" + extension)
        filename, _ = QFileDialog.getSaveFileName(self, self.t("export"), "Niro-Report" + extension, file_filter)
        if not filename:
            return
        path = self._extension(Path(filename), extension)
        if not self._destination_allowed(path):
            return
        self.export_busy = True
        self.reports.set_busy(True)
        self.notify(self.t("working_export"), False)

        def work():
            records = filter_records(view.records, view.snapshot.columns, rules=rules, any_rule=any_match)
            rows = tuple(tuple(item.values[index] for index in projection) for item in records)
            export(Report(title, tuple(column.title(language) for column in selected), rows, language, layout), path)

        def done(result, error):
            self.export_busy = False
            self.reports.set_busy(False)
            self.notify(self._reason(error) if error else self.t("exported"))

        self.bus.submit(work, done)

    def closeEvent(self, event):
        if self.export_busy or self.write_busy:
            self.notify(self.t("busy_close"))
            event.ignore()
        else:
            self.sync_timer.stop()
            event.accept()
