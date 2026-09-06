"""Unit tests for lang/csharp.lang (headless, no GTK/GI needed)."""

import os
import re
import xml.etree.ElementTree as ET

REPO = os.path.join(os.path.dirname(__file__), "..")
LANG = os.path.join(REPO, "lang", "csharp.lang")
INSTALL = os.path.join(REPO, "install.sh")


def _tree():
    return ET.parse(LANG).getroot()


def test_lang_is_wellformed_xml_with_csharp_id():
    root = _tree()
    assert root.tag == "language"
    assert root.get("id") == "c-sharp"


def test_function_and_class_name_styles_mapped():
    root = _tree()
    styles = {s.get("id"): s.get("map-to") for s in root.find("styles")}
    assert styles.get("function") == "def:function"
    assert styles.get("class-name") == "def:type"


def test_declaration_and_method_contexts_wired():
    root = _tree()
    defs = root.find("definitions")
    by_id = {c.get("id"): c for c in defs.findall("context")}
    for ctx in ("type-declaration", "namespace-declaration",
                "object-creation", "method"):
        assert ctx in by_id, ctx
    refs = [c.get("ref") for c in defs.find(
        "context[@id='c-sharp']/include").findall("context")]
    for ctx in ("type-declaration", "namespace-declaration",
                "object-creation", "method"):
        assert ctx in refs, ctx
    assert refs.index("method") < refs.index("keywords")


def test_method_regex_excludes_statement_keywords():
    text = open(LANG, encoding="utf-8").read()
    m = re.search(r'<context id="method">.*?</context>',
                  text, re.DOTALL)
    assert m, "method context missing"
    for kw in ("if", "for", "foreach", "while", "switch",
               "catch", "using", "lock", "typeof", "nameof"):
        assert kw in m.group(0), kw


def test_install_ships_lang_specs():
    text = open(INSTALL, encoding="utf-8").read()
    assert "gtksourceview-4/language-specs" in text
    assert "lang/*.lang" in text
