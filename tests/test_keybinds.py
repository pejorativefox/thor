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


def _key(window):
    return lambda keyname, ctrl, shift, alt: keybinds.handle_global_key(
        window, keyname, ctrl, shift, alt
    )


def test_ctrl_page_down_advances():
    window = _FakeWindow(3)
    assert _key(window)("Page_Down", True, False, False) is True
    assert window.activated == [window.tabs[1]]


def test_ctrl_page_up_goes_back():
    window = _FakeWindow(3)
    window.active = window.tabs[1]
    assert _key(window)("Page_Up", True, False, False) is True
    assert window.activated == [window.tabs[0]]


def test_wraparound_last_to_first():
    window = _FakeWindow(3)
    window.active = window.tabs[2]
    assert _key(window)("Page_Down", True, False, False) is True
    assert window.activated == [window.tabs[0]]


def test_wraparound_first_to_last():
    window = _FakeWindow(3)
    assert _key(window)("Page_Up", True, False, False) is True
    assert window.activated == [window.tabs[2]]


def test_keypad_variants():
    window = _FakeWindow(3)
    assert _key(window)("KP_Page_Down", True, False, False) is True
    assert _key(window)("KP_Page_Up", True, False, False) is True
    assert window.activated == [window.tabs[1], window.tabs[0]]


def test_single_tab_noop():
    window = _FakeWindow(1)
    assert _key(window)("Page_Down", True, False, False) is True
    assert window.activated == []


def test_unknown_active_falls_back_to_first():
    window = _FakeWindow(3)
    window.active = object()
    assert _key(window)("Page_Down", True, False, False) is True
    assert window.activated == [window.tabs[1]]


def test_wrong_modifiers_ignored():
    window = _FakeWindow(3)
    assert _key(window)("Page_Down", True, True, False) is False
    assert _key(window)("Page_Down", True, False, True) is False
    assert _key(window)("Page_Down", False, False, False) is False
    assert _key(window)("Page_Up", True, True, False) is False
    assert window.activated == []


def test_other_keys_ignored():
    window = _FakeWindow(3)
    assert _key(window)("x", True, False, False) is False
    assert _key(window)("Tab", True, False, False) is False
    assert window.activated == []


def test_window_key_press_drives_handler():
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
    except Exception as e:
        pytest.skip(f"no Gdk ({e})")
    window = _FakeWindow(3)
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
    window = _FakeWindow(3)
    window.get_active_view = lambda: view
    return window, view, buf, store


def test_copy_no_selection_copies_line(monkeypatch):
    window, _v, buf, store = _clip_ns(monkeypatch, ["hello", "world"], cursor=0)
    assert _key(window)("c", True, False, False) is True
    assert store["text"] == "hello\n"
    assert buf.lines == ["hello", "world"]
    assert buf.cursor == 0


def test_cut_no_selection_removes_line(monkeypatch):
    window, _v, buf, store = _clip_ns(monkeypatch, ["hello", "world"], cursor=0)
    assert _key(window)("x", True, False, False) is True
    assert store["text"] == "hello\n"
    assert buf.lines == ["world"]
    assert buf.cursor == 0


def test_copy_cut_paste_with_selection_fall_through(monkeypatch):
    for key in ("c", "x", "v"):
        window, _v, buf, store = _clip_ns(
            monkeypatch, ["hello"], cursor=0, selection=True, clip_in="hello\n")
        assert _key(window)(key, True, False, False) is False
    assert store["set"] == []


def test_paste_line_block_above_current(monkeypatch):
    window, _v, buf, _s = _clip_ns(
        monkeypatch, ["aaa", "bbb"], cursor=1, clip_in="hello\n")
    assert _key(window)("v", True, False, False) is True
    assert buf.lines == ["aaa", "hello", "bbb"]


def test_paste_inline_text_falls_through(monkeypatch):
    window, _v, buf, _s = _clip_ns(monkeypatch, ["aaa"], cursor=0, clip_in="hi")
    assert _key(window)("v", True, False, False) is False
    assert buf.lines == ["aaa"]


def test_unfocused_or_missing_view_falls_through(monkeypatch):
    window, _v, _b, _s = _clip_ns(monkeypatch, ["aaa"], focus=False)
    assert _key(window)("c", True, False, False) is False
    window2 = _FakeWindow(3)
    assert _key(window2)("c", True, False, False) is False


def test_cut_last_line_deletes_text_only(monkeypatch):
    window, _v, buf, store = _clip_ns(monkeypatch, ["aaa", "bbb"], cursor=1)
    assert _key(window)("x", True, False, False) is True
    assert store["text"] == "bbb\n"
    assert buf.lines == ["aaa", ""]


def test_ctrl_w_closes_active_tab_when_editor_focused(monkeypatch):
    window, _v, _b, _s = _clip_ns(monkeypatch, ["hello"], focus=True)
    closed = []
    window.close_tab = lambda tab: closed.append(tab)
    assert _key(window)("w", True, False, False) is True
    assert closed == [window.active]


def test_ctrl_w_falls_through_when_unfocused(monkeypatch):
    window, _v, _b, _s = _clip_ns(monkeypatch, ["hello"], focus=False)
    closed = []
    window.close_tab = lambda tab: closed.append(tab)
    assert _key(window)("w", True, False, False) is False
    assert closed == []
