"""Tests for window/panel state persistence (TOML), XDG integration, and startup focus."""

from __future__ import annotations

import json
import os
import tempfile
import pytest

from thor import state, xdg


def test_xdg_state_path():
    with tempfile.TemporaryDirectory() as tmp:
        saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = tmp
        try:
            expected = os.path.join(tmp, "thor", "state.toml")
            assert xdg.state_path() == expected
        finally:
            if saved is not None:
                os.environ["XDG_CONFIG_HOME"] = saved
            else:
                os.environ.pop("XDG_CONFIG_HOME", None)


def test_load_default_state_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        non_existent = os.path.join(tmp, "does_not_exist.toml")
        loaded = state.load_state(non_existent)
        assert loaded == state.DEFAULT_STATE
        # Ensure it's a copy
        loaded["side_panel_visible"] = False
        assert state.DEFAULT_STATE["side_panel_visible"] is True


def test_load_state_corrupt_or_partial_toml():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "corrupt.toml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("side_panel_visible = [unclosed\n")
        loaded = state.load_state(path)
        assert loaded == state.DEFAULT_STATE

        # Partial TOML
        with open(path, "w", encoding="utf-8") as f:
            f.write("side_panel_visible = false\nside_panel_size = 350\n")
        loaded2 = state.load_state(path)
        assert loaded2["side_panel_visible"] is False
        assert loaded2["side_panel_size"] == 350
        assert loaded2["bottom_panel_visible"] == state.DEFAULT_STATE["bottom_panel_visible"]
        assert loaded2["bottom_panel_size"] == state.DEFAULT_STATE["bottom_panel_size"]


def test_save_and_load_state_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "thor", "state.toml")
        custom_state = {
            "side_panel_visible": False,
            "side_panel_size": 280,
            "side_panel_active_page": 1,
            "bottom_panel_visible": True,
            "bottom_panel_size": 220,
            "bottom_panel_active_page": 2,
            "window_x": 100,
            "window_y": 150,
            "window_width": 1400,
            "window_height": 900,
            "window_maximized": True,
        }
        state.save_state(custom_state, path)
        loaded = state.load_state(path)
        assert loaded == {**state.DEFAULT_STATE, **custom_state}


def test_none_coordinates_survive_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.toml")
        custom_state = dict(state.DEFAULT_STATE)
        custom_state["side_panel_size"] = 300
        state.save_state(custom_state, path)
        loaded = state.load_state(path)
        assert loaded == custom_state
        assert loaded["window_x"] is None
        assert loaded["window_y"] is None


def test_legacy_json_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = tmp_path / "thor" / "panel_state.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"side_panel_visible": False, "side_panel_size": 350}), encoding="utf-8")
    loaded = state.load_state()
    assert loaded["side_panel_visible"] is False
    assert loaded["side_panel_size"] == 350


def test_thor_panel_target_visibility():
    pytest.importorskip("gi")
    from thor.panel import ThorPanel
    from gi.repository import Gtk

    panel = ThorPanel()
    # By default, target_visible is True, but with 0 children panel is hidden
    assert panel.get_target_visible() is True
    assert panel.get_visible() is False

    btn1 = Gtk.Button(label="Tab 1")
    panel.add_item(btn1, "tab1", "text-x-generic")
    assert panel.get_visible() is True

    # Explicitly set target visible to False
    panel.set_target_visible(False)
    assert panel.get_visible() is False

    # Adding another item should not force it visible if target_visible is False
    btn2 = Gtk.Button(label="Tab 2")
    panel.add_item(btn2, "tab2", "text-x-generic")
    assert panel.get_visible() is False

    # Setting target visible to True reveals it because it has items
    panel.set_target_visible(True)
    assert panel.get_visible() is True


def test_window_panel_state_restore_and_focus(monkeypatch):
    pytest.importorskip("gi")
    from gi.repository import Gtk, GLib
    from thor.window import ThorWindow

    with tempfile.TemporaryDirectory() as tmp:
        state_file = os.path.join(tmp, "state.toml")
        saved_state = {
            "side_panel_visible": False,
            "side_panel_size": 290,
            "side_panel_active_page": 0,
            "bottom_panel_visible": False,
            "bottom_panel_size": 210,
            "bottom_panel_active_page": 0,
        }
        state.save_state(saved_state, state_file)

        monkeypatch.setattr(xdg, "state_path", lambda: state_file)

        app = Gtk.Application(application_id="dev.thor.testpanelstate")
        win = ThorWindow(app=app, initial_folder=None)
        try:
            # Check initial state was loaded
            assert win._panel_state["side_panel_visible"] is False
            assert win._panel_state["side_panel_size"] == 290
            assert win._panel_state["bottom_panel_visible"] is False
            assert win._panel_state["bottom_panel_size"] == 210

            # Add sample items to side and bottom panels
            btn_side = Gtk.Button(label="Side Item")
            win._side_panel.add_item(btn_side, "side", "text-x-generic")

            btn_bot = Gtk.Button(label="Bot Item")
            win._bottom_panel.add_item(btn_bot, "bot", "text-x-generic")

            # Restore panel state
            win._restore_panel_state()

            # Side and bottom panels should remain hidden per saved state
            assert win._side_panel.get_visible() is False
            assert win._bottom_panel.get_visible() is False

            # Create a tab and test focus_active_editor
            tab = win.create_tab(jump_to=True)
            assert tab is not None
            assert win.get_active_tab() is tab

            # Calling focus_active_editor should not raise and should focus the view
            win.focus_active_editor()
            view = tab.get_view()
            assert view is not None

            # Regression: must return None (falsy) so it is safe to pass
            # directly to GLib.idle_add; a truthy return would re-arm the
            # idle source forever and spin the main loop.
            assert not win.focus_active_editor()

            # Changing panel visibility updates panel state file
            win._side_panel.set_target_visible(True)
            win._save_panel_state()
            reloaded = state.load_state(state_file)
            assert reloaded["side_panel_visible"] is True
        finally:
            win.destroy()


def test_window_position_and_size_persistence(monkeypatch):
    pytest.importorskip("gi")
    from gi.repository import Gtk
    from thor.window import ThorWindow

    with tempfile.TemporaryDirectory() as tmp:
        state_file = os.path.join(tmp, "state.toml")
        saved_state = {
            "side_panel_visible": True,
            "bottom_panel_visible": False,
            "side_panel_size": 260,
            "bottom_panel_size": 200,
            "side_panel_active_page": 0,
            "bottom_panel_active_page": 0,
            "window_x": 150,
            "window_y": 120,
            "window_width": 1024,
            "window_height": 768,
            "window_maximized": False,
        }
        state.save_state(saved_state, state_file)
        monkeypatch.setattr(xdg, "state_path", lambda: state_file)

        app = Gtk.Application(application_id="dev.thor.testwinpos")
        win = ThorWindow(app=app, initial_folder=None)
        try:
            assert win._panel_state["window_x"] == 150
            assert win._panel_state["window_y"] == 120
            assert win._panel_state["window_width"] == 1024
            assert win._panel_state["window_height"] == 768

            # Calling _save_panel_state persists current window position and size
            win._save_panel_state()
            reloaded = state.load_state(state_file)
            assert reloaded["window_width"] == 1024
            assert reloaded["window_height"] == 768
            assert reloaded["window_x"] is not None
            assert reloaded["window_y"] is not None
        finally:
            win.destroy()


def test_window_manager_close_persists_state(monkeypatch):
    pytest.importorskip("gi")
    from gi.repository import Gtk, Gdk
    from thor.window import ThorWindow

    with tempfile.TemporaryDirectory() as tmp:
        state_file = os.path.join(tmp, "state.toml")
        saved_state = {
            "side_panel_visible": True,
            "bottom_panel_visible": False,
            "side_panel_size": 260,
            "bottom_panel_size": 200,
            "side_panel_active_page": 0,
            "bottom_panel_active_page": 0,
            "window_x": 100,
            "window_y": 100,
            "window_width": 1100,
            "window_height": 750,
            "window_maximized": False,
        }
        state.save_state(saved_state, state_file)
        monkeypatch.setattr(xdg, "state_path", lambda: state_file)

        app = Gtk.Application(application_id="dev.thor.testwmclose")
        win = ThorWindow(app=app, initial_folder=None)

        # Add item to panel, mutate side panel visibility, and resize window in GTK
        btn = Gtk.Button(label="Item")
        win._side_panel.add_item(btn, "item", "text-x-generic")
        win._side_panel.set_target_visible(False)
        win.resize(1200, 820)
        while Gtk.events_pending():
            Gtk.main_iteration()

        # Simulate WM delete-event (clicking close 'X' button)
        event = Gdk.Event.new(Gdk.EventType.DELETE)
        handled = win.emit("delete-event", event)
        assert handled is False  # allows window to close

        # Verify state is saved on delete-event without Ctrl-Q
        reloaded = state.load_state(state_file)
        assert reloaded["side_panel_visible"] is False
        assert reloaded["window_width"] == 1200
        assert reloaded["window_height"] == 820

        win.destroy()
