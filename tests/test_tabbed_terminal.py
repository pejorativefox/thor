"""tabbed-terminal plugin behavior (headless)."""

import os
import sys
import types

import pytest


import thor.terminal as tabbedterminal
IS_THOR = True  # thor standalone, skip plugin UI tests


def test_shell_is_fixed_bash():
    assert tabbedterminal.SHELL_ARGV == ["/bin/bash"]
    assert tabbedterminal.PANEL_TITLE == "Terminal"


def test_unique_label_first_free():
    assert tabbedterminal.unique_label("Terminal", []) == "Terminal"
    assert tabbedterminal.unique_label("Terminal", ["Terminal"]) == "Terminal 2"
    assert tabbedterminal.unique_label("Terminal", ["Terminal", "Terminal 2"]) == "Terminal 3"
    # gap fill
    assert tabbedterminal.unique_label("Terminal", ["Terminal", "Terminal 3"]) == "Terminal 2"


def test_unique_label_accepts_set_and_tuple():
    if not hasattr(tabbedterminal, "TabbedTerminalPlugin"):
        import pytest; pytest.skip("TabbedTerminalPlugin not available in thor")
    assert tabbedterminal.unique_label("Terminal", {"Terminal"}) == "Terminal 2"
    assert tabbedterminal.unique_label("Terminal", ("Terminal",)) == "Terminal 2"


def test_keybinding_new_close():
    if not hasattr(tabbedterminal, "TabbedTerminalPlugin"):
        import pytest; pytest.skip("TabbedTerminalPlugin not available in thor")
    assert tabbedterminal.handle_global_key("t", True, True, False) == "new"
    assert tabbedterminal.handle_global_key("T", True, True, False) == "new"
    assert tabbedterminal.handle_global_key("w", True, True, False) == "close"
    assert tabbedterminal.handle_global_key("W", True, True, False) == "close"


def test_keybinding_focus_backtick():
    if not hasattr(tabbedterminal, "TabbedTerminalPlugin"):
        import pytest; pytest.skip("TabbedTerminalPlugin not available in thor")
    for name in ("grave", "quoteleft", "`"):
        assert tabbedterminal.handle_global_key(name, True, False, False) == "focus", name


def test_keybinding_rejects_wrong_modifiers():
    if not hasattr(tabbedterminal, "TabbedTerminalPlugin"):
        import pytest; pytest.skip("TabbedTerminalPlugin not available in thor")
    assert tabbedterminal.handle_global_key("t", False, True, False) is None
    assert tabbedterminal.handle_global_key("t", True, False, False) is None
    assert tabbedterminal.handle_global_key("t", True, True, True) is None
    assert tabbedterminal.handle_global_key("w", True, False, False) is None
    assert tabbedterminal.handle_global_key("grave", False, False, False) is None
    assert tabbedterminal.handle_global_key("grave", True, True, False) is None
    assert tabbedterminal.handle_global_key("x", True, True, False) is None
    assert tabbedterminal.handle_global_key("", True, True, False) is None


def _plugin_ns():
    cls = tabbedterminal.TabbedTerminalPlugin
    ns = types.SimpleNamespace()
    for name in ("_new_terminal", "_close_current", "_reveal", "_handle_global_key"):
        setattr(ns, name, types.MethodType(getattr(cls, name), ns))
    return ns


def test_plugin_dispatches_new_close_focus():
    if not hasattr(tabbedterminal, "TabbedTerminalPlugin"):
        import pytest; pytest.skip("TabbedTerminalPlugin not available in thor")
    ns = _plugin_ns()
    called = []
    ns.panel = types.SimpleNamespace(
        new_terminal=lambda: called.append("new"),
        close_current_terminal=lambda: called.append("close"),
        focus_current=lambda: called.append("focus"),
    )
    ns._reveal = lambda focus=False: called.append(f"reveal:{focus}")
    assert ns._handle_global_key("t", True, True, False) is True
    assert ns._handle_global_key("w", True, True, False) is True
    assert ns._handle_global_key("grave", True, False, False) is True
    assert ns._handle_global_key("x", True, False, False) is False
    assert called == ["new", "reveal:True", "close", "reveal:True"]


def test_reveal_activates_panel_and_focuses():
    if not hasattr(tabbedterminal, "TabbedTerminalPlugin"):
        import pytest; pytest.skip("TabbedTerminalPlugin not available in thor")
    if tabbedterminal.Gtk is None:
        pytest.skip("no Gtk")
    cls = tabbedterminal.TabbedTerminalPlugin
    activated = []
    bottom = types.SimpleNamespace(
        get_visible=lambda: True,
        activate_item=lambda item: activated.append(item),
        set_visible=lambda v: (_ for _ in ()).throw(AssertionError("should stay visible")),
    )
    focused = []
    ns = types.SimpleNamespace(
        panel=types.SimpleNamespace(focus_current=lambda: focused.append(True)),
        window=types.SimpleNamespace(get_bottom_panel=lambda: bottom),
    )
    ns._safe = staticmethod(cls._safe)
    ns._set_pane_action = lambda *a: True
    saved = tabbedterminal.GLib
    tabbedterminal.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
    try:
        types.MethodType(cls._reveal, ns)(focus=True)
    finally:
        tabbedterminal.GLib = saved
    assert activated == [ns.panel]
    assert focused == [True]


def test_window_key_press_drives_handler():
    if not hasattr(tabbedterminal, "TabbedTerminalPlugin"):
        import pytest; pytest.skip("TabbedTerminalPlugin not available in thor")
    if tabbedterminal.Gtk is None or tabbedterminal.Gdk is None:
        pytest.skip("no Gtk/Gdk")
    from gi.repository import Gdk

    ns = _plugin_ns()
    called = []
    ns._handle_global_key = lambda *args: (called.append(tuple(args)), True)[1]
    event = types.SimpleNamespace(
        state=int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK),
        keyval=Gdk.keyval_from_name("t"),
    )
    cls = tabbedterminal.TabbedTerminalPlugin
    assert types.MethodType(cls._on_window_key_press, ns)(None, event) is True
    assert called and called[0][0] == "t"


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