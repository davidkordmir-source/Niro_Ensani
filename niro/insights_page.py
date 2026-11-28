"""The analytics selection, source mapping, and comparison workspace."""

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QScrollArea, QVBoxLayout, QWidget,
)

from .insight_charts import ChartPanel
from .insights import FIELD_NAMES, GROUPS, METRICS, Query, date_label, format_value, person_label, tr
from .locale import translate
from .pages import _scroll
from .record_fields import DateField, date_from_text
from .widgets import CardGrid, ColumnsDialog, ComboBox, button, card, field, fill_options, icon, label, row_layout, section_header
from .workbook import Column


_PRESETS = ("month", "quarter", "year", "two_years", "custom")
_DEFAULT_METRICS = ("total", "hires", "membership")


def person_options(data, language, stable_only=False):
    for person in data.people if data else ():
        if stable_only and not person.stable:
            continue
        yield person.ref, person_label(person, language, distinct=True)


class SourceDialog(QDialog):
    def __init__(self, parent, language, columns, bindings, scale):
        super().__init__(parent)
        self.setWindowTitle(tr(language, "source_fields"))
        self.resize(615, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(19, 17, 19, 17)
        layout.addWidget(label(tr(language, "source_fields"), "pageTitle"))
        layout.addWidget(label(tr(language, "source_fields_lead"), "muted", True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        self.fields = {}
        options = (("", tr(language, "source_auto")), *((col.key, col.title(language)) for col in columns))
        for key in FIELD_NAMES:
            combo = ComboBox()
            fill_options(combo, options, bindings.get(key, ""))
            form.addRow(label(tr(language, "field_" + key)), combo)
            self.fields[key] = combo
        self.scale = ComboBox()
        fill_options(self.scale, (("percent", tr(language, "scale_percent")), ("five", tr(language, "scale_five"))), scale)
        form.addRow(label(tr(language, "scale")), self.scale)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        actions = row_layout()
        actions.addStretch()
        apply = button(translate(language, "save"), "primary")
        cancel = button(translate(language, "cancel"))
        cancel.clicked.connect(self.reject)
        apply.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(apply)
        layout.addLayout(actions)

    def binding_values(self):
        return {key: combo.currentData() for key, combo in self.fields.items() if combo.currentData()}


class _MetricCard(QWidget):
    def __init__(self, key, language, palette, settings):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        frame, body = card()
        body.addWidget(label(tr(language, "metric_" + key), "sectionTitle", True))
        self.headline = label("", "metricValue", True)
        self.note = label("", "smallMuted", True)
        body.addWidget(self.headline)
        body.addWidget(self.note)
        self.chart = ChartPanel(language, palette, settings, key)
        body.addWidget(self.chart, 1)
        layout.addWidget(frame)

    def show_result(self, result):
        self.headline.setText(result.headline)
        self.note.setText(result.note)
        self.note.setVisible(bool(result.note))
        self.chart.set_data(result.chart)


class AnalyticsPage(QWidget):
    changed = Signal()
    configure = Signal()
    create_profile = Signal()

    def __init__(self, language, palette, settings):
        super().__init__()
        self.language, self.palette, self.settings = language, palette, settings
        self.data = None
        self.scale = "percent"
        self.t = lambda key, **values: tr(language, key, **values)
        selected = settings.value("analytics/metrics", ",".join(_DEFAULT_METRICS), str).split(",")
        self.metrics = tuple(key for key in METRICS if key in selected) or _DEFAULT_METRICS
        self.cards = {}
        self.updating_dates = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        layout = _scroll(outer)
        layout.addWidget(section_header(self.t("title"), self.t("lead")))

        tools, content = card()
        selection = row_layout()
        self.metric_button = button(self.t("pick_metrics"), "primary")
        field(selection, self.t("metrics"), self.metric_button)
        self.group = ComboBox()
        fill_options(self.group, ((key, self.t("group_" + key)) for key in GROUPS), settings.value("analytics/group", "membership", str))
        field(selection, self.t("group"), self.group, 2)
        self.person = ComboBox()
        self.person.setSizeAdjustPolicy(ComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.person.setMinimumContentsLength(10)
        field(selection, self.t("person"), self.person, 2)
        content.addLayout(selection)

        dates = row_layout()
        self.preset = ComboBox()
        fill_options(self.preset, ((key, self.t(key)) for key in _PRESETS), settings.value("analytics/preset", "year", str))
        field(dates, self.t("event_period"), self.preset)
        self.start = DateField("", language)
        self.end = DateField("", language)
        field(dates, self.t("from"), self.start)
        field(dates, self.t("to"), self.end)
        content.addLayout(dates)

        sources = row_layout()
        self.profile_notice = label("", "smallMuted", True)
        sources.addWidget(self.profile_notice, 1)
        self.map_button = button(self.t("source_fields"))
        self.create_button = button(self.t("create_profile"))
        sources.addWidget(self.map_button)
        sources.addWidget(self.create_button)
        content.addLayout(sources)
        self.profile_error = label("", "warning", True)
        self.profile_error.hide()
        content.addWidget(self.profile_error)
        layout.addWidget(tools)

        self.status = label("", "muted", True)
        layout.addWidget(self.status)
        self.comparison, box = card()
        box.addWidget(label(self.t("compare"), "sectionTitle"))
        self.compare_note = label("", "smallMuted", True)
        box.addWidget(self.compare_note)
        self.compare_chart = ChartPanel(language, palette, settings, "comparison")
        box.addWidget(self.compare_chart)
        self.comparison.hide()
        layout.addWidget(self.comparison)
        self.grid = CardGrid()
        layout.addWidget(self.grid)
        layout.addStretch(1)

        self._restore_dates()
        self.metric_button.clicked.connect(self.pick_metrics)
        self.group.currentIndexChanged.connect(self._controls_changed)
        self.preset.currentIndexChanged.connect(self._choose_preset)
        self.person.currentIndexChanged.connect(self.changed)
        self.start.input.textChanged.connect(self._edit_dates)
        self.end.input.textChanged.connect(self._edit_dates)
        self.map_button.clicked.connect(self.configure)
        self.create_button.clicked.connect(self.create_profile)
        self._metric_caption()
        self.set_dataset(None)
        self.recolor(palette)

    def _metric_caption(self):
        self.metric_button.setText(self.t("metric_selected", n=format_value(len(self.metrics), self.language)))

    def pick_metrics(self):
        options = tuple(Column(key, self.t("metric_" + key), i) for i, key in enumerate(METRICS))
        chosen = ColumnsDialog(self, self.language, options, set(self.metrics), title=self.t("pick_metrics"))
        if chosen.exec() != QDialog.DialogCode.Accepted:
            return
        keys = chosen.keys()
        if not keys:
            self.status.setText(self.t("pick_at_least_one"))
            self.status.show()
            return
        self.metrics = tuple(key for key in METRICS if key in keys)
        self.settings.setValue("analytics/metrics", ",".join(self.metrics))
        self._metric_caption()
        self.changed.emit()

    def _restore_dates(self):
        self.updating_dates = True
        if self.preset.currentData() == "custom":
            for key, edit in (("start", self.start), ("end", self.end)):
                saved = date_from_text(self.settings.value("analytics/" + key, "", str))
                edit.input.setText(date_label(saved, self.language))
        else:
            self.refresh_dates()
        self.updating_dates = False

    def refresh_dates(self):
        preset = self.preset.currentData()
        if preset in ("custom", None):
            return
        today = QDate.currentDate()
        beginning = {"month": today.addDays(-29), "quarter": today.addDays(-89),
                     "year": today.addYears(-1), "two_years": today.addYears(-2)}[preset]
        self.updating_dates = True
        self.start.input.setText(date_label(beginning, self.language))
        self.end.input.setText(date_label(today, self.language))
        self.updating_dates = False

    def _controls_changed(self):
        self.settings.setValue("analytics/group", self.group.currentData())
        self.changed.emit()

    def _choose_preset(self):
        self.refresh_dates()
        self.settings.setValue("analytics/preset", self.preset.currentData())
        self._remember_custom_dates()
        self.changed.emit()

    def _edit_dates(self):
        if self.updating_dates:
            return
        if self.preset.currentData() != "custom":
            self.preset.setCurrentIndex(self.preset.findData("custom"))
        else:
            self._remember_custom_dates()
            self.changed.emit()

    def _remember_custom_dates(self):
        if self.preset.currentData() == "custom":
            for key, edit in (("start", self.start), ("end", self.end)):
                day = date_from_text(edit.text())
                if day.isValid():
                    self.settings.setValue("analytics/" + key, day.toString("yyyy-MM-dd"))

    def set_dataset(self, dataset, scale="percent", loading=False):
        previous = self.person.currentData()
        self.data, self.scale = dataset, scale
        fill_options(self.person, [(None, self.t("person_all")), *person_options(dataset, self.language)], previous)
        self.map_button.setEnabled(dataset is not None)
        self.create_button.setEnabled(dataset is not None and dataset.profiles is None)
        self.profile_notice.setText(self.t("profile_ready") if dataset and dataset.profiles else
                                    self.t("profile_help") if dataset else "")
        issues = dataset.profile_issues if dataset else 0
        self.profile_error.setText(self.t("profile_unmatched", count=format_value(issues, self.language)))
        self.profile_error.setVisible(bool(issues))
        if not dataset:
            self.status.setText(self.t("waiting") if loading else translate(self.language, "no_source"))
            self.status.show()
            self.comparison.hide()
            for widget in self.cards.values():
                widget.hide()
            self.grid.show_cards(())
        self.changed.emit()

    def query(self):
        if self.data is None:
            return None
        self.refresh_dates()
        start, end = (date_from_text(edit.text()) for edit in (self.start, self.end))
        if not start.isValid() or not end.isValid() or start > end:
            self.show_error(self.t("bad_range"))
            return None
        self.status.setText(self.t("calculating"))
        self.status.show()
        return Query(self.language, self.metrics, start, end, QDate.currentDate(),
                     self.group.currentData(), self.person.currentData(), self.scale)

    def show_error(self, message):
        self.status.setText(message)
        self.status.show()
        self.comparison.hide()
        for widget in self.cards.values():
            widget.hide()
        self.grid.show_cards(())

    def present(self, analysis):
        self.status.hide()
        chart = analysis.compare
        self.comparison.setVisible(chart is not None)
        if chart is not None:
            mode = "compare_table" if chart.table_only else "compare_scaled" if chart.scaled is not None else "compare_raw"
            self.compare_note.setText(self.t(mode))
            self.compare_chart.set_data(chart)
        new_order = []
        for result in analysis.metrics:
            panel = self.cards.get(result.key)
            if panel is None:
                panel = self.cards[result.key] = _MetricCard(result.key, self.language, self.palette, self.settings)
                panel.setParent(self.grid)
            panel.show_result(result)
            panel.show()
            new_order.append(panel)
        for panel in self.cards.values():
            if panel not in new_order:
                panel.hide()
        self.grid.show_cards(new_order)

    def recolor(self, palette):
        self.palette = palette
        self.metric_button.setIcon(icon("analytics", "#ffffff", 18))
        self.map_button.setIcon(icon("settings", palette.accent, 16))
        self.create_button.setIcon(icon("excel", palette.accent, 17))
        self.start.recolor(palette.text)
        self.end.recolor(palette.text)
        self.compare_chart.recolor(palette)
        for panel in self.cards.values():
            panel.chart.recolor(palette)
