"""Document find — headless search + Ctrl+F bar (Xvfb)."""

import os
import sys

# headless helpers live in thor.find.search (no GTK)
from thor.find.search import find_all, next_index, prev_index, current_index


def test_find_all_case_insensitive():
    assert find_all("hello hello HELLO", "hello") == [(0, 5), (6, 11), (12, 17)]
    assert find_all("aAa", "aa") == [(0, 2)]  # case-insensitive, non-overlapping
    assert find_all("aaa", "AA", case_sensitive=True) == []


def test_find_all_case_sensitive():
    assert find_all("hello hello HELLO", "hello", case_sensitive=True) == [(0, 5), (6, 11)]
    assert find_all("hello hello HELLO", "HELLO", case_sensitive=True) == [(12, 17)]
    assert find_all("hello", "HELLO", case_sensitive=True) == []


def test_find_all_empty_and_overlap():
    assert find_all("abc", "") == []
    assert find_all("", "a") == []
    assert find_all("aaa", "aa") == [(0, 2)]  # not (0,2),(1,3)
    assert find_all("aaaa", "aa") == [(0, 2), (2, 4)]


def test_next_prev_current():
    hits = [(0, 3), (5, 8), (10, 13)]
    assert next_index(hits, -1) == 0
    assert next_index(hits, 0) == 1
    assert next_index(hits, 2) == 1
    assert next_index(hits, 8) == 2
    assert next_index(hits, 13) == 0
    assert next_index(hits, 13, wrap=False) is None
    assert prev_index(hits, 20) == 2
    assert prev_index(hits, 6) == 0
    assert prev_index(hits, 0) == 2
    assert current_index(hits, 6) == 1
    assert current_index(hits, 4) is None
    assert next_index([], 0) is None
    assert prev_index([], 0) is None


def test_find_bar_ctrl_f():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkSource", "4")
        from gi.repository import Gtk, Gdk
        from thor.window import ThorWindow
        from thor.find import attach, detach
    except Exception as e:
        import pytest

        pytest.skip(f"no Gtk: {e}")
    if not os.environ.get("DISPLAY"):
        import pytest

        pytest.skip("no DISPLAY")

    app = Gtk.Application(application_id="dev.thor.testfind2")
    win = ThorWindow(app, initial_folder=None)
    tab = win.create_tab(jump_to=True)
    doc = tab.get_document()
    doc.set_text("foo bar foo baz foo bar")
    mgr = attach(win)
    assert mgr is not None
    assert mgr.bar is not None
    assert not mgr.is_visible()

    # show with explicit query
    mgr.show("foo")
    while Gtk.events_pending():
        Gtk.main_iteration()
    assert mgr.is_visible()
    assert mgr.bar.entry.get_text() == "foo"
    assert len(mgr._hits) == 3
    assert "3" in mgr.bar.count_label.get_text()

    # next / prev
    cur = mgr._current
    mgr._go_next()
    assert mgr._current != cur
    mgr._go_prev()
    assert mgr._current == cur

    # case toggle: "Foo" case-sensitive finds nothing
    mgr.bar.case_btn.set_active(True)
    mgr.bar.entry.set_text("Foo")
    while Gtk.events_pending():
        Gtk.main_iteration()
    assert len(mgr._hits) == 0
    assert "No results" in mgr.bar.count_label.get_text()
    # back to insensitive → 3 hits
    mgr.bar.case_btn.set_active(False)
    while Gtk.events_pending():
        Gtk.main_iteration()
    assert len(mgr._hits) == 3

    # selection as initial query: place cursor inside "bar"
    mgr.hide()
    assert not mgr.is_visible()
    it = doc.get_iter_at_offset(4)
    doc.place_cursor(it)
    mgr.show()  # no explicit query → should pick word "bar"
    while Gtk.events_pending():
        Gtk.main_iteration()
    assert mgr.bar.entry.get_text() == "bar"
    assert len(mgr._hits) == 2

    # Ctrl+F handler
    class FakeEv:
        def __init__(self, keyval, state):
            self.keyval = keyval
            self.state = state

    mgr.hide()
    key_f = Gdk.keyval_from_name("f")
    ev = FakeEv(key_f, Gdk.ModifierType.CONTROL_MASK)
    assert mgr._on_window_key(win, ev) is True
    assert mgr.is_visible()

    # Escape hides
    key_esc = Gdk.keyval_from_name("Escape")
    ev2 = FakeEv(key_esc, 0)
    assert mgr._on_window_key(win, ev2) is True
    assert not mgr.is_visible()

    # get_searchbar shim
    assert win.get_searchbar() is mgr.bar

    # Edit menu exists
    # find Edit menu by label
    menubar = win._menubar
    labels = []
    for ch in menubar.get_children():
        try:
            labels.append(ch.get_label())
        except Exception:
            pass
    assert "Edit" in labels

    detach(win)
    win.destroy()
    app.quit()
