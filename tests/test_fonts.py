"""Fonts: headless get/set roundtrip + palette registry (no display)."""

import thor.fonts as fonts
import thor.palette as palette


class _FakeWindow:
    def __init__(self):
        self._panel_state = {}


def _reset():
    fonts._CACHE.clear()


def test_defaults_empty(tmp_path, monkeypatch):
    _reset()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for kind in fonts.KINDS:
        assert fonts.current(kind) == ""


def test_set_font_roundtrip_windowless(tmp_path, monkeypatch):
    _reset()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert fonts.set_font(None, "editor", "Monospace 12") is True
    assert fonts.current("editor") == "Monospace 12"
    assert fonts.set_font(None, "bogus", "Monospace 12") is False


def test_set_font_persists_to_window_state(tmp_path, monkeypatch):
    _reset()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    win = _FakeWindow()
    assert fonts.set_font(win, "terminal", "Monospace 14") is True
    assert win._panel_state["terminal_font"] == "Monospace 14"
    assert fonts.current("terminal", win) == "Monospace 14"


def test_apply_all_headless_no_crash(tmp_path, monkeypatch):
    _reset()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    fonts.apply_all(None)
    fonts.apply_all(_FakeWindow())
    fonts.apply_to_view(None)
    fonts.apply_to_term(None)
    fonts.apply_to_panel_widget(None)


def test_palette_lists_font_commands():
    commands = palette.get_commands(_FakeWindow())
    labels = [c["label"] for c in commands]
    assert labels == ["Edit Settings file", "Select Fonts"]
