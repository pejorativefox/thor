# -*- coding: utf-8 -*-
"""Thor keybinds — tab cycling, line copy/cut/paste, tab close/reopen.

Key ownership (return True ONLY when handled, else False so the next
window key-router handler runs — see ``window.register_key_handler``):

- This module: Ctrl+PageUp/PageDown (tab cycle), Ctrl+C/X/V whole-line
  hijack (only when the focused editable view has NO selection), Ctrl+W
  (close the active document tab — only when an editor is focused),
  Ctrl+Shift+T (reopen last closed document — consumed even when the
  history stack is empty).
- panel_hider: Ctrl+B/J/E (panel toggles).
- terminal: Ctrl+Alt+T (new tab), Ctrl+` (two-way focus/reveal),
  Ctrl+Shift+W (close terminal tab — NOT window close).
- fuzzy: Ctrl+P.  palette: Ctrl+Shift+P.  find: Ctrl+F, Ctrl+G(+Shift), F3.

Keys owned by sibling features are declined via
``thor.keys.CTRL_FEATURE_KEYS`` (see that module for the canonical
ownership table).

Headless-safe: pure helpers importable without a display.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk, Gdk  # type: ignore
except Exception:  # headless
    Gtk = Gdk = None  # type: ignore[assignment]

from ..keys import decode_key_event

def _get_clipboard_text():
    """Return clipboard text or None. Uses Gtk.Clipboard.get_default."""
    if Gtk is None or Gdk is None:
        return None
    # Prefer get_default (modern) with fallback to get(SELECTION_CLIPBOARD)
    try:
        display = Gdk.Display.get_default()
        if display is not None and hasattr(Gtk.Clipboard, "get_default"):
            clipboard = Gtk.Clipboard.get_default(display)
            if clipboard is not None:
                return clipboard.wait_for_text()
    except Exception:
        pass
    # Fallback for older GTK or headless mock
    try:
        return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()  # type: ignore[union-attr]
    except Exception:
        return None

def _set_clipboard_text(text) -> bool:
    """Set clipboard text. Uses Gtk.Clipboard.get_default. Returns success."""
    if Gtk is None or Gdk is None:
        return False
    try:
        display = Gdk.Display.get_default()
        if display is not None and hasattr(Gtk.Clipboard, "get_default"):
            clipboard = Gtk.Clipboard.get_default(display)
            if clipboard is not None:
                clipboard.set_text(text, -1)
                return True
    except Exception:
        pass
    try:
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)  # type: ignore[union-attr]
        return True
    except Exception:
        return False

def _doc_location(doc):
    """Resolve Gio.File location for a Thor document, or None."""
    try:
        location = doc.get_location()
    except Exception:
        location = None
    if location is None:
        try:
            location = doc.get_file().get_location()
        except Exception:
            location = None
    return location

def _step_tab(window, direction: int) -> None:
    """Cycle active tab by direction (+1 next, -1 prev), wrapping."""
    try:
        docs = list(window.get_documents())
        if len(docs) < 2:
            return
        tabs = []
        for doc in docs:
            try:
                tab = window.get_tab_from_location(_doc_location(doc))
            except Exception:
                tab = None
            if tab is not None:
                tabs.append(tab)
        if len(tabs) < 2:
            return
        try:
            active = window.get_active_tab()
            idx = tabs.index(active)
        except Exception:
            idx = 0
        window.set_active_tab(tabs[(idx + direction) % len(tabs)])
    except Exception as e:
        logger.debug("tab step failed: %r", e, exc_info=True)

def _active_editor_view(window):
    """Return focused editable view or None."""
    try:
        view = window.get_active_view()
    except Exception:
        return None
    try:
        if view is None or not view.is_focus():
            return None
    except Exception:
        return None
    return view

def _handle_clipboard_key(lowered, view) -> bool:
    """Handle Ctrl+C/X/V line copy/cut/paste when no selection. Pure helper.

    Strict gate: only when ``view`` is focused AND editable AND the buffer
    has NO selection. Any selection, non-editable view, or inline (non
    ``\\n``-terminated) clipboard content falls through (False) so stock
    GTK bindings run. Returns True only when this handler acted.
    """
    try:
        if not view.is_focus() or not view.get_editable():
            return False
    except Exception as e:
        logger.debug("clipboard focus/edit check failed: %r", e, exc_info=True)
        return False
    try:
        buffer = view.get_buffer()
        if buffer.get_has_selection():
            return False
    except Exception as e:
        logger.debug("clipboard selection check failed: %r", e, exc_info=True)
        return False
    try:
        if lowered in ("c", "x"):
            insert = buffer.get_insert()
            line = buffer.get_iter_at_mark(insert).get_line()
            start = buffer.get_iter_at_line(line)
            end = start.copy()
            end.forward_to_line_end()
            text = buffer.get_text(start, end, True)
            _set_clipboard_text(text + "\n")
            if lowered == "c":
                return True
            buffer.begin_user_action()
            try:
                del_end = end.copy()
                if not del_end.is_end():
                    del_end.forward_char()
                buffer.delete(start, del_end)
            finally:
                try:
                    buffer.end_user_action()
                except Exception as e:
                    logger.debug("clipboard end action failed: %r", e, exc_info=True)
            try:
                target = min(line, buffer.get_line_count() - 1)
                buffer.place_cursor(buffer.get_iter_at_line(target))
            except Exception as e:
                logger.debug("clipboard cursor restore failed: %r", e, exc_info=True)
            return True
        if lowered == "v":
            text = _get_clipboard_text()
            if not text or not text.endswith("\n"):
                return False
            stripped = text[:-1]
            cursor_line = buffer.get_iter_at_mark(buffer.get_insert()).get_line()
            buffer.begin_user_action()
            try:
                buffer.insert(buffer.get_iter_at_line(cursor_line), stripped + "\n")
            finally:
                try:
                    buffer.end_user_action()
                except Exception as e:
                    logger.debug("clipboard end action failed: %r", e, exc_info=True)
            return True
        return False
    except Exception as e:
        logger.debug("clipboard key failed: %r", e, exc_info=True)
        return False

def handle_global_key(window, keyname: str, ctrl: bool, shift: bool, alt: bool) -> bool:
    """Handle a window-level key. Returns True only when handled."""
    lowered = (keyname or "").lower()
    if ctrl and shift and not alt and lowered == "t":
        # Ctrl+Shift+T reopens the last closed document (browser-style).
        # Consumed even when the stack is empty so the key never falls
        # through to another handler; Ctrl+Alt+T stays terminal-owned.
        if window is None:
            return True
        try:
            reopen = getattr(window, "reopen_last_closed", None)
            if callable(reopen):
                reopen()
        except Exception as e:
            logger.debug("reopen tab failed: %r", e, exc_info=True)
        return True
    # Owned Ctrl-only keys below; everything else falls through (False)
    # so the owning feature handler runs. Returning True here would
    # swallow sibling keys.
    if not (ctrl and not shift and not alt):
        return False
    if lowered == "w":
        # Ctrl+W closes the active document tab, but only when the focus
        # is in the editor — never from the terminal (which owns
        # Ctrl+Shift+W) or other widgets. Falls through when unfocused.
        if window is None:
            return False
        if _active_editor_view(window) is None:
            return False
        try:
            tab = window.get_active_tab()
        except Exception:
            return False
        if tab is None:
            return False
        try:
            window.close_tab(tab)
        except Exception as e:
            logger.debug("close tab failed: %r", e, exc_info=True)
            return False
        return True
    if lowered in ("page_up", "kp_page_up"):
        logger.debug("key: Ctrl+PageUp previous-tab")
        # _step_tab swallows exceptions internally; window may be None in
        # legacy no-window tests, in which case we still report handled.
        _step_tab(window, -1)  # type: ignore[arg-type]
        return True
    if lowered in ("page_down", "kp_page_down"):
        logger.debug("key: Ctrl+PageDown next-tab")
        _step_tab(window, +1)  # type: ignore[arg-type]
        return True
    if lowered in ("c", "x", "v"):
        # Needs a focused editor view. Without a window, or when the view is
        # missing/unfocused, fall through so stock GTK bindings run.
        view = _active_editor_view(window) if window is not None else None
        if view is None:
            return False
        return _handle_clipboard_key(lowered, view)
    return False

def attach(window) -> int | None:
    """Register keybinds with the window key router.

    Returns None (kept for API symmetry). Headless-safe: no-op when Gtk
    is None.
    """
    if window is None:
        return None
    if Gtk is None:
        logger.debug("Gtk not available, attach no-op")
        return None
    # avoid double attach
    if getattr(window, "_thor_keybinds_handler", None) is not None:
        return None
    try:
        window.register_key_handler(handle_global_key)
        window._thor_keybinds_handler = handle_global_key  # type: ignore[attr-defined]
        logger.debug("keybinds attached")
        return None
    except Exception as e:
        logger.debug("attach failed: %r", e, exc_info=True)
        return None

def detach(window) -> None:
    """Unregister keybinds handler from window."""
    if window is None:
        return
    handler = getattr(window, "_thor_keybinds_handler", None)
    if handler is None:
        return
    try:
        window.unregister_key_handler(handler)
    except Exception:
        pass
    try:
        delattr(window, "_thor_keybinds_handler")
    except Exception:
        pass

__all__ = [
    "attach",
    "detach",
    "handle_global_key",
    "_get_clipboard_text",
    "_set_clipboard_text",
    "_doc_location",
    "_step_tab",
    "_active_editor_view",
    "_handle_clipboard_key",
]
