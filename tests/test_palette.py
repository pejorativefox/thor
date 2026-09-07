"""Palette: default settings target, Edit Settings command, Ctrl+Shift+P (headless)."""

import os

import thor.palette as palette


def _fake_window():
    class FakeWin:
        def __init__(self):
            self.opened = []

        def open_file(self, path, jump_to=True):
            self.opened.append(path)
            return path

    return FakeWin()


def test_edit_settings_opens_default_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # The palette opens the single main config file (state.toml).
    from thor import state as state_mod

    state_mod.save_state({})
    window = _fake_window()
    commands = palette.get_commands(window)
    assert commands[0]["label"] == "Edit Settings file"
    commands[0]["run"]()
    assert window.opened == [palette.default_settings_path()]
    assert os.path.isfile(window.opened[0])


def test_filter_empty_all_fuzzy_hit_and_miss():
    labels = ["Edit Settings file"]
    assert palette.filter_commands("", labels) == [("Edit Settings file", [])]
    assert palette.filter_commands("edit", labels)[0][0] == "Edit Settings file"
    assert palette.filter_commands("zzzqqq", labels) == []


def test_ctrl_shift_p_only():
    window = _fake_window()
    mgr = palette._PaletteManager(window)
    mgr._show = lambda: setattr(mgr, "shown", True)
    assert mgr._handle_global_key("p", True, True, False) is True
    assert mgr._handle_global_key("P", True, True, False) is True
    assert mgr._handle_global_key("p", True, False, False) is False
    assert mgr._handle_global_key("o", True, True, False) is False


def test_enter_activates_selected_command():
    import os as _os

    if not _os.environ.get("DISPLAY"):
        import pytest

        pytest.skip("no DISPLAY")
    try:
        from gi.repository import Gdk
        from thor.palette import CommandPaletteDialog
    except Exception as e:
        import pytest

        pytest.skip(f"no Gtk: {e}")
    dlg = CommandPaletteDialog(parent=None)
    try:
        fired = []
        dlg.connect("activate-command", lambda _w, label: fired.append(label))
        dlg.set_commands([
            {"label": "Edit Settings file", "detail": "", "run": lambda: None},
            {"label": "Select Fonts", "detail": "", "run": lambda: None},
        ])
        assert dlg._selected_label() == "Edit Settings file"
        dlg._activate_selected()
        assert fired == ["Edit Settings file"]
        ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        ev.keyval = Gdk.keyval_from_name("Return")
        fired.clear()
        assert dlg._on_entry_key(dlg._entry, ev) is True
        assert fired == ["Edit Settings file"]
    finally:
        try:
            dlg.destroy()
        except Exception:
            pass
