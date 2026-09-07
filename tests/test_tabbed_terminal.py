"""Terminal feature behavior (headless)."""

import os

import pytest

import thor.terminal as tabbedterminal


def test_shell_is_fixed_bash(monkeypatch):
    monkeypatch.delenv("SHELL", raising=False)
    assert tabbedterminal._resolve_shell_argv() == ["/bin/bash"]
    assert tabbedterminal.PANEL_TITLE == "Terminal"


def test_unique_label_first_free():
    assert tabbedterminal.unique_label("Terminal", []) == "Terminal"
    assert tabbedterminal.unique_label("Terminal", ["Terminal"]) == "Terminal 2"
    assert tabbedterminal.unique_label("Terminal", ["Terminal", "Terminal 2"]) == "Terminal 3"
    # gap fill
    assert tabbedterminal.unique_label("Terminal", ["Terminal", "Terminal 3"]) == "Terminal 2"


def test_unique_label_accepts_set_and_tuple():
    assert tabbedterminal.unique_label("Terminal", {"Terminal"}) == "Terminal 2"
    assert tabbedterminal.unique_label("Terminal", ("Terminal",)) == "Terminal 2"


def test_keybinding_new_close():
    assert tabbedterminal.handle_global_key("t", True, True, False) == "new"
    assert tabbedterminal.handle_global_key("T", True, True, False) == "new"
    assert tabbedterminal.handle_global_key("w", True, True, False) == "close"
    assert tabbedterminal.handle_global_key("W", True, True, False) == "close"


def test_keybinding_focus_backtick():
    for name in ("grave", "quoteleft", "`"):
        assert tabbedterminal.handle_global_key(name, True, False, False) == "focus", name


def test_keybinding_rejects_wrong_modifiers():
    assert tabbedterminal.handle_global_key("t", False, True, False) is None
    assert tabbedterminal.handle_global_key("t", True, False, False) is None
    assert tabbedterminal.handle_global_key("t", True, True, True) is None
    assert tabbedterminal.handle_global_key("w", True, False, False) is None
    assert tabbedterminal.handle_global_key("grave", False, False, False) is None
    assert tabbedterminal.handle_global_key("grave", True, True, False) is None
    assert tabbedterminal.handle_global_key("x", True, True, False) is None
    assert tabbedterminal.handle_global_key("", True, True, False) is None


def _style_xml_colors():
    import xml.etree.ElementTree as ET

    path = os.path.join(os.path.dirname(__file__), "..", "styles", "atom-one-dark.xml")
    root = ET.parse(path).getroot()
    return {c.get("name"): (c.get("value") or "").upper() for c in root.iter("color")}


def test_atom_theme_matches_style_xml():
    xml = _style_xml_colors()
    theme = tabbedterminal.atom_one_dark_theme()
    assert theme["fg"] == xml["fg"]
    assert theme["bg"] == xml["bg"]
    assert theme["cursor"] == xml["cursor"]
    assert theme["highlight"] == xml["selection"]
    assert theme["palette"] == [
        xml["bg"], xml["red"], xml["green"], xml["yellow"],
        xml["blue"], xml["purple"], xml["cyan"], xml["fg"],
        xml["comment"], xml["red"], xml["green"], xml["yellow"],
        xml["blue"], xml["purple"], xml["cyan"], xml["white"],
    ]


def test_atom_theme_returns_fresh_copy():
    first = tabbedterminal.atom_one_dark_theme()
    first["palette"].append("#000000")
    assert len(tabbedterminal.atom_one_dark_theme()["palette"]) == 16


def test_palette_from_scheme_colors_fills_missing():
    palette = tabbedterminal.palette_from_scheme_colors({"fg": "#111111", "bg": "#222222"})
    assert len(palette) == 16
    assert palette[0] == "#222222"
    assert palette[7] == "#111111"
    assert palette[1] == "#111111"  # missing red falls back to fg


def test_build_vte_theme_none_without_text_colors():
    assert tabbedterminal.build_vte_theme(None) is None
    assert tabbedterminal.build_vte_theme({}) is None
    # tango-style schemes inherit the GTK theme: keep VTE defaults.
    assert tabbedterminal.build_vte_theme({"gray": "#888A85"}) is None


def test_build_vte_theme_maps_roles_to_ansi():
    colors = {
        "fg": "#ABB2BF", "bg": "#282C34", "cursor": "#528BFF",
        "selection": "#3E4451", "gray": "#5C6370", "red": "#E06C75",
        "green": "#98C379", "yellow": "#E5C07B", "blue": "#61AFEF",
        "magenta": "#C678DD", "cyan": "#56B6C2", "white": "#DCDFE4",
    }
    assert tabbedterminal.build_vte_theme(colors) == tabbedterminal.atom_one_dark_theme()


def test_apply_theme_rejects_bad_input_without_display():
    assert tabbedterminal.apply_theme_to_terminal(None, None) is False
    assert tabbedterminal.apply_theme_to_terminal(None, {"palette": []}) is False
    theme = tabbedterminal.atom_one_dark_theme()
    theme["palette"] = theme["palette"][:8]
    assert tabbedterminal.apply_theme_to_terminal(object(), theme) is False


def test_extract_atom_scheme_end_to_end():
    if tabbedterminal.GtkSource is None:
        pytest.skip("no GtkSource")
    from gi.repository import GtkSource

    manager = GtkSource.StyleSchemeManager.get_default()
    repo_styles = os.path.join(os.path.dirname(__file__), "..", "styles")
    try:
        if repo_styles not in (manager.get_search_path() or []):
            manager.append_search_path(repo_styles)
    except Exception:
        pass
    colors = tabbedterminal.extract_scheme_colors("atom-one-dark")
    assert colors and colors["fg"] == "#ABB2BF" and colors["bg"] == "#282C34"
    assert tabbedterminal.build_vte_theme(colors) == tabbedterminal.atom_one_dark_theme()


def test_current_theme_follows_editor_scheme():
    if tabbedterminal.GtkSource is None:
        pytest.skip("no GtkSource")
    if tabbedterminal.current_scheme_id() != "atom-one-dark":
        pytest.skip("editor scheme is not atom-one-dark")
    theme = tabbedterminal.current_editor_theme()
    assert theme == tabbedterminal.atom_one_dark_theme()


def test_terminal_panel_no_toolbar_buttons_or_status_label():
    if tabbedterminal.Gtk is None:
        pytest.skip("no Gtk")
    panel = tabbedterminal.TerminalPanel()
    assert not hasattr(panel, "_status")
    # Verify no button labeled "+ New" or "Close" and no label "Terminal" in panel children
    def _find_labels_and_buttons(widget):
        texts = []
        if isinstance(widget, tabbedterminal.Gtk.Label):
            texts.append(widget.get_text())
        elif isinstance(widget, tabbedterminal.Gtk.Button):
            texts.append(widget.get_label() or "")
        if hasattr(widget, "get_children"):
            for child in widget.get_children():
                texts.extend(_find_labels_and_buttons(child))
        return texts

    found = _find_labels_and_buttons(panel)
    assert "+ New" not in found
    assert "Close" not in found
    assert "Terminal" not in found