"""Reopen-last-closed-document history (headless)."""

import thor.keybinds as keybinds
import thor.terminal as tabbedterminal
from thor.window import (
    CLOSED_HISTORY_LIMIT,
    closed_entry_from_tab,
    pop_closed_entry,
    push_closed_entry,
    reopen_next_entry,
)


class _FakeLoc:
    def __init__(self, path):
        self._path = path

    def get_path(self):
        return self._path


class _FakeIter:
    def __init__(self, line, col):
        self._line = line
        self._col = col

    def get_line(self):
        return self._line

    def get_line_offset(self):
        return self._col


class _FakeBuffer:
    def __init__(self, line=0, col=0):
        self._iter = _FakeIter(line, col)

    def get_insert(self):
        return object()

    def get_iter_at_mark(self, _mark):
        return self._iter


class _FakeView:
    def __init__(self, line=0, col=0):
        self._buf = _FakeBuffer(line, col)

    def get_buffer(self):
        return self._buf


class _FakeDoc:
    def __init__(self, location):
        self._location = location

    def get_location(self):
        return self._location


class _FakeTab:
    def __init__(self, location, line=0, col=0):
        self._doc = _FakeDoc(location)
        self._view = _FakeView(line, col)

    def get_document(self):
        return self._doc

    def get_view(self):
        return self._view


def test_closed_entry_captures_path_and_cursor():
    entry = closed_entry_from_tab(_FakeTab(_FakeLoc("/tmp/a.py"), 12, 4))
    assert entry == ("/tmp/a.py", 12, 4)


def test_closed_entry_accepts_plain_string_location():
    entry = closed_entry_from_tab(_FakeTab("/tmp/b.py", 3, 0))
    assert entry == ("/tmp/b.py", 3, 0)


def test_closed_entry_skips_untitled():
    assert closed_entry_from_tab(_FakeTab(None)) is None
    assert closed_entry_from_tab(_FakeTab(_FakeLoc(""))) is None
    assert closed_entry_from_tab(_FakeTab(_FakeLoc(None))) is None


def test_closed_entry_cursor_fallback_without_view():
    tab = _FakeTab(_FakeLoc("/tmp/c.py"))
    tab._view = None
    assert closed_entry_from_tab(tab) == ("/tmp/c.py", -1, -1)


def test_push_caps_oldest():
    stack: list = []
    for i in range(CLOSED_HISTORY_LIMIT + 5):
        push_closed_entry(stack, (f"/tmp/f{i}.py", i, 0))
    assert len(stack) == CLOSED_HISTORY_LIMIT
    assert stack[0] == ("/tmp/f5.py", 5, 0)
    assert stack[-1] == (f"/tmp/f{CLOSED_HISTORY_LIMIT + 4}.py", CLOSED_HISTORY_LIMIT + 4, 0)


def test_push_ignores_none_and_pop_empty():
    stack: list = []
    push_closed_entry(stack, None)
    assert stack == []
    assert pop_closed_entry(stack) is None


def test_reopen_pops_lifo_with_cursor():
    stack = [("/tmp/old.py", 1, 2), ("/tmp/new.py", 7, 8)]
    seen = []

    def _open(path, line, col):
        seen.append((path, line, col))
        return object()

    first = reopen_next_entry(stack, _open)
    assert first is not None
    assert seen == [("/tmp/new.py", 7, 8)]
    second = reopen_next_entry(stack, _open)
    assert seen == [("/tmp/new.py", 7, 8), ("/tmp/old.py", 1, 2)]
    assert second is not None
    assert stack == []


def test_reopen_skips_missing_and_bad_entries():
    stack = [("", 0, 0), ("gone", 0, 0), ("bad",), ("/tmp/ok.py", 4, 1)]

    def _open(path, line, col):
        assert (path, line, col) == ("/tmp/ok.py", 4, 1)
        return None if path == "gone" else object()

    # LIFO: ok.py first (returns tab), then bad/gone/empty skipped on later calls.
    assert reopen_next_entry(stack, _open) is not None
    assert reopen_next_entry(stack, _open) is None
    assert stack == []


def test_reopen_empty_stack_noop():
    assert reopen_next_entry([], lambda p, ln, c: object()) is None


class _FakeWindow:
    def __init__(self):
        self.calls = 0

    def reopen_last_closed(self):
        self.calls += 1
        return object()


def test_keybinds_ctrl_shift_t_reopens():
    window = _FakeWindow()
    assert keybinds.handle_global_key(window, "t", True, True, False) is True
    assert keybinds.handle_global_key(window, "T", True, True, False) is True
    assert window.calls == 2


def test_keybinds_ctrl_shift_t_consumed_without_window():
    assert keybinds.handle_global_key(None, "t", True, True, False) is True


def test_keybinds_declines_terminal_alt_t():
    window = _FakeWindow()
    assert keybinds.handle_global_key(window, "t", True, False, True) is False
    assert keybinds.handle_global_key(window, "t", True, True, True) is False
    assert window.calls == 0


def test_terminal_moved_to_alt_t():
    assert tabbedterminal.handle_global_key("t", True, False, True) == "new"
    assert tabbedterminal.handle_global_key("T", True, False, True) == "new"
    assert tabbedterminal.handle_global_key("t", True, True, False) is None
