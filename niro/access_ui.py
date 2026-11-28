"""Offline sign-in, role settings, and activity history."""

from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit

from .access import Account, ROLES, SENSITIVE
from .locale import STRINGS, translate
from .office_ui import OfficeDialog, RowsTable, actions, confirm, form_field, popup
from .widgets import ColumnsDialog, ComboBox, button, fill_options, label
from .workbook import normalize


def sign_in(access, language):
    if not access.accounts():
        return Account()
    t = lambda key: translate(language, key)
    dialog, layout = popup(None, t("signin"), 455, 290)
    direction = Qt.LayoutDirection.RightToLeft if language == "fa" else Qt.LayoutDirection.LeftToRight
    dialog.setLayoutDirection(direction)
    layout.addWidget(label(t("signin"), "pageTitle", True))
    user = form_field(layout, t("login_name"), QLineEdit())
    password = form_field(layout, t("password"), QLineEdit())
    password.setEchoMode(QLineEdit.EchoMode.Password)
    notice = label("", "warning", True)
    layout.addWidget(notice)
    go, cancel = button(t("signin"), "primary"), button(t("cancel"))
    go.setDefault(True)
    actions(layout, go, cancel)
    account = None

    def accept():
        nonlocal account
        account = access.authenticate(user.text(), password.text())
        password.clear()
        if account:
            dialog.accept()
        else:
            notice.setText(t("login_failed"))
            password.setFocus()

    go.clicked.connect(accept)
    password.returnPressed.connect(accept)
    cancel.clicked.connect(dialog.reject)
    dialog.exec()
    return account


class AccountsDialog(OfficeDialog):
    def __init__(self, window):
        super().__init__(window, "access", (720, 570))
        self.content.addWidget(label(self.t("access_intro"), "muted", True))
        self.table = RowsTable(map(self.t, ("login_name", "role", "read_private", "allow_export")))
        self.content.addWidget(self.table, 1)
        new = button(self.t("add"), "primary")
        change = button(self.t("edit"))
        remove = button(self.t("delete"), "danger")
        private = button(self.t("private_fields"))
        private.setEnabled(bool(self.view and self.view.snapshot.columns))
        actions(self.content, new, change, remove)
        actions(self.content, private)
        new.clicked.connect(lambda: self.edit(None))
        change.clicked.connect(lambda: self.edit(self.table.chosen(self.accounts)))
        self.table.doubleClicked.connect(lambda: self.edit(self.table.chosen(self.accounts)))
        remove.clicked.connect(self.remove)
        private.clicked.connect(self.fields)
        self.accounts = ()
        self.refresh()

    def refresh(self):
        self.accounts = self.window.access.accounts()
        self.table.replace((account.name, self.t("role_" + account.role), self.yes_no(account.sees_sensitive), self.yes_no(account.exports))
                           for account in self.accounts)

    def yes_no(self, value):
        return self.t("yes_value" if value else "no_value")

    def edit(self, account):
        if not self.account.manages:
            return
        box, layout = popup(self, self.t("access"), 490, 390)
        username = form_field(layout, self.t("login_name"), QLineEdit(account.name if account else ""))
        username.setReadOnly(bool(account))
        password = form_field(layout, self.t("password"), QLineEdit())
        password.setEchoMode(QLineEdit.EchoMode.Password)
        if account:
            password.setPlaceholderText(self.t("password_keep"))
        roles = ComboBox()
        fill_options(roles, ((key, self.t("role_" + key)) for key in ROLES),
                     account.role if account else "reader" if self.accounts else "admin")
        roles.setEnabled(bool(self.accounts and (not account or account.key != self.account.key)))
        form_field(layout, self.t("role"), roles)
        private, export = QCheckBox(self.t("read_private")), QCheckBox(self.t("allow_export"))
        private.setChecked(bool(account and account.sees_sensitive))
        export.setChecked(bool(account and account.exports))
        layout.addWidget(private)
        layout.addWidget(export)

        def permissions():
            fixed = roles.currentData() == "admin"
            private.setEnabled(not fixed)
            export.setEnabled(not fixed)

        roles.currentIndexChanged.connect(permissions)
        permissions()
        notice = label("", "warning", True)
        layout.addWidget(notice)
        save, cancel = button(self.t("save"), "primary"), button(self.t("cancel"))
        actions(layout, save, cancel)
        cancel.clicked.connect(box.reject)

        def commit():
            try:
                result = self.window.access.save(self.account, username.text(), password.text(), roles.currentData(),
                                                 private.isChecked(), export.isChecked(), key=account.key if account else None)
            except Exception as error:
                notice.setText(self.window._reason(error))
                return
            if not self.account.key or result.key == self.account.key:
                self.window.account = self.account = result
            box.accept()

        save.clicked.connect(commit)
        if box.exec() == box.DialogCode.Accepted:
            self.refresh()

    def remove(self):
        selected = self.table.chosen(self.accounts)
        if selected and selected.key != self.account.key and self.account.manages and confirm(
                self, self.t("delete"), self.t("confirm_remove")):
            self.run(lambda: self.window.access.remove(self.account, selected), lambda _: self.refresh())

    def fields(self):
        if not self.view or not self.current():
            return
        cols = tuple(col for col in self.view.snapshot.columns if normalize(col.label) not in SENSITIVE)
        current = self.window.private_fields(self.view)
        dialog = ColumnsDialog(self, self.language, cols, current, title=self.t("private_fields"),
                               search_placeholder=self.t("search_columns"))
        if dialog.exec() == QDialog.DialogCode.Accepted and self.current():
            self.window.change_private_fields(dialog.keys())


class AuditDialog(OfficeDialog):
    def __init__(self, window):
        super().__init__(window, "audit")
        self.table = RowsTable(map(self.t, ("form_date", "login_name", "field_kind", "field_note")))
        self.content.addWidget(self.table, 1)
        QTimer.singleShot(0, lambda: self.run(lambda: window.store.audit_entries(self.scope), self.present))

    def present(self, entries):
        self.table.replace((datetime.fromisoformat(when).astimezone().isoformat(sep=" ", timespec="seconds"),
                            actor, self.t(action) if action in STRINGS else action, detail)
                           for when, actor, action, detail in entries)
