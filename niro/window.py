"""Main window and asynchronous coordination for sync, search, and exports."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from PySide6.QtCore import (
    QDate, QEasingCurve, QObject, QPropertyAnimation, QRunnable, QSettings, QStandardPaths, QThreadPool, QTimer, Qt, Signal, Slot,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QMainWindow, QMessageBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import __version__
from .access import Access, Account, visible_view
from .access_ui import AccountsDialog, AuditDialog
from .attendance_page import AttendanceDialog, AttendancePage
from .catalog import distinct_values, report_filename
from .dashboard import DashboardPage, summarize
from .dashboard_tools import LayoutDialog, PhraseDialog, ResultsDialog, export_pack, meeting_settings, pack_charts, prepare_pack
from .filters import Rule, filter_records
from .insights import evaluate, load_dataset, prepare_profile, save_attendance, tr as insight_text
from .insights_page import AnalyticsPage, SourceDialog
from .locale import number, translate
from .office import OfficeStore, reminders, roster
from .office_ui import DossierDialog, OfficePage, QualityDialog, ReminderDialog, reminder_settings
from .pages import PeoplePage, ReportPage, SettingsPage
from .record_fields import DEFAULT_COMBO_COLUMNS
from .reports import Report, export
from .state import Store, namespace
from .theme import ASSETS, FONT_NAMES, THEMES, application_palette, interface_font, style
from .time_ui import TimekeepingDialog
from .visuals import brandmark
from .widgets import ColumnsDialog, ElidedLabel, RecordDialog, button, icon, label, row_layout
from .workbook import WorkbookError, group_key, make_template, normalize, read_workbook, stamp, update_workbook


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
    audit_error = Signal()

    def __init__(self, store: Store, settings: QSettings, system_font, initial_file: Path | None = None,
                 watch_interval=950, *, account=None, access=None):
        super().__init__()
        self.store = store
        self.access = access if access else Access(store)
        self.account = account if account else Account()
        self.office_store = OfficeStore(store)
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
        self.source_view = None
        self.last_read = None
        self.problem = ""
        self.status = "disconnected"
        self.page_key = "dashboard"
        self.record_dialog = None
        self.catalog = {}
        self.summary = summarize(None)
        self.analytics_data = None
        self.analytics_scale = "percent"
        self.analysis_busy = False
        self.analysis_revision = 0
        self.metadata_busy = False
        self.metadata_date = QDate.currentDate()
        self.page_animation = None
        self.animating_page = None
        self.sync_busy = False
        self.write_busy = False
        self.export_busy = False
        self.filter_busy = False
        self.filter_revision = 0
        self.filter_queued = False
        self.report_records = []
        self.directory_records = []
        self.directory_rules = ()
        self.chart_selection = {}
        self.reminder_busy = False
        self.reminder_count = 0
        self.bus = TaskBus(self)
        self.audit_error.connect(self._notify_audit_error, Qt.ConnectionType.QueuedConnection)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(155)
        self.filter_timer.timeout.connect(self._filter)
        self.analysis_timer = QTimer(self)
        self.analysis_timer.setSingleShot(True)
        self.analysis_timer.setInterval(150)
        self.analysis_timer.timeout.connect(self._analyze)
        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(watch_interval)
        self.sync_timer.timeout.connect(self._sync)
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self._clear_notice)
        self.reminder_timer = QTimer(self)
        self.reminder_timer.setSingleShot(True)
        self.reminder_timer.setInterval(260)
        self.reminder_timer.timeout.connect(self._refresh_reminders)
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
        app.setPalette(application_palette(self.colors, app.style().standardPalette()))
        app.setStyleSheet(style(self.colors))

    def _build(self):
        self._finish_transition()
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
        self.brand_mark = label()
        brand.addWidget(self.brand_mark)
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
        for key in ("dashboard", "analytics", "people", "office", "attendance", "reports", "settings"):
            if key in ("analytics", "attendance") and not self.account.sees_sensitive:
                continue
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
        self.dashboard = DashboardPage(self.language, self.colors)
        self.analytics = AnalyticsPage(self.language, self.colors, self.settings)
        self.people = PeoplePage(self.language, self.colors)
        self.attendance = AttendancePage(self.language, self.colors)
        self.reports = ReportPage(self.language, self.colors)
        self.office_page = OfficePage(self)
        self.options = SettingsPage(self.language, self.colors, self.font_key, self.theme, self.display_name, self.system_font)
        for key, page in (
            ("dashboard", self.dashboard), ("analytics", self.analytics), ("people", self.people),
            ("office", self.office_page), ("attendance", self.attendance), ("reports", self.reports), ("settings", self.options),
        ):
            if key in self.nav:
                self.pages[key] = page
                self.stack.addWidget(page)
            else:
                page.setParent(self.stack)
                page.hide()
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

        self.dashboard.choose_charts.connect(self._configure_dashboard_charts)
        self.dashboard.choose_layout.connect(self._configure_layout)
        self.dashboard.category_selected.connect(self._select_chart_category)
        self.dashboard.selection_cleared.connect(self._clear_chart_selection)
        self.dashboard.open_selection.connect(self._open_chart_results)
        self.dashboard.meeting_requested.connect(self.export_meeting)
        self.people.connect_source.connect(self.choose_file)
        self.people.dossier_requested.connect(self.open_selected_dossier)
        self.people.clear_extra_filters.connect(self.clear_directory_rules)
        self.people.add_person.connect(lambda: self._record("add"))
        self.people.edit_person.connect(lambda: self._record("edit"))
        self.people.remove_person.connect(lambda: self._record("delete"))
        self.people.restore.connect(self.restore_record)
        self.people.search_changed.connect(self._plan_filter)
        self.people.sort_changed.connect(self._plan_filter)
        self.analytics.changed.connect(self._plan_analysis)
        self.analytics.configure.connect(self._configure_analytics)
        self.analytics.create_profile.connect(self._create_profile_sheet)
        self.attendance.add.connect(lambda: self._attendance_dialog("add"))
        self.attendance.edit.connect(lambda: self._attendance_dialog("edit"))
        self.attendance.remove.connect(lambda: self._attendance_dialog("remove"))
        self.reports.rules_changed.connect(self._plan_filter)
        self.reports.export_requested.connect(self.export_report)
        self.options.connect_source.connect(self.choose_file)
        self.options.create_template.connect(self.create_file)
        self.options.sheet_changed.connect(self.choose_sheet)
        self.options.appearance_changed.connect(self.update_appearance)
        self.options.save_name.connect(self.save_name)
        self.options.copy_backup.connect(self.copy_backup)
        self.options.reset_local.connect(self.reset_local)
        self.options.configure_combos.connect(self.configure_combos)
        self.setCentralWidget(root)
        self._recolor()
        self.set_page(self.page_key, animate=False)
        self._update_name()
        self._draw_source()
        self._draw_layout()
        self._show_metadata()
        self._clear_notice()

    def _recolor(self):
        self.brand_mark.setPixmap(brandmark(self.colors, 49))
        self.source_icon.setPixmap(icon("excel", self.colors.mint, 18).pixmap(18, 18))
        self.offline.setPixmap(icon("shield", self.colors.good, 17).pixmap(17, 17))
        self._color_navigation()
        for page in self.pages.values():
            page.recolor(self.colors)
        if self.record_dialog is not None:
            self.record_dialog.recolor(self.colors)
        self._draw_source()

    def _color_navigation(self):
        for key, item in self.nav.items():
            color = "#ffffff" if self.colors.graphic and key == self.page_key else self.colors.sidebar_text
            item.setIcon(icon("briefcase" if key == "office" else key, color))

    def set_page(self, key, animate=True):
        if key not in self.pages:
            key = "dashboard"
        target = self.pages[key]
        changed = self.stack.currentWidget() is not target
        self._finish_transition()
        self.page_key = key
        self.stack.setCurrentWidget(target)
        self.nav[key].setChecked(True)
        self._color_navigation()
        self.breadcrumb.setText(self.t("workspace") + " / " + self.t(key))
        if key == "analytics":
            self._plan_analysis()
        if changed and animate and self.isVisible():
            effect = QGraphicsOpacityEffect(target)
            effect.setOpacity(0.78)
            target.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", self)
            animation.setDuration(205)
            animation.setStartValue(0.78)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.finished.connect(self._finish_transition)
            self.page_animation = animation
            self.animating_page = target
            animation.start()

    def _finish_transition(self):
        if self.page_animation is not None:
            self.page_animation.stop()
            self.page_animation.deleteLater()
            self.page_animation = None
            self.animating_page.setGraphicsEffect(None)
            self.animating_page = None

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
        value_filter = self.people.selected_value()
        analysis_person = self.analytics.person.currentData()
        attendance_filter = self.attendance.search.text(), self.attendance.status.currentData()
        plan = (self.reports.title.text(), self.reports.style.currentData(), self.reports.format.currentData(),
                self.reports.combine.currentData(), self.reports.selected_keys, self.reports.rule_values())
        self.language = value
        self.settings.setValue("language", value)
        self._style_app()
        self._build()
        self.people.term.setText(search[0])
        self.people.search_column.setCurrentIndex(max(0, self.people.search_column.findData(search[1])))
        self.people.current_sort, self.people.sort_reverse = search[2:]
        self.people.select_value(value_filter)
        self.analytics.person.setCurrentIndex(max(0, self.analytics.person.findData(analysis_person)))
        self.attendance.search.setText(attendance_filter[0])
        self.attendance.status.setCurrentIndex(max(0, self.attendance.status.findData(attendance_filter[1])))
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
        self.dashboard.chart_button.setEnabled(bool(cols))
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
        self._show_permissions()
        self._show_combo_count()
        self.people.present([], self.view is not None, bool(self.view and self.view.records))
        self.reports.present([], self.view is not None)

    def _apply(self, view, reading=True):
        previous = self.view.snapshot if self.view else None
        self.source_view = view
        view = visible_view(view, self.account, self.private_fields(view))
        snapshot = view.snapshot
        previous_columns = previous.columns if previous else ()
        if not previous or previous.path != snapshot.path or previous.sheet.key != snapshot.sheet.key:
            self.catalog = {}
            self.summary = summarize(None)
            self.analytics_data = None
            self.dashboard.chart_to_reveal = None
            self.chart_selection.clear()
            self.directory_rules = ()
            self.reminder_count = 0
            self._show_metadata()
        self.view = view
        valid = {column.key for column in snapshot.columns}
        self.chart_selection = {key: value for key, value in self.chart_selection.items() if key in valid}
        self.directory_rules = tuple(item for item in self.directory_rules if item.key in valid)
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
        self._show_combo_count()
        self._show_permissions()
        self._draw_source()
        self._plan_filter(immediate=True)
        self._update_metadata()
        self.schedule_reminders()

    def _show_metadata(self):
        self.people.set_distinct_values(self.catalog)
        self.reports.set_distinct_values(self.catalog)
        current, _ = self._layouts(self.view)
        self.dashboard.set_card_layout(current.get("order", ()), current.get("wide", ()))
        self.dashboard.set_summary(self.summary)
        self._show_chart_selection()
        self.people.extra_filters.setVisible(bool(self.directory_rules))
        self.analytics.set_dataset(self.analytics_data, self.analytics_scale, loading=self.view is not None)
        self.attendance.set_dataset(self.analytics_data)
        self._show_permissions()

    def _update_metadata(self):
        if self.metadata_busy:
            return
        self.metadata_busy = True
        view, today = self.view, QDate.currentDate()
        bindings, scale = self._analysis_preferences(view)
        chart_keys = self._dashboard_chart_keys(view)
        sensitive = self.account.sees_sensitive

        def collect():
            catalog, summary = distinct_values(view), summarize(view, today, chart_keys)
            try:
                data = load_dataset(view, bindings) if sensitive else None
            except Exception as issue:
                return catalog, summary, None, issue
            return catalog, summary, data, None

        def prepared(result, error):
            self.metadata_busy = False
            if (self.view is not view or self._analysis_preferences(view) != (bindings, scale)
                    or self._dashboard_chart_keys(view) != chart_keys):
                self._update_metadata()
            elif error:
                self.notify(self._reason(error))
            else:
                self.catalog, self.summary, self.analytics_data, data_issue = result
                self.analytics_scale = scale
                self.metadata_date = today
                self._show_metadata()
                if data_issue:
                    self.analytics.show_error(self._reason(data_issue))

        self.bus.submit(collect, prepared)

    def _dashboard_chart_keys(self, view):
        if view is None:
            return frozenset()
        raw = self.settings.value(self._dashboard_key(view), "[]", str)
        try:
            keys = json.loads(raw)
        except ValueError:
            return frozenset()
        return frozenset(key for key in keys if isinstance(key, str)) if isinstance(keys, list) else frozenset()

    def _configure_dashboard_charts(self):
        if self.view is None:
            return
        snapshot = self.view.snapshot
        book_key = namespace(snapshot)
        current = self._dashboard_chart_keys(self.view)
        dialog = ColumnsDialog(self, self.language, snapshot.columns, current, title=self.t("dashboard_columns"),
                               search_placeholder=self.t("search_columns"))
        if dialog.exec() != dialog.DialogCode.Accepted or self.view is None or namespace(self.view.snapshot) != book_key:
            return
        checked = dialog.keys()
        keys = [column.key for column in self.view.snapshot.columns if column.key in checked]
        if set(keys) != current:
            self.settings.setValue(self._dashboard_key(self.view), json.dumps(keys, ensure_ascii=False))
            self.dashboard.chart_to_reveal = next((key for key in keys if key not in current), None)
            self._update_metadata()

    def _session_suffix(self):
        if self.account.manages:
            return ""
        return "/" + sha256(self.account.key.encode()).hexdigest()

    def _dashboard_key(self, view):
        return "dashboard/columns/" + namespace(view.snapshot) + self._session_suffix()

    def _layouts(self, view):
        if not view:
            return {}, {}
        key = "dashboard/layouts/" + namespace(view.snapshot) + self._session_suffix()
        try:
            stored = json.loads(self.settings.value(key, "{}", str))
        except ValueError:
            stored = {}

        def clean(layout):
            return {key: [value for value in layout.get(key, ()) if isinstance(value, str)]
                    for key in ("order", "wide") if isinstance(layout.get(key), list)} if isinstance(layout, dict) else {}

        current = clean(stored.get("current")) if isinstance(stored, dict) else {}
        layouts = stored.get("presets") if isinstance(stored, dict) else {}
        return current, {name: clean(value) for name, value in layouts.items()} if isinstance(layouts, dict) else {}

    def _configure_layout(self):
        view = self.view
        if not view:
            return
        current, layouts = self._layouts(view)
        dialog = LayoutDialog(self, view.snapshot.columns, current, layouts, self._dashboard_chart_keys(view))
        if dialog.exec() != dialog.DialogCode.Accepted or self.view is not view:
            return
        name, config = dialog.output
        if name:
            layouts[name] = config
        key = "dashboard/layouts/" + namespace(view.snapshot) + self._session_suffix()
        self.settings.setValue(key, json.dumps({"current": config, "presets": layouts}, ensure_ascii=False))
        self.settings.setValue(self._dashboard_key(view), json.dumps(config["order"], ensure_ascii=False))
        self._clear_chart_selection()
        self._update_metadata()

    def _select_chart_category(self, key, value):
        if self.view is None or key not in {column.key for column in self.view.snapshot.columns}:
            return
        old = self.chart_selection.get(key)
        if old is not None and group_key(old) == group_key(value):
            self.chart_selection.pop(key)
        else:
            self.chart_selection[key] = value
        self._show_chart_selection()

    def _clear_chart_selection(self):
        self.chart_selection.clear()
        self._show_chart_selection()

    def _show_chart_selection(self):
        titles = {column.key: column.title(self.language) for column in self.view.snapshot.columns} if self.view else {}
        self.dashboard.set_selection(self.chart_selection, titles)

    def _chart_rules(self):
        return tuple(Rule(key, "group", value) if value else Rule(key, "blank") for key, value in self.chart_selection.items())

    def _open_chart_results(self):
        if self.view and self.chart_selection:
            dialog = ResultsDialog(self, self._chart_rules())
            if dialog.exec() == dialog.DialogCode.Accepted:
                self.transfer_rules(dialog.rules, dialog.transfer)

    def open_phrase(self):
        if self.view and self.view.snapshot.columns:
            dialog = PhraseDialog(self)
            if dialog.exec() == dialog.DialogCode.Accepted:
                self.transfer_rules(dialog.rules, dialog.transfer)

    def transfer_rules(self, rules, page):
        if page == "people":
            self.people.term.clear()
            self.people.search_column.setCurrentIndex(0)
            self.people.select_value(None)
            self.directory_rules = rules
            self.people.extra_filters.setVisible(bool(rules))
        elif page == "reports":
            for item in tuple(self.reports.editors):
                self.reports.remove_rule(item, False)
            self.reports.combine.setCurrentIndex(0)
            for rule in rules:
                self.reports.add_rule(rule, False)
        else:
            return
        self.set_page(page)
        self._plan_filter(immediate=True)

    def clear_directory_rules(self):
        self.directory_rules = ()
        self.people.extra_filters.hide()
        self._plan_filter(immediate=True)

    def _show_permissions(self):
        manager, editable, sensitive = self.account.manages, self.account.edits, self.account.sees_sensitive
        available = bool(self.view and self.view.snapshot.columns)
        self.link_button.setEnabled(manager)
        self.people.permissions(editable, editable and sensitive, sensitive)
        self.people.table.empty_action.setEnabled(manager)
        self.reports.can_export = self.account.exports
        self.reports.set_busy(self.export_busy)
        for button_ in self.options.source_actions:
            button_.setEnabled(manager)
        if not manager:
            for control in (self.options.sheet, self.options.backup_button, self.options.reset_button):
                control.setEnabled(False)
        self.options.combo_button.setEnabled(manager and available)
        if not editable:
            self.analytics.create_button.setEnabled(False)
        self.attendance.set_permissions(editable and sensitive)
        self.dashboard.layout_button.setEnabled(available)
        self.dashboard.meeting_button.setEnabled(available and self.account.exports and not self.export_busy)
        self.office_page.set_connected(available)
        self.office_page.set_reminders(self.reminder_count)

    def private_fields(self, view):
        if not view:
            return set()
        key = "office/private/" + namespace(view.snapshot)
        try:
            value = json.loads(self.settings.value(key, "[]", str))
        except ValueError:
            return set()
        return {key for key in value if isinstance(key, str)} if isinstance(value, list) else set()

    def change_private_fields(self, keys):
        if self.account.manages and self.view:
            scope = namespace(self.view.snapshot)
            self.settings.setValue("office/private/" + scope, json.dumps(sorted(keys), ensure_ascii=False))
            self.store.audit(self.actor(), scope, "private_saved", str(len(keys)))

    def actor(self):
        return self.account.name or self.t("local_guest")

    def with_audit(self, work, action, view=None, detail=""):
        scope, actor = namespace(view.snapshot) if view else "", self.actor()

        def logged():
            result = work()
            try:
                self.store.audit(actor, scope, action, detail)
            except (OSError, sqlite3.Error):
                self.audit_error.emit()
            return result

        return logged

    def _notify_audit_error(self):
        QTimer.singleShot(0, self, lambda: self.notify(self.t("audit_error")))

    def open_access(self):
        if self.account.manages:
            original = self.account
            AccountsDialog(self).exec()
            if self.account != original:
                self._build()

    def open_audit(self):
        if self.account.manages:
            AuditDialog(self).exec()

    def open_dossier(self, selected=None):
        if self.view and self.account.sees_sensitive:
            DossierDialog(self, selected if isinstance(selected, str) else None).exec()

    def open_selected_dossier(self):
        selected = self.people.selected()
        if selected:
            self.open_dossier(selected.token)

    def open_reminders(self):
        if self.view and self.account.sees_sensitive:
            ReminderDialog(self).exec()

    def open_timekeeping(self):
        if self.view and self.account.sees_sensitive:
            TimekeepingDialog(self).exec()

    def open_quality(self):
        if self.view and self.account.sees_sensitive:
            QualityDialog(self).exec()

    def schedule_reminders(self):
        if self.view and self.account.sees_sensitive:
            self.reminder_timer.start()

    def _refresh_reminders(self):
        view = self.view
        if not view or not self.account.sees_sensitive:
            return
        if self.reminder_busy:
            self.schedule_reminders()
            return
        self.reminder_busy = True
        _, lead, dates, required = reminder_settings(self, view)
        scope, today = namespace(view.snapshot), QDate.currentDate()

        def scan():
            return len(reminders(view, roster(view), self.office_store.entries(scope, "profile"), today, lead, dates, required))

        def ready(count, error):
            self.reminder_busy = False
            if self.view is not view:
                self.schedule_reminders()
            elif not error:
                self.reminder_count = count
                self.office_page.set_reminders(count)
                key = "office/notified/" + scope + self._session_suffix()
                date = today.toString("yyyy-MM-dd")
                if count and self.settings.value(key, "", str) != date:
                    self.settings.setValue(key, date)
                    self.notify(self.t("next_deadlines", number=self.n(count)))

        self.bus.submit(scan, ready)

    def _analysis_preferences(self, view):
        if view is None:
            return {}, "percent"
        key = "analytics/source/" + namespace(view.snapshot)
        raw = self.settings.value(key, "{}", str)
        try:
            selected = json.loads(raw)
        except ValueError:
            selected = {}
        if not isinstance(selected, dict):
            return {}, "percent"
        columns = selected.get("columns", {})
        if not isinstance(columns, dict):
            columns = {}
        return {field: col for field, col in columns.items() if isinstance(col, str)}, selected.get("scale", "percent")

    def _configure_analytics(self):
        view = self.view
        if view is None or not self.account.sees_sensitive:
            return
        bindings, scale = self._analysis_preferences(view)
        dialog = SourceDialog(self, self.language, view.snapshot.columns, bindings, scale)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.settings.setValue("analytics/source/" + namespace(view.snapshot),
                               json.dumps({"columns": dialog.binding_values(), "scale": dialog.scale.currentData()}, ensure_ascii=False))
        self._update_metadata()

    def _plan_analysis(self):
        self.analysis_revision += 1
        if self.page_key == "analytics":
            self.analysis_timer.start()

    def _analyze(self):
        if self.analysis_busy or self.page_key != "analytics" or not self.analytics_data:
            return
        query = self.analytics.query()
        if query is None:
            return
        self.analysis_busy = True
        revision, data = self.analysis_revision, self.analytics_data

        def done(result, error):
            self.analysis_busy = False
            if revision != self.analysis_revision:
                if self.page_key == "analytics":
                    self.analysis_timer.start(0)
                return
            if error:
                self.analytics.show_error(self._reason(error))
            else:
                self.analytics.present(result)

        self.bus.submit(lambda: evaluate(data, query), done)

    def _create_profile_sheet(self):
        data = self.analytics_data
        if data is None or data.profiles is not None or not self.account.edits or not self.account.sees_sensitive:
            return
        snapshot = data.view.snapshot

        def write():
            new = prepare_profile(data, lambda payload: self.store.backup(snapshot.path, payload))
            return self.store.view(new)

        def saved(view):
            self._apply(view)
            self.notify(insight_text(self.language, "profile_created"))

        self._run_write(self.with_audit(write, "logged_profile", data.view), saved, expected=data.view)

    def _attendance_dialog(self, operation):
        data = self.analytics_data
        if data is None or self.write_busy or not self.account.edits or not self.account.sees_sensitive:
            return
        event = self.attendance.selected() if operation != "add" else None
        if operation != "add" and event is None:
            return
        path = data.view.snapshot.path
        t = lambda key: insight_text(self.language, key)

        def write(request, delete=False):
            person, day, session, status = request
            updated = save_attendance(data, person, day, session, status, event.row if event else None, delete,
                                      lambda payload: self.store.backup(path, payload))
            return self.store.view(updated)

        if operation == "remove":
            box = QMessageBox(QMessageBox.Icon.Warning, t("attendance_title"), t("attendance_delete_question"), parent=self)
            remove = box.addButton(self.t("delete"), QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton(self.t("cancel"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() is remove:
                def deleted(view):
                    self._apply(view)
                    self.notify(t("attendance_deleted"))
                self._run_write(self.with_audit(lambda: write((None, None, "", ""), delete=True), "logged_sport", data.view),
                                deleted, expected=data.view)
            return

        dialog = AttendanceDialog(self, self.language, self.colors, data, event)

        def submitted(request):
            def saved(view):
                self._apply(view)
                self.notify(t("attendance_saved"))
                dialog.accept()
            self._run_write(self.with_audit(lambda: write(request), "logged_sport", data.view),
                            saved, dialog.failed, expected=data.view)

        dialog.submitted.connect(submitted)
        dialog.exec()

    def _combo_overrides(self, view):
        raw = self.settings.value("formFields/" + namespace(view.snapshot), "{}", str)
        try:
            saved = json.loads(raw)
        except ValueError:
            return {}
        return {key: value for key, value in saved.items() if isinstance(value, bool)} if isinstance(saved, dict) else {}

    def _combo_keys(self, view):
        if not view:
            return set()
        overrides = self._combo_overrides(view)
        return {column.key for column in view.snapshot.columns
                if overrides.get(column.key, normalize(column.label) in DEFAULT_COMBO_COLUMNS)}

    def _show_combo_count(self):
        self.options.show_combo_count(bool(self.view and self.view.snapshot.columns), len(self._combo_keys(self.view)))

    def configure_combos(self):
        view = self.view
        if not view or not view.snapshot.columns or not self.account.manages:
            return
        columns = view.snapshot.columns
        dialog = ColumnsDialog(self, self.language, columns, self._combo_keys(view), title=self.t("combo_setup"))
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        selected = dialog.keys()
        overrides = self._combo_overrides(view)
        for column in columns:
            enabled = column.key in selected
            if enabled == (normalize(column.label) in DEFAULT_COMBO_COLUMNS):
                overrides.pop(column.key, None)
            else:
                overrides[column.key] = enabled
        self.settings.setValue("formFields/" + namespace(view.snapshot), json.dumps(overrides, ensure_ascii=False))
        self._show_combo_count()
        self.notify(self.t("combo_settings_saved"))

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
        if self.view and self.metadata_date != QDate.currentDate():
            self._update_metadata()
            self.schedule_reminders()
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
        if not self.account.manages:
            return
        selected, _ = QFileDialog.getOpenFileName(self, self.t("open_source"), str(self.source_path or ""), "Excel (*.xlsx)")
        if selected:
            self._load(Path(selected), manual=True)

    def create_file(self):
        if not self.account.manages:
            return
        destination, _ = QFileDialog.getSaveFileName(self, self.t("blank_workbook"), "Personnel.xlsx", "Excel (*.xlsx)")
        if not destination:
            return
        path = self._extension(Path(destination), ".xlsx")
        if not self._destination_allowed(path):
            return
        self._run_write(lambda: make_template(path), lambda _: self._load(path, manual=True))

    def choose_sheet(self):
        if not self.account.manages:
            return
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
        exact = self.people.filter_rule()
        directory_rules = self.directory_rules + ((exact,) if exact is not None else ())

        def filter_all():
            directory = filter_records(records, columns, term, search, rules=directory_rules, sort_key=order, reverse=reverse)
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
        if not self.account.edits or operation == "delete" and not self.account.sees_sensitive:
            return
        if self.record_dialog is not None:
            if self.record_dialog.isMinimized():
                self.record_dialog.showNormal()
            self.record_dialog.raise_()
            self.record_dialog.activateWindow()
            return
        view = self.view
        if not view or self.write_busy or not view.snapshot.columns:
            return
        record = self.people.selected() if operation != "add" else None
        if operation != "add" and (not record or not any(item is record for item in view.records)):
            return
        raw = self.source_view
        source = next((item for item in raw.records if item.token == record.token), None) if record else None
        if record and source is None:
            return
        choices = {key: self.catalog.get(key, ()) for key in self._combo_keys(view)}
        dialog = RecordDialog(self, self.language, view.snapshot.columns, record, operation,
                              choices=choices, palette=self.colors)
        self.record_dialog = dialog

        def close_dialog():
            self.record_dialog = None
            dialog.deleteLater()
        def commit(change):
            target, values = change
            if view is not self.view:
                dialog.failed(self.t("source_changed"))
                return
            if values.keys() - {column.key for column in view.snapshot.columns}:
                dialog.failed(self.t("access_denied"))
                return

            def apply():
                if target == "local":
                    return self.store.change(raw, operation, source, values)
                snapshot = raw.snapshot
                row = source.source.number if source and source.source else None
                verb = "add" if row is None and operation == "edit" else operation
                updated = update_workbook(snapshot, verb, row, values, lambda payload: self.store.backup(snapshot.path, payload))
                return self.store.finish_source_change(updated, source, verb, values)

            def finished(new_view):
                self._apply(new_view, reading=target == "excel")
                self.notify(self.t("saved_excel" if target == "excel" else "saved_local"))
                dialog.accept()

            fields = tuple(column.title(self.language) for column in view.snapshot.columns if column.key in values)
            detail = " · ".join((record.token if record else target, *fields))
            self._run_write(self.with_audit(apply, "logged_" + operation, view, detail),
                            finished, dialog.failed, expected=view)
        dialog.submitted.connect(commit)
        dialog.finished.connect(close_dialog)
        dialog.show()

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
        if not self.account.edits or not self.account.sees_sensitive:
            return
        record = self.people.selected()
        if record and record.overlay and self.view and any(item is record for item in self.view.records):
            snapshot = self.view.snapshot
            self._run_write(self.with_audit(lambda: self.store.clear_record(snapshot, record), "logged_restore", self.view, record.token),
                            lambda view: self._apply(view, False), expected=self.view)

    def reset_local(self):
        if not self.view or not self.account.manages:
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
            self._run_write(self.with_audit(lambda: self.store.clear(snapshot), "logged_restore", self.view), finished, expected=self.view)

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
        if not self.view or not self.account.manages:
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
        if not view or not selected or not self.reports.complete_rules() or self.export_busy or not self.account.exports:
            return
        extension = self.reports.format.currentData()
        title = self.reports.title.text().strip() or self.t("report_default")
        rules, any_match = self.reports.rule_values(), self.reports.combine.currentData()
        language, layout = self.language, self.reports.style.currentData()
        file_filter = self.t(extension[1:]).replace("(" + extension, "(*" + extension)
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        suggested = Path(folder) / report_filename(title, extension, "Niro-Report")
        filename, _ = QFileDialog.getSaveFileName(self, self.t("export"), str(suggested), file_filter)
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

        self.bus.submit(self.with_audit(work, "logged_export", view, title), done)

    def export_meeting(self):
        view = self.view
        if not view or self.export_busy or not self.account.exports:
            return
        settings = meeting_settings(self)
        if not settings or self.view is not view:
            return
        begin, end, column = settings
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        choice, _ = QFileDialog.getSaveFileName(self, self.t("meeting"), str(Path(folder) / "Niro-Meeting.zip"), "ZIP (*.zip)")
        if not choice:
            return
        path = self._extension(Path(choice), ".zip")
        if not self._destination_allowed(path):
            return
        language, colors = self.language, self.colors
        selection, keys = self._chart_rules(), self._dashboard_chart_keys(view)
        self.export_busy = True
        self._show_permissions()
        self.notify(self.t("working_export"), False)

        def completed(result, problem):
            self.export_busy = False
            self._show_permissions()
            self.notify(self._reason(problem) if problem else self.t("exported"))

        def prepared(result, problem):
            if problem:
                completed(None, problem)
                return
            summary, rows = result
            try:
                pictures = pack_charts(summary, language, colors)
            except Exception as error:
                completed(None, error)
                return

            def export_files():
                export_pack(path, view.snapshot.columns, rows, summary, pictures, language, begin, end, column, selection)

            self.bus.submit(self.with_audit(export_files, "meeting_saved", view, path.name), completed)

        self.bus.submit(lambda: prepare_pack(view, keys, selection, begin, end, column), prepared)

    def closeEvent(self, event):
        if self.export_busy or self.write_busy:
            self.notify(self.t("busy_close"))
            event.ignore()
        else:
            self.sync_timer.stop()
            self.reminder_timer.stop()
            self._finish_transition()
            event.accept()
