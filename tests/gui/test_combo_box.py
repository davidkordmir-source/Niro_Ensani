from unittest import TestCase

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from niro.widgets import ComboBox, DistinctValuesCombo


class ComboBoxWheelTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.scroll = QScrollArea()
        self.addCleanup(self.scroll.close)
        self.scroll.setWidgetResizable(True)
        form = QWidget()
        form.setMinimumHeight(900)
        fields = QVBoxLayout(form)
        self.combo = ComboBox()
        self.combo.setMaxVisibleItems(6)
        self.combo.setStyleSheet("combobox-popup: 0;")
        self.editable = DistinctValuesCombo("Choose", editable=True)
        values = tuple(f"Value {index:02}" for index in range(35))
        self.combo.addItems(values)
        self.editable.set_choices(values, values[0])
        fields.addWidget(self.combo)
        fields.addWidget(self.editable)
        fields.addStretch()
        self.scroll.setWidget(form)
        self.scroll.resize(320, 160)
        self.scroll.show()
        self.app.processEvents()

    def wheel(self, target):
        window = target.window()
        center = target.mapTo(window, target.rect().center())
        QTest.wheelEvent(window.windowHandle(), center, QPoint(0, -120))
        self.app.processEvents()

    def test_closed_fields_keep_their_values_while_scrolling_the_form(self):
        self.editable.setEditText("Custom value")
        for combo, target in ((self.combo, self.combo), (self.editable, self.editable.lineEdit())):
            with self.subTest(editable=combo.isEditable()):
                combo.setFocus()
                self.scroll.verticalScrollBar().setValue(0)
                original = combo.currentIndex(), combo.currentText()
                self.wheel(target)
                self.assertEqual((combo.currentIndex(), combo.currentText()), original)
                self.assertGreater(self.scroll.verticalScrollBar().value(), 0)

    def test_open_list_scrolls_and_a_click_commits_the_option(self):
        self.combo.showPopup()
        self.addCleanup(self.combo.hidePopup)
        self.app.processEvents()
        view = self.combo.view()
        self.wheel(view.viewport())
        self.assertGreater(view.verticalScrollBar().value(), 0)
        self.assertEqual(self.combo.currentIndex(), 0)
        point = view.viewport().rect().center()
        choice = view.indexAt(point)
        self.assertGreater(choice.row(), 0)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(self.combo.currentIndex(), choice.row())
