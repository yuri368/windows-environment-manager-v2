from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from envmanager.config import parse_config
from envmanager.config_document import ConfigurationDocument
from envmanager.config_editing import ConfigEditingSession
from envmanager.models import ConfigError, ConflictError


OPTIONS = (
    "# original\r\n[Java.One]\r\n"
    "HOME = {default_value='A',scope='User',mode='replace',editable=true} # one\r\n"
    "[Java.Two]\r\n"
    "HOME = {default_value='B',scope='User',mode='replace',editable=true} # two\r\n"
)
EXPLICIT = '[Java]\r\ndefault_option = "Two" # default note\r\n' + OPTIONS


class DefaultConfigurationTests(unittest.TestCase):
    def test_implicit_default_and_explicit_nonfirst_default(self):
        implicit = parse_config(OPTIONS)
        self.assertEqual(implicit.default_options, {})
        self.assertEqual(implicit.default_option("Java"), "One")
        explicit = parse_config(EXPLICIT)
        self.assertEqual(explicit.options("Java"), ("One", "Two"))
        self.assertEqual(explicit.default_options, {"Java": "Two"})
        self.assertEqual(explicit.default_option("Java"), "Two")
        self.assertEqual(len(explicit.variables("Java", "Two")), 1)

    def test_invalid_defaults_rejected(self):
        for value in ('1', 'true', '[]', '{}', '"Missing"', '"two"', '""'):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, "default_option"):
                parse_config(f"[Java]\ndefault_option = {value}\n" + OPTIONS)
        with self.assertRaisesRegex(ConfigError, "必须包含方案"):
            parse_config('[Java]\ndefault_option = "One"\n')
        with self.assertRaisesRegex(ConfigError, "保留"):
            parse_config('[Java.default_option]\nX = {default_value="A",scope="User",mode="replace",editable=true}\n')

    def test_quoted_names_and_metadata_after_subtables(self):
        text = ('["Java App"."Two.One"]\n'
                'X = {default_value="A",scope="User",mode="replace",editable=true}\n'
                '["Java App"]\ndefault_option = "Two.One"\n')
        config = parse_config(text)
        self.assertEqual(config.default_option("Java App"), "Two.One")
        self.assertEqual(config.options("Java App"), ("Two.One",))

    def test_set_default_preserves_trivia_and_is_atomic(self):
        doc = ConfigurationDocument(OPTIONS)
        doc.set_default_option("Java", "Two")
        self.assertEqual(doc.default_option("Java"), "Two")
        self.assertTrue(doc.text.startswith("# original\r\n"))
        self.assertIn(OPTIONS.removeprefix("# original\r\n"), doc.text)
        self.assertNotIn("\n", doc.text.replace("\r\n", ""))
        before = doc.text
        doc.set_default_option("Java", "Two")
        self.assertEqual(doc.text, before)
        for app, option in (("Java", "Missing"), ("Missing", "One"), ("Java", 1)):
            with self.assertRaises(ConfigError):
                doc.set_default_option(app, option)
            self.assertEqual(doc.text, before)

    def test_delete_default_group_or_last_record_retargets_then_removes_metadata(self):
        for method in ("group", "record"):
            with self.subTest(method=method):
                doc = ConfigurationDocument(EXPLICIT)
                if method == "group":
                    doc.delete_group("Java", "Two")
                else:
                    doc.delete_record(doc.records("Java", "Two")[0])
                self.assertEqual(doc.default_option("Java"), "One")
                self.assertEqual(parse_config(doc.text).default_options, {"Java": "One"})
                self.assertIn("# default note", doc.text)
                doc.delete_record(doc.records("Java", "One")[0])
                self.assertEqual(doc.groups(), ())
                self.assertNotIn("default_option", doc.text)
                self.assertNotIn("[Java]", doc.text)

    def test_move_last_record_preserves_valid_defaults_and_comments(self):
        doc = ConfigurationDocument(EXPLICIT)
        old = doc.records("Java", "Two")[0]
        doc.put_record(replace(old, option="Three"), original=old)
        self.assertEqual(doc.default_option("Java"), "One")
        self.assertIn("# default note", doc.text)
        self.assertIn("# two", doc.text)
        sole = ConfigurationDocument('[Java]\ndefault_option="One"\n' + OPTIONS.split("[Java.Two]")[0])
        old = sole.records("Java", "One")[0]
        sole.put_record(replace(old, option="Renamed"), original=old)
        self.assertEqual(sole.default_option("Java"), "Renamed")
        old = sole.records("Java", "Renamed")[0]
        sole.put_record(replace(old, app="New App"), original=old)
        self.assertEqual(sole.groups(), (("New App", "Renamed"),))
        self.assertEqual(sole.default_option("New App"), "Renamed")
        self.assertNotIn("[Java]", sole.text)

    def test_default_metadata_cannot_be_deleted_as_a_group(self):
        doc = ConfigurationDocument(EXPLICIT)
        with self.assertRaises(ConfigError):
            doc.delete_group("Java", "default_option")
        self.assertEqual(doc.text, EXPLICIT)

    def test_draft_default_undo_and_explicit_save_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_bytes(OPTIONS.encode("utf-8"))
            session = ConfigEditingSession(path)
            session.set_default_option("Java", "Two")
            changed = session.text
            self.assertEqual(path.read_bytes(), OPTIONS.encode("utf-8"))
            self.assertTrue(session.dirty)
            session.undo()
            self.assertEqual(session.text, OPTIONS)
            session.redo()
            self.assertEqual(session.text, changed)
            path.write_bytes((OPTIONS + "# external\r\n").encode("utf-8"))
            with self.assertRaises(ConflictError):
                session.save(object())
            self.assertEqual(session.text, changed)
            self.assertIn(b"# external", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
