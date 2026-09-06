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
