"""Illustration and preview painting for the three graphic designs."""

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

from .theme import ASSETS, Palette


def _tint(color: str, opacity: int) -> QColor:
    result = QColor(color)
    result.setAlpha(opacity)
    return result


@lru_cache(maxsize=3)
def illustration(name: str) -> QImage:
    return QImage(str(ASSETS / "looks" / name))


def artwork_width(bounds: QRectF) -> float:
    return min(bounds.width() * 0.45, bounds.height() * 2.6)


def draw_banner(painter: QPainter, bounds: QRectF, palette: Palette, rtl: bool, radius: float = 17) -> None:
    design = palette.graphic
    if design is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(bounds, radius, radius)
    painter.setClipPath(clip)

    main = QLinearGradient(bounds.topRight() if rtl else bounds.topLeft(),
                           bounds.bottomLeft() if rtl else bounds.bottomRight())
    main.setColorAt(0, QColor(palette.hero_start))
    main.setColorAt(1, QColor(palette.hero_end))
    painter.fillRect(bounds, main)

    width = artwork_width(bounds)
    art_side = QRectF(bounds.left() if rtl else bounds.right() - width, bounds.top(), width, bounds.height())
    glow = QRadialGradient(art_side.center(), max(1.0, width))
    glow.setColorAt(0, _tint(design.detail, 110 if design.glow else 88))
    glow.setColorAt(1, _tint(design.detail, 0))
    painter.fillRect(bounds, glow)

    painter.save()
    painter.translate(bounds.left() if rtl else bounds.right(), bounds.top())
    painter.scale(bounds.width() / 920 * (1 if rtl else -1), bounds.height() / 180)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for row in range(6):
        rise = row * 10
        color = _tint(design.detail, 67 - row * 6)
        painter.setPen(QPen(color, 1.15 if design.glow else 1.8))
        curve = QPainterPath(QPointF(-30, 78 + rise))
        curve.cubicTo(144, 21 + rise, 312, 222 - rise, 451, 134 + rise)
        curve.cubicTo(495, 106 + rise, 583, 67 + rise, 741, 170 + rise)
        painter.drawPath(curve)
    painter.restore()

    art = illustration(design.artwork)
    if not art.isNull() and width > 0:
        area = art_side.adjusted(width * 0.04, bounds.height() * 0.045, -width * 0.06, -bounds.height() * 0.04)
        visible = QRectF(*design.crop)
        footprint = QSizeF(visible.size())
        footprint.scale(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        position = QPointF(area.center().x() - footprint.width() / 2, area.center().y() - footprint.height() / 2)
        painter.drawImage(QRectF(position, footprint), art, visible)
    painter.restore()


def preview_icon(palette: Palette, rtl: bool) -> QIcon:
    pixmap = QPixmap(312, 168)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    design = palette.graphic
    bounds = QRectF(0.5, 0.5, 155, 83)
    clip = QPainterPath()
    clip.addRoundedRect(bounds, 6, 6)
    painter.setClipPath(clip)
    painter.fillRect(bounds, QColor(palette.page))
    rail = QRectF(132 if rtl else 0, 0, 24, 84)
    painter.fillRect(rail, QColor(palette.sidebar))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(design.active_start))
    painter.drawRoundedRect(QRectF(rail.left() + 4, 21, 16, 7), 2, 2)
    painter.setBrush(QColor(palette.sidebar_muted))
    for y in (12, 33, 41):
        painter.drawRoundedRect(QRectF(rail.left() + 7, y, 10, 2), 1, 1)

    stage = QRectF(4 if rtl else 28, 5, 124, 74)
    hero = QRectF(stage.left(), stage.top(), stage.width(), 48 if design.glow else 29)
    draw_banner(painter, hero, palette, rtl, 4)
    painter.setBrush(QColor(design.hero_ink))
    title_x = hero.right() - 43 if rtl else hero.left() + 7
    painter.drawRoundedRect(QRectF(title_x, hero.top() + 11, 36, 3), 1.5, 1.5)
    painter.setBrush(_tint(design.hero_ink, 122))
    painter.drawRoundedRect(QRectF(title_x + (10 if rtl else 0), hero.top() + 17, 26, 2), 1, 1)

    metric_span = 24 if design.glow else 41
    metric_y = 34 if design.glow else 38
    metrics_left = stage.left() + (51 if design.glow and rtl else 0)
    for index, tone in enumerate(design.metrics):
        slot = 2 - index if rtl else index
        left = metrics_left + slot * metric_span
        item = QRectF(left, metric_y, metric_span - 2, 15)
        fill = QLinearGradient(item.topLeft(), item.bottomRight())
        fill.setColorAt(0, QColor(tone.start))
        fill.setColorAt(1, QColor(tone.end))
        painter.setBrush(fill)
        painter.drawRoundedRect(item, 3, 3)
        painter.setBrush(QColor(tone.detail))
        painter.drawRoundedRect(QRectF(left + 4, metric_y + 5, 4, 4), 2, 2)
        painter.setBrush(QColor(tone.ink))
        painter.drawRoundedRect(QRectF(left + 11, metric_y + 5, metric_span - 16, 2), 1, 1.5)

    chart = QRectF(stage.left() + (41 if rtl else 0), 57, 82, 22)
    ring = QRectF(stage.left() + (0 if rtl else 86), 57, 38, 22)
    painter.setBrush(QColor(palette.surface))
    painter.setPen(QPen(QColor(palette.line), .7))
    painter.drawRoundedRect(chart, 3, 3)
    painter.drawRoundedRect(ring, 3, 3)
    for index, color in enumerate(design.trend):
        if design.line_chart:
            line = QPainterPath(QPointF(chart.left() + 8, chart.bottom() - 5 + index))
            line.cubicTo(chart.left() + 16, chart.top() + 5 + index * 5,
                         chart.left() + 37, chart.bottom() - 11 + index * 5,
                         chart.right() - 8, chart.top() + 5 + index * 6)
            painter.setPen(QPen(QColor(color), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(line)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            for column, height in enumerate((6, 9, 8, 12, 11)):
                size = height if not index else height / 2
                painter.drawRoundedRect(QRectF(chart.left() + 8 + column * 14 + index * 4, chart.bottom() - 3 - size, 3, size), 1, 1)
    circle = QRectF(ring.center().x() - 6, ring.center().y() - 6, 12, 12)
    angle = 90
    for fraction, color in zip((175, 120, 56), design.ring):
        painter.setPen(QPen(QColor(color), 2.8))
        painter.drawArc(circle, angle * 16, -fraction * 16)
        angle -= fraction + 3
    painter.end()
    result = QIcon(pixmap)
    result.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.On)
    return result


def brandmark(palette: Palette, size: int) -> QPixmap:
    design = palette.graphic
    if design is None:
        pixmap = QPixmap(str(ASSETS / "niro.png")).scaled(
            size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(2)
        return pixmap
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 48, size / 48)
    painter.setPen(Qt.PenStyle.NoPen)
    tile = QRectF(0.5, 0.5, 47, 47)
    gradient = QLinearGradient(tile.topLeft(), tile.bottomRight())
    gradient.setColorAt(0, QColor(design.active_start))
    gradient.setColorAt(1, QColor(design.active_end))
    painter.setBrush(gradient)
    painter.drawRoundedRect(tile, 14, 14)
    painter.setBrush(QColor(design.detail))
    painter.drawEllipse(QRectF(27, 12, 9, 9))
    painter.drawRoundedRect(QRectF(25, 23, 14, 13), 5, 5)
    painter.setBrush(QColor("#fff8ef"))
    painter.drawEllipse(QRectF(12, 10, 11, 11))
    painter.drawRoundedRect(QRectF(9, 23, 17, 14), 6, 6)
    painter.end()
    return pixmap
