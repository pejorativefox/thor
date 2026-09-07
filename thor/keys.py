# -*- coding: utf-8 -*-
"""Shared GTK key-event decoding and ownership table.

Every module that binds window-level (or view-level) shortcuts repeats the
same fragile dance: mask ``event.state`` against the default modifier mask,
name the keyval, then compute ctrl/shift/alt booleans — each wrapped in its
own ``Gtk/Gdk is None`` guard and ``try/except``.  That decode logic now
lives here, plus the single source of truth for which keys are owned by
which sibling key handlers.

Key ownership (window-level ``key-press-event`` handlers; first handler to
return True wins, in connect order — see ``host.attach_builtin_plugins``):

- window (``ThorWindow._on_key_press``): Ctrl+S/O/N (+Shift) and Ctrl+Q
  (window close via the delete-event path — one process owns one window,
  so this quits exactly this window).  Must never swallow the keys below.
- fuzzy: Ctrl+P.  palette: Ctrl+Shift+P.  find: Ctrl+F, Ctrl+G(+Shift), F3.
- panel_hider: Ctrl+B/J/E.  terminal: Ctrl+Shift+T/W, Ctrl+` (+Shift+W is
  terminal close, not window close).
- keybinds: Ctrl+PageUp/Down, Ctrl+C/X/V line hijack, Ctrl+W (document tab
  close, only when an editor is focused), Ctrl+,.

The ``*_PLUGIN_KEYS`` sets are the union of keys owned by *other* handlers:
window and keybinds check these and return False so the owning handler runs.

Headless-safe: imports degrade to None and ``decode_key_event`` returns None
when GTK is unavailable, so callers just fall through (return False).
"""

from __future__ import annotations

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk, Gdk  # type: ignore
except Exception:  # headless
    Gtk = Gdk = None  # type: ignore[assignment]

# Ctrl (no shift) keys owned by sibling window-key handlers.  window and
# keybinds both decline these so the owning handler runs.
CTRL_PLUGIN_KEYS = frozenset({
    "p",        # fuzzy finder
    "b", "j", "e",  # panel_hider
    "f", "g",   # find
    "grave", "quoteleft", "asciigrave", "`",  # terminal
})

# Ctrl+Shift keys owned by siblings.  Declined by window._on_key_press.
CTRL_SHIFT_PLUGIN_KEYS = frozenset({
    "p",        # palette
    "t", "w",   # terminal (new / close tab)
    "g",        # find previous
})


def decode_key_event(event):
    """Decode a GTK key event into (keyname, ctrl, shift, alt).

    ``keyname`` is the raw ``Gdk.keyval_name`` (callers lowercase as needed,
    since some bindings match uppercase names like ``F12`` / ``KP_Enter``).
    Returns None when GTK is unavailable or decoding fails — callers should
    fall through (return False).
    """
    if Gtk is None or Gdk is None:
        return None
    try:
        mods = event.state & Gtk.accelerator_get_default_mod_mask()  # type: ignore[union-attr]
        keyname = Gdk.keyval_name(event.keyval) or ""  # type: ignore[union-attr]
        ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)  # type: ignore[union-attr]
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)  # type: ignore[union-attr]
        alt = bool(mods & Gdk.ModifierType.MOD1_MASK)  # type: ignore[union-attr]
    except Exception:
        return None
    return keyname, ctrl, shift, alt


__all__ = [
    "CTRL_PLUGIN_KEYS",
    "CTRL_SHIFT_PLUGIN_KEYS",
    "decode_key_event",
    "Gtk",
    "Gdk",
]
