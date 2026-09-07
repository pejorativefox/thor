"""Keybinds tab-switching shortcuts (headless)."""

import types

import pytest

import thor.keybinds as keybinds


class _FakeDoc:
    def __init__(self, location):
        self._location = location

    def get_location(self):
        return self._location

    def get_file(self):
        raise AttributeError("no file")


class _FakeWindow:
    def __init__(self, n):
        self.tabs = [object() for _ in range(n)]
        self.docs = [_FakeDoc(f"loc{i}") for i in range(n)]
        self._by_location = {f"loc{i}": self.tabs[i] for i in range(n)}
        self.active = self.tabs[0] if self.tabs else None
        self.activated = []

    def get_documents(self):
        return list(self.docs)

    def get_tab_from_location(self, location):
        return self._by_location[location]

    def get_active_tab(self):
        return self.active

    def set_active_tab(self, tab):
        self.activated.append(tab)
        self.active = tab


def _plugin_ns(n=3):
    window = _FakeWindow(n)
    cls = keybinds.KeybindsPlugin
    ns = types.SimpleNamespace(window=window)
    ns._doc_location = cls._doc_location
    for name in (
        "_step_tab",
        "_handle_global_key",
        "_active_editor_view",
        "_handle_clipboard_key",
        "_open_preferences",
        "_on_window_key_press",
    ):
        if hasattr(cls, name):
            setattr(ns, name, types.MethodType(getattr(cls, name), ns))
    return ns, window


def test_ctrl_page_down_advances():
    ns, window = _plugin_ns()
    assert ns._handle_global_key("Page_Down", True, False, False) is True
    assert window.activated == [window.tabs[1]]


def test_ctrl_page_up_goes_back():
    ns, window = _plugin_ns()
    window.active = window.tabs[1]
    assert ns._handle_global_key("Page_Up", True, False, False) is True
    assert window.activated == [window.tabs[0]]


def test_wraparound_last_to_first():
    ns, window = _plugin_ns()
    window.active = window.tabs[2]
    assert ns._handle_global_key("Page_Down", True, False, False) is True
    assert window.activated == [window.tabs[0]]


def test_wraparound_first_to_last():
    ns, window = _plugin_ns()
    assert ns._handle_global_key("Page_Up", True, False, False) is True
    assert window.activated == [window.tabs[2]]


def test_keypad_variants():
    ns, window = _plugin_ns()
    assert ns._handle_global_key("KP_Page_Down", True, False, False) is True
    assert ns._handle_global_key("KP_Page_Up", True, False, False) is True
    assert window.activated == [window.tabs[1], window.tabs[0]]


def test_single_tab_noop():
    ns, window = _plugin_ns(n=1)
    assert ns._handle_global_key("Page_Down", True, False, False) is True
    assert window.activated == []


def test_unknown_active_falls_back_to_first():
    ns, window = _plugin_ns()
    window.active = object()
    assert ns._handle_global_key("Page_Down", True, False, False) is True
    assert window.activated == [window.tabs[1]]


def test_wrong_modifiers_ignored():
    ns, window = _plugin_ns()
    assert ns._handle_global_key("Page_Down", True, True, False) is False
    assert ns._handle_global_key("Page_Down", True, False, True) is False
    assert ns._handle_global_key("Page_Down", False, False, False) is False
    assert ns._handle_global_key("Page_Up", True, True, False) is False
    assert window.activated == []


def test_other_keys_ignored():
    ns, window = _plugin_ns()
    assert ns._handle_global_key("x", True, False, False) is False
    assert ns._handle_global_key("Tab", True, False, False) is False
    assert window.activated == []


def test_window_key_press_drives_handler():
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
    except Exception as e:
        pytest.skip(f"no Gdk ({e})")
    _ns, window = _plugin_ns()
    event = types.SimpleNamespace(
        state=int(Gdk.ModifierType.CONTROL_MASK),
        keyval=Gdk.keyval_from_name("Page_Down"),
    )
    assert keybinds._on_window_key_press(window, event) is True
    assert window.activated == [window.tabs[1]]


class _FakeIter:
    def __init__(self, buf, line, eol=False):
        self._buf = buf
        self._line = line
        self._eol = eol

    def get_line(self):
        return self._line

    def copy(self):
        return _FakeIter(self._buf, self._line, self._eol)

    def forward_to_line_end(self):
        self._eol = True

    def forward_char(self):
        if self._eol and self._line + 1 < len(self._buf.lines):
            self._line += 1
            self._eol = False

    def is_end(self):
        return self._eol and self._line == len(self._buf.lines) - 1


class _FakeBuffer:
    def __init__(self, lines, cursor=0, selection=False):
        self.lines = list(lines)
        self.cursor = cursor
        self._selection = selection

    def get_has_selection(self):
        return self._selection

    def get_insert(self):
        return object()

    def get_iter_at_mark(self, _mark):
        return _FakeIter(self, self.cursor)

    def get_iter_at_line(self, line):
        return _FakeIter(self, max(0, min(line, len(self.lines) - 1)))

    def get_line_count(self):
        return len(self.lines)

    def get_text(self, start, _end, _include):
        return self.lines[start.get_line()]

    def delete(self, start, end):
        if end.get_line() > start.get_line():
            del self.lines[start.get_line()]
            self.cursor = min(start.get_line(), len(self.lines) - 1)
        else:
            self.lines[start.get_line()] = ""

    def insert(self, it, text):
        assert text.endswith("\n")
        self.lines.insert(it.get_line(), text[:-1])

    def place_cursor(self, it):
        self.cursor = it.get_line()

    def begin_user_action(self):
        return

    def end_user_action(self):
        return


class _FakeView:
    def __init__(self, buf, editable=True, focus=True):
        self._buf = buf
        self._editable = editable
        self._focus = focus

    def get_buffer(self):
        return self._buf

    def get_editable(self):
        return self._editable

    def is_focus(self):
        return self._focus


def _clip_ns(monkeypatch, lines, cursor=0, selection=False, editable=True,
              focus=True, clip_in=None):
    store = {"text": clip_in, "set": []}
    monkeypatch.setattr(keybinds, "_get_clipboard_text", lambda: store["text"])

    def _set(text):
        store["set"].append(text)
        store["text"] = text
        return True

    monkeypatch.setattr(keybinds, "_set_clipboard_text", _set)
    buf = _FakeBuffer(lines, cursor=cursor, selection=selection)
    view = _FakeView(buf, editable=editable, focus=focus)
    ns, window = _plugin_ns()
    window.get_active_view = lambda: view
    return ns, window, view, buf, store


def test_copy_no_selection_copies_line(monkeypatch):
    ns, _w, _v, buf, store = _clip_ns(monkeypatch, ["hello", "world"], cursor=0)
    assert ns._handle_global_key("c", True, False, False) is True
    assert store["text"] == "hello\n"
    assert buf.lines == ["hello", "world"]
    assert buf.cursor == 0


def test_cut_no_selection_removes_line(monkeypatch):
    ns, _w, _v, buf, store = _clip_ns(monkeypatch, ["hello", "world"], cursor=0)
    assert ns._handle_global_key("x", True, False, False) is True
    assert store["text"] == "hello\n"
    assert buf.lines == ["world"]
    assert buf.cursor == 0


def test_copy_cut_paste_with_selection_fall_through(monkeypatch):
    for key in ("c", "x", "v"):
        ns, _w, _v, buf, store = _clip_ns(
            monkeypatch, ["hello"], cursor=0, selection=True, clip_in="hello\n")
        assert ns._handle_global_key(key, True, False, False) is False
    assert store["set"] == []


def test_paste_line_block_above_current(monkeypatch):
    ns, _w, _v, buf, _s = _clip_ns(
        monkeypatch, ["aaa", "bbb"], cursor=1, clip_in="hello\n")
    assert ns._handle_global_key("v", True, False, False) is True
    assert buf.lines == ["aaa", "hello", "bbb"]


def test_paste_inline_text_falls_through(monkeypatch):
    ns, _w, _v, buf, _s = _clip_ns(
        monkeypatch, ["aaa"], cursor=0, clip_in="hi")
    assert ns._handle_global_key("v", True, False, False) is False
    assert buf.lines == ["aaa"]


def test_unfocused_or_missing_view_falls_through(monkeypatch):
    ns, _w, _v, _b, _s = _clip_ns(monkeypatch, ["aaa"], focus=False)
    assert ns._handle_global_key("c", True, False, False) is False
    ns2, window2 = _plugin_ns()
    assert ns2._handle_global_key("c", True, False, False) is False


def test_cut_last_line_deletes_text_only(monkeypatch):
    ns, _w, _v, buf, store = _clip_ns(monkeypatch, ["aaa", "bbb"], cursor=1)
    assert ns._handle_global_key("x", True, False, False) is True
    assert store["text"] == "bbb\n"
    assert buf.lines == ["aaa", ""]


class _FakeAction:
    def __init__(self, calls, fail=False):
        self._calls = calls
        self._fail = fail

    def activate(self):
        if self._fail:
            raise RuntimeError("boom")
        self._calls.append("EditPreferences")


class _FakeActionGroup:
    def __init__(self, actions):
        self._actions = actions

    def get_action(self, name):
        return self._actions.get(name)


class _FakeUIManager:
    def __init__(self, groups):
        self._groups = groups

    def get_action_groups(self):
        return self._groups


def _prefs_ns(calls, with_action=True, fail=False):
    ns, window = _plugin_ns()
    actions = {"EditPreferences": _FakeAction(calls, fail=fail)} if with_action else {}
    window.get_ui_manager = lambda: _FakeUIManager([_FakeActionGroup(actions)])
    return ns, window


def test_ctrl_comma_opens_preferences():
    calls = []
    ns, _w = _prefs_ns(calls)
    assert ns._handle_global_key("comma", True, False, False) is True
    assert calls == ["EditPreferences"]


def test_ctrl_comma_missing_action_falls_through():
    calls = []
    ns, _w = _prefs_ns(calls, with_action=False)
    assert ns._handle_global_key("comma", True, False, False) is False
    assert calls == []


def test_ctrl_comma_activate_failure_falls_through():
    calls = []
    ns, _w = _prefs_ns(calls, fail=True)
    assert ns._handle_global_key("comma", True, False, False) is False
    assert calls == []


def test_ctrl_comma_wrong_modifiers_ignored():
    calls = []
    ns, _w = _prefs_ns(calls)
    assert ns._handle_global_key("comma", True, True, False) is False
    assert ns._handle_global_key("comma", False, False, False) is False
    assert calls == []


def test_ctrl_w_closes_active_tab_when_editor_focused(monkeypatch):
    ns, window, _v, _b, _s = _clip_ns(monkeypatch, ["hello"], focus=True)
    closed = []
    window.close_tab = lambda tab: closed.append(tab)
    assert ns._handle_global_key("w", True, False, False) is True
    assert closed == [window.active]


def test_ctrl_w_falls_through_when_unfocused(monkeypatch):
    ns, window, _v, _b, _s = _clip_ns(monkeypatch, ["hello"], focus=False)
    closed = []
    window.close_tab = lambda tab: closed.append(tab)
    assert ns._handle_global_key("w", True, False, False) is False
    assert closed == []