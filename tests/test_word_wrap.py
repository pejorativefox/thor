"""Ctrl+R word-wrap toggle (needs display)."""

from __future__ import annotations

import os
import tempfile
import types

import pytest


def _setup_gtk():
    gi = pytest.importorskip("gi")
    try:
        # Pin before any gi.repository import: Gtk 4 is also installed and
        # a bare import would poison thor.window into its headless stub.
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
    except (ValueError, ImportError):
        pytest.skip("Gtk 3 unavailable (another version already loaded)")
    from gi.repository import Gtk, Gdk
    return Gtk, Gdk


def _make_window(Gtk):
    from thor.window import ThorWindow

    app = Gtk.Application(application_id="dev.thor.testwordwrap")
    win = ThorWindow(app=app, initial_folder=None)
    return win


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Redirect state.toml to a tmp file so toggles never touch real config."""
    from thor import xdg

    state_file = str(tmp_path / "state.toml")
    monkeypatch.setattr(xdg, "state_path", lambda: state_file)
    return state_file


def test_ctrl_r_toggles_wrap_on_all_views(isolated_state):
    Gtk, Gdk = _setup_gtk()
    win = _make_window(Gtk)
    try:
        assert win._word_wrap is False
        tab1 = win.create_tab(jump_to=True)
        tab2 = win.create_tab(jump_to=True)
        for tab in (tab1, tab2):
            assert tab.get_view().get_wrap_mode() == Gtk.WrapMode.NONE

        event = types.SimpleNamespace(
            keyval=Gdk.keyval_from_name("r"),
            state=Gdk.ModifierType.CONTROL_MASK,
        )
        assert win._on_key_press(win, event) is True
        assert win._word_wrap is True
        for tab in (tab1, tab2):
            assert tab.get_view().get_wrap_mode() == Gtk.WrapMode.WORD

        # Toggle back off.
        assert win._on_key_press(win, event) is True
        assert win._word_wrap is False
        for tab in (tab1, tab2):
            assert tab.get_view().get_wrap_mode() == Gtk.WrapMode.NONE
    finally:
        win.destroy()


def test_new_tabs_inherit_wrap_setting(isolated_state):
    Gtk, Gdk = _setup_gtk()
    win = _make_window(Gtk)
    try:
        assert win.toggle_word_wrap() is True
        tab = win.create_tab(jump_to=True)
        assert tab.get_view().get_wrap_mode() == Gtk.WrapMode.WORD
    finally:
        win.destroy()

    # Persisted: a fresh window restores wrap from state.toml.
    win2 = _make_window(Gtk)
    try:
        assert win2._word_wrap is True
        tab2 = win2.create_tab(jump_to=True)
        assert tab2.get_view().get_wrap_mode() == Gtk.WrapMode.WORD
    finally:
        win2.destroy()


def test_ctrl_r_declined_when_terminal_focused(isolated_state):
    Gtk, Gdk = _setup_gtk()
    win = _make_window(Gtk)
    try:
        # Fake a terminal panel containing the focused widget.
        panel = Gtk.Box()
        inner = Gtk.Label(label="term")
        panel.pack_start(inner, True, True, 0)
        win._thor_terminal_panel = panel
        orig_get_focus = win.get_focus
        win.get_focus = lambda: inner  # type: ignore[method-assign]
        try:
            from thor.terminal import focus_in_panel

            assert focus_in_panel(win) is True
            event = types.SimpleNamespace(
                keyval=Gdk.keyval_from_name("r"),
                state=Gdk.ModifierType.CONTROL_MASK,
            )
            assert win._on_key_press(win, event) is False
            assert win._word_wrap is False
        finally:
            win.get_focus = orig_get_focus  # type: ignore[method-assign]
            win._thor_terminal_panel = None
    finally:
        win.destroy()


def test_word_wrap_persists_to_state_file():
    _setup_gtk()
    from thor import state, xdg

    with tempfile.TemporaryDirectory() as tmp:
        state_file = os.path.join(tmp, "state.toml")
        saved = dict(state.DEFAULT_STATE)
        saved["word_wrap"] = True
        state.save_state(saved, state_file)
        assert state.load_state(state_file)["word_wrap"] is True
        assert xdg.state_path  # smoke: helper still importable
