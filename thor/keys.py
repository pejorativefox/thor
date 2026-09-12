# -*- coding: utf-8 -*-
"""Shared GTK key-event decoding and ownership table.

Every module that binds window-level (or view-level) shortcuts repeats the
same fragile dance: mask ``event.state`` against the default modifier mask,
name the keyval, then compute ctrl/shift/alt booleans — each wrapped in its
own ``Gtk/Gdk is None`` guard and ``try/except``.  That decode logic now
lives here, plus the single source of truth for which keys are owned by
which feature key handlers.

Key ownership (the window's single key router dispatches to feature
handlers in attach order — see ``window.register_key_handler`` and
``host.attach_builtin_features``; each handler only ever returns True
for the keys it owns):

- window (``ThorWindow._on_key_press``): Ctrl+S/O/N (+Shift), Ctrl+Q
  (window close via the delete-event path — one process owns one window,
  so this quits exactly this window), and Ctrl+R (word-wrap toggle,
  declined when the terminal has focus).  Must never swallow the keys below.
- fuzzy: Ctrl+P.  palette: Ctrl+Shift+P.  find: Ctrl+F, Ctrl+G(+Shift), F3.
- panel_hider: Ctrl+B/J/E.  terminal: Ctrl+Alt+T (new tab), Ctrl+Shift+W,
  Ctrl+` (+Shift+W is
  terminal close, not window close).  keybinds: Ctrl+Shift+T (reopen last
  closed document).
- keybinds: Ctrl+PageUp/Down, Ctrl+C/X/V line hijack, Ctrl+W (document tab
  close, only when an editor is focused), Ctrl+,.

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
    "decode_key_event",
    "Gtk",
    "Gdk",
]
