"""Reusable native widgets for the personnel workspace."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import (
    QAbstractListModel, QAbstractTableModel, QModelIndex, QPointF, QRectF, QSize, QSignalBlocker, Qt, Signal,
)
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QTableView, QVBoxLayout, QWidget,
)

from .filters import NO_VALUE, OPERATORS, Rule
from .locale import number, translate
from .theme import ASSETS
from .visuals import artwork_width, brandmark, draw_banner


def label(text: str = "", style: str = "", wrap: bool = False) -> QLabel:
    item = QLabel()
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setText(text)
    item.setObjectName(style)
    item.setWordWrap(wrap)
    return item


def button(text: str, style: str = "") -> QPushButton:
    item = QPushButton(text)
    item.setObjectName(style)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    return item


def card() -> tuple[QFrame, QVBoxLayout]:
    wrapper = QFrame()
    wrapper.setObjectName("surface")
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(17, 14, 17, 14)
    layout.setSpacing(10)
    return wrapper, layout


def row_layout(spacing: int = 9) -> QHBoxLayout:
    line = QHBoxLayout()
    line.setSpacing(spacing)
    return line


def section_header(title: str, lead: str, action: QPushButton | None = None) -> QWidget:
    wrapper = QWidget()
    line = row_layout()
    wrapper.setLayout(line)
    line.setContentsMargins(0, 0, 0, 0)
    left = QVBoxLayout()
    left.setSpacing(1)
    left.addWidget(label(title, "pageTitle"))
    left.addWidget(label(lead, "muted", True))
    line.addLayout(left, 1)
    if action is not None:
        line.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
    return wrapper


def field(layout, title: str, widget, stretch=1):
    stack = QVBoxLayout()
    stack.setSpacing(4)
    text = label(title, "smallMuted")
    text.setBuddy(widget)
    widget.setAccessibleName(title)
    stack.addWidget(text)
    stack.addWidget(widget)
    layout.addLayout(stack, stretch)
    return stack


class ComboBox(QComboBox):
    """Leave wheel gestures to the page or the open popup list."""

    def wheelEvent(self, event):
        event.ignore()


def fill_options(combo: QComboBox, options, value=None) -> None:
    combo.blockSignals(True)
    combo.clear()
    for key, text in options:
        combo.addItem(text, key)
    index = combo.findData(value) if value is not None else -1
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


class ElidedLabel(QLabel):
    def __init__(self, style=""):
        super().__init__()
        self.full_text = ""
        self.setObjectName(style)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):
        self.full_text = text
        self.setToolTip(escaped_tooltip(text))
        self._fit()

    def _fit(self):
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideMiddle, self.contentsRect().width()))

    def resizeEvent(self, event):
        self._fit()
        super().resizeEvent(event)


def escaped_tooltip(text: str) -> str:
    return "<qt>" + escape(text).replace("\n", "<br>") + "</qt>"


def icon(name: str, color: str, size: int = 20) -> QIcon:
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.GlobalColor.transparent)
    pix.setDevicePixelRatio(2)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

    def line(*points):
        painter.drawPolyline(QPolygonF([QPointF(*point) for point in points]))

    def rect(x, y, w, h, r=2):
        painter.drawRoundedRect(QRectF(x, y, w, h), r, r)

    def dot(x, y, r):
        painter.drawEllipse(QPointF(x, y), r, r)

    def curve(*points):
        path = QPainterPath(QPointF(*points[0]))
        path.cubicTo(*(QPointF(*point) for point in points[1:]))
        painter.drawPath(path)

    if name == "people":
        dot(9, 7, 3)
        curve((3, 19), (2.5, 12), (15.5, 12), (15, 19))
        curve((16, 4.5), (21.5, 3), (22, 11), (16.5, 10.5))
        curve((17, 13), (20, 13), (22, 15), (21, 19))
    elif name == "dashboard":
        rect(3.5, 3.5, 7, 9)
        rect(13.5, 3.5, 7, 6)
        rect(3.5, 15.5, 7, 5)
        rect(13.5, 12.5, 7, 8)
    elif name == "reports":
        rect(4, 3, 16, 18)
        line((8, 16), (8, 13))
        line((12, 16), (12, 10))
        line((16, 16), (16, 7))
    elif name == "analytics":
        line((3, 4), (3, 20), (21, 20))
        line((6, 15), (10, 10), (14, 12), (20, 5))
        dot(20, 5, 1)
    elif name == "attendance":
        rect(4, 5, 16, 16)
        line((8, 3), (8, 7))
        line((16, 3), (16, 7))
        line((4, 9), (20, 9))
        line((8, 15), (11, 18), (16, 12))
    elif name == "search":
        dot(10, 10, 5.5)
        line((14.2, 14.2), (20, 20))
    elif name == "settings":
        line((4, 6), (20, 6))
        line((4, 12), (20, 12))
        line((4, 18), (20, 18))
        painter.setBrush(QColor(color))
        for x, y in ((9, 6), (15, 12), (10, 18)):
            dot(x, y, 1.8)
    elif name == "excel":
        rect(3.5, 4, 17, 16)
        line((4, 9), (20, 9))
        line((9, 9), (9, 20))
        line((15, 9), (15, 20))
        line((4, 14), (20, 14))
    elif name == "shield":
        line((12, 3), (20, 6), (19, 15), (15, 20), (12, 21), (9, 20), (5, 15), (4, 6), (12, 3))
        line((8, 12), (11, 15), (16, 9))
    elif name == "edit":
        line((5, 19), (9, 18), (20, 7), (17, 4), (6, 15), (5, 19))
        line((14, 7), (17, 10))
    elif name == "delete":
        line((4, 6), (20, 6))
        line((9, 6), (9, 3), (15, 3), (15, 6))
        line((6, 6), (7, 20), (17, 20), (18, 6))
        line((10, 10), (10, 16))
        line((14, 10), (14, 16))
    elif name == "download":
        line((12, 3), (12, 15))
        line((7, 10), (12, 15), (17, 10))
        line((4, 15), (4, 20), (20, 20), (20, 15))
    elif name == "plus":
        line((5, 12), (19, 12))
        line((12, 5), (12, 19))
    elif name == "departure":
        line((11, 4), (5, 4), (5, 20), (11, 20))
        line((10, 12), (20, 12))
        line((16, 8), (20, 12), (16, 16))
    elif name == "close":
        line((7, 7), (17, 17))
        line((17, 7), (7, 17))
    elif name == "check":
        line((4, 12), (9, 17), (20, 6))
    elif name == "offline":
        dot(12, 12, 8)
        dot(12, 12, 2.2)
    painter.end()
    return QIcon(pix)


class Hero(QWidget):
    def __init__(self, palette):
        super().__init__()
        self.palette = palette
        self.setMinimumHeight(134)
        stack = self.stack = QVBoxLayout(self)
        stack.setSpacing(2)
        self.tag = label("", "heroTag")
        self.title = ElidedLabel("heroTitle")
        self.subtitle = ElidedLabel("heroMeta")
        stack.addStretch()
        stack.addWidget(self.tag)
        stack.addWidget(self.title)
        stack.addWidget(self.subtitle)
        stack.addStretch()
        self._inset()

    def recolor(self, palette):
        self.palette = palette
        self._inset()
        self.update()

    def _inset(self):
        space = round(artwork_width(QRectF(self.rect()))) if self.palette.graphic else 0
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        self.stack.setContentsMargins(24 + (space if rtl else 0), 13, 24 + (0 if rtl else space), 13)

    def resizeEvent(self, event):
        self._inset()
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(0, 0, -1, -1)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        if self.palette.graphic:
            draw_banner(painter, bounds, self.palette, rtl)
            return
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 17, 17)
        painter.setClipPath(clip)
        tone = QLinearGradient(bounds.topRight() if rtl else bounds.topLeft(), bounds.bottomLeft() if rtl else bounds.bottomRight())
        tone.setColorAt(0, QColor(self.palette.hero_start))
        tone.setColorAt(1, QColor(self.palette.hero_end))
        painter.fillRect(bounds, tone)
        painter.save()
        painter.translate(0 if rtl else self.width(), 0)
        if not rtl:
            painter.scale(-1, 1)
        painter.setPen(Qt.PenStyle.NoPen)
        fill = QColor(self.palette.mint)
        fill.setAlpha(23)
        painter.setBrush(fill)
        painter.drawEllipse(QRectF(-114, -71, 307, 227))
        fill.setAlpha(29)
        painter.setBrush(fill)
        painter.drawEllipse(QRectF(-74, 63, 218, 144))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(11):
            line_color = QColor(self.palette.hero_secondary)
            line_color.setAlpha(58 - i * 3)
            painter.setPen(QPen(line_color, 1.1))
            path = QPainterPath(QPointF(-20, 24 + i * 8))
            path.cubicTo(58, -42 + i * 12, 106, 105 + i * 8, 190, 61 + i * 7)
            path.cubicTo(213, 47 + i * 7, 228, 18 + i * 8, 257, 28 + i * 8)
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, r in ((36, 33, 2.4), (109, 25, 1.6), (167, 104, 2), (211, 40, 1.4), (70, 92, 1.4)):
            painter.setBrush(QColor(self.palette.mint))
            painter.drawEllipse(QPointF(x, y), r, r)
        painter.restore()


class Metric(QFrame):
    def __init__(self, title: str, name: str, language: str):
        super().__init__()
        self.name = name
        self.setObjectName("surface")
        line = QHBoxLayout(self)
        line.setContentsMargins(15, 8, 15, 7)
        line.setSpacing(10)
        self.icon_label = label()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedSize(40, 40)
        line.addWidget(self.icon_label)
        body = QVBoxLayout()
        body.setSpacing(0)
        self.value = label("—", "metricValue")
        side = Qt.AlignmentFlag.AlignRight if language == "fa" else Qt.AlignmentFlag.AlignLeft
        self.value.setAlignment(side | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter)
        body.addWidget(self.value)
        body.addWidget(label(title, "smallMuted", True))
        line.addLayout(body, 1)

    def recolor(self, palette):
        self.icon_label.setStyleSheet(f"background:{palette.soft}; border-radius:11px;")
        self.icon_label.setPixmap(icon(self.name, palette.accent, 20).pixmap(20, 20))


class TableModel(QAbstractTableModel):
    """Virtualized table access, with no widget or item allocated per workbook cell."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns = ()
        self.rows = ()
        self.positions = ()
        self.language = "fa"

    def show_records(self, columns, rows, language, positions=None):
        self.beginResetModel()
        self.columns = columns
        self.rows = rows
        self.language = language
        self.positions = tuple(range(len(columns))) if positions is None else positions
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows) and 0 <= index.column() < len(self.positions)):
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
            return None
        value = self.rows[index.row()].values[self.positions[index.column()]]
        return escaped_tooltip(value) if role == Qt.ItemDataRole.ToolTipRole else value

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            title = self.columns[section].title(self.language)
            if role == Qt.ItemDataRole.DisplayRole:
                return title
            if role == Qt.ItemDataRole.ToolTipRole:
                return escaped_tooltip(title)
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return number(self.language, section + 1)
        return None


class TablePanel(QStackedWidget):
    def __init__(self):
        super().__init__()
        self.view = QTableView()
        self.model = TableModel(self.view)
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.view.setWordWrap(False)
        self.view.setShowGrid(False)
        self.view.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.view.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.view.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.view.horizontalHeader().setDefaultSectionSize(136)
        self.view.horizontalHeader().setMinimumSectionSize(88)
        self.view.verticalHeader().setDefaultSectionSize(40)
        self.view.verticalHeader().setMinimumWidth(37)
        self.addWidget(self.view)
        panel, content = card()
        self.empty_icon = label()
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty = label("", "sectionTitle", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_action = button("", "primary")
        content.addStretch()
        content.addWidget(self.empty_icon)
        content.addWidget(self.empty)
        content.addWidget(self.empty_action, 0, Qt.AlignmentFlag.AlignHCenter)
        content.addStretch()
        self.addWidget(panel)
        self.setMinimumHeight(190)

    def recolor(self, palette):
        self.empty_icon.setPixmap(icon("excel", palette.accent, 35).pixmap(35, 35))

    def present(self, columns, records, language, empty, allow_link=False, positions=None):
        stable = bool(records and self.model.rows and self.model.columns == columns)
        previous_horizontal = self.view.horizontalScrollBar().value()
        previous_vertical = self.view.verticalScrollBar().value()
        self.model.show_records(columns, records, language, positions)
        self.empty.setText(empty)
        self.empty_action.setText(translate(language, "open_source"))
        self.empty_action.setVisible(allow_link)
        self.setCurrentIndex(0 if records and columns else 1)
        if stable:
            self.view.horizontalScrollBar().setValue(previous_horizontal)
            self.view.verticalScrollBar().setValue(previous_vertical)


class _ValueListModel(QAbstractListModel):
    def __init__(self, parent, leading):
        super().__init__(parent)
        self.leading = leading
        self.values = ()
        self.extra = None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.leading) + len(self.values) + int(self.extra is not None)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < self.rowCount():
            return None
        row = index.row()
        if row < len(self.leading):
            value, display = self.leading[row]
        else:
            offset = row - len(self.leading)
            value = self.values[offset] if offset < len(self.values) else self.extra
            display = value
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return display
        if role == Qt.ItemDataRole.UserRole:
            return value
        return None

    def set_values(self, values, extra):
        if values is self.values and extra == self.extra:
            return
        self.beginResetModel()
        self.values = values
        self.extra = extra
        self.endResetModel()

    def index_of(self, value, values):
        for index, (leading_value, _) in enumerate(self.leading):
            if leading_value == value:
                return index
        try:
            return len(self.leading) + values.index(value)
        except ValueError:
            return -1


class DistinctValuesCombo(ComboBox):
    """Use an immutable value sequence directly, even for columns with many distinct values."""

    choiceChanged = Signal()

    def __init__(self, placeholder, editable=False, clear_option=False, blank_label=None):
        super().__init__()
        self.placeholder = placeholder
        leading = [(None, placeholder)] if clear_option else []
        if blank_label is not None:
            leading.append(("", blank_label))
        self.value_model = _ValueListModel(self, leading)
        self.setModel(self.value_model)
        self.setAccessibleName(placeholder)
        self.setPlaceholderText(placeholder)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMaxVisibleItems(14)
        self.view().setUniformItemSizes(True)
        self.setStyleSheet("combobox-popup: 0;")
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.set_editable_choice(editable)
        self.currentIndexChanged.connect(self.choiceChanged)
        self.editTextChanged.connect(self.choiceChanged)

    def set_editable_choice(self, editable):
        if self.isEditable() == editable:
            return
        with QSignalBlocker(self):
            previous = self.choice()
            self.setEditable(editable)
            if editable:
                self.lineEdit().setPlaceholderText(self.placeholder)
                self.lineEdit().setClearButtonEnabled(True)
                self.lineEdit().setStyleSheet("background: transparent; border: 0; padding: 0;")
            self.set_choices(self.value_model.values, previous)

    def set_choices(self, values, selected=None):
        if not isinstance(values, tuple):
            values = tuple(values)
        with QSignalBlocker(self):
            index = self.value_model.index_of(selected, values)
            extra = selected if selected not in (None, "") and index < 0 and not self.isEditable() else None
            self.value_model.set_values(values, extra)
            if extra is not None:
                index = self.value_model.rowCount() - 1
            self.setCurrentIndex(index)
            if self.isEditable() and index < 0:
                self.setEditText(selected or "")

    def choice(self):
        if self.isEditable() and self.currentText() != self.itemText(self.currentIndex()):
            return self.currentText() or None
        return self.currentData()


class RuleEditor(QFrame):
    changed = Signal()
    removed = Signal()

    def __init__(self, language, columns, rule=None, distinct_values=None):
        super().__init__()
        self.setObjectName("rule")
        t = lambda key: translate(language, key)
        self.columns = ComboBox()
        self.columns.setAccessibleName(t("report_columns"))
        self.columns.setMinimumWidth(105)
        self.operators = ComboBox()
        self.operators.setAccessibleName(t("rules"))
        self.distinct_values = distinct_values if distinct_values is not None else {}
        self.value = DistinctValuesCombo(t("rule_value"))
        self.value.setMinimumWidth(90)
        self.upper = DistinctValuesCombo(t("rule_upper"), editable=True)
        self.upper.setMinimumWidth(88)
        self.remove = button("")
        self.remove.setFixedWidth(36)
        self.remove.setToolTip(t("remove_rule"))
        self.remove.setAccessibleName(t("remove_rule"))
        line = QHBoxLayout(self)
        line.setContentsMargins(7, 5, 7, 5)
        line.addWidget(self.columns, 2)
        line.addWidget(self.operators, 2)
        line.addWidget(self.value, 2)
        line.addWidget(self.upper, 1)
        line.addWidget(self.remove)
        self.set_columns(columns, language, rule.key if rule else None)
        fill_options(self.operators, ((op, t(op)) for op in OPERATORS), rule.operator if rule else "equals")
        self._operator()
        self._choices(rule.value if rule else None, rule.upper if rule else None)
        self.operators.currentIndexChanged.connect(self._operator)
        self.columns.currentIndexChanged.connect(self._column_changed)
        self.value.choiceChanged.connect(self.changed)
        self.upper.choiceChanged.connect(self.changed)
        self.remove.clicked.connect(self.removed)

    def set_columns(self, columns, language, selected=None):
        keep = selected or self.columns.currentData()
        options = [(col.key, col.title(language)) for col in columns]
        if keep and keep not in {key for key, _ in options}:
            options.insert(0, (keep, translate(language, "missing_column")))
        fill_options(self.columns, options, keep)

    def set_distinct_values(self, values):
        if values is self.distinct_values:
            return
        self.distinct_values = values
        self._choices(self.value.choice(), self.upper.choice())

    def _choices(self, value=None, upper=None):
        values = self.distinct_values.get(self.columns.currentData(), ())
        self.value.set_choices(values, value)
        self.upper.set_choices(values, upper)

    def _column_changed(self):
        self._choices()
        self.changed.emit()

    def recolor(self, palette):
        self.remove.setIcon(icon("close", palette.muted, 17))

    def _operator(self):
        current = self.operators.currentData()
        self.value.set_editable_choice(current not in ("equals", "not_equals"))
        self.value.setVisible(current not in NO_VALUE)
        self.upper.setVisible(current == "between")
        self.changed.emit()

    def rule(self):
        return Rule(self.columns.currentData(), self.operators.currentData(), self.value.choice() or "", self.upper.choice() or "")


class ColumnsDialog(QDialog):
    def __init__(self, parent, language, columns, selected, *, title=None):
        super().__init__(parent)
        t = lambda key: translate(language, key)
        title = title or t("choose_columns")
        self.setWindowTitle(title)
        self.resize(430, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(label(title, "sectionTitle"))
        self.list = QListWidget()
        for column in columns:
            item = QListWidgetItem(column.title(language), self.list)
            item.setData(Qt.ItemDataRole.UserRole, column.key)
            item.setCheckState(Qt.CheckState.Checked if column.key in selected else Qt.CheckState.Unchecked)
        controls = row_layout()
        all_fields = button(t("all"))
        none = button(t("none"))
        controls.addWidget(all_fields)
        controls.addWidget(none)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(self.list, 1)
        foot = row_layout()
        foot.addStretch()
        cancel = button(t("cancel"))
        apply = button(t("apply"), "primary")
        foot.addWidget(cancel)
        foot.addWidget(apply)
        layout.addLayout(foot)
        all_fields.clicked.connect(lambda: self._choose(Qt.CheckState.Checked))
        none.clicked.connect(lambda: self._choose(Qt.CheckState.Unchecked))
        cancel.clicked.connect(self.reject)
        apply.clicked.connect(self.accept)

    def _choose(self, value):
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(value)

    def keys(self):
        return {self.list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.list.count())
                if self.list.item(index).checkState() == Qt.CheckState.Checked}


class RecordDialog(QDialog):
    submitted = Signal(object)

    def __init__(self, parent, language, columns, record, operation, choices=None, *, palette=None):
        from .record_fields import DEFAULT_COMBO_COLUMNS, DateField, catalog_values, is_date_column
        from .workbook import label_key

        super().__init__(parent)
        self.t = lambda key: translate(language, key)
        self.operation = operation
        self.record = record
        self.editors = {}
        self.original = {}
        self.date_fields = []
        self.busy = False
        self.header = None
        title = self.t({"add": "new_person", "edit": "edit_person", "delete": "confirm_delete"}[operation])
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(str(ASSETS / "niro.png")))
        if operation != "delete":
            self.setWindowFlags(
                Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
                | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint
            )
        width, height = (500, 260) if operation == "delete" else (795, 662)
        self.resize(width, height)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(21, 19, 21, 19)
        if operation == "delete":
            layout.addWidget(label(title, "pageTitle"))
            layout.addWidget(label(self.t("delete_lead"), "muted", True))
        else:
            self._add_header(layout, title, language)
        if operation != "delete":
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget()
            content.setObjectName("scrollContent")
            body = QVBoxLayout(content)
            body.setContentsMargins(0, 0, 8, 2)
            body.setSpacing(10)
            formulas = record.source.formulas if record and record.source else ()
            for index in range(0, len(columns), 2):
                line = row_layout(12)
                for position, column in enumerate(columns[index:index + 2], index):
                    original = record.values[position] if record else ""
                    is_formula = column.key in formulas
                    configured = column.key in choices if choices is not None else label_key(column.label) in DEFAULT_COMBO_COLUMNS
                    combo = None
                    if configured and not is_formula:
                        placeholder = "انتخاب یا وارد کنید" if language == "fa" else "Select or enter a value"
                        combo = DistinctValuesCombo(placeholder, editable=True)
                        combo.lineEdit().setMaxLength(32767)
                        observed = choices.get(column.key, ()) if choices is not None else ()
                        combo.set_choices(catalog_values(column.label, observed), original)
                    if is_date_column(column.label):
                        editor = DateField(original, language, combo)
                        self.date_fields.append(editor)
                    elif combo is not None:
                        editor = combo
                    elif column.key in ("توضیحات", "notes") or "\n" in original:
                        editor = QPlainTextEdit(original)
                        editor.setMaximumHeight(85)
                    else:
                        editor = QLineEdit(original)
                        editor.setMaxLength(32767)
                    if is_formula:
                        editor.setReadOnly(True)
                        editor.setToolTip(self.t("formula"))
                    self.editors[column.key] = editor
                    self.original[column.key] = original
                    field(line, column.title(language), editor)
                body.addLayout(line)
            body.addStretch()
            scroll.setWidget(content)
            layout.addWidget(scroll, 1)
        self.scope = ComboBox()
        choices = [("local", self.t("scope_local"))]
        if operation != "delete" or (record and record.source):
            choices.append(("excel", self.t("scope_excel")))
        fill_options(self.scope, choices, "local")
        block = row_layout()
        field(block, self.t("scope"), self.scope)
        block.addStretch(1)
        layout.addLayout(block)
        self.scope_hint = label("", "smallMuted", True)
        self.error = label("", "warning", True)
        layout.addWidget(self.scope_hint)
        layout.addWidget(self.error)
        self.error.hide()
        footer = row_layout()
        footer.addStretch()
        self.cancel = button(self.t("cancel"))
        self.save = button(self.t("delete" if operation == "delete" else "save"), "primary")
        self.save.setDefault(True)
        footer.addWidget(self.cancel)
        footer.addWidget(self.save)
        layout.addLayout(footer)
        self.scope.currentIndexChanged.connect(self._scope_text)
        self.cancel.clicked.connect(self.reject)
        self.save.clicked.connect(self._submit)
        self._scope_text()
        if palette is not None:
            self.recolor(palette)

    def _add_header(self, layout, title, language):
        banner, banner_layout = card()
        banner.setObjectName("recordHeader")
        self.header = banner
        direction = Qt.LayoutDirection.RightToLeft if language == "fa" else Qt.LayoutDirection.LeftToRight
        banner.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        line = row_layout(14)
        banner_layout.addLayout(line)
        mark = self.header_mark = label()
        ratio = mark.devicePixelRatioF()
        pixels = round(55 * ratio)
        logo = QPixmap(str(ASSETS / "niro.png")).scaled(
            pixels, pixels, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        logo.setDevicePixelRatio(ratio)
        mark.setPixmap(logo)
        line.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        words = QWidget()
        words.setLayoutDirection(direction)
        copy = QVBoxLayout(words)
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(2)
        copy.addWidget(label(title, "pageTitle"))
        copy.addWidget(label(self.t("record_lead"), "muted", True))
        line.addWidget(words, 1)
        layout.addWidget(banner)

    def recolor(self, palette):
        if self.header is not None:
            self.header_mark.setPixmap(brandmark(palette, 55))
        for editor in self.date_fields:
            editor.recolor(palette.text)

    def _scope_text(self):
        excel = self.scope.currentData() == "excel"
        if self.operation == "delete":
            key = "scope_delete_excel" if excel else "scope_delete_local" if self.record.source else "scope_delete_addition"
        elif self.operation == "add" and excel:
            key = "scope_add_excel"
        else:
            key = "scope_edit_excel" if excel else "scope_edit_local"
        self.scope_hint.setText(self.t(key))

    def set_busy(self, enabled):
        self.busy = enabled
        self.save.setEnabled(not enabled)
        self.cancel.setEnabled(not enabled)
        self.scope.setEnabled(not enabled)
        for editor in self.editors.values():
            editor.setEnabled(not enabled)
        self.save.setText(self.t("working" if enabled else "delete" if self.operation == "delete" else "save"))

    def failed(self, text):
        self.set_busy(False)
        self.error.setText(text)
        self.error.show()

    def reject(self):
        if not self.busy:
            super().reject()

    def _submit(self):
        if self.busy:
            return
        target = self.scope.currentData()
        def value(widget):
            if isinstance(widget, QPlainTextEdit):
                return widget.toPlainText()
            if isinstance(widget, DistinctValuesCombo):
                return widget.choice() or ""
            return widget.text()

        values = {key: value(widget) for key, widget in self.editors.items()}
        if self.operation != "delete" and not any(value.strip() for value in values.values()):
            self.failed(self.t("need_values"))
            return
        if self.operation == "edit" and not (target == "excel" and not self.record.source):
            values = {key: text for key, text in values.items() if self.original[key] != text}
            if not values:
                self.failed(self.t("no_edits"))
                return
        if self.operation == "add" or (self.operation == "edit" and target == "excel" and not self.record.source):
            values = {key: value for key, value in values.items() if value.strip()}
        self.error.hide()
        self.set_busy(True)
        self.submitted.emit((target, values))
