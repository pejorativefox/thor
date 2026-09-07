"""Shared key-event decoding and ownership table (headless)."""

import types

import pytest

import thor.keys as keys


def test_decode_key_event_headless_returns_none():
    if keys.Gtk is not None:
        pytest.skip("GTK available; headless-only path not exercised")
    assert keys.decode_key_event(object()) is None


def test_decode_key_event_decodes_modifiers():
    if keys.Gtk is None or keys.Gdk is None:
        pytest.skip("no Gdk available")
    from gi.repository import Gdk

    def event(state, keyval_name):
        return types.SimpleNamespace(
            state=int(state),
            keyval=Gdk.keyval_from_name(keyval_name),
        )

    ctrl = int(Gdk.ModifierType.CONTROL_MASK)
    shift = int(Gdk.ModifierType.SHIFT_MASK)
    mod1 = int(Gdk.ModifierType.MOD1_MASK)  # type: ignore[attr-defined]

    assert keys.decode_key_event(event(0, "s")) == ("s", False, False, False)
    assert keys.decode_key_event(event(ctrl, "s")) == ("s", True, False, False)
    assert keys.decode_key_event(event(ctrl | shift, "S")) == ("S", True, True, False)
    assert keys.decode_key_event(event(ctrl | shift | mod1, "q")) == ("q", True, True, True)
