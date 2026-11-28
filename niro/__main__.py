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
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QComboBox

    from .record_fields import DateField, GREGORIAN, JALALI, _DatePicker
    from .reports import Report, export
    from .visuals import illustration
    from .workbook import make_template, read_workbook, update_workbook

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
        report = Report("گزارش", ("نام",), (("آزمایشی",),))
        for extension in (".xlsx", ".docx", ".pptx", ".csv"):
            export(report, root / (extension[1:] + "-report" + extension))
        settings = QSettings(str(root / "preferences.ini"), QSettings.Format.IniFormat)
        window = Window(store, settings, system_font, database, watch_interval=150)
        window.show()
        checks = QTimer()
        checks.setInterval(60)
        result = []
        def check():
            if window.people.table.model.rowCount() != 1 or window.catalog.get(city) != ("تهران",):
                return
            checks.stop()
            try:
                assert window.people.table.model.index(0, 0).data() == "محلی"
                assert window.summary.total == 1
                assert sum(window.summary.periods[0].hires) == 1
                assert window.summary.departures_by_status == 1

                window.dashboard.selector.setCurrentIndex(window.dashboard.selector.findData(90))
                for key in ("neon_night", "turquoise_wave", "warm_atlas"):
                    window.set_page("settings", animate=False)
                    choice = window.options.themes[key]
                    assert not choice.icon().isNull()
                    choice.click()
                    assert window.theme == key and choice.isChecked()
                    assert settings.value("theme") == key
                    art = illustration(window.colors.graphic.artwork)
                    assert not art.isNull() and art.hasAlphaChannel()
                    window.set_page("dashboard", animate=False)
                    assert window.dashboard.showcase.isVisible()
                    assert window.dashboard.selector.currentData() == 90
                    assert not window.dashboard.grab().isNull()
                settings.sync()
                reloaded = QSettings(str(root / "preferences.ini"), QSettings.Format.IniFormat)
                assert reloaded.value("theme") == "warm_atlas"

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
                assert isinstance(form.editors[marital], QComboBox)
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
            return 1
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
