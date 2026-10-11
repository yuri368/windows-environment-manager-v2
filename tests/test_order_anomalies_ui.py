"""Existing list anomalies remain exact while the ordering UI names them."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from envmanager.engine import calculate
from envmanager.models import PlanChange, RegistryState, VariableSpec
from envmanager.theme import apply_theme
from envmanager.ui import OrderDialog


class OrderAnomaliesUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def dialog(self, value, selected="X", name="Path"):
        spec = VariableSpec("App", "Default", name, "System", "append", True, selected)
        before = RegistryState(name, "System", True, value, 2)
        calculation = calculate(spec, selected, before)
        dialog = OrderDialog(PlanChange(before, calculation.result, "append", selected, calculation=calculation))
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return spec, before, dialog

    def test_existing_trailing_empty_is_named_and_moved_without_inserting_items(self):
        spec, before, dialog = self.dialog("A;B;")
        self.assertEqual(dialog.order(), ("A", "B", "", "X"))
        empty = dialog.list.item(2)
        self.assertIn("原始值中的空段", empty.text())
        self.assertEqual(empty.data(Qt.ItemDataRole.UserRole), "")
        self.assertIn('""', empty.toolTip())
        self.assertIn("1 个空项", dialog.existing_notice.text())
        dialog.list.setCurrentRow(2)
        dialog._move_to(1)
        confirmed = calculate(spec, "X", before, dialog.order())
        self.assertEqual(confirmed.result.value, "A;;B;X")
        self.assertEqual(dialog.order().count(""), 1)
        dialog._restore()
        self.assertEqual(dialog.order(), ("A", "B", "", "X"))
        self.assertIn("原始值中的空段", dialog.list.item(2).text())

    def test_repeated_empty_and_whitespace_items_keep_exact_data(self):
        spec, before, dialog = self.dialog(";A;;  ;\t;B;")
        self.assertEqual(dialog.order(), ("", "A", "", "  ", "\t", "B", "", "X"))
        self.assertIn("3 个空项", dialog.existing_notice.text())
        self.assertIn("2 个纯空白项", dialog.existing_notice.text())
        self.assertIn("纯空白项", dialog.list.item(3).text())
        self.assertEqual(dialog.list.item(3).data(Qt.ItemDataRole.UserRole), "  ")
        self.assertEqual(dialog.list.item(4).data(Qt.ItemDataRole.UserRole), "\t")
        self.assertEqual(calculate(spec, "X", before, dialog.order()).result.value, ";A;;  ;\t;B;;X")

    def test_empty_existing_value_and_empty_input_do_not_create_a_list_item(self):
        spec, before, dialog = self.dialog("", selected="")
        self.assertEqual(dialog.list.count(), 0)
        self.assertEqual(dialog.order(), ())
        self.assertTrue(dialog.existing_notice.isHidden())
        self.assertEqual(calculate(spec, "", before, dialog.order()).result.value, "")

    def test_sensitive_reveal_and_hide_preserve_original_empty_item(self):
        _, _, dialog = self.dialog("A;", name="PRIVATE_TOKEN")
        self.assertEqual(dialog.list.item(1).text(), "••••••••")
        self.assertEqual(dialog.list.item(1).toolTip(), "")
        dialog._reveal(True)
        self.assertIn("原始值中的空段", dialog.list.item(1).text())
        self.assertNotEqual(dialog.list.item(1).toolTip(), "")
        dialog._reveal(False)
        self.assertEqual(dialog.list.item(1).text(), "••••••••")
        self.assertEqual(dialog.list.item(1).toolTip(), "")
        self.assertEqual(dialog.order(), ("A", "", "X"))


if __name__ == "__main__":
    unittest.main()
