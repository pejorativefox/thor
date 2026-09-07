"""Ctrl+Shift+O reaches the shared new-window folder dialog (needs display)."""

from __future__ import annotations

import types

import pytest


def test_ctrl_shift_o_opens_shared_folder_dialog():
    gi = pytest.importorskip("gi")
    try:
        # Pin before any gi.repository import: Gtk 4 is also installed and
        # a bare import would poison thor.window into its headless stub.
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
    except (ValueError, ImportError):
        pytest.skip("Gtk 3 unavailable (another version already loaded)")
    from gi.repository import Gtk, Gdk
    from thor.window import ThorWindow

    app = Gtk.Application(application_id="dev.thor.testopenshortcut")
    win = ThorWindow(app=app, initial_folder=None)
    try:
        calls = []
        win._app = types.SimpleNamespace(
            _prompt_open_folder=lambda: calls.append("open-folder"))
        event = types.SimpleNamespace(
            keyval=Gdk.keyval_from_name("O"),
            state=(Gdk.ModifierType.CONTROL_MASK
                   | Gdk.ModifierType.SHIFT_MASK),
        )
        assert win._on_key_press(win, event) is True
        assert calls == ["open-folder"]
    finally:
        win.destroy()


def test_same_window_switch_is_gone():
    import thor.project as projectmode

    assert not hasattr(projectmode, "_choose_root")
    assert not hasattr(projectmode, "_project_key")
