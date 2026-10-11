from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from envmanager.config import load_config, parse_config, save_config
from envmanager.engine import calculate, validate_value
from envmanager.elements import CleanupSettings, cleanup_value, effective_elements
from envmanager.models import Choice, ConfigError, RegistryState, ValidationError, VariableSpec
from envmanager.selection import SelectionSession


def spec(mode="replace", default="A", editable=True, choices=(), scope="User", name="Path"):
    return VariableSpec("App", "Default", name, scope, mode, editable, default, choices)


def state(value=None, exists=True, registry_type=1, scope="User", name="Path"):
    return RegistryState(name, scope, exists, value if exists else None,
                         registry_type if exists else None)


class ConfigTests(unittest.TestCase):
    def test_special_strict_bool_replace_only_and_all_targets_consistent(self):
        def record(special="", scope="User", mode="replace"):
            return '{default_value=" A;A; ",scope="' + scope + '",mode="' + mode + '",editable=true' + special + '}'
        for special in (',special=1', ',special="true"'):
            with self.subTest(special=special), self.assertRaises(ConfigError):
                parse_config('[A.One]\nPath=' + record(special))
        for mode in ("+append", "-append", "append"):
            with self.subTest(mode=mode), self.assertRaises(ConfigError):
                parse_config('[A.One]\nPath=' + record(',special=true', mode=mode))
        # The other definition may belong to a non-selected scheme or app.
        for header in ('A.Two', 'Other.Two'):
            with self.subTest(header=header), self.assertRaisesRegex(ConfigError, 'special'):
                parse_config('[A.One]\nPath=' + record(',special=true') + '\n[' + header + ']\nPATH=' + record())
        config = parse_config('[A.One]\nPath=' + record(',special=true') + '\n[B.Two]\nPATH=' + record(scope='System'))
        self.assertTrue(config.variables('A', 'One')[0].special)
        self.assertFalse(config.variables('B', 'Two')[0].special)

    def test_empty_slices_are_valid_raw_configuration(self):
        for mode in ('replace', '+append', '-append', 'append'):
            text = '[A.B]\nPath={default_value=" ; A;; ",scope="User",mode="' + mode + '",editable=true}'
            self.assertEqual(parse_config(text).variables('A', 'B')[0].default_value, ' ; A;; ')

    def test_single_dual_quoted_and_empty(self):
        text = '''["Visual Studio Code".Default]
"A.B" = { default_value = "", scope = "User", mode = "replace", editable = false, choices = [{label = "空", value = ""}] }
Path = [
 {default_value = 'A', scope = "System", mode = "+append", editable = true},
 {default_value = 'B', scope = "User", mode = "append", editable = false},
]
'''
        config = parse_config(text)
        records = config.variables("Visual Studio Code", "Default")
        self.assertEqual([(item.name, item.scope) for item in records],
                         [("A.B", "User"), ("Path", "System"), ("Path", "User")])
        self.assertEqual(records[0].default_value, "")
        self.assertEqual(config.digest, parse_config(text).digest)

    def test_multiline_value_text_does_not_become_table_or_assignment(self):
        text = '''[App.Default]
ONE = {default_value="""first
[Fake.Section]
X = 1
# text that is part of the value
second""",scope="User",mode="replace",editable=true}
TWO = {default_value='ok',scope="User",mode="replace",editable=true}
'''
        records = parse_config(text).variables("App", "Default")
        self.assertEqual([item.name for item in records], ["ONE", "TWO"])
        self.assertIn("[Fake.Section]", records[0].default_value)

    def test_bad_structure_and_duplicate_case(self):
        cases = [
            '[A.B]\nPath = 1\n',
            '[A.B.Path]\ndefault_value = "x"\nscope = "User"\nmode = "replace"\neditable = true\n',
            '[A.B]\nPath = [{default_value="x",scope="User",mode="replace",editable=true}]\n',
            '[A.B]\nPath = [{default_value="x",scope="User",mode="replace",editable=true},{default_value="y",scope="User",mode="replace",editable=true}]\n',
            '[A.B]\nPath = {default_value="x",scope="User",mode="replace",editable=true}\nPATH = {default_value="y",scope="System",mode="replace",editable=true}\n',
            '[A.B]\nPath = {default_value="x",scope="User",mode="replace",editable=true}\nPath = {default_value="y",scope="User",mode="replace",editable=true}\n',
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ConfigError):
                parse_config(text)
        distinct = '''[A.B]
"ß" = {default_value="x",scope="User",mode="replace",editable=true}
SS = {default_value="y",scope="User",mode="replace",editable=true}
'''
        self.assertEqual(len(parse_config(distinct).variables("A", "B")), 2)

    def test_invalid_fields_values_and_choices(self):
        records = [
            '{default_value="A",scope="User",mode="replace"}',
            '{default_value=1,scope="User",mode="replace",editable=true}',
            '{default_value="A",scope="user",mode="replace",editable=true}',
            '{default_value="A",scope="User",mode="REPLACE",editable=true}',
            '{default_value="A",scope="User",mode="replace",editable=1}',
            '{default_value="A",scope="User",mode="replace",editable=true,extra=1}',
            '{default_value="A",scope="User",mode="replace",editable=true,choices=[]}',
            '{default_value="A",scope="User",mode="replace",editable=true,choices=[{label=" ",value="B"}]}',
            '{default_value="A",scope="User",mode="replace",editable=true,choices=[{label="B",value="C"},{label="B",value="D"}]}',
            '{default_value="A",scope="User",mode="replace",editable=true,choices=[{label="B",value="C"},{label="D",value="C"}]}',
        ]
        for record in records:
            with self.subTest(record=record), self.assertRaises(ConfigError):
                parse_config("[A.B]\nPath = " + record + "\n")

    def test_save_is_validated_and_atomic(self):
        valid = '[A.B]\nPath = {default_value="",scope="User",mode="replace",editable=false}\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save_config(path, valid)
            self.assertEqual(load_config(path).variables("A", "B")[0].default_value, "")
            with self.assertRaises(ConfigError):
                save_config(path, "[A.B]\nPath = 1\n")
            self.assertEqual(path.read_text(encoding="utf-8"), valid)


class SelectionTests(unittest.TestCase):
    def test_last_empty_valid_and_source_identity(self):
        item = spec(default="", choices=(Choice("空候选", ""),))
        session = SelectionSession({item.key: ""})
        self.assertEqual((session.get(item).source, session.get(item).value), ("last", ""))
        self.assertEqual([source for source, _, _ in session.sources(item)],
                         ["default", "last", "choice:0", "custom"])
        session.choose(item, "choice:0")
        self.assertEqual(session.get(item).source, "choice:0")

    def test_invalid_last_falls_back_and_warning(self):
        item = spec(mode="+append", default="A")
        session = SelectionSession({item.key: "B\x00C"})
        self.assertEqual((session.get(item).source, session.get(item).value), ("default", "A"))
        self.assertTrue(session.get(item).warning)
        self.assertNotIn("last", [source for source, _, _ in session.sources(item)])

    def test_edit_draft_invalid_then_restore(self):
        item = spec(mode="+append", default="A", choices=(Choice("B", "B"),))
        session = SelectionSession({})
        self.assertEqual(session.edit(item, "A").source, "default")
        selection = session.edit(item, "A\x00")
        self.assertEqual((selection.source, selection.custom_draft), ("custom", "A\x00"))
        self.assertTrue(selection.error)
        session.choose(item, "choice:0")
        self.assertIsNone(selection.error)
        session.choose(item, "custom")
        self.assertEqual((selection.value, selection.custom_draft), ("A\x00", "A\x00"))
        self.assertTrue(selection.error)

    def test_noneditable_and_refresh_preserves_runtime_selection(self):
        item = spec(editable=False)
        session = SelectionSession({item.key: "B"})
        with self.assertRaises(ValidationError):
            session.edit(item, "C")
        with self.assertRaises(ValidationError):
            session.choose(item, "custom")
        session.refresh_last({item.key: "C"})
        self.assertEqual((session.get(item).source, session.get(item).value), ("last", "C"))
        session.choose(item, "default")
        session.refresh_last({item.key: "D"})
        self.assertEqual((session.get(item).source, session.get(item).value), ("default", "A"))

    def test_independent_records_and_custom_refresh(self):
        first = spec(name="A")
        second = spec(name="B")
        session = SelectionSession({first.key: "X"})
        session.edit(first, "draft")
        session.refresh_last({first.key: "Y", second.key: "Z"})
        self.assertEqual((session.get(first).source, session.get(first).value), ("custom", "draft"))
        self.assertEqual((session.get(second).source, session.get(second).value), ("last", "Z"))

    def test_reconcile_preserves_only_unchanged_record_drafts(self):
        stable = spec(name="Stable")
        changed = spec(name="Changed", default="old")
        removed = spec(name="Removed")
        session = SelectionSession({})
        session.edit(stable, "keep")
        session.edit(changed, "discard")
        session.edit(removed, "discard")
        changed_new = spec(name="Changed", default="new", editable=False)
        session.reconcile((stable, changed_new))
        self.assertEqual((session.get(stable).source, session.get(stable).value), ("custom", "keep"))
        self.assertEqual((session.get(changed_new).source, session.get(changed_new).value),
                         ("default", "new"))
        self.assertNotIn(removed.key, session._selections)


class EngineTests(unittest.TestCase):
    def test_ascii_only_normalization_and_independent_cleanup_switches(self):
        from itertools import product
        raw = ' A ;A;;\t; a ;\u00a0'
        self.assertEqual(effective_elements(raw), ('A', 'a', '\u00a0'))
        for trim, empty, dedup in product((False, True), repeat=3):
            settings = CleanupSettings(trim, empty, dedup)
            expected = []
            seen = set()
            for part in raw.split(';'):
                key = part.strip(' \t')
                if not key and empty:
                    continue
                if key and dedup and key in seen:
                    continue
                seen.add(key)
                expected.append(key if trim else part)
            with self.subTest(settings=settings):
                result = calculate(spec(), raw, state('before'), cleanup=settings)
                self.assertEqual(result.result.value, ';'.join(expected))
                self.assertEqual(effective_elements(result.result.value), effective_elements(raw))
        self.assertEqual(cleanup_value('A;A; B ;;\t;C;'), 'A;B;C')
        self.assertEqual(cleanup_value('A;a;A'), 'A;a')
        self.assertEqual(cleanup_value(' ;\t;;'), '')
        self.assertEqual(cleanup_value(''), '')

    def test_special_preserves_raw_strings_and_selection(self):
        item = VariableSpec('App', 'Default', 'Path', 'User', 'replace', True,
                            '  A;A;B  ', (Choice('空白', ' \t '),), True)
        session = SelectionSession({item.key: '\t ; A;A  '})
        self.assertEqual(session.get(item).value, '\t ; A;A  ')
        session.choose(item, 'choice:0')
        self.assertIsNone(session.get(item).error)
        for value in ('  A;A;B  ', ' \t ', ''):
            with self.subTest(value=value):
                self.assertEqual(calculate(item, value, state('before')).result.value, value)
        invalid = VariableSpec('App', 'Default', 'Path', 'User', '+append', True, 'A', special=True)
        with self.assertRaises(ValidationError):
            calculate(invalid, 'A', state('before'))
        with self.assertRaises(ValidationError):
            validate_value('A', 'replace', 1)

    def test_disabled_cleanup_preserves_unselected_raw_slices_and_duplicates(self):
        settings = CleanupSettings(False, False, False)
        for mode, expected in (('+append', 'B; A ;; A ; C '), ('-append', ' A ;; A ; C ;B')):
            with self.subTest(mode=mode):
                self.assertEqual(calculate(spec(mode=mode), 'B', state(' A ;; A ; C '), cleanup=settings).result.value,
                                 expected)
        self.assertEqual(calculate(spec(), ' A ;;A; ', state('old'), cleanup=settings).result.value, ' A ;;A; ')
        for mode in ('+append', '-append', 'append'):
            item = spec(mode=mode)
            initial = calculate(item, 'A;A', state('A;B;A;A'), cleanup=settings)
            if initial.needs_order:
                initial = calculate(item, 'A;A', state('A;B;A;A'), initial.items, settings)
            again = calculate(item, 'A;A', initial.result, cleanup=settings)
            if again.needs_order:
                again = calculate(item, 'A;A', initial.result, again.items, settings)
            with self.subTest(mode=mode):
                self.assertEqual(again.result.value, initial.result.value)
                self.assertEqual(again.result.value.split(';').count('A'), 3)

    def test_mode_copy_priority_and_ordering(self):
        self.assertEqual(calculate(spec(mode='+append'), 'A;B;A', state('B;C;A')).result.value, 'A;B;C')
        self.assertEqual(calculate(spec(mode='-append'), 'A;B;A', state('B;C;A')).result.value, 'C;B;A')
        self.assertEqual(calculate(spec(), 'A;B;A', state('B;A')).result.value, 'A;B')

    def test_replace_is_cleaned_and_preserves_type(self):
        result = calculate(spec(), "A;;B", state("old", registry_type=2))
        self.assertEqual((result.result.exists, result.result.value, result.result.registry_type),
                         (True, "A;B", 2))
        self.assertEqual(calculate(spec(), "", state("old")).result.value, "")
        created = calculate(spec(), "%JAVA_HOME%\\bin", state(exists=False))
        self.assertEqual(created.result.registry_type, 2)
        self.assertEqual(calculate(spec(), "plain", state(exists=False)).result.registry_type, 1)

    def test_front_back_dedup_and_idempotency(self):
        front = calculate(spec(mode="+append"), "a", state("A;B;A;C"))
        self.assertEqual((front.result.value, front.deduplicated), ("a;A;B;C", ("A",)))
        back = calculate(spec(mode="-append"), "a", state("A;B;A;C"))
        self.assertEqual(back.result.value, "A;B;C;a")
        self.assertEqual(calculate(spec(mode="-append"), "A", state("A;B;B;C")).result.value,
                         "B;C;A")
        again = calculate(spec(mode="+append"), "a", front.result)
        self.assertEqual(again.result.value, "a;A;B;C")

    def test_cleans_involved_target_even_with_empty_input(self):
        result = calculate(spec(mode="+append"), "X", state("A;; ;A; B ;"))
        self.assertEqual(result.result.value, "X;A;B")
        self.assertTrue(result.warnings)
        self.assertEqual(calculate(spec(mode="-append"), "", state("A;;B")).result.value,
                         "A;B")
        self.assertEqual(calculate(spec(mode="+append"), "", state(exists=False)).result.value,
                         "")

    def test_input_case_dedup_no_expanding_fold(self):
        result = calculate(spec(mode="+append"), "a;A;ß;SS", state("z"))
        self.assertEqual(result.result.value, "a;A;ß;SS;z")
        self.assertEqual(result.deduplicated, ())

    def test_append_requires_exact_multiset_confirmation(self):
        item = spec(mode="append")
        initial = calculate(item, "X;Y", state("A;B;B"))
        self.assertTrue(initial.needs_order)
        self.assertEqual(initial.items, ("A", "B", "X", "Y"))
        ordered = calculate(item, "X;Y", state("A;B;B"), ("Y", "B", "A", "X"))
        self.assertEqual(ordered.result.value, "Y;B;A;X")
        with self.assertRaises(ValidationError):
            calculate(item, "X;Y", state("A;B;B"), ("Y", "B", "A"))
        with self.assertRaises(ValidationError):
            calculate(item, "X;Y", state("A;B;B"), ("Y", "b", "A", "X"))

    def test_validation_type_scope_and_length(self):
        for value in ("A\x00B", None, 1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_value(value, "+append")
        with self.assertRaises(ValidationError):
            calculate(spec(), "A", state("old", registry_type=7))
        with self.assertRaises(ValidationError):
            calculate(spec(), "A", state("old", scope="System"))
        with self.assertRaises(ValidationError):
            validate_value("😀" * 16384, "replace")
        with self.assertRaises(ValidationError):
            calculate(spec(mode="+append"), "X", state("A" * 32766))


if __name__ == "__main__":
    unittest.main()
