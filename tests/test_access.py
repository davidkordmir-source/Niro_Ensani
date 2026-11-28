from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import xlsxwriter

from niro.access import Access, Account, visible_view
from niro.state import Store, namespace
from niro.workbook import WorkbookError, read_workbook, update_workbook_cells


class LocalAccessTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root / "data")
        self.book = self.root / "test.xlsx"
        with xlsxwriter.Workbook(self.book) as file:
            page = file.add_worksheet("Roster")
            page.write_row(0, 0, ("Name", "National ID", "Custom private", "Department"))
            page.write_row(1, 0, ("Test staff", "0012345678", "SHOULD-WITHHOLD", "Finance"))
            page.write_row(2, 0, ("Second staff", "0034567890", "CLASSIFIED", "HR"))
        self.view = self.store.view(read_workbook(self.book))

    def test_passwords_role_changes_and_reviewable_projection(self):
        access = Access(self.store)
        owner = access.save(Account(), "Owner", "owner-test-password", "reader", False, False)
        self.assertTrue(owner.manages)
        editor = access.save(owner, "Desk", "desk-test-password", "editor", False, True)
        self.assertIsNone(access.authenticate("Desk", "wrong"))
        self.assertEqual(access.authenticate("desk", "desk-test-password"), editor)
        self.assertNotIn(b"desk-test-password", self.store.path.read_bytes())
        private = self.view.snapshot.columns[2].key
        masked = visible_view(self.view, editor, {private})
        self.assertEqual(tuple(col.label for col in masked.snapshot.columns), ("Name", "Department"))
        self.assertEqual(masked.records[0].source.values, ("Test staff", "Finance"))
        self.assertNotIn("CLASSIFIED", repr(masked))
        self.assertTrue(editor.edits and editor.exports and not editor.sees_sensitive)
        with self.assertRaisesRegex(WorkbookError, "access_denied"):
            access.save(editor, "Ghost", "test-long-password", "admin", True, True)
        with self.assertRaisesRegex(WorkbookError, "last_admin"):
            access.save(owner, owner.name, "", "reader", True, False, key=owner.key)
        access.remove(owner, editor)
        self.assertIsNone(access.authenticate("Desk", "desk-test-password"))
        self.assertIn("account_removed", tuple(entry[2] for entry in self.store.audit_entries(namespace(self.view.snapshot))))

    def test_batch_change_and_dated_recovery_are_atomic(self):
        initial = self.book.read_bytes()
        snapshot = self.view.snapshot
        field = snapshot.columns[-1].key
        original_backup = lambda data: self.store.backup(self.book, data)
        updated = update_workbook_cells(snapshot, {2: {field: "Sales"}, 3: {field: "Sales"}}, original_backup)
        self.assertEqual([record.values[-1] for record in updated.rows], ["Sales", "Sales"])
        changed = self.book.read_bytes()
        with self.assertRaisesRegex(WorkbookError, "source_changed"):
            update_workbook_cells(snapshot, {2: {field: "Blocked"}}, original_backup)
        self.assertEqual(self.book.read_bytes(), changed)
        with self.assertRaisesRegex(WorkbookError, "source_changed"):
            update_workbook_cells(updated, {2: {field: "Blocked"}, 550: {field: "Missing"}}, original_backup)
        self.assertEqual(self.book.read_bytes(), changed)
        versions = self.store.versions(self.book)
        self.assertEqual(len(versions), 1)
        recovery = self.root / "recovered.xlsx"
        self.store.copy_version(self.book, versions[0], recovery)
        self.assertEqual(recovery.read_bytes(), initial)
