# -*- coding: utf-8 -*-
"""Panel hider — toggle side/bottom panels via Ctrl+B/J/E."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GTK imports — soft-fail headless
# ---------------------------------------------------------------------------

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk, Gdk, Gio, GLib  # type: ignore
except Exception:  # headless / missing typelib
    Gtk = Gdk = Gio = GLib = None  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Helpers (window-aware, preserve original names/logic)
# ---------------------------------------------------------------------------

def _safe(fn):
    try:
        return fn()
    except Exception:
        return None

def _panel_widget(window, which: str):
    try:
        get = window.get_side_panel if which == "side" else window.get_bottom_panel
        return _safe(get)
    except Exception:
        return None

def _pane_visible(window, which: str) -> bool:
    widget = _panel_widget(window, which)
    if widget is not None:
        try:
            return bool(widget.is_visible())
        except Exception:
            pass
        try:
            return bool(widget.get_visible())
        except Exception:
            pass
    return True

def _set_panes(window, side=None, bottom=None) -> None:
    for which, value in (("side", side), ("bottom", bottom)):
        if value is None:
            continue
        widget = _panel_widget(window, which)
        if widget is not None:
            try:
                if hasattr(widget, "set_target_visible"):
                    widget.set_target_visible(bool(value))
                else:
                    widget.set_visible(bool(value))
            except Exception as e:
                logger.debug(f"panes widget failed: {e!r}")

def _hide_all_panels(window) -> None:
    logger.debug("panels: hiding side + bottom")
    _set_panes(window, side=False, bottom=False)

def _show_all_panels(window) -> None:
    logger.debug("panels: showing side + bottom")
    _set_panes(window, side=True, bottom=True)

def _toggle_all_panels(window) -> None:
    """Two-way focus-mode toggle: hide when anything is visible, else show."""
    try:
        side_vis = _pane_visible(window, "side")
    except Exception as e:
        logger.debug("toggle side visible failed: %r", e, exc_info=True)
        side_vis = True
    try:
        bottom_vis = _pane_visible(window, "bottom")
    except Exception as e:
        logger.debug("toggle bottom visible failed: %r", e, exc_info=True)
        bottom_vis = True
    if side_vis or bottom_vis:
        _hide_all_panels(window)
    else:
        _show_all_panels(window)
def _pane_geometry(window, which: str):
    try:
        widget = _panel_widget(window, which)
        if widget is None:
            return None
        paned = widget.get_parent()
        pos = int(paned.get_position())
        try:
            max_pos = int(paned.get_property("max-position"))
        except Exception:
            max_pos = -1
        try:
            if paned.get_child1() is widget:
                pane_number = 1
            elif paned.get_child2() is widget:
                pane_number = 2
            else:
                pane_number = 0
        except Exception:
            pane_number = 0
        try:
            key = "bottom_panel_size" if which == "bottom" else "side_panel_size"
            saved = int((getattr(window, "_panel_state", None) or {}).get(key) or 0)
        except Exception:
            saved = 0
        if saved <= 0:
            saved = 300 if which == "bottom" else 250
        return paned, pane_number, pos, max_pos, saved
    except Exception as e:
        logger.debug(f"panels: {which} geometry probe failed: {e!r}")
        return None

def _fix_pane_size(window, which: str) -> bool:
    info = _pane_geometry(window, which)
    if info is None:
        return False
    paned, pane_number, pos, max_pos, saved = info
    target = None
    if pane_number == 2 and max_pos > 0:
        if pos >= max_pos - 1 or pos <= 0:
            target = max(0, max_pos - saved)
    elif pane_number == 1:
        if pos <= 1:
            target = saved
    elif pos <= 0:
        target = saved
    if target is None:
        return False
    try:
        paned.set_position(target)
        logger.debug(f"panels: {which} size restored to {target}")
        return True
    except Exception as e:
        logger.debug(f"panes size restore failed: {e!r}")
        return False

def _ensure_pane_size(window, which: str) -> None:
    _fix_pane_size(window, which)
    if GLib is None:
        return
    try:
        GLib.timeout_add(250, lambda: _delayed_pane_size(window, which))
    except Exception:
        pass

def _delayed_pane_size(window, which: str) -> bool:
    try:
        _fix_pane_size(window, which)
    except Exception as e:
        logger.debug(f"panes delayed size failed: {e!r}")
    return False

def _toggle_pane(window, which: str) -> None:
    target = not _pane_visible(window, which)
    logger.debug(f"panels: {which} -> {target}")
    _set_panes(window, **{which: target})
    if target:
        _ensure_pane_size(window, which)

def _toggle_bottom_panel(window) -> None:
    _toggle_pane(window, "bottom")

def _toggle_side_panel(window) -> None:
    _toggle_pane(window, "side")

def _handle_global_key(window, keyname: str, ctrl: bool, shift: bool, alt: bool) -> bool:
    # Owned keys only: Ctrl+B/J/E without shift/alt. Everything else —
    # notably Ctrl+P (fuzzy), Ctrl+Shift+P (palette), Ctrl+, (keybinds),
    # Ctrl+W / Ctrl+Shift+T (keybinds), Ctrl+Alt+T / Ctrl+Shift+W and
    # Ctrl+` (terminal) — falls
    # through (False) so the owning handler runs.
    lowered = (keyname or "").lower()
    if ctrl and not shift and not alt and lowered == "b":
        logger.debug("key: Ctrl+B toggle-panels (two-way)")
        _toggle_all_panels(window)
        return True
    if ctrl and not shift and not alt and lowered == "j":
        logger.debug("key: Ctrl+J toggle-bottom-panel")
        _toggle_bottom_panel(window)
        return True
    if ctrl and not shift and not alt and lowered == "e":
        logger.debug("key: Ctrl+E toggle-side-panel")
        _toggle_side_panel(window)
        return True
    return False

# ---------------------------------------------------------------------------
# Public API — attach/detach for ThorWindow
# ---------------------------------------------------------------------------

def attach(window) -> int | None:
    """Wire panel-hider keybindings to *window*.

    Registers with the window key router and toggles side/bottom
    panels: Ctrl+B hide both, Ctrl+J toggle bottom, Ctrl+E toggle side.
    Soft-fails (returns None) when Gtk is unavailable.
    """
    if Gtk is None or window is None:
        return None
    # avoid double-attach
    try:
        if getattr(window, "_thor_panel_hider_key_handler", None) is not None:
            return None
    except Exception:
        pass
    try:
        window.register_key_handler(_handle_global_key)
        window._thor_panel_hider_key_handler = _handle_global_key  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug(f"window keys register failed: {e!r}")
        return None
    return None

def detach(window) -> None:
    """Disconnect panel-hider key handler from *window*."""
    if window is None:
        return
    handler = getattr(window, "_thor_panel_hider_key_handler", None)
    if handler is not None:
        try:
            window.unregister_key_handler(handler)
        except Exception:
            pass
        try:
            delattr(window, "_thor_panel_hider_key_handler")
        except Exception:
            pass
