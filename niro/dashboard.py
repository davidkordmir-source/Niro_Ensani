"""Offline workbook analytics and native dashboard widgets."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil, floor, log10
import unicodedata

from PySide6.QtCore import QCalendar, QDate, QEasingCurve, QEvent, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QBoxLayout, QFrame, QScrollArea, QToolTip, QVBoxLayout, QWidget

from .locale import PERSIAN_DIGITS, number
from .record_fields import date_from_text
from .state import View
from .theme import linear_fill
from .visuals import artwork_width, draw_banner
from .widgets import ComboBox, ElidedLabel, card, fill_options, icon, label, row_layout, section_header
from .workbook import normalize


COPY = {
    "title": ("داشبورد منابع انسانی", "Workforce dashboard"),
    "intro": ("ترکیب نیروی انسانی و تغییرات ثبت‌شده در شیت فعال", "Workforce composition and activity recorded on the active sheet"),
    "period": ("بازهٔ نمودار", "Chart period"),
    "days": ("{count} روز گذشته", "Last {count} days"),
    "total": ("تعداد کل نفرات", "Total personnel"),
    "total_note": ("تمام رکوردهای شیت فعال در برنامه", "All records on the active sheet in the app"),
    "hire": ("افراد جذب جدید", "New hires"),
    "hire_note": ("بر اساس تاریخ ورود به سازمان", "Based on organization start dates"),
    "hire_empty": ("تاریخ ورود قابل محاسبه در شیت نیست.", "No usable start dates on this sheet."),
    "exit": ("خروج کارکنان", "Departures"),
    "exit_date": ("بر اساس تاریخ خروج در بازه", "Dated departures in this period"),
    "exit_status": ("بر اساس وضعیت ثبت‌شده، در همهٔ تاریخ‌ها", "Based on recorded status across all dates"),
    "exit_empty": ("ستون وضعیت یا تاریخ خروج در شیت نیست.", "No status or departure dates on this sheet."),
    "exit_unknown": ("تاریخ خروج و وضعیت خروج مشخص نشده‌اند.", "No usable departure dates or statuses."),
    "trend": ("روند جذب و خروج", "Hiring and departures"),
    "trend_note": ("رویدادهای دارای تاریخ ثبت‌شده", "Events with recorded dates"),
    "trend_limited": ("از تاریخ ورود برای رسم روند جذب استفاده شد. روند خروج به تاریخ خروج نیاز دارد.",
                      "Start dates power the hiring trend. The departure trend requires departure dates."),
    "trend_undated": ("{count} رکورد خروج بدون تاریخ معتبر در روند لحاظ نشده است.",
                      "{count} status-marked departures have no usable date and are excluded from the trend."),
    "trend_empty": ("روند با ثبت تاریخ ورود یا خروج نمایش داده می‌شود.", "Record start or departure dates to see the trend."),
    "no_events": ("رویداد تاریخ‌دار در این بازه ثبت نشده است.", "No dated events in the selected period."),
    "statuses": ("وضعیت اشتغال", "Employment status"),
    "status_empty": ("ستون وضعیت اشتغال در شیت فعال نیست.", "The active sheet has no employment status column."),
    "empty": ("هنوز رکوردی در شیت فعال وجود ندارد.", "The active sheet has no personnel records yet."),
    "unlinked": ("برای دیدن آمار، بانک اکسل را متصل کنید.", "Connect an Excel workbook to view your analytics."),
    "unspecified": ("ثبت‌نشده", "Unspecified"),
    "other": ("سایر وضعیت‌ها", "Other statuses"),
}


def _text(language: str, key: str, **values) -> str:
    return COPY[key][language == "en"].format(**values)


def _key(text: str) -> str:
    return "".join(char for char in normalize(text) if not unicodedata.combining(char))


_START_COLUMNS = (
    "تاریخ ورود به سازمان", "تاریخ استخدام", "تاریخ جذب", "تاریخ شروع همکاری",
    "organization start date", "hire date", "hiring date", "employment start date", "start date", "date of hire",
)
_EXIT_COLUMNS = (
    "تاریخ خروج", "تاریخ خروج از سازمان", "تاریخ ترک کار", "تاریخ پایان همکاری", "تاریخ قطع همکاری",
    "departure date", "exit date", "termination date", "employment end date", "end date", "separation date",
)
_STATUS_COLUMNS = ("وضعیت اشتغال", "وضعیت استخدام", "وضعیت همکاری", "employment status", "employee status", "work status")
_DEPARTED = frozenset(map(_key, (
    "قطع همکاری", "پایان همکاری", "ترک کار", "خروج", "خروج از سازمان", "خارج شده", "مستعفی", "استعفا",
    "استعفا داده", "اخراج", "اخراج شده", "بازنشسته", "بازنشستگی", "بازخرید", "فوت", "فوت شده", "اتمام قرارداد",
    "terminated", "retired", "former", "departed", "left", "resigned", "dismissed", "separated", "deceased", "contract ended",
)))
_EMPLOYED = frozenset(map(_key, ("شاغل", "فعال", "active", "employed")))
_PERIODS = ((30, 7), (90, 15), (365, 61))


@dataclass(frozen=True, slots=True)
class Period:
    days: int
    first_day: QDate
    bucket_days: int
    hires: tuple[int, ...] | None
    departures: tuple[int, ...] | None

    @property
    def slots(self) -> int:
        return ceil(self.days / self.bucket_days)


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    total: int | None
    statuses: tuple[tuple[str | None, int], ...] | None
    departures_by_status: int | None
    undated_departures: int
    periods: tuple[Period, ...]


def summarize(view: View | None, today: QDate | None = None) -> DashboardSummary:
    """Summarize the workbook in one pass. Call off the UI thread for large sheets."""
    today = today or QDate.currentDate()
    records = view.records if view else ()
    labels = {}
    if view is not None:
        for i, column in enumerate(view.snapshot.columns):
            labels.setdefault(_key(column.label), i)

    def position(names):
        return next((labels[name] for candidate in names if (name := _key(candidate)) in labels), None)

    hire_column, exit_column, status_column = map(position, (_START_COLUMNS, _EXIT_COLUMNS, _STATUS_COLUMNS))
    hires, departures, statuses = Counter(), Counter(), Counter()
    display: dict[str, str | None] = {"": None}
    marked = undated = 0
    parsed_dates = {}

    def age(values, index):
        if index is None:
            return None
        raw = values[index]
        if raw not in parsed_dates:
            date = date_from_text(raw)
            parsed_dates[raw] = date.daysTo(today) if date.isValid() else None
        return parsed_dates[raw]

    for record in records:
        values = record.values
        hire_age, exit_age = age(values, hire_column), age(values, exit_column)
        if hire_age is not None:
            hires[hire_age] += 1
        if exit_age is not None:
            departures[exit_age] += 1
        if status_column is not None:
            spelling = values[status_column].strip()
            status = _key(spelling)
            display.setdefault(status, spelling)
            statuses[status] += 1
            if status in _DEPARTED:
                marked += 1
                undated += exit_age is None

    def buckets(counter, days, step, enabled):
        if not enabled:
            return None
        result = [0] * ceil(days / step)
        for age_in_days, count in counter.items():
            if 0 <= age_in_days < days:
                result[(days - 1 - age_in_days) // step] += count
        return tuple(result)

    periods = tuple(
        Period(days, today.addDays(1 - days), step,
               buckets(hires, days, step, bool(hires) or not records and hire_column is not None),
               buckets(departures, days, step, bool(departures) or not records and exit_column is not None))
        for days, step in _PERIODS
    )
    order = sorted(statuses, key=lambda name: (-statuses[name], name))
    distribution = tuple((display[name], statuses[name]) for name in order)
    return DashboardSummary(len(records) if view is not None else None,
                            distribution if status_column is not None else None,
                            marked if any(order) or not records and status_column is not None else None, undated, periods)


def _date(language, date, full=False):
    if language == "en":
        return date.toString("yyyy-MM-dd" if full else "MM/dd")
    parts = QCalendar(QCalendar.System.Jalali).partsFromDate(date)
    year = f"{parts.year:04}/" if full else ""
    return f"{year}{parts.month:02}/{parts.day:02}".translate(PERSIAN_DIGITS)


def _smooth_line(points: list[QPointF]) -> QPainterPath:
    path = QPainterPath(points[0])
    for start, end in zip(points, points[1:]):
        offset = (end.x() - start.x()) * 0.43
        path.cubicTo(start.x() + offset, start.y(), end.x() - offset, end.y(), end.x(), end.y())
    return path


class _DashboardBanner(QWidget):
    def __init__(self, title, lead, palette):
        super().__init__()
        self.design_palette = palette
        self.content = QVBoxLayout(self)
        self.content.setSpacing(5)
        self.content.addStretch(1)
        self.content.addWidget(label(title, "showcaseTitle", True))
        self.content.addWidget(label(lead, "showcaseLead", True))
        self.content.addSpacing(5)
        self.content.addStretch(1)
        self.setMinimumHeight(197)

    def set_design(self, palette):
        self.design_palette = palette
        self._inset()
        self.update()

    def set_period_selector(self, selector):
        self.content.insertWidget(self.content.count() - 1, selector, 0, Qt.AlignmentFlag.AlignLeading)

    def _inset(self):
        bounds = QRectF(self.rect())
        artwork = round(artwork_width(bounds))
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        self.content.setContentsMargins(22 + (artwork if rtl else 0), 15, 22 + (0 if rtl else artwork), 15)

    def resizeEvent(self, event):
        self._inset()
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        draw_banner(painter, QRectF(self.rect()), self.design_palette, rtl)


class _MiniTrend(QWidget):
    def __init__(self, language):
        super().__init__()
        self.rtl = language == "fa"
        self.values = ()
        self.color = QColor()
        self.setFixedHeight(24)

    def present(self, values, color):
        self.values = values or ()
        self.color = QColor(color)
        self.setVisible(len(self.values) > 1)
        self.update()

    def paintEvent(self, event):
        if len(self.values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = QRectF(self.rect()).adjusted(2, 3, -2, -2)
        values = self.values[::-1] if self.rtl else self.values
        peak = max(1, max(values))
        points = [
            QPointF(plot.left() + plot.width() * index / (len(values) - 1), plot.bottom() - plot.height() * value / peak)
            for index, value in enumerate(values)
        ]
        line = _smooth_line(points)
        fill = QPainterPath(line)
        fill.lineTo(points[-1].x(), plot.bottom())
        fill.lineTo(points[0].x(), plot.bottom())
        fill.closeSubpath()
        shade = QLinearGradient(plot.topLeft(), plot.bottomLeft())
        tone = QColor(self.color)
        tone.setAlpha(85)
        shade.setColorAt(0, tone)
        tone.setAlpha(0)
        shade.setColorAt(1, tone)
        painter.fillPath(fill, QBrush(shade))
        painter.setPen(QPen(self.color, 1.7))
        painter.drawPath(line)


class _CountCard(QFrame):
    def __init__(self, language, title, icon_name, tone, index):
        super().__init__()
        self.icon_name = icon_name
        self.tone = tone
        self.index = index
        self.design_colors = None
        self.setObjectName("surface")
        stack = QVBoxLayout(self)
        stack.setContentsMargins(15, 14, 15, 13)
        stack.setSpacing(5)
        header = row_layout(8)
        self.mark = label()
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mark.setFixedSize(34, 34)
        header.addWidget(self.mark)
        self.heading = label(title, "sectionTitle", True)
        header.addWidget(self.heading, 1)
        stack.addLayout(header)
        self.value = label("—", "metricValue")
        side = Qt.AlignmentFlag.AlignRight if language == "fa" else Qt.AlignmentFlag.AlignLeft
        self.value.setAlignment(side | Qt.AlignmentFlag.AlignAbsolute)
        stack.addWidget(self.value)
        self.note = label("", "smallMuted", True)
        self.note.setAlignment(side | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignTop)
        stack.addWidget(self.note)
        self.sparkline = _MiniTrend(language)
        stack.addWidget(self.sparkline)
        self.sparkline.hide()
        stack.addStretch()

    def recolor(self, palette):
        colors = self.design_colors = palette.graphic.metrics[self.index] if palette.graphic else None
        color = colors.detail if colors else getattr(palette, self.tone)
        if colors:
            fill = linear_fill(colors.start, colors.end)
            self.setStyleSheet(f"QFrame#surface {{ background: {fill}; border: 1px solid {color}; border-radius: 17px; }}")
            self.heading.setStyleSheet(f"color: {colors.ink};")
            self.value.setStyleSheet(f"color: {colors.ink}; font-size: 38px;")
            self.note.setStyleSheet(f"color: {colors.ink};")
        else:
            self.setStyleSheet("")
            for widget in (self.heading, self.value, self.note):
                widget.setStyleSheet("")
        backing = QColor(color)
        self.mark.setStyleSheet(f"background: rgba({backing.red()}, {backing.green()}, {backing.blue()}, 28); border-radius: 11px;")
        self.mark.setPixmap(icon(self.icon_name, color, 19).pixmap(19, 19))

    def set_series(self, values):
        self.sparkline.present(values if self.design_colors else None,
                               self.design_colors.detail if self.design_colors else "transparent")


class _AnimatedChart(QWidget):
    def __init__(self, language, palette):
        super().__init__()
        self.language, self.palette = language, palette
        self.progress = 1.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(270)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.valueChanged.connect(self._advance)

    def _advance(self, value):
        self.progress = value
        self.update()

    def animate(self):
        self.animation.stop()
        if self.isVisible():
            self.animation.start()
        else:
            self._advance(1.0)

    def recolor(self, palette):
        self.palette = palette
        self.update()


class _Trend(_AnimatedChart):
    def __init__(self, language, palette):
        super().__init__(language, palette)
        self.period = None
        self.empty_text = ""
        self.setMinimumHeight(228)

    def set_period(self, period, empty_text):
        if self.period == period and self.empty_text == empty_text:
            return
        self.period, self.empty_text = period, empty_text
        summary = []
        for key, values in (("hire", period.hires), ("exit", period.departures)):
            if values is not None:
                summary.append(f"{_text(self.language, key)}: {number(self.language, sum(values))}")
        self.setAccessibleName(". ".join(summary) or empty_text)
        self.setToolTip("\n".join(summary) or empty_text)
        self.animate()

    def _plot(self):
        plot = QRectF(self.rect()).adjusted(4, 16, -4, -27)
        series = tuple(values for values in (self.period.hires, self.period.departures) if values is not None)
        if not series:
            return plot, 0
        largest = max(max(values) for values in series)
        axis_width = max(34, self.fontMetrics().horizontalAdvance(number(self.language, largest * 2)) + 9)
        rtl = self.language == "fa"
        return plot.adjusted(0 if rtl else axis_width, 0, -axis_width if rtl else 0, 0), axis_width

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and self.period is not None:
            period = self.period
            plot, _ = self._plot()
            point = QPointF(event.pos())
            if (period.hires is not None or period.departures is not None) and plot.contains(point):
                index = min(period.slots - 1, floor((point.x() - plot.left()) / (plot.width() / period.slots)))
                if self.language == "fa":
                    index = period.slots - 1 - index
                offset = index * period.bucket_days
                start = _date(self.language, period.first_day.addDays(offset), full=True)
                end = _date(self.language, period.first_day.addDays(min(period.days, offset + period.bucket_days) - 1), full=True)
                details = [f"{start} — {end}"]
                for key, values in (("hire", period.hires), ("exit", period.departures)):
                    if values is not None:
                        details.append(f"{_text(self.language, key)} · {number(self.language, values[index])}")
                QToolTip.showText(event.globalPos(), "\n".join(details), self)
                return True
        return super().event(event)

    def _draw_lines(self, painter, plot, series, ceiling, step, rtl):
        painter.save()
        painter.setClipRect(plot.adjusted(-7, -8, 7, 2))
        for values, color in series:
            points = []
            for index, value in enumerate(values):
                slot = self.period.slots - 1 - index if rtl else index
                x = plot.left() + step * (slot + 0.5)
                y = plot.bottom() - plot.height() * value / ceiling * self.progress
                points.append(QPointF(x, y))
            line = _smooth_line(points)
            area = QPainterPath(line)
            area.lineTo(points[-1].x(), plot.bottom())
            area.lineTo(points[0].x(), plot.bottom())
            area.closeSubpath()
            shade = QLinearGradient(plot.topLeft(), plot.bottomLeft())
            tone = QColor(color)
            tone.setAlpha(115 if self.palette.graphic.glow else 88)
            shade.setColorAt(0, tone)
            tone.setAlpha(6)
            shade.setColorAt(1, tone)
            painter.fillPath(area, QBrush(shade))
            if self.palette.graphic.glow:
                tone.setAlpha(45)
                painter.setPen(QPen(tone, 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawPath(line)
            painter.setPen(QPen(QColor(color), 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(line)
            painter.setBrush(QColor(self.palette.surface))
            for point in points:
                painter.drawEllipse(point, 3.4, 3.4)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.restore()

    def paintEvent(self, event):
        if self.period is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = self.palette
        plot, axis_width = self._plot()
        if self.period.hires is None and self.period.departures is None:
            dot = QColor(colors.line)
            painter.setPen(QPen(dot, 1.1))
            for x in range(13, max(13, self.width() - 10), 14):
                for y in range(8, max(8, self.height() - 5), 14):
                    painter.drawPoint(x, y)
            painter.setPen(QColor(colors.muted))
            painter.drawText(plot.adjusted(14, 0, -14, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
            return
        rtl = self.language == "fa"
        chart_colors = colors.graphic.trend if colors.graphic else (colors.accent, colors.warning)
        series = [(values, color) for values, color in zip((self.period.hires, self.period.departures), chart_colors) if values is not None]
        peak = max(max(values) for values, _ in series)
        division = max(1, ceil(peak / 4))
        power = 10 ** floor(log10(division))
        division = next(tick * power for tick in (1, 2, 5, 10) if tick * power >= division)
        lines = max(1, min(4, ceil(peak / division)))
        ceiling = division * lines
        for tick in range(lines + 1):
            level = tick * division
            y = plot.bottom() - plot.height() * level / ceiling
            painter.setPen(QPen(QColor(colors.line), 1, Qt.PenStyle.SolidLine if not tick else Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(colors.muted))
            painter.drawText(QRectF(plot.right() + 4 if rtl else 0, y - 10, axis_width - 3, 20), Qt.AlignmentFlag.AlignCenter, number(self.language, level))
        step = plot.width() / self.period.slots
        lines_chart = colors.graphic is not None and colors.graphic.line_chart
        if lines_chart and peak:
            self._draw_lines(painter, plot, series, ceiling, step, rtl)
        bar = min(32.0, step * 0.56 / len(series))
        for i in range(self.period.slots):
            visual_index = self.period.slots - 1 - i if rtl else i
            x = plot.left() + step * (visual_index + 0.5)
            for j, (values, color) in enumerate(() if lines_chart else series):
                value = values[i]
                if not value:
                    continue
                height = plot.height() * value / ceiling * self.progress
                left = x + (j - len(series) / 2) * bar
                fill = QLinearGradient(0, plot.bottom() - height, 0, plot.bottom())
                primary = QColor(color)
                fill.setColorAt(0, primary.lighter(135) if colors.graphic else primary)
                primary.setAlpha(227 if colors.graphic else 134)
                fill.setColorAt(1, primary)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(QRectF(left + 2, plot.bottom() - height, bar - 4, height), 4, 4)
            if step >= 49 or i in (0, self.period.slots // 2, self.period.slots - 1):
                painter.setPen(QColor(colors.muted))
                painter.drawText(QRectF(x - 27, plot.bottom() + 4, 54, 20), Qt.AlignmentFlag.AlignCenter,
                                 _date(self.language, self.period.first_day.addDays(i * self.period.bucket_days)))
        if not peak:
            painter.setPen(QColor(colors.muted))
            painter.drawText(plot.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)


class _Donut(_AnimatedChart):
    def __init__(self, language, palette):
        super().__init__(language, palette)
        self.total = 0
        self.segments = ()
        self.setFixedHeight(143)

    def set_counts(self, segments, total):
        if segments != self.segments or total != self.total:
            self.segments, self.total = segments, total
            self.animate()

    def recolor(self, palette):
        self.setFixedHeight(171 if palette.graphic else 143)
        super().recolor(palette)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        styled = self.palette.graphic is not None
        stroke = 23 if styled else 14
        extent = min(141 if styled else 110, max(1, self.width() - stroke - 14))
        ring = QRectF((self.width() - extent) / 2, (self.height() - extent) / 2, extent, extent)
        painter.setPen(QPen(QColor(self.palette.line), stroke))
        painter.drawEllipse(ring)
        angle = 90.0
        for count, color in self.segments:
            span = count / self.total * 360
            gap = min(4, span * 0.2) if len(self.segments) > 1 else 0
            tone = QColor(color)
            if styled:
                luster = QLinearGradient(ring.topLeft(), ring.bottomRight())
                luster.setColorAt(0, tone.lighter(130))
                luster.setColorAt(0.55, tone)
                luster.setColorAt(1, tone.darker(122))
                painter.setPen(QPen(QBrush(luster), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            else:
                painter.setPen(QPen(tone, stroke))
            painter.drawArc(ring, round((angle - gap / 2) * 16), -round((span - gap) * 16 * self.progress))
            angle -= span
        painter.setPen(QColor(self.palette.text))
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + (9 if styled else 6))
        painter.setFont(font)
        painter.drawText(ring, Qt.AlignmentFlag.AlignCenter, number(self.language, self.total))


class _StatusComposition(QWidget):
    def __init__(self, donut, legend):
        super().__init__()
        self.decorated = False
        self.content = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(12)
        self.content.addWidget(donut, 1)
        self.content.addWidget(legend, 1)

    def set_graphic(self, enabled):
        self.decorated = enabled
        self._orient()

    def resizeEvent(self, event):
        self._orient()
        super().resizeEvent(event)

    def _orient(self):
        side_by_side = self.decorated and self.width() >= 318
        direction = QBoxLayout.Direction.RightToLeft if side_by_side else QBoxLayout.Direction.TopToBottom
        self.content.setDirection(direction)


class DashboardPage(QWidget):
    def __init__(self, language, palette):
        super().__init__()
        self.setObjectName("page")
        self.language, self.palette = language, palette
        self.summary = summarize(None)
        self._t = lambda key, **values: _text(language, key, **values)
        self._n = lambda count: number(language, count)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll)
        area = QWidget()
        area.setObjectName("scrollContent")
        scroll.setWidget(area)
        body = self.body = QVBoxLayout(area)
        body.setContentsMargins(27, 11, 27, 18)
        body.setSpacing(14)

        self.plain_header = QWidget()
        header = self.heading_layout = row_layout(12)
        header.setContentsMargins(0, 0, 0, 0)
        self.plain_header.setLayout(header)
        header.addWidget(section_header(self._t("title"), self._t("intro")), 1)
        self.selector = ComboBox()
        self.selector.setAccessibleName(self._t("period"))
        fill_options(self.selector, ((days, self._t("days", count=self._n(days))) for days, _ in _PERIODS))
        header.addWidget(self.selector)
        body.addWidget(self.plain_header)
        self.showcase = _DashboardBanner(self._t("title"), self._t("intro"), palette)
        body.addWidget(self.showcase)

        self.metric_row = QWidget()
        metrics = row_layout(11)
        metrics.setContentsMargins(0, 0, 0, 0)
        self.metric_row.setLayout(metrics)
        self.metrics = (
            _CountCard(language, self._t("total"), "people", "accent", 0),
            _CountCard(language, self._t("hire"), "plus", "good", 1),
            _CountCard(language, self._t("exit"), "departure", "warning", 2),
        )
        for metric in self.metrics:
            metrics.addWidget(metric, 1)
        body.addWidget(self.metric_row)

        charts = row_layout(12)
        trend_frame, trend_body = card()
        trend_body.addWidget(label(self._t("trend"), "sectionTitle", True))
        trend_body.addWidget(label(self._t("trend_note"), "smallMuted", True))
        self.trend = _Trend(language, palette)
        trend_body.addWidget(self.trend, 1)
        key_bar = row_layout(12)
        self.trend_keys = {}
        for key in ("hire", "exit"):
            handle = row_layout(5)
            dot = label()
            dot.setFixedSize(9, 9)
            text = label(self._t(key), "smallMuted", True)
            handle.addWidget(dot)
            handle.addWidget(text)
            key_bar.addLayout(handle)
            self.trend_keys[key] = (dot, text)
        key_bar.addStretch()
        trend_body.addLayout(key_bar)
        self.trend_hint = label("", "smallMuted", True)
        trend_body.addWidget(self.trend_hint)
        charts.addWidget(trend_frame, 7)

        status_frame, status_body = card()
        status_body.addWidget(label(self._t("statuses"), "sectionTitle", True))
        self.donut = _Donut(language, palette)
        self.status_hint = label("", "smallMuted", True)
        status_body.addWidget(self.status_hint)
        self.legend_area = QWidget()
        legend_body = QVBoxLayout(self.legend_area)
        legend_body.setContentsMargins(0, 0, 0, 0)
        legend_body.addStretch()
        self.legend = QVBoxLayout()
        self.legend.setSpacing(6)
        legend_body.addLayout(self.legend)
        legend_body.addStretch()
        self.status_composition = _StatusComposition(self.donut, self.legend_area)
        status_body.addWidget(self.status_composition, 1)
        charts.addWidget(status_frame, 4)
        body.addLayout(charts, 1)
        self.selector.currentIndexChanged.connect(self._present_period)
        self.recolor(palette)

    def resizeEvent(self, event):
        self._place_metrics()
        super().resizeEvent(event)

    def _place_metrics(self):
        design = self.palette.graphic
        inside = design is not None and design.glow and self.width() >= 1040
        in_banner = self.metric_row.parentWidget() is self.showcase
        if inside and not in_banner:
            self.body.removeWidget(self.metric_row)
            self.showcase.content.insertWidget(self.showcase.content.count() - 1, self.metric_row)
        elif in_banner and not inside:
            self.showcase.content.removeWidget(self.metric_row)
            self.body.insertWidget(self.body.indexOf(self.showcase) + 1, self.metric_row)
        self.metric_row.show()

    def set_summary(self, summary: DashboardSummary):
        self.summary = summary
        self.selector.setEnabled(summary.total is not None)
        self._present_period()
        self._present_statuses()

    def _present_period(self):
        summary = self.summary
        period = next(item for item in summary.periods if item.days == self.selector.currentData())
        if period.departures is None:
            departure_total = summary.departures_by_status
            exit_note = "exit_status" if departure_total is not None else "exit_empty" if summary.statuses is None else "exit_unknown"
        else:
            departure_total = sum(period.departures)
            exit_note = "exit_date"
        values = (summary.total, sum(period.hires) if period.hires is not None else None, departure_total)
        notes = ("total_note", "hire_note" if values[1] is not None else "hire_empty", exit_note)
        for metric, value, note, trend in zip(self.metrics, values, notes, (None, period.hires, period.departures)):
            metric.value.setText("—" if value is None else self._n(value))
            metric.note.setText(self._t(note) if summary.total is not None else "")
            metric.set_series(trend)
        self.trend.set_period(period, self._t("unlinked" if summary.total is None else "trend_empty" if all(v is None for v in (period.hires, period.departures)) else "no_events"))
        for key, series in zip(("hire", "exit"), (period.hires, period.departures)):
            for widget in self.trend_keys[key]:
                widget.setVisible(series is not None)
        hint = ""
        if period.departures is None and period.hires is not None:
            hint = self._t("trend_limited")
        elif period.departures is not None and summary.undated_departures:
            hint = self._t("trend_undated", count=self._n(summary.undated_departures))
        self.trend_hint.setText(hint)
        self.trend_hint.setVisible(bool(hint))

    def _present_statuses(self):
        while self.legend.count():
            item = self.legend.takeAt(0)
            item.widget().deleteLater()
        total, statuses = self.summary.total, self.summary.statuses
        hint = "unlinked" if total is None else "empty" if not total else "status_empty"
        self.status_hint.setText(self._t(hint))
        self.status_hint.setVisible(not statuses and total is not None)
        self.status_composition.setVisible(bool(statuses))
        if not statuses:
            self.donut.set_counts((), 0)
            return
        colors = iter((self.palette.accent, "#4382ab", "#a17b55", self.palette.muted, self.palette.hero_end, "#7c8d67"))
        categories = [(name or self._t("unspecified"), count, _key(name) if name else "") for name, count in statuses[:5]]
        if len(statuses) > 5:
            categories.append((self._t("other"), sum(count for _, count in statuses[5:]), ""))
        segments = []
        for index, (name, count, status) in enumerate(categories):
            if self.palette.graphic:
                color = self.palette.graphic.ring[index]
            else:
                color = self.palette.warning if status in _DEPARTED else self.palette.good if status in _EMPLOYED else next(colors)
            item = QWidget()
            line = row_layout(7)
            if self.palette.graphic:
                item.setObjectName("statusRow")
                item.setStyleSheet(f"QWidget#statusRow {{ background: {self.palette.soft}; border-radius: 7px; }}")
                line.setContentsMargins(6, 4, 6, 4)
            else:
                line.setContentsMargins(0, 0, 0, 0)
            item.setLayout(line)
            dot = label()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet(f"background:{color}; border-radius:4px;")
            category = ElidedLabel("smallMuted")
            category.setText(name)
            line.addWidget(dot)
            line.addWidget(category, 1)
            line.addWidget(label(self._n(count), "smallMuted"))
            self.legend.addWidget(item)
            segments.append((count, color))
        self.donut.set_counts(tuple(segments), total)

    def recolor(self, palette):
        self.palette = palette
        graphic = palette.graphic is not None
        if graphic and self.selector.parentWidget() is not self.showcase:
            self.heading_layout.removeWidget(self.selector)
            self.showcase.set_period_selector(self.selector)
        elif not graphic and self.selector.parentWidget() is not self.plain_header:
            self.showcase.content.removeWidget(self.selector)
            self.heading_layout.addWidget(self.selector)
        self.plain_header.setVisible(not graphic)
        self.showcase.setVisible(graphic)
        self.selector.show()
        self._place_metrics()
        if graphic:
            self.showcase.set_design(palette)
        for metric in self.metrics:
            metric.recolor(palette)
        self.trend.recolor(palette)
        self.donut.recolor(palette)
        self.status_composition.set_graphic(graphic)
        trend_colors = palette.graphic.trend if graphic else (palette.accent, palette.warning)
        for key, color in zip(("hire", "exit"), trend_colors):
            self.trend_keys[key][0].setStyleSheet(f"background:{color}; border-radius:4px;")
        self.set_summary(self.summary)
