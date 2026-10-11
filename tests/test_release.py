"""The source archive contains public inputs and can start without personal data."""

from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from tools.build_release import ROOT, ROOT_FILES, release_files, build_release


class ReleaseTests(unittest.TestCase):
    def test_release_has_no_personal_or_generated_files(self):
        names = [path.relative_to(ROOT).as_posix() for path in release_files(ROOT)]
        self.assertIn("env.example.toml", names)
        self.assertIn("LICENSE", names)
        self.assertIn("envmanager/windows_shell.py", names)
        for name in names:
            self.assertNotEqual(name, "env.toml")
            self.assertFalse(name.startswith(("artifacts/", ".impeccable/", ".venv/", "dist/")))
            self.assertNotIn("__pycache__", name)
            self.assertFalse(name.endswith((".sqlite3", ".bak", ".log")))

    def test_archive_checksum_and_clean_extracted_configuration(self):
        import hashlib
        from unittest.mock import patch
        from envmanager import main
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            archive, checksum, count = build_release(output=output)
            self.assertTrue(checksum.read_text().startswith(hashlib.sha256(archive.read_bytes()).hexdigest()))
            with ZipFile(archive) as bundle:
                self.assertEqual(len(bundle.namelist()), count)
                self.assertIsNone(bundle.testzip())
                bundle.extractall(output / "extracted")
                directory = output / "extracted" / bundle.namelist()[0].split('/')[0]
            self.assertFalse((directory / "env.toml").exists())
            with patch.object(main, "PROJECT_ROOT", directory):
                self.assertEqual(main.main(["--check"]), 0)
                self.assertFalse((directory / "env.toml").exists())
                self.assertEqual(main.resolve_config_path(), directory / "env.toml")
                self.assertEqual((directory / "env.toml").read_bytes(), (directory / "env.example.toml").read_bytes())


if __name__ == "__main__":
    unittest.main()
