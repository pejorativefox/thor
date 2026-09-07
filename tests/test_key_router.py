"""Window key router: one dispatch point, deterministic order."""

import types

import pytest

from thor.keys import decode_key_event


def _event(keyval_name="p", ctrl=True, shift=False):
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk

        Gtk  # noqa: B018 — imported for availability check
    except Exception as e:
        pytest.skip(f"no Gtk/Gdk ({e})")
    mods = 0
    if ctrl:
        mods |= int(Gdk.ModifierType.CONTROL_MASK)
    if shift:
        mods |= int(Gdk.ModifierType.SHIFT_MASK)
    return types.SimpleNamespace(
        state=mods, keyval=Gdk.keyval_from_name(keyval_name)
    )


def _make_window():
    try:
        from gi.repository import Gtk
        from thor.window import ThorWindow
    except Exception as e:
        pytest.skip(f"no Gtk: {e}")
    if not __import__("os").environ.get("DISPLAY"):
        pytest.skip("no DISPLAY")
    app = Gtk.Application(application_id="dev.thor.testkeyrouter")
    return ThorWindow(app, initial_folder=None)


def test_handlers_run_in_registration_order():
    win = _make_window()
    try:
        calls = []
        # First handler declines everything; second claims Ctrl+P.
        win.register_key_handler(
            lambda w, k, c, s, a: (calls.append("first"), False)[1]
        )
        win.register_key_handler(
            lambda w, k, c, s, a: (calls.append("second"), k == "p" and c)[1]
        )
        assert win._on_key_press(None, _event("p")) is True
        assert calls == ["first", "second"]
        # Order holds for other keys too (both decline).
        assert win._on_key_press(None, _event("x")) is False
        assert calls == ["first", "second", "first", "second"]
    finally:
        win.destroy()


def test_first_true_wins():
    win = _make_window()
    try:
        calls = []
        win.register_key_handler(
            lambda w, k, c, s, a: (calls.append("first"), k == "p" and c)[1]
        )
        win.register_key_handler(
            lambda w, k, c, s, a: (calls.append("second"), True)[1]
        )
        assert win._on_key_press(None, _event("p")) is True
        assert calls == ["first"]
    finally:
        win.destroy()


def test_fall_through_when_no_handler_claims_key():
    win = _make_window()
    try:
        calls = []
        win.register_key_handler(
            lambda w, k, c, s, a: (calls.append("h"), False)[1]
        )
        # Ctrl+P is not a window shortcut and the handler declines:
        # unhandled keys propagate (False), not vanish.
        assert win._on_key_press(None, _event("p")) is False
        assert calls == ["h"]
    finally:
        win.destroy()


def test_handler_exception_does_not_break_dispatch():
    win = _make_window()
    try:
        calls = []

        def _boom(w, k, c, s, a):
            raise RuntimeError("boom")

        win.register_key_handler(_boom)
        win.register_key_handler(
            lambda w, k, c, s, a: (calls.append("after"), True)[1]
        )
        assert win._on_key_press(None, _event("p")) is True
        assert calls == ["after"]
    finally:
        win.destroy()


def test_unregister_removes_handler():
    win = _make_window()
    try:
        calls = []
        fn = lambda w, k, c, s, a: (calls.append("fn"), True)[1]  # noqa: E731
        win.register_key_handler(fn)
        win.unregister_key_handler(fn)
        win.register_key_handler(fn)  # re-register is fine
        win.unregister_key_handler(fn)
        win.unregister_key_handler(fn)  # idempotent
        win.register_key_handler(fn)
        assert win._on_key_press(None, _event("p")) is True
        assert calls == ["fn"]
    finally:
        win.destroy()


def test_window_shortcuts_take_priority_over_features():
    win = _make_window()
    try:
        calls = []
        win.register_key_handler(
            lambda w, k, c, s, a: (calls.append("feature"), True)[1]
        )
        # Ctrl+S is a window shortcut (save) — feature handler must not run.
        assert win._on_key_press(None, _event("s")) is True
        assert calls == []
    finally:
        win.destroy()


def test_decode_key_event_unaffected_by_router():
    event = _event("b", ctrl=True)
    assert decode_key_event(event) == ("b", True, False, False)
