"""Tab focus: switching, closing, and browser-open land in the editor (display).

Repro for: changing tabs, closing tabs, or opening a file from the file
browser must shift keyboard focus to the active document buffer, so typing
works immediately without an extra click.
"""

import os

import pytest


def _gui():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkSource", "4")
        from gi.repository import Gtk

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        if not ok or not os.environ.get("DISPLAY"):
            return None
        return Gtk
    except Exception:
        return None


_GUI = _gui()


def _pump(Gtk, rounds=30):
    for _ in range(rounds):
        while Gtk.events_pending():
            Gtk.main_iteration()


def _make_window(Gtk):
    from thor.window import ThorWindow

    app = Gtk.Application(application_id="dev.thor.testtabfocus")
    win = ThorWindow(app=app, initial_folder=None)
    win.present()
    _pump(Gtk)
    return win


def _assert_editor_focused(tab, which):
    view = tab.get_view()
    assert view is not None
    assert view.is_focus() or view.has_focus(), f"{which}: focus did not land in editor"


def test_switch_tab_focuses_editor():
    if _GUI is None:
        pytest.skip("no display")
    Gtk = _GUI
    win = _make_window(Gtk)
    try:
        tab1 = win.create_tab(jump_to=True)
        tab2 = win.create_tab(jump_to=True)
        _pump(Gtk)
        # Park focus in tab1, then switch to tab2.
        tab1.get_view().grab_focus()
        _pump(Gtk)
        win.set_active_tab(tab2)
        _pump(Gtk)
        assert win.get_active_tab() is tab2
        _assert_editor_focused(tab2, "switch")
    finally:
        win.destroy()
        _pump(Gtk)


def test_close_tab_focuses_editor():
    if _GUI is None:
        pytest.skip("no display")
    Gtk = _GUI
    win = _make_window(Gtk)
    try:
        tab1 = win.create_tab(jump_to=True)
        tab2 = win.create_tab(jump_to=True)
        _pump(Gtk)
        win.set_active_tab(tab2)
        _pump(Gtk)
        _assert_editor_focused(tab2, "close setup")
        win.close_tab(tab2)
        _pump(Gtk)
        assert win.get_active_tab() is tab1
        _assert_editor_focused(tab1, "close")
    finally:
        win.destroy()
        _pump(Gtk)


def test_browser_open_focuses_editor(tmp_path):
    if _GUI is None:
        pytest.skip("no display")
    Gtk = _GUI
    from thor.project import _open_in_thor

    win = _make_window(Gtk)
    try:
        tab1 = win.create_tab(jump_to=True)
        _pump(Gtk)
        target = tmp_path / "hello.txt"
        target.write_text("hi\n", encoding="utf-8")
        # New file via the file-browser path.
        tab1.get_view().grab_focus()
        _pump(Gtk)
        _open_in_thor(win, str(target))
        _pump(Gtk)
        opened = win.get_active_tab()
        assert opened is not None and opened is not tab1
        _assert_editor_focused(opened, "browser open (new tab)")
        # Same file again: existing-tab branch must also steal focus back.
        tab1.get_view().grab_focus()
        _pump(Gtk)
        _open_in_thor(win, str(target))
        _pump(Gtk)
        assert win.get_active_tab() is opened
        _assert_editor_focused(opened, "browser open (existing tab)")
    finally:
        win.destroy()
        _pump(Gtk)
