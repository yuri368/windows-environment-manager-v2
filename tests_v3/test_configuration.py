"""Standalone V3 configuration tests: only temporary directories are written."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from envmanager_v3.configuration import (
    ConfigurationError, ConfigRepository, MISSING_REVISION,
    parse_config, serialize_config, validate_config,
)


def fixture(value="", **updates):
    variable = {"name": "PATH", "scope": "User", "mode": "replace",
                "defaultValue": value, "editable": True, "special": False, "choices": []}
    variable.update(updates)
    return [{"name": "工具", "default": "默认", "profiles": [{"name": "默认", "vars": [variable]}]}]


def variables(apps):
    return apps[0]["profiles"][0]["vars"]


class FormatTests(unittest.TestCase):
    def test_raw_roundtrip(self):
        for raw in ["", " ", "  edge spaces  ", "\n", "\r\n", "\n\n leading\r\n;\rtrailing\n",
                    '"quoted" \\server\\path\\', "中文😀ßİıﬀ", "a;;a; ;", "\x7f",
                    "\x01\b\t\x0b\f\x1f", "\u2028\u2029"]:
            with self.subTest(raw=repr(raw)):
                apps = fixture(raw, choices=[{"label": " 候选 ", "value": raw}])
                serialized = serialize_config(apps)
                self.assertEqual(parse_config(serialized), apps)
                self.assertEqual(tomllib.loads(serialized)["工具"]["默认"]["PATH"]["default_value"], raw)
        self.assertIn(r"\u007f", serialize_config(fixture("\x7f")))

    def test_names_preserve_spaces_quotes_backslashes_and_newlines(self):
        apps = fixture("raw")
        apps[0]["name"] = ' 应用."\\\n '
        apps[0]["default"] = apps[0]["profiles"][0]["name"] = ' 方案."\\\r\n '
        variables(apps)[0]["name"] = ' 变量."\\\n '
        self.assertEqual(parse_config(serialize_config(apps)), apps)

    def test_omitted_defaults_and_legacy_choices(self):
        apps = parse_config('[A.P]\nX = { choices = ["", " C:\\\\bin "] }\n')
        self.assertEqual(apps[0]["default"], "P")
        self.assertEqual(variables(apps)[0], {
            "name": "X", "scope": "User", "mode": "replace", "defaultValue": "",
            "editable": True, "special": False,
            "choices": [{"label": "候选值 1", "value": ""}, {"label": "候选值 2", "value": " C:\\bin "}],
        })
        self.assertEqual(parse_config(serialize_config(apps)), apps)

    def test_default_option_explicit_and_first(self):
        source = '[A]\ndefault_option="P2"\n[A.P1]\nX={}\n[A.P2]\nY={}\n'
        self.assertEqual(parse_config(source)[0]["default"], "P2")
        self.assertEqual(parse_config(source.replace('default_option="P2"\n', ''))[0]["default"], "P1")
        with self.assertRaises(ConfigurationError):
            parse_config(source.replace('default_option="P2"', 'default_option="missing"'))

    def test_dual_scope_array(self):
        apps = parse_config('[A.P]\nPath = [{scope="User",default_value="u"},{scope="System",default_value="s"}]\n')
        self.assertEqual([v["scope"] for v in variables(apps)], ["User", "System"])
        text = serialize_config(apps)
        self.assertIn('"Path" = [ {', text)
        self.assertEqual(parse_config(text), apps)

    def test_case_variant_dual_scope_preserves_original_names(self):
        apps = fixture()
        variables(apps).append({**variables(apps)[0], "name": "Path", "scope": "System"})
        self.assertEqual(parse_config(serialize_config(apps)), apps)

    def test_case_matching_has_no_multichar_expansion(self):
        apps = fixture(name="ß")
        variables(apps).append({**variables(apps)[0], "name": "SS"})
        self.assertEqual(validate_config(apps), [])
        variables(apps).append({**variables(apps)[0], "name": "ss"})
        self.assertTrue(any(e["field"] == "scope" for e in validate_config(apps)))

    def test_supplementary_case_matching_uses_js_utf16_length(self):
        apps = fixture(name="\U00010428")
        variables(apps).append({**variables(apps)[0], "name": "\U00010400"})
        self.assertEqual(validate_config(apps), [])

    def test_app_and_profile_names_are_exact(self):
        apps = fixture()
        other = deepcopy(apps[0])
        other["name"] += " "
        apps.append(other)
        profile = deepcopy(apps[0]["profiles"][0])
        profile["name"] += " "
        apps[0]["profiles"].append(profile)
        self.assertEqual(validate_config(apps), [])
        apps.append(deepcopy(apps[0]))
        self.assertTrue(any(e["field"] == "appName" for e in validate_config(apps)))
        apps = fixture()
        apps[0]["profiles"].append(deepcopy(apps[0]["profiles"][0]))
        self.assertTrue(any(e["field"] == "profileName" for e in validate_config(apps)))

    def test_empty_root_and_named_empty_profiles(self):
        self.assertEqual(validate_config([]), [])
        for text in ["", "# new configuration\n", serialize_config([]), "\ufeff"]:
            self.assertEqual(parse_config(text), [])
        for text in ['[A.P]\n', '[A]\n', '[A.default_option]\nX={}\n']:
            with self.subTest(text=text), self.assertRaises(ConfigurationError):
                parse_config(text)
        apps = fixture()
        variables(apps).clear()
        with self.assertRaises(ConfigurationError):
            serialize_config(apps)

    def test_special_only_replace_and_global_target_consistency(self):
        for mode in ["append", "+append", "-append"]:
            self.assertTrue(any(e["field"] == "special" for e in validate_config(fixture(mode=mode, special=True))))
        apps = fixture(special=True)
        other = deepcopy(apps[0])
        other["name"] = "Another app"
        other["profiles"][0]["vars"][0].update(name="path", special=False)
        apps.append(other)
        self.assertTrue(any(e["field"] == "special" for e in validate_config(apps)))
        other["profiles"][0]["vars"][0]["scope"] = "System"
        self.assertEqual(validate_config(apps), [])

    def test_choices_exact_duplicates_and_label_rules(self):
        valid = [{"label": "A", "value": "x"}, {"label": "a", "value": "X"},
                 {"label": " A ", "value": " x "}, {"label": "empty", "value": ""}]
        self.assertEqual(validate_config(fixture(choices=valid)), [])
        for choices in [valid + [valid[0]], [{"label": " ", "value": "x"}],
                        [{"label": "A", "value": "x"}, {"label": "B", "value": "x"}],
                        [{"label": "A", "value": "x"}, {"label": "A", "value": "y"}],
                        ["same", "same"]]:
            with self.subTest(choices=choices):
                self.assertTrue(validate_config(fixture(choices=choices)))

    def test_nul_and_utf16_limits(self):
        for value in ["a" * 32766, "😀" * 16383, "😀" * 16382 + "ab"]:
            self.assertEqual(parse_config(serialize_config(fixture(value))), fixture(value))
        for value in ["a\0b", "a" * 32767, "😀" * 16383 + "a", "\ud800"]:
            with self.subTest(value=repr(value[:10])):
                self.assertTrue(validate_config(fixture(value)))
                self.assertTrue(validate_config(fixture(choices=[{"label": "bad", "value": value}])))
        for name in ["", "X=Y", "X\0Y"]:
            self.assertTrue(validate_config(fixture(name=name)))

    def test_all_modes_and_boolean_validation(self):
        for mode in ["replace", "append", "+append", "-append"]:
            self.assertEqual(validate_config(fixture(mode=mode, editable=False)), [])
        for updates in [{"scope": "user"}, {"mode": "APPEND"}, {"editable": 1},
                        {"special": "false"}, {"defaultValue": 2}]:
            self.assertTrue(validate_config(fixture(**updates)))

    def test_malformed_dtos_return_structured_errors(self):
        for apps in [None, {}, [None], [{"profiles": None}],
                     [{"name": "A", "default": "P", "profiles": [None]}],
                     fixture(choices=None), fixture(choices=[None]), fixture(name=[]),
                     fixture(scope=[]), fixture(mode=[]), fixture(choices=[{"label": [], "value": []}])]:
            with self.subTest(apps=apps):
                errors = validate_config(apps)
                self.assertTrue(errors)
                for error in errors:
                    self.assertEqual(set(error), {"field", "message", "location"})
                    self.assertIsInstance(error["location"], list)

    def test_bad_toml_shapes_and_duplicates(self):
        for text in ['[A.P', '[A.P]\nX={}\nX={}\n', '[A.P]\nX=[]',
                     '[A.P]\nX=[{},{}]', '[A.P]\nX="value"', 'A=1', '[A]\nP=1',
                     '[A.P]\nX={scope="System"}\nPATH={}\npath={}']:
            with self.subTest(text=text), self.assertRaises(ConfigurationError) as caught:
                parse_config(text)
            self.assertIn(caught.exception.code, ["invalid_toml", "invalid_configuration"])
            self.assertTrue(caught.exception.message)

    def test_runtime_and_display_fields_never_exported(self):
        apps = fixture("configured")
        runtime = {"savedValue": "SAVED_SENTINEL", "custom": "CUSTOM_SENTINEL",
                   "baseline": "BASELINE_SENTINEL", "active": True, "id": "ID_SENTINEL",
                   "selection": "SELECTION_SENTINEL", "icon": "ICON_SENTINEL",
                   "iconBg": "COLOR_SENTINEL", "iconFg": "FG_SENTINEL"}
        apps[0].update(runtime)
        apps[0]["profiles"][0].update(runtime)
        variables(apps)[0].update(runtime)
        snapshot = deepcopy(apps)
        text = serialize_config(apps)
        for value in runtime.values():
            if isinstance(value, str):
                self.assertNotIn(value, text)
        self.assertEqual(parse_config(text), fixture("configured"))
        self.assertEqual(apps, snapshot)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "config.toml"
        self.repo = ConfigRepository(self.path)

    def test_missing_load_is_read_only_and_first_save_is_utf8(self):
        nested = self.root / "not_created" / "new.toml"
        repo = ConfigRepository(nested)
        loaded = repo.load()
        self.assertEqual(loaded, {"apps": [], "revision": MISSING_REVISION, "text": "", "path": str(nested)})
        self.assertFalse(nested.parent.exists())
        saved = repo.save(fixture("中文\r\n raw "), loaded["revision"])
        data = nested.read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(saved["revision"], hashlib.sha256(data).hexdigest())
        self.assertEqual(repo.load(), saved)
        self.assertFalse((nested.parent / "backups").exists())

    def test_bom_and_crlf_revision_and_exact_backup(self):
        old = b"\xef\xbb\xbf" + serialize_config(fixture("original")).replace("\n", "\r\n").encode("utf-8")
        self.path.write_bytes(old)
        loaded = self.repo.load()
        self.assertEqual(loaded["revision"], hashlib.sha256(old).hexdigest())
        self.assertNotIn("\ufeff", loaded["text"])
        self.assertIn("\r\n", loaded["text"])
        saved = self.repo.save(fixture("changed"), loaded["revision"])
        backups = list((self.root / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), old)
        self.assertEqual(self.repo.load(), saved)

    def test_external_write_is_rejected_including_comment_only_change(self):
        first = self.repo.save(fixture(), MISSING_REVISION)
        external = self.path.read_bytes() + b"\n# external\n"
        self.path.write_bytes(external)
        with self.assertRaises(ConfigurationError) as caught:
            self.repo.save(fixture("overwrite"), first["revision"])
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(self.path.read_bytes(), external)
        self.assertFalse((self.root / "backups").exists())

    def test_external_creation_and_deletion_are_conflicts(self):
        missing = self.repo.load()
        self.path.write_text(serialize_config(fixture()), encoding="utf-8", newline="")
        with self.assertRaises(ConfigurationError):
            self.repo.save([], missing["revision"])
        loaded = self.repo.load()
        self.path.unlink()
        with self.assertRaises(ConfigurationError):
            self.repo.save([], loaded["revision"])
        self.assertFalse(self.path.exists())

    def test_invalid_save_leaves_no_files_or_directories(self):
        repo = ConfigRepository(self.root / "absent" / "config.toml")
        for bad in [fixture("nul\0"), fixture(choices=["duplicate", "duplicate"]),
                    [{"name": "A", "default": "P", "profiles": [{"name": "P", "vars": []}]}]]:
            with self.assertRaises(ConfigurationError) as caught:
                repo.save(bad, MISSING_REVISION)
            self.assertEqual(caught.exception.code, "invalid_configuration")
            self.assertFalse(repo.path.parent.exists())
        first = self.repo.save(fixture("untouched"), MISSING_REVISION)
        original = self.path.read_bytes()
        with self.assertRaises(ConfigurationError):
            self.repo.save(fixture("\0"), first["revision"])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.root / "backups").exists())

    def test_atomic_replace_failure_preserves_original_and_cleans_staging(self):
        first = self.repo.save(fixture("old"), MISSING_REVISION)
        old = self.path.read_bytes()
        with patch("envmanager_v3.configuration.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(ConfigurationError) as caught:
                self.repo.save(fixture("new"), first["revision"])
        self.assertEqual(caught.exception.code, "io_error")
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertEqual([p.read_bytes() for p in (self.root / "backups").iterdir()], [old])

    def test_backup_failure_preserves_original(self):
        first = self.repo.save(fixture("old"), MISSING_REVISION)
        old = self.path.read_bytes()
        (self.root / "backups").write_bytes(b"block directory")
        with self.assertRaises(ConfigurationError):
            self.repo.save(fixture("new"), first["revision"])
        self.assertEqual(self.path.read_bytes(), old)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_external_write_during_staging_is_rechecked(self):
        first = self.repo.save(fixture("old"), MISSING_REVISION)
        external = b"# external writer\n"
        original_check = self.repo._check_revision
        calls = 0

        def race(revision):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.path.write_bytes(external)
            return original_check(revision)

        with patch.object(self.repo, "_check_revision", side_effect=race):
            with self.assertRaises(ConfigurationError) as caught:
                self.repo.save(fixture("new"), first["revision"])
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(self.path.read_bytes(), external)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_import_uses_same_validation_cas_and_backup(self):
        first = self.repo.save(fixture("old"), MISSING_REVISION)
        raw = '\ufeff[A.P]\nX={choices=["first", "second"]}\n'
        saved = self.repo.import_text(raw, first["revision"])
        self.assertEqual(saved["apps"][0]["name"], "A")
        self.assertEqual(len(list((self.root / "backups").iterdir())), 1)
        with self.assertRaises(ConfigurationError) as caught:
            self.repo.import_text(raw, first["revision"])
        self.assertEqual(caught.exception.code, "revision_conflict")
        before = self.path.read_bytes()
        with self.assertRaises(ConfigurationError):
            self.repo.import_text('[Broken', saved["revision"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_save_empty_root(self):
        saved = self.repo.save([], MISSING_REVISION)
        self.assertEqual(saved["apps"], [])
        self.assertEqual(self.repo.load(), saved)

    def test_invalid_encoding_is_typed_error(self):
        self.path.write_bytes(b"\xff\xfe\x00\x00")
        with self.assertRaises(ConfigurationError) as caught:
            self.repo.load()
        self.assertEqual(caught.exception.code, "invalid_encoding")

    def test_unchanged_document_retains_all_original_text(self):
        text = (
            "# owner comment\r\n\r\n[ 'App' ]  # app comment\r\n"
            "default_option = 'P' # selected\r\n\r\n['App'.'P'] # profile\r\n"
            "# variable comment\r\nX  = {default_value = ' raw ', editable=false} # X\r\n"
            "Y = {choices=[' a ', 'b']} # legacy\r\n"
        )
        self.path.write_bytes(text.encode("utf-8"))
        loaded = self.repo.load()
        saved = self.repo.save(loaded["apps"], loaded["revision"])
        self.assertEqual(saved["text"], text)
        self.assertEqual(self.path.read_bytes(), text.encode("utf-8"))

    def test_edit_changes_only_requested_field(self):
        text = (
            "# owner comment\n['App'] # app\ndefault_option='P' # selected\n"
            "['App'.'P'] # profile\nX = {default_value='old', editable=false} # X comment\n"
            "Y  = {default_value='untouched', choices=['a', 'b']} # Y comment\n"
        )
        self.path.write_bytes(text.encode("utf-8"))
        loaded = self.repo.load()
        variables(loaded["apps"])[0]["defaultValue"] = "\r\n new\x7f "
        saved = self.repo.save(loaded["apps"], loaded["revision"])
        self.assertEqual(saved["text"], text.replace("'old'", '"\\r\\n new\\u007f "'))
        self.assertEqual(variables(saved["apps"])[0]["defaultValue"], "\r\n new\x7f ")
        self.assertNotIn('scope =', saved["text"])
        self.assertNotIn('mode =', saved["text"])

    def test_edit_choices_retains_unedited_fields_and_comments(self):
        text = (
            "[A.P]\nX={default_value='default',choices=[\n"
            "  {label='first',value='one'}, # first comment\n"
            "  {label='second',value='two'}, # second comment\n"
            "]} # variable comment\nY={} # keep Y\n"
        )
        self.path.write_bytes(text.encode("utf-8"))
        loaded = self.repo.load()
        choices = variables(loaded["apps"])[0]["choices"]
        choices[1]["value"] = "new two"
        saved = self.repo.save(loaded["apps"], loaded["revision"])
        self.assertEqual(saved["text"], text.replace("'two'", '"new two"'))
        choices = variables(saved["apps"])[0]["choices"]
        choices.pop(0)
        choices.append({"label": "third", "value": "three"})
        changed = self.repo.save(saved["apps"], saved["revision"])
        self.assertEqual(variables(changed["apps"])[0]["choices"], choices)
        self.assertIn("Y={} # keep Y", changed["text"])

    def test_legacy_choices_only_converts_edited_item(self):
        text = "[A.P]\nX={choices=['one', 'two']} # legacy\n"
        self.path.write_text(text, encoding="utf-8", newline="")
        loaded = self.repo.load()
        variables(loaded["apps"])[0]["choices"][1]["label"] = "Renamed"
        saved = self.repo.save(loaded["apps"], loaded["revision"])
        self.assertIn("choices=['one', {", saved["text"])
        self.assertIn("# legacy", saved["text"])
        self.assertEqual(variables(saved["apps"])[0]["choices"][1], {"label": "Renamed", "value": "two"})

    def test_incremental_add_delete_profiles_apps_and_scopes(self):
        self.path.write_text("# root\n[A.P]\nX={} # keep\n[A.Other]\nY={} # other\n", encoding="utf-8", newline="")
        loaded = self.repo.load()
        variables(loaded["apps"]).append({**variables(loaded["apps"])[0], "scope": "System", "defaultValue": "sys"})
        loaded["apps"][0]["profiles"].append({"name": "New", "vars": [{"name": "Z"}]})
        loaded["apps"].extend(fixture("other app"))
        saved = self.repo.save(loaded["apps"], loaded["revision"])
        self.assertIn("# root", saved["text"])
        self.assertIn("# keep", saved["text"])
        self.assertIn("Y={} # other", saved["text"])
        self.assertEqual(len(variables(saved["apps"])), 2)
        saved["apps"].pop()
        saved["apps"][0]["profiles"].pop()
        variables(saved["apps"]).pop(0)
        changed = self.repo.save(saved["apps"], saved["revision"])
        self.assertEqual(len(changed["apps"]), 1)
        self.assertEqual(variables(changed["apps"])[0]["scope"], "System")
        self.assertIn("Y={} # other", changed["text"])

    def test_incremental_profile_removal_preserves_default(self):
        text = "[A.First]\nX={}\n[A.Second]\nY={}\n[A.Third]\nZ={}\n"
        self.path.write_text(text, encoding="utf-8", newline="")
        loaded = self.repo.load()
        loaded["apps"][0]["profiles"].pop(0)
        loaded["apps"][0]["default"] = "Third"
        saved = self.repo.save(loaded["apps"], loaded["revision"])
        self.assertEqual(saved["apps"][0]["default"], "Third")
        self.assertIn("Y={}\n", saved["text"])
        self.assertIn("Z={}\n", saved["text"])

    def test_existing_runtime_fields_are_removed_without_touching_neighbors(self):
        text = '[A.P]\nX={default_value="x", savedValue="runtime"} # X\nY={} # Y\n'
        self.path.write_text(text, encoding="utf-8", newline="")
        loaded = self.repo.load()
        saved = self.repo.save(loaded["apps"], loaded["revision"])
        self.assertNotIn("savedValue", saved["text"])
        self.assertIn("Y={} # Y\n", saved["text"])

    def test_missing_tomlkit_has_typed_error_and_retains_original(self):
        self.path.write_text(serialize_config(fixture("old")), encoding="utf-8", newline="")
        loaded = self.repo.load()
        before = self.path.read_bytes()
        with patch.dict("sys.modules", {"tomlkit": None}):
            with self.assertRaises(ConfigurationError) as caught:
                self.repo.save(fixture("new"), loaded["revision"])
        self.assertEqual(caught.exception.code, "dependency_missing")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / "backups").exists())

    def test_external_write_immediately_before_replace_is_rechecked(self):
        first = self.repo.save(fixture("old"), MISSING_REVISION)
        external = b"# external writer after backup\n"
        original_check = self.repo._check_revision
        calls = 0

        def race(revision):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.path.write_bytes(external)
            return original_check(revision)

        with patch.object(self.repo, "_check_revision", side_effect=race):
            with self.assertRaises(ConfigurationError) as caught:
                self.repo.save(fixture("new"), first["revision"])
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(self.path.read_bytes(), external)
        self.assertFalse(list(self.root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
