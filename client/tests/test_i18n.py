"""Guard against untranslated client notices and translation fallback regressions."""
import ast
from pathlib import Path
import re
from string import Formatter

import i18n


def test_english_translations_cover_every_japanese_key_and_placeholder():
    assert set(i18n.EN) == set(i18n.JA)
    fields = lambda value: {name for _, name, _, _ in Formatter().parse(value) if name is not None}
    for key in i18n.JA:
        assert i18n.EN[key].strip(), key
        assert fields(i18n.EN[key]) == fields(i18n.JA[key]), key


def test_builtin_english_notices_do_not_contain_japanese_text():
    for key, text in i18n.EN.items():
        if key.startswith(("notify.", "toast.", "wine.")) or key in {
            "tray.already_running", "tray.instance_check_failed", "tray.startup_notice",
        }:
            assert not re.search("[ぁ-んァ-ン一-龯]", text), key


def test_literal_translation_keys_resolve_in_both_languages():
    for path in Path(i18n.__file__).parent.glob("*.py"):
        if path.name == "i18n.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t" and node.args:
                key = node.args[0]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    assert key.value in i18n.JA and key.value in i18n.EN, (path.name, node.lineno, key.value)
