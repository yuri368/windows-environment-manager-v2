from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from envmanager.config import parse_config
from envmanager.config_document import ConfigurationDocument
from envmanager.config_editing import ConfigEditingSession
from envmanager.models import Choice, ConfigError, VariableSpec
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore


def spec(app="App", option="Default", name="Path", scope="User", mode="replace",
         editable=True, value=r"C:\Tools", choices=()):
    return VariableSpec(app, option, name, scope, mode, editable, value, choices)


class DocumentTests(unittest.TestCase):
    def test_special_raw_strings_round_trip_and_consistency_is_atomic(self):
        item = replace(spec(name="RAW", value="  A;A;B\t ",
                            choices=(Choice("空白", " \t "), Choice("空", ""))), special=True)
        doc = ConfigurationDocument("# preserve\n")
        doc.put_record(item)
        self.assertIn("special = true", doc.text)
        self.assertEqual(doc.records("App", "Default"), (item,))
        source = doc.text
        with self.assertRaises(ConfigError):
            doc.put_record(replace(item, app="Other", name="raw", special=False))
        self.assertEqual(doc.text, source)
        # A copy preserves the target-wide attribute; User/System remain separate.
        doc.put_record(replace(item, app="Other", option="Copy"))
        doc.put_record(replace(item, app="SystemApp", scope="System", special=False))
        old = doc.records("App", "Default")[0]
        source = doc.text
        with self.assertRaises(ConfigError):
            doc.put_record(replace(old, special=False), original=old)
        self.assertEqual(doc.text, source)

    def test_special_field_can_be_removed_for_an_unshared_target(self):
        item = replace(spec(name="RAW", value=" A;B "), special=True)
        doc = ConfigurationDocument("")
        doc.put_record(item)
        ordinary = replace(item, special=False)
        doc.put_record(ordinary, original=item)
        self.assertNotIn("special", doc.text)
        self.assertEqual(doc.records("App", "Default"), (ordinary,))

    def test_blank_comment_draft_add_and_last_delete(self):
        doc = ConfigurationDocument("# keep this comment\r\n")
        self.assertEqual(doc.groups(), ())
        item = spec()
        doc.put_record(item)
        self.assertEqual(doc.groups(), (("App", "Default"),))
        self.assertEqual(doc.records("App", "Default"), (item,))
        self.assertIn("default_value = 'C:\\Tools'", doc.text)
        self.assertTrue(doc.text.startswith("# keep this comment\r\n"))
        self.assertNotIn("\n", doc.text.replace("\r\n", ""))
        doc.delete_record(item)
        self.assertTrue(doc.text.startswith("# keep this comment\r\n"))
        self.assertNotIn("[App.Default]", doc.text)
        self.assertEqual(doc.groups(), ())
        with self.assertRaises(ConfigError):
            parse_config(doc.text)  # An empty draft cannot be saved as a full config.

    def test_noop_and_existing_field_change_preserve_unrelated_layout(self):
        source = ("# header\r\n[App.Default]\r\n"
                  "Path = { default_value = 'C:\\Old', scope = \"User\", mode = \"replace\", editable = true } # path note\r\n"
                  "OTHER = {default_value='Z',scope='User',mode='replace',editable=false} # other note\r\n")
        doc = ConfigurationDocument(source)
        current = doc.records("App", "Default")[0]
        doc.put_record(current, original=current)
        self.assertEqual(doc.text, source)
        updated = replace(current, default_value=r"D:\New")
        doc.put_record(updated, original=current)
        self.assertIn("Path = { default_value = 'D:\\New', scope = \"User\", mode = \"replace\", editable = true } # path note\r\n", doc.text)
        self.assertIn("OTHER = {default_value='Z',scope='User',mode='replace',editable=false} # other note\r\n", doc.text)
        self.assertEqual(doc.records("App", "Default")[0], updated)

    def test_dual_scope_uses_existing_spelling_then_collapses(self):
        source = "[App.Default]\nPath = {default_value='A',scope='User',mode='replace',editable=true} # note\n"
        doc = ConfigurationDocument(source)
        system = spec(name="PATH", scope="System", value="B")
        doc.put_record(system)
        self.assertIn("Path = [", doc.text)
        self.assertNotIn("PATH =", doc.text)
        self.assertIn("# note", doc.text)
        pair = doc.records("App", "Default")
        self.assertEqual([(item.name, item.scope) for item in pair],
                         [("Path", "User"), ("Path", "System")])
        doc.delete_record(pair[0])
        self.assertIn("Path = {", doc.text)
        self.assertNotIn("Path = [", doc.text)
        self.assertEqual(doc.records("App", "Default")[0].scope, "System")

    def test_delete_dual_member_preserves_array_trailing_comment(self):
        source = ("[App.Default]\n"
                  "Path = [{default_value='U',scope='User',mode='replace',editable=true},"
                  "{default_value='S',scope='System',mode='replace',editable=true}] # both scopes\n")
        doc = ConfigurationDocument(source)
        user, system = doc.records("App", "Default")
        doc.delete_record(user)
        self.assertIn("Path = {", doc.text)
        self.assertIn("# both scopes", doc.text)
        self.assertEqual(doc.records("App", "Default"), (system,))

    def test_choices_and_string_encoding_round_trip(self):
        item = spec(value="C:\\O'Brien", choices=(Choice("空值", ""),
                                                  Choice("特殊", "x\x01y")))
        doc = ConfigurationDocument("")
        doc.put_record(item)
        self.assertIn('default_value = "C:\\\\O\'Brien"', doc.text)
        self.assertIn('value = \'\'', doc.text)
        self.assertIn("\\u0001", doc.text)
        self.assertEqual(doc.records("App", "Default"), (item,))
        self.assertEqual(parse_config(doc.text).variables("App", "Default"), (item,))

    def test_update_every_editable_field_and_remove_choices(self):
        source = "[App.Default]\nPath = {default_value='A',scope='User',mode='replace',editable=true}\n"
        doc = ConfigurationDocument(source)
        old = doc.records("App", "Default")[0]
        updated = replace(old, default_value="X;Y", mode="+append", editable=False,
                          choices=(Choice("空", ""), Choice("新", "X;Y")))
        doc.put_record(updated, original=old)
        self.assertEqual(doc.records("App", "Default"), (updated,))
        self.assertIn("choices = [", doc.text)
        no_choices = replace(updated, choices=())
        doc.put_record(no_choices, original=updated)
        self.assertNotIn("choices", doc.text)
        self.assertEqual(doc.records("App", "Default"), (no_choices,))

    def test_invalid_values_and_choices_leave_text_unchanged(self):
        source = "# original\n[App.Default]\nPath = {default_value='A',scope='User',mode='replace',editable=true}\n"
        bad = [
            spec(mode="bogus"),
            spec(mode="+append", value="A;;B"),
            spec(value=" "),
            spec(value="x\x00y"),
            spec(choices=(Choice("x", "A"), Choice("x", "B"))),
            spec(choices=(Choice("x", "A"), Choice("y", "A"))),
            spec(choices=(Choice(" ", "A"),)),
            spec(mode="+append", choices=(Choice("bad", "A;"),)),
        ]
        for item in bad:
            doc = ConfigurationDocument(source)
            with self.subTest(item=item), self.assertRaises(ConfigError):
                doc.put_record(item)
            self.assertEqual(doc.text, source)

    def test_rename_case_and_move_across_application_option(self):
        source = ("# intro\n[App.Old]\n"
                  "Path = {default_value='A',scope='User',mode='replace',editable=true} # moved\n"
                  "OTHER = {default_value='Z',scope='User',mode='replace',editable=false}\n")
        doc = ConfigurationDocument(source)
        original = doc.records("App", "Old")[0]
        renamed = replace(original, name="PATH")
        doc.put_record(renamed, original=original)
        self.assertIn("PATH =", doc.text)
        self.assertIn("# moved", doc.text)
        self.assertEqual([item.name for item in doc.records("App", "Old")], ["PATH", "OTHER"])
        moved = replace(doc.records("App", "Old")[0], app="New App", option="New.Option")
        doc.put_record(moved, original=doc.records("App", "Old")[0])
        self.assertEqual(doc.groups(), (("App", "Old"), ("New App", "New.Option")))
        self.assertIn('["New App"."New.Option"]', doc.text)
        self.assertEqual(doc.records("New App", "New.Option"), (moved,))
        self.assertIn("# moved", doc.text)

    def test_rename_preserves_position_spacing_and_dual_key(self):
        source = ("[App.Default]\n"
                  "FIRST = {default_value='1',scope='User',mode='replace',editable=true}\n"
                  "Path   =  [{default_value='U',scope='User',mode='replace',editable=true},{default_value='S',scope='System',mode='replace',editable=true}] # attached\n"
                  "LAST = {default_value='2',scope='User',mode='replace',editable=true}\n")
        doc = ConfigurationDocument(source)
        old = doc.records("App", "Default")[1]
        doc.put_record(replace(old, name="PATH"), original=old)
        self.assertIn("PATH   =  [", doc.text)
        self.assertIn("# attached", doc.text)
        self.assertLess(doc.text.index("FIRST ="), doc.text.index("PATH   ="))
        self.assertLess(doc.text.index("PATH   ="), doc.text.index("LAST ="))
        self.assertEqual([item.name for item in doc.records("App", "Default")],
                         ["FIRST", "PATH", "PATH", "LAST"])
        with self.assertRaises(ConfigError):
            doc.delete_record(next(item for item in parse_config(source).variables("App", "Default")
                                   if item.name == "Path" and item.scope == "System"))
        self.assertEqual(doc.records("App", "Default")[2].scope, "System")

    def test_quoted_variable_key_and_stale_after_successful_change(self):
        source = "[App.Default]\nPath  = {default_value='A',scope='User',mode='replace',editable=true}\n"
        doc = ConfigurationDocument(source)
        old = doc.records("App", "Default")[0]
        renamed = replace(old, name="My.Path")
        doc.put_record(renamed, original=old)
        self.assertIn('"My.Path"  =', doc.text)
        self.assertEqual(doc.records("App", "Default"), (renamed,))
        with self.assertRaises(ConfigError):
            doc.delete_record(old)

    def test_move_into_existing_other_scope_merges_and_source_group_removed(self):
        source = ("[App.Old]\nPath = {default_value='U',scope='User',mode='replace',editable=true}\n"
                  "[App.New]\nPATH = {default_value='S',scope='System',mode='replace',editable=false}\n")
        doc = ConfigurationDocument(source)
        user = doc.records("App", "Old")[0]
        doc.put_record(replace(user, option="New"), original=user)
        self.assertEqual(doc.groups(), (("App", "New"),))
        self.assertIn("PATH = [", doc.text)
        self.assertNotIn("Path =", doc.text)
        self.assertEqual({item.scope for item in doc.records("App", "New")}, {"User", "System"})

    def test_move_merge_preserves_top_and_record_comments(self):
        source = ("# top-level context\n[App.Old]\n# source record\n"
                  "Path = {default_value='U',scope='User',mode='replace',editable=true} # source trailing\n"
                  "[App.New]\n# target record\n"
                  "PATH = {default_value='S',scope='System',mode='replace',editable=true} # target trailing\n")
        doc = ConfigurationDocument(source)
        old = doc.records("App", "Old")[0]
        doc.put_record(replace(old, option="New"), original=old)
        self.assertEqual(doc.groups(), (("App", "New"),))
        for comment in ("# top-level context", "# source record", "# source trailing",
                        "# target record", "# target trailing"):
            self.assertIn(comment, doc.text)
        self.assertLess(doc.text.index("# source record"), doc.text.index("PATH = ["))
        self.assertEqual({item.scope for item in doc.records("App", "New")}, {"User", "System"})

    def test_duplicate_scope_and_stale_original_are_rejected_atomically(self):
        source = "[App.Default]\nPath = {default_value='A',scope='User',mode='replace',editable=true}\n"
        doc = ConfigurationDocument(source)
        current = doc.records("App", "Default")[0]
        with self.assertRaises(ConfigError):
            doc.put_record(spec(name="PATH", value="B"))
        self.assertEqual(doc.text, source)
        stale = replace(current, default_value="changed elsewhere")
        with self.assertRaises(ConfigError):
            doc.put_record(replace(current, default_value="new"), original=stale)
        with self.assertRaises(ConfigError):
            doc.delete_record(stale)
        with self.assertRaises(ConfigError):
            doc.put_record(spec(name="OTHER"), original=spec(name="missing"))
        self.assertEqual(doc.text, source)

    def test_move_into_occupied_scope_rejected_without_source_loss(self):
        source = ("[App.Old]\nPath = {default_value='A',scope='User',mode='replace',editable=true}\n"
                  "[App.New]\nPATH = {default_value='B',scope='User',mode='replace',editable=true}\n")
        doc = ConfigurationDocument(source)
        old = doc.records("App", "Old")[0]
        with self.assertRaises(ConfigError):
            doc.put_record(replace(old, option="New"), original=old)
        self.assertEqual(doc.text, source)
        self.assertEqual(doc.records("App", "Old"), (old,))

    def test_scope_change_conflict_and_valid_change(self):
        single = ConfigurationDocument("[App.Default]\nPath = {default_value='A',scope='User',mode='replace',editable=true}\n")
        old = single.records("App", "Default")[0]
        single.put_record(replace(old, scope="System"), original=old)
        self.assertEqual(single.records("App", "Default")[0].scope, "System")
        dual = ConfigurationDocument("[App.Default]\nPath = [{default_value='A',scope='User',mode='replace',editable=true},{default_value='B',scope='System',mode='replace',editable=true}]\n")
        old = dual.records("App", "Default")[0]
        before = dual.text
        with self.assertRaises(ConfigError):
            dual.put_record(replace(old, scope="System"), original=old)
        self.assertEqual(dual.text, before)

    def test_delete_group_option_application_and_missing(self):
        source = ("# root\n[App.One]\nA = {default_value='A',scope='User',mode='replace',editable=true}\n"
                  "[App.Two]\nB = {default_value='B',scope='User',mode='replace',editable=true}\n"
                  "[Else.Main]\nC = {default_value='C',scope='User',mode='replace',editable=true}\n")
        doc = ConfigurationDocument(source)
        doc.delete_group("App", "One")
        self.assertEqual(doc.groups(), (("App", "Two"), ("Else", "Main")))
        doc.delete_group("App")
        self.assertEqual(doc.groups(), (("Else", "Main"),))
        before = doc.text
        with self.assertRaises(ConfigError):
            doc.delete_group("App")
        self.assertEqual(doc.text, before)
        doc.delete_group("Else", "Main")
        self.assertEqual(doc.text, "# root\n")

    def test_multiline_value_and_crlf_survive_unrelated_edit(self):
        source = ("# lead\r\n[App.Default]\r\n"
                  "MULTI = {default_value=\"\"\"one\r\n[Fake.Option]\r\nX = 1\r\ntwo\"\"\",scope=\"User\",mode=\"replace\",editable=true}\r\n"
                  "Path = {default_value='A',scope='User',mode='replace',editable=true}\r\n")
        doc = ConfigurationDocument(source)
        path = next(item for item in doc.records("App", "Default") if item.name == "Path")
        doc.put_record(replace(path, default_value="B"), original=path)
        self.assertIn("[Fake.Option]\r\nX = 1\r\n", doc.text)
        self.assertNotIn("\n", doc.text.replace("\r\n", ""))
        self.assertEqual(len(doc.records("App", "Default")), 2)

    def test_constructor_rejects_nonempty_invalid_configuration(self):
        with self.assertRaises(ConfigError):
            ConfigurationDocument("[App.Default]\nPath = 1\n")
        with self.assertRaises(ConfigError):
            ConfigurationDocument("[App.Default]\n")

    def test_explicit_save_preserves_raw_newlines_and_value_characters(self):
        source = ("# old\r\n[App.Default]\r\n"
                  "Path = {default_value=\"A\u2028B\u2029C\",scope=\"User\",mode=\"replace\",editable=true}\r\n")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_bytes(source.encode("utf-8"))
            session = ConfigEditingSession(path)
            session.replace_text(source.replace("# old", "# new"))
            store = MemoryStore()
            service = EnvironmentService(parse_config(source), store, StateRepository(Path(directory) / "state"))
            session.save(service)
            self.assertEqual(path.read_bytes(), session.text.encode("utf-8"))
            self.assertNotIn("\n", session.text.replace("\r\n", ""))
            self.assertEqual(parse_config(path.read_bytes().decode("utf-8")).variables("App", "Default")[0].default_value,
                             "A\u2028B\u2029C")
            self.assertEqual(service.config.variables("App", "Default")[0].default_value, "A\u2028B\u2029C")
            self.assertEqual(store.writes, [])
            self.assertEqual(store.notifications, 0)


if __name__ == "__main__":
    unittest.main()
