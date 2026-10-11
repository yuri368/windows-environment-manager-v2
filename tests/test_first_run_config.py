"""A source checkout starts with a private copy of the public example."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from envmanager import main


EXAMPLE = '[App.Default]\nHOME = { default_value = "example", scope = "User", mode = "replace", editable = false }\n'


class FirstRunConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.example = self.root / "env.example.toml"
        self.example.write_text(EXAMPLE, encoding="utf-8")
        self.personal = self.root / "env.toml"
        self.patch = patch.object(main, "PROJECT_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_first_formal_start_copies_valid_example_once(self):
        self.assertEqual(main.resolve_config_path(), self.personal)
        self.assertEqual(self.personal.read_text(encoding="utf-8"), EXAMPLE)
        self.personal.write_text(EXAMPLE.replace("example", "personal"), encoding="utf-8")
        main.resolve_config_path()
        self.assertIn("personal", self.personal.read_text(encoding="utf-8"))

    def test_demo_and_check_do_not_create_personal_configuration(self):
        self.assertEqual(main.resolve_config_path(demo=True), self.example)
        self.assertEqual(main.resolve_config_path(readonly=True), self.example)
        self.assertFalse(self.personal.exists())
        with patch.object(main, "ensure_administrator") as elevate:
            self.assertEqual(main.main(["--check"]), 0)
        elevate.assert_not_called()
        self.assertFalse(self.personal.exists())

    def test_demo_uses_example_even_when_personal_exists(self):
        self.personal.write_text("private invalid data", encoding="utf-8")
        self.assertEqual(main.resolve_config_path(demo=True), self.example)
        self.assertEqual(main.resolve_config_path(readonly=True), self.personal)

    def test_explicit_path_is_never_replaced_or_created(self):
        missing = self.root / "requested.toml"
        self.assertEqual(main.resolve_config_path(missing), missing)
        with patch.object(main, "ensure_administrator") as elevate:
            self.assertEqual(main.main(["--check", "--config", str(missing)]), 1)
        elevate.assert_not_called()
        self.assertFalse(missing.exists())
        self.assertFalse(self.personal.exists())

    def test_bad_example_does_not_create_personal_file(self):
        self.example.write_text("bad toml =", encoding="utf-8")
        with self.assertRaises(ValueError):
            main.resolve_config_path()
        self.assertFalse(self.personal.exists())

    def test_uac_failure_stops_before_initial_file_creation(self):
        with patch.object(main, "ensure_administrator", side_effect=OSError("cancelled")), \
                patch.object(main, "report_startup_error") as report:
            self.assertEqual(main.main([]), 1)
        report.assert_called_once()
        self.assertFalse(self.personal.exists())

    def test_frozen_default_configuration_is_outside_program_directory(self):
        local = self.root / "local-app-data"
        with patch.object(main.sys, "frozen", True, create=True), \
                patch.dict(main.os.environ, {"LOCALAPPDATA": str(local)}):
            self.assertEqual(main.resolve_config_path(readonly=True), self.example)
            self.assertFalse(local.exists())
            created = main.resolve_config_path()
            self.assertEqual(created, local / "EnvironmentManagerV2/config/env.toml")
            self.assertEqual(created.read_bytes(), self.example.read_bytes())
            self.assertFalse(self.personal.exists())


if __name__ == "__main__":
    unittest.main()
