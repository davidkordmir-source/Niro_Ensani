"""Entry point for the installed and development versions of Niro Desk."""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from PySide6.QtCore import QLockFile, QSettings, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressBar, QVBoxLayout, QWidget

from .locale import translate
from .state import Store
from .theme import ASSETS, FONT_NAMES, THEMES, interface_font, load_fonts, style
from .widgets import label
from .window import Window


class Splash(QWidget):
    def __init__(self, language, colors, display_name):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(440, 285)
        self.setObjectName("splash")
        self.setStyleSheet(f"""
            QWidget#splash {{ background:{colors.sidebar}; border:1px solid {colors.sidebar_hover}; }}
            QLabel {{ color:{colors.sidebar_text}; }}
            QLabel#pageTitle {{ color:{colors.sidebar_text}; }}
            QProgressBar {{ background:{colors.sidebar_hover}; border:none; border-radius:3px; }}
            QProgressBar::chunk {{ background:{colors.mint}; border-radius:3px; }}
        """)
        body = QVBoxLayout(self)
        body.setContentsMargins(52, 26, 52, 27)
        body.setSpacing(6)
        mark = label()
        mark.setPixmap(QPixmap(str(ASSETS / "niro.png")).scaled(82, 82, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = label(display_name or translate(language, "default_name"), "pageTitle")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status = label(translate(language, "startup"))
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        body.addStretch()
        body.addWidget(mark)
        body.addWidget(name)
        body.addWidget(status)
        body.addSpacing(14)
        body.addWidget(bar)
        body.addStretch()
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())


def _self_test(app, system_font, output):
    from PySide6.QtCore import QCoreApplication, QDate, QEvent, QPoint, QPointF, QRectF
    from PySide6.QtGui import QFontDatabase, QWheelEvent

    from .insights import load_dataset, prepare_profile, save_attendance
    from .record_fields import DateField, GREGORIAN, JALALI, _DatePicker
    from .reports import Report, export
    from .schema import PROFILE_SHEET
    from .visuals import illustration
    from .widgets import ComboBox
    from .workbook import companion_snapshots, label_key, make_template, read_workbook, update_workbook

    with TemporaryDirectory(prefix="niro-self-test-") as directory:
        root = Path(directory)
        database = root / "test.xlsx"
        make_template(database)
        store = Store(root / "local")
        snapshot = read_workbook(database)
        city = snapshot.columns[7].key
        marital = snapshot.columns[8].key
        start = snapshot.columns[16].key
        today = QDate.currentDate()
        draft = {
            snapshot.columns[0].key: "آزمایشی", snapshot.columns[4].key: "0012",
            city: "تهران", marital: "مجرد", snapshot.columns[10].key: "قطع همکاری",
            start: today.toString("yyyy/MM/dd"),
        }
        new = update_workbook(snapshot, "add", None, draft, lambda data: store.backup(database, data))
        view = store.view(new)
        assert view.records[0].values[0] == "آزمایشی"
        local = store.change(view, "edit", view.records[0], {snapshot.columns[0].key: "محلی"})
        assert local.records[0].values[0] == "محلی"
        store.copy_backup(database, root / "recovery.xlsx")
        assert not read_workbook(root / "recovery.xlsx").rows
        backup = lambda payload: store.backup(database, payload)
        augmented = prepare_profile(load_dataset(local), backup)
        sheet = companion_snapshots(augmented)[PROFILE_SHEET]
        update_workbook(sheet, "edit", sheet.rows[0].number, {label_key("مدیریت"): "آزمایش", label_key("تعداد مرخصی"): "2"}, backup)
        sports = load_dataset(store.view(read_workbook(database)))
        save_attendance(sports, "p:0012", today, "جلسهٔ آزمایشی", "present", None, False, backup)
        report = Report("گزارش", ("نام",), (("آزمایشی",),))
        for extension in (".xlsx", ".docx", ".pptx", ".csv"):
            export(report, root / (extension[1:] + "-report" + extension))
        settings = QSettings(str(root / "preferences.ini"), QSettings.Format.IniFormat)
        settings.setValue("analytics/metrics", "department,leave,sport")
        window = Window(store, settings, system_font, database, watch_interval=150)
        window.show()
        checks = QTimer()
        checks.setInterval(60)
        result = []
        def check():
            if window.people.table.model.rowCount() != 1 or window.catalog.get(city) != ("تهران",):
                return
            if window.page_key != "analytics":
                window.set_page("analytics", animate=False)
                return
            if "sport" not in window.analytics.cards:
                return
            checks.stop()
            try:
                assert window.people.table.model.index(0, 0).data() == "محلی"
                assert window.summary.total == 1
                assert sum(window.summary.periods[0].hires) == 1
                assert window.summary.departures_by_status == 1
                assert window.attendance.model.rowCount() == 1
                assert window.attendance.model.rows[0].status == "present"
                assert window.analytics_data.profiles is not None

                assert window.analytics.cards["department"].headline.text() == "۱ نفر"
                assert window.analytics.cards["leave"].headline.text() == "۲ بار"
                assert window.analytics.cards["sport"].headline.text() == "۱۰۰٪"
                panel = window.analytics.cards["department"].chart
                assert panel.isVisible() and not panel.size().isEmpty(), panel.size()
                for mode in ("bar", "line", "area", "ring", "table"):
                    index = panel.types.findData(mode)
                    assert index >= 0, f"Missing chart mode {mode}"
                    panel.types.setCurrentIndex(index)
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
                    display = panel.table if mode == "table" else panel.plot
                    assert panel.stack.currentWidget() is display, f"Chart mode {mode} did not switch its view"
                    assert panel.height() >= 216, f"Chart mode {mode} left no space for the plot or table"
                    assert not panel.grab().isNull(), f"Chart mode {mode} could not render at {panel.size()}"
                assert window.analytics.compare_chart.data.table_only

                window.dashboard.selector.setCurrentIndex(window.dashboard.selector.findData(90))
                window.set_page("settings", animate=False)
                available = set(QFontDatabase.families())
                assert set(FONT_NAMES.values()) - {""} <= available
                assert window.options.font_choice.count() == len(FONT_NAMES)
                for key, palette in THEMES.items():
                    if palette.graphic is None:
                        continue
                    choice = window.options.themes[key]
                    art = illustration(palette.graphic.artwork)
                    assert not choice.icon().isNull() and not art.isNull(), key
                    assert QRectF(art.rect()).contains(QRectF(*palette.graphic.crop)), key
                for key in ("neon_night", "turquoise_wave", "warm_atlas", "citrus_studio", "blueprint"):
                    window.set_page("settings", animate=False)
                    choice = window.options.themes[key]
                    choice.click()
                    assert window.theme == key and choice.isChecked()
                    assert settings.value("theme") == key
                    window.set_page("dashboard", animate=False)
                    assert window.dashboard.showcase.isVisible()
                    assert window.dashboard.selector.currentData() == 90
                    assert not window.dashboard.grab().isNull()
                window.set_page("settings", animate=False)
                font_choice = window.options.font_choice
                font_choice.setCurrentIndex(font_choice.findData("inter"))
                assert app.font().family() == "Inter"
                settings.sync()
                reloaded = QSettings(str(root / "preferences.ini"), QSettings.Format.IniFormat)
                assert reloaded.value("theme") == "blueprint" and reloaded.value("font") == "inter"

                window.set_page("reports")
                window.reports.add_rule()
                rule = window.reports.editors[0]
                rule.columns.setCurrentIndex(rule.columns.findData(city))
                assert rule.value.findData("تهران") >= 0
                rule.value.setCurrentIndex(rule.value.findData("تهران"))
                assert window.reports.rule_values()[0].value == "تهران"

                window.set_page("people")
                window.people.search_column.setCurrentIndex(window.people.search_column.findData(city))
                window.people.select_value("تهران")
                assert window.people.filter_rule().value == "تهران"

                window._record("add")
                form = window.record_dialog
                marital_choice = form.editors[marital]
                assert isinstance(marital_choice, ComboBox)
                marital_choice.setCurrentIndex(0)
                marital_choice.setFocus()
                wheel = QWheelEvent(QPointF(5, 5), QPointF(marital_choice.mapToGlobal(QPoint(5, 5))),
                                    QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                                    Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                QApplication.sendEvent(marital_choice, wheel)
                assert marital_choice.currentIndex() == 0 and not wheel.isAccepted()
                assert isinstance(form.editors[start], DateField)
                assert form.windowFlags() & Qt.WindowType.WindowMinMaxButtonsHint
                form.reject()
                picker = _DatePicker(window, True, today, JALALI)
                picker.kind.setCurrentIndex(1)
                assert picker.system() != JALALI
                assert picker.calendar.selectedDate() == today
                assert picker.system() == GREGORIAN
            except Exception as error:
                result.append(error)
            else:
                result.append(True)
            app.quit()
        checks.timeout.connect(check)
        checks.start()
        QTimer.singleShot(10000, app.quit)
        app.exec()
        if not result:
            raise RuntimeError(f"Timed out at {window.page_key}: {window.analytics.status.text()}")
        if result[0] is not True:
            raise result[0]
        if output is not None:
            Path(output).write_text("Niro Desk native and offline smoke test passed.\n", encoding="utf-8")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Niro Desk")
    parser.add_argument("workbook", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--test-output", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if os.name != "nt":
        os.umask(0o077)
    app = QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    app.setOrganizationName("NiroDesk")
    app.setApplicationName("Niro Desk")
    app.setWindowIcon(QIcon(str(ASSETS / "niro.png")))
    system_font = app.font()
    load_fonts()

    if options.self_test:
        return _self_test(app, system_font, options.test_output)

    settings = QSettings()
    language = settings.value("language", "fa", str)
    if language not in ("fa", "en"):
        language = "fa"
    colors = THEMES.get(settings.value("theme", "violet", str), THEMES["violet"])
    app.setStyleSheet(style(colors))
    font_key = settings.value("font", "vazir", str)
    app.setFont(interface_font(font_key if font_key in FONT_NAMES else "vazir", system_font))
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    try:
        store = Store(root)
        lock = QLockFile(str(root / "running.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            QMessageBox.information(None, "Niro Desk", translate(language, "already_running"))
            return 0
    except (OSError, sqlite3.Error):
        QMessageBox.warning(None, "Niro Desk", translate(language, "permission_error"))
        return 1

    splash = Splash(language, colors, settings.value("name", "", str))
    splash.show()
    windows = []
    def launch():
        window = Window(store, settings, system_font, options.workbook)
        windows.append(window)
        window.show()
        splash.close()

    QTimer.singleShot(675, launch)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
