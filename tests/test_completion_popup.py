"""Completion popup keyboard tests (need a display; run under Xvfb or skip).

Covers the "popup shows but ignores keys" regression: navigation works via
the popup's own handler AND via view-level forwarding when the popup never
receives focus.
"""

import os
import sys
import types

import pytest



def _gui():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gtk, Gdk

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        if not ok:
            return None
        return Gtk, Gdk
    except Exception:
        return None


_GUI = _gui()
if _GUI is not None:
    Gtk, Gdk = _GUI
    from thor.csharp.completion import CompletionPopup, NAV_KEYS
    from thor.csharp.intelligence import CompletionItem

    _ITEMS = [
        CompletionItem(label="WriteLine", detail="void", documentation="writes"),
        CompletionItem(label="Write", detail="void", documentation="writes x"),
        CompletionItem(label="WriteAsync", detail="Task", documentation="async"),
    ]


def _popup():
    popup = CompletionPopup()
    popup.set_items(list(_ITEMS))
    return popup


def test_nav_keys_covered():
    if _GUI is None:
        pytest.skip("no display")
    for key in ("Up", "Down", "Return", "Tab", "Escape"):
        assert key in NAV_KEYS, key


def test_move_selection_wraps():
    """Like most editors, moving past the ends wraps around."""
    if _GUI is None:
        pytest.skip("no display")
    popup = _popup()
    assert popup.selected_item().label == "WriteLine"
    assert popup.move_selection(1) is True
    assert popup.selected_item().label == "Write"
    assert popup.move_selection(1) is True
    assert popup.selected_item().label == "WriteAsync"
    assert popup.move_selection(1) is True
    assert popup.selected_item().label == "WriteLine"
    assert popup.move_selection(-1) is True
    assert popup.selected_item().label == "WriteAsync"


def test_handle_nav_key_accept_and_dismiss():
    if _GUI is None:
        pytest.skip("no display")
    popup = _popup()
    activated = []
    dismissed = []
    popup.connect("item-activated", lambda _w, item: activated.append(item.label))
    popup.connect("dismissed", lambda _w: dismissed.append(True))
    popup.move_selection(1)
    assert popup.handle_nav_key("Return") is True
    assert activated == ["Write"], activated
    assert popup.handle_nav_key("bogus-key") is False
    assert popup.handle_nav_key("Escape") is True
    assert dismissed == [True]


def test_shift_tab_steps_back_without_accepting():
    if _GUI is None:
        pytest.skip("no display")
    popup = _popup()
    activated = []
    popup.connect("item-activated", lambda _w, item: activated.append(item.label))
    popup.move_selection(1)  # on Write
    assert popup.handle_nav_key("ISO_Left_Tab") is True
    assert activated == []
    assert popup.selected_item().label == "WriteLine"


def test_update_filter_narrows_and_selects_first():
    if _GUI is None:
        pytest.skip("no display")
    popup = _popup()
    popup.set_items(list(_ITEMS))
    assert [i.label for i in popup.update_filter("WriteA")] == ["WriteAsync"]
    assert popup.selected_item().label == "WriteAsync"
    assert popup.update_filter("zzz") == []
    assert popup.selected_item() is None


def test_popup_does_not_steal_focus():
    """The editor keeps focus so typing filters; the popup never grabs it."""
    if _GUI is None:
        pytest.skip("no display")
    popup = _popup()
    assert popup.get_accept_focus() is False
    assert popup.get_focus_on_map() is False


def test_show_at_view_keeps_editor_focus():
    """Regression: show_at_view must not present() (which steals focus).

    gtk_window_present() moves keyboard focus to the popup, so keystrokes
    land in the list instead of the buffer and typing never filters.
    Showing must map the window and hand focus back to the editor view.
    """
    if _GUI is None:
        pytest.skip("no display")
    popup = _popup()
    presented = []
    popup.present = lambda *a: presented.append(True)  # type: ignore[method-assign]
    grabbed = []

    class FakeBuf:
        def get_iter_at_line_offset(self, line, char):
            raise RuntimeError("no buffer")

    class FakeView:
        def get_toplevel(self):
            return None

        def get_buffer(self):
            return FakeBuf()

        def grab_focus(self):
            grabbed.append(True)

    popup.show_at_view(FakeView(), 0, 0)
    try:
        assert presented == [], "show_at_view called present(), stealing focus"
        assert grabbed, "editor view was not refocused"
        assert popup.get_visible(), "popup should be mapped"
    finally:
        popup.destroy()


def test_focus_stays_in_editor_through_show_nav_filter():
    """End-to-end focus regression: show/nav/filter must not move GTK focus."""
    if _GUI is None:
        pytest.skip("no display")
    win = Gtk.Window()
    view = Gtk.TextView()
    win.add(view)
    win.show_all()
    win.present()
    while Gtk.events_pending():
        Gtk.main_iteration()
    popup = CompletionPopup()
    assert popup.get_window_type() == Gtk.WindowType.POPUP
    try:
        view.grab_focus()
        while Gtk.events_pending():
            Gtk.main_iteration()
        popup.set_items(list(_ITEMS))
        popup.show_at_view(view, 0, 0)
        for _ in range(20):
            while Gtk.events_pending():
                Gtk.main_iteration()
        assert view.has_focus(), "show stole editor focus"
        assert not popup.is_focus()
        popup.move_selection(1)
        popup.update_filter("Wr")
        for _ in range(10):
            while Gtk.events_pending():
                Gtk.main_iteration()
        assert view.has_focus(), "nav/filter stole editor focus"
    finally:
        popup.destroy()
        win.destroy()


def test_view_forwarding_without_popup_focus():
    """The editor view forwards nav keys even if the popup never got focus."""
    if _GUI is None:
        pytest.skip("no display")
    import thor.csharp as thorcsharp

    plugin = thorcsharp.CSharpManager.__new__(thorcsharp.CSharpManager)
    popup = _popup()
    plugin.completion_popup = popup
    plugin._completion_forward = None
    activated = []
    popup.connect("item-activated", lambda _w, item: activated.append(item.label))
    popup.show()  # visible but focus stays wherever it was

    def _key(name):
        return types.SimpleNamespace(keyval=Gdk.keyval_from_name(name))

    assert plugin._forward_completion_key(None, _key("Down")) is True
    assert popup.selected_item().label == "Write"
    assert plugin._forward_completion_key(None, _key("a")) is False
    assert plugin._forward_completion_key(None, _key("Return")) is True
    assert activated == ["Write"], activated
    popup.destroy()


def test_forwarding_ignored_when_hidden():
    if _GUI is None:
        pytest.skip("no display")
    import thor.csharp as thorcsharp

    plugin = thorcsharp.CSharpManager.__new__(thorcsharp.CSharpManager)
    popup = _popup()  # never shown
    plugin.completion_popup = popup
    plugin._completion_forward = None

    def _key(name):
        return types.SimpleNamespace(keyval=Gdk.keyval_from_name(name))

    assert plugin._forward_completion_key(None, _key("Down")) is False
    popup.destroy()


def test_real_gtk_coordinate_shapes():
    """The shapes real Gtk calls return must flow through xy_of."""
    if _GUI is None:
        pytest.skip("no display")
    from thor.csharp.intelligence import xy_of

    tv = Gtk.TextView()
    w = Gtk.Window()
    w.add(tv)
    w.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()
    try:
        assert xy_of(tv.buffer_to_window_coords(Gtk.TextWindowType.TEXT, 10, 20)) == (10, 20)
        origin = tv.get_window(Gtk.TextWindowType.TEXT).get_origin()
        assert len(tuple(origin)) == 3, tuple(origin)  # (ok, x, y): the reported bug
        assert xy_of(origin) == (tuple(origin)[1], tuple(origin)[2])
    finally:
        w.destroy()
