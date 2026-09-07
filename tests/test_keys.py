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


def test_ownership_constants_cover_sibling_keys():
    # Keys owned by sibling feature key handlers must be declined by window
    # and keybinds so the owning handler runs.
    assert {"p", "b", "j", "e", "f", "g", "grave", "quoteleft", "asciigrave", "`"} == keys.CTRL_FEATURE_KEYS
    assert {"p", "t", "w", "g"} == keys.CTRL_SHIFT_FEATURE_KEYS
    # Window/global ctrl keys are intentionally NOT in the feature sets.
    assert not {"s", "o", "n", "w", "c", "x", "v"} & keys.CTRL_FEATURE_KEYS
