"""Native selectable plots and data tables for analytics."""

from math import atan2, ceil, degrees, floor, log10

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QStackedWidget, QTableView, QToolTip, QVBoxLayout, QWidget,
)

from .insights import Chart, format_value, tr
from .widgets import button, escaped_tooltip, fill_options, label, row_layout


def _colors(palette):
    base = palette.graphic
    first = base.trend + base.ring if base else (palette.accent, palette.good, palette.warning)
    return tuple(dict.fromkeys((*first, "#5395df", "#b166b4", "#c57b2c", "#52988d", "#df70a4",
                               "#8199ed", "#a58f44", "#477aab", "#c3654d", "#40a8a1", "#a975c7", "#f38c63")))


class _DataTable(QAbstractTableModel):
    def __init__(self, language, parent):
        super().__init__(parent)
        self.language = language
        self.chart = Chart((), ())

    def set_data(self, chart):
        self.beginResetModel()
        self.chart = chart
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.chart.labels)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 1 + len(self.chart.series)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal or not 0 <= section < self.columnCount():
            return None
        return tr(self.language, "chart_group") if section == 0 else self.chart.series[section - 1].label

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole) or not index.isValid():
            return None
        row, column = index.row(), index.column()
        if column:
            series = self.chart.series[column - 1]
            value = format_value(series.values[row], self.language, series.unit)
        else:
            value = self.chart.labels[row]
        return escaped_tooltip(value) if role == Qt.ItemDataRole.ToolTipRole else value


class _Plot(QWidget):
    def __init__(self, language, palette):
        super().__init__()
        self.language = language
        self.palette = palette
        self.chart = Chart((), ())
        self.mode = "bar"
        self.offset = 0
        self.window_size = 8
        self.slices = ()
        self.setMinimumHeight(216)

    def set_plot(self, chart, mode, offset, window_size, slices):
        self.chart, self.mode, self.offset, self.window_size, self.slices = chart, mode, offset, window_size, slices
        self.update()

    def _area(self):
        left, right = (11, 45) if self.language == "fa" else (45, 11)
        return QRectF(self.rect()).adjusted(left, 20, -right, -31)

    def _displayed(self):
        return list(range(self.offset, min(len(self.chart.labels), self.offset + self.window_size)))

    def _ring_bounds(self):
        diameter = min(self.height() - 25, self.width() - 28, 186)
        return QRectF((self.width() - diameter) / 2, (self.height() - diameter) / 2, diameter, diameter)

    def event(self, event):
        if event.type() != QEvent.Type.ToolTip:
            return super().event(event)
        point = QPointF(event.pos())
        if self.mode == "ring" and self.slices:
            bounds = self._ring_bounds()
            delta = point - bounds.center()
            distance = (delta.x() ** 2 + delta.y() ** 2) ** .5
            if bounds.width() / 2 - 25 <= distance <= bounds.width() / 2 + 25:
                position = (degrees(atan2(delta.y(), delta.x())) + 90) % 360
                total = sum(value for _, value in self.slices)
                walked = 0
                for name, value in self.slices:
                    walked += 360 * value / total
                    if position <= walked:
                        QToolTip.showText(event.globalPos(), escaped_tooltip(f"{name}\n{format_value(value, self.language)} · {format_value(100 * value / total, self.language, 'percent')}"), self)
                        return True
        elif self._displayed() and self._area().contains(point):
            area = self._area()
            positions = self._displayed()
            slot = min(len(positions) - 1, int((point.x() - area.left()) / (area.width() / len(positions))))
            index = positions[-1 - slot] if self.language == "fa" else positions[slot]
            lines = [self.chart.labels[index]]
            for series in self.chart.series:
                lines.append(f"{series.label} · {format_value(series.values[index], self.language, series.unit)}")
            QToolTip.showText(event.globalPos(), escaped_tooltip("\n".join(lines)), self)
            return True
        return super().event(event)

    def _ring(self, painter):
        bounds = self._ring_bounds()
        total = sum(value for _, value in self.slices)
        angle = 90.0
        for index, (_, value) in enumerate(self.slices):
            sector = 360 * value / total
            painter.setPen(QPen(QColor(_colors(self.palette)[index]), 25, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            gap = min(3, sector * .16) if len(self.slices) > 1 else 0
            painter.drawArc(bounds, round((angle - gap / 2) * 16), -round((sector - gap) * 16))
            angle -= sector
        painter.setPen(QColor(self.palette.text))
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 5)
        painter.setFont(font)
        mixed = len({series.unit for series in self.chart.series}) > 1
        unit = self.chart.series[0].unit if self.chart.series and not mixed else ""
        painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, format_value(total, self.language, unit))

    def _plot(self, painter, rows, indices):
        colors = self.palette
        plot = self._area()
        rtl = self.language == "fa"
        series_colors = _colors(colors)
        percent = bool(rows) and all(series.unit == "percent" for series in rows)
        peak = max((row.values[i] for row in rows for i in indices if row.values[i] is not None), default=0)
        if percent:
            level, tick_count = 25, 4
        else:
            step = max(1e-6, peak / 4) if peak else 1
            magnitude = 10 ** floor(log10(step))
            level = next(magnitude * factor for factor in (1, 2, 5, 10) if magnitude * factor >= step)
            if peak >= 1:
                level = max(1, level)
            tick_count = max(1, ceil(peak / level))
        ceiling = level * tick_count
        for tick in range(tick_count + 1):
            y = plot.bottom() - tick * plot.height() / tick_count
            painter.setPen(QPen(QColor(colors.line), 1, Qt.PenStyle.DashLine if tick else Qt.PenStyle.SolidLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(colors.muted))
            caption = format_value(tick * level, self.language, "percent" if percent else "")
            painter.drawText(QRectF(plot.right() + 3 if rtl else 2, y - 8, 42, 16), Qt.AlignmentFlag.AlignCenter, caption)

        step = plot.width() / len(indices)
        visual = (indices[::-1] if rtl else indices)
        middle = {index: plot.left() + (place + .5) * step for place, index in enumerate(visual)}
        for index in indices:
            text = painter.fontMetrics().elidedText(self.chart.labels[index], Qt.TextElideMode.ElideRight, max(8, int(step) - 4))
            painter.setPen(QColor(colors.muted))
            painter.drawText(QRectF(middle[index] - step / 2, plot.bottom() + 4, step, 24), Qt.AlignmentFlag.AlignCenter, text)

        for j, series in enumerate(rows):
            tone = QColor(series_colors[j % len(series_colors)])
            if self.mode == "bar":
                bar_width = min(37.0, step * .8 / len(rows))
                for index in indices:
                    value = series.values[index]
                    if value is None:
                        continue
                    height = plot.height() * value / ceiling
                    x = middle[index] + (j - (len(rows) - 1) / 2) * bar_width
                    fill = QLinearGradient(0, plot.bottom() - max(1., height), 0, plot.bottom() + 2)
                    fill.setColorAt(0, tone.lighter(120))
                    fill.setColorAt(1, tone)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(fill)
                    painter.drawRoundedRect(QRectF(x - bar_width / 2 + .5, plot.bottom() - height,
                                                   max(.7, bar_width - 1), max(1, height)), 4, 4)
            else:
                segments = [[]]
                for index in visual:
                    value = series.values[index]
                    if value is None:
                        if segments[-1]:
                            segments.append([])
                    else:
                        segments[-1].append(QPointF(middle[index], plot.bottom() - plot.height() * value / ceiling))
                for points in segments:
                    if not points:
                        continue
                    line = QPainterPath(points[0])
                    for point in points[1:]:
                        line.lineTo(point)
                    if self.mode == "area" and len(points) > 1:
                        area = QPainterPath(line)
                        area.lineTo(points[-1].x(), plot.bottom())
                        area.lineTo(points[0].x(), plot.bottom())
                        area.closeSubpath()
                        shade = QLinearGradient(plot.topLeft(), plot.bottomLeft())
                        faint = QColor(tone)
                        faint.setAlpha(105)
                        shade.setColorAt(0, faint)
                        faint.setAlpha(14)
                        shade.setColorAt(1, faint)
                        painter.fillPath(area, QBrush(shade))
                    painter.setPen(QPen(tone, 2.4))
                    painter.setBrush(QColor(colors.surface))
                    painter.drawPath(line)
                    for point in points:
                        painter.drawEllipse(point, 3, 3)
                    painter.setBrush(Qt.BrushStyle.NoBrush)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.mode == "ring" and self.slices:
            self._ring(painter)
            return
        rows = self.chart.scaled if self.chart.scaled is not None else self.chart.series
        indices = self._displayed()
        has_values = any(series.values[index] is not None for series in rows for index in indices)
        if has_values and self.mode != "ring":
            self._plot(painter, rows, indices)
        else:
            painter.setPen(QColor(self.palette.muted))
            painter.drawText(self._area(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, tr(self.language, "chart_empty"))


class ChartPanel(QWidget):
    def __init__(self, language, palette, settings, key):
        super().__init__()
        self.language = language
        self.palette = palette
        self.settings, self.key = settings, "analytics/chart/" + key
        self.data = Chart((), ())
        self.offset = 0
        self.plot = _Plot(language, palette)
        self.types = QComboBox()
        self.types.setAccessibleName(tr(language, "chart_type"))
        self.types.currentIndexChanged.connect(self._type_changed)
        self.types.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.table = QTableView()
        self.table.setAccessibleName(tr(language, "chart_table"))
        self.model = _DataTable(language, self.table)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setDefaultSectionSize(148)
        self.table.setMinimumHeight(216)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.plot)
        self.stack.addWidget(self.table)

        pane = QVBoxLayout(self)
        pane.setContentsMargins(0, 0, 0, 0)
        top = row_layout()
        top.addWidget(label(tr(language, "chart_type"), "smallMuted"))
        top.addWidget(self.types)
        top.addStretch()
        pane.addLayout(top)
        pane.addWidget(self.stack, 1)
        self.legend_area = QWidget()
        self.legend = QGridLayout(self.legend_area)
        self.legend.setContentsMargins(0, 0, 0, 0)
        self.legend.setSpacing(5)
        pane.addWidget(self.legend_area)

        self.navigation = QWidget()
        page = row_layout(6)
        page.setContentsMargins(0, 0, 0, 0)
        self.navigation.setLayout(page)
        self.previous = button(tr(language, "prev"))
        self.next = button(tr(language, "next"))
        self.page_text = label("", "smallMuted")
        page.addStretch(1)
        page.addWidget(self.previous)
        page.addWidget(self.page_text)
        page.addWidget(self.next)
        self.previous.clicked.connect(lambda: self._move(-1))
        self.next.clicked.connect(lambda: self._move(1))
        pane.addWidget(self.navigation)
        self.set_data(self.data)

    def set_data(self, chart):
        if chart.labels != self.data.labels:
            self.offset = 0
        self.data = chart
        self.model.set_data(chart)
        allowed = ["bar", "line", "area"] if not chart.table_only else []
        if not chart.table_only and (chart.slices or not chart.labels):
            allowed.append("ring")
        allowed.append("table")
        preference = self.settings.value(self.key, "bar", str)
        fill_options(self.types, ((mode, tr(self.language, "chart_" + mode)) for mode in allowed), preference)
        self._update()

    def _type_changed(self):
        self.settings.setValue(self.key, self.types.currentData())
        self._update()

    def _move(self, step):
        self.offset += step * self.plot.window_size
        self._update()

    def _update(self):
        mode = self.types.currentData()
        table, ring = mode == "table", mode == "ring"
        self.stack.setCurrentWidget(self.table if table else self.plot)
        slices = sorted(((name, count) for name, count in self.data.slices if count), key=lambda item: -item[1])
        if len(slices) > 8:
            slices = [*slices[:7], (tr(self.language, "chart_more"), sum(count for _, count in slices[7:]))]
        total = len(self.data.labels)
        size = self.plot.window_size
        self.offset = max(0, min(self.offset, (max(0, total - 1) // size) * size))
        self.plot.set_plot(self.data, mode, self.offset, size, tuple(slices))
        self.navigation.setVisible(not table and not ring and total > size)
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + size < total)
        self.page_text.setText(tr(self.language, "page_interval", first=format_value(self.offset + 1, self.language),
                                   last=format_value(min(total, self.offset + size), self.language),
                                   total=format_value(total, self.language)))
        while self.legend.count():
            self.legend.takeAt(0).widget().deleteLater()
        shown = [(item.label, None, index) for index, item in enumerate(self.data.series)
                 if any(value is not None for value in item.values)] if not ring else [
                     (name, count, index) for index, (name, count) in enumerate(slices)]
        self.legend_area.setVisible(not table and (ring or len(shown) > 1))
        for position, (caption, amount, index) in enumerate(shown):
            color = _colors(self.palette)[index % len(_colors(self.palette))]
            text = "●  " + caption if amount is None else "●  " + caption + " · " + format_value(amount, self.language)
            entry = label(text, "smallMuted", True)
            entry.setStyleSheet(f"border: 1px solid {color}; border-radius: 7px; padding: 4px 6px;")
            self.legend.addWidget(entry, position // 3, position % 3)
            entry.show()

    def recolor(self, palette):
        self.palette = self.plot.palette = palette
        self._update()
