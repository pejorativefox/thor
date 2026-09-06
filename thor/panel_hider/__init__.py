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

PANES_SCHEMA = "org.x.editor.preferences.ui"

# ---------------------------------------------------------------------------
# Helpers (window-aware, preserve original names/logic)
# ---------------------------------------------------------------------------

def _safe(fn):
    try:
        return fn()
    except Exception:
        return None

def _panes_settings():
    if Gio is None:
        return None
    try:
        return Gio.Settings.new(PANES_SCHEMA)
    except Exception as e:
        logger.debug(f"panes settings unavailable: {e!r}")
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
    settings = _panes_settings()
    if settings is not None:
        try:
            key = "side-panel-visible" if which == "side" else "bottom-panel-visible"
            return bool(settings.get_boolean(key))
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
                widget.set_visible(bool(value))
            except Exception as e:
                logger.debug(f"panes widget failed: {e!r}")
    settings = _panes_settings()
    if settings is None:
        return
    try:
        if side is not None:
            settings.set_boolean("side-panel-visible", bool(side))
        if bottom is not None:
            settings.set_boolean("bottom-panel-visible", bool(bottom))
    except Exception as e:
        logger.debug(f"panes set failed: {e!r}")

def _set_pane_action(window, name: str, visible: bool) -> bool:
    try:
        manager = window.get_ui_manager()
        if manager is None:
            return False
        groups = manager.get_action_groups() or []
    except Exception as e:
        logger.debug(f"pane action lookup failed: {e!r}")
        return False
    for group in groups:
        try:
            action = group.get_action(name)
        except Exception:
            continue
        if action is None:
            continue
        try:
            action.set_active(bool(visible))
            return True
        except Exception:
            try:
                action.activate()
                return True
            except Exception as e:
                logger.debug(f"pane action activate failed: {e!r}")
                return False
    return False

def _hide_all_panels(window) -> None:
    logger.debug("panels: hiding side + bottom")
    ok_side = _set_pane_action(window, "ViewSidePane", False)
    ok_bottom = _set_pane_action(window, "ViewBottomPane", False)
    if not ok_side or not ok_bottom:
        _set_panes(window, side=None if ok_side else False, bottom=None if ok_bottom else False)

def _pane_geometry(window, which: str):
    try:
        widget = _panel_widget(window, which)
        if widget is None or Gio is None:
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
            state = Gio.Settings.new("org.x.editor.state.window")
            key = "bottom-panel-size" if which == "bottom" else "side-panel-size"
            saved = int(state.get_int(key))
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

def _toggle_pane(window, action_name: str, which: str) -> None:
    target = not _pane_visible(window, which)
    if _set_pane_action(window, action_name, target):
        logger.debug(f"panels: {which} toggled via menu action")
    else:
        logger.debug(f"panels: {which} -> {target} (fallback)")
        _set_panes(window, **{which: target})
    if target:
        _ensure_pane_size(window, which)

def _toggle_bottom_panel(window) -> None:
    _toggle_pane(window, "ViewBottomPane", "bottom")

def _toggle_side_panel(window) -> None:
    _toggle_pane(window, "ViewSidePane", "side")

def _handle_global_key(window, keyname: str, ctrl: bool, shift: bool, alt: bool) -> bool:
    if ctrl and not shift and not alt and keyname.lower() == "b":
        logger.debug("key: Ctrl+B hide-panels")
        _hide_all_panels(window)
        return True
    if ctrl and not shift and not alt and keyname.lower() == "j":
        logger.debug("key: Ctrl+J toggle-bottom-panel")
        _toggle_bottom_panel(window)
        return True
    if ctrl and not shift and not alt and keyname.lower() == "e":
        logger.debug("key: Ctrl+E toggle-side-panel")
        _toggle_side_panel(window)
        return True
    return False

def _on_window_key_press(window, event) -> bool:
    if Gtk is None or Gdk is None:
        return False
    try:
        mods = event.state & Gtk.accelerator_get_default_mod_mask()
        keyname = Gdk.keyval_name(event.keyval) or ""
        ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(mods & Gdk.ModifierType.MOD1_MASK)
    except Exception:
        return False
    return _handle_global_key(window, keyname, ctrl, shift, alt)

# ---------------------------------------------------------------------------
# Public API — attach/detach for ThorWindow
# ---------------------------------------------------------------------------

def attach(window) -> int | None:
    """Wire panel-hider keybindings to *window*.

    Connects ``key-press-event`` and toggles side/bottom panels:
    Ctrl+B hide both, Ctrl+J toggle bottom, Ctrl+E toggle side.
    Soft-fails (returns None) when Gtk is unavailable.
    """
    if Gtk is None or window is None:
        return None
    # avoid double-attach
    try:
        existing = getattr(window, "_thor_panel_hider_key_id", None)
        if existing is not None:
            return existing
    except Exception:
        pass
    try:
        hid = window.connect("key-press-event", _on_window_key_press)
    except Exception as e:
        logger.debug(f"window keys connect failed: {e!r}")
        return None
    try:
        window._thor_panel_hider_key_id = hid  # type: ignore[attr-defined]
    except Exception:
        pass
    return hid

def detach(window) -> None:
    """Disconnect panel-hider key handler from *window*."""
    if window is None:
        return
    hid = None
    try:
        hid = getattr(window, "_thor_panel_hider_key_id", None)
    except Exception:
        pass
    if hid is not None:
        try:
            window.disconnect(hid)
        except Exception:
            pass
        try:
            delattr(window, "_thor_panel_hider_key_id")
        except Exception:
            try:
                window._thor_panel_hider_key_id = None  # type: ignore[attr-defined]
            except Exception:
                pass


# Compatibility shim for plugin tests
class PanelHiderPlugin:  # type: ignore[no-redef]
    PANES_SCHEMA = PANES_SCHEMA
    _safe = staticmethod(_safe)
    _panel_widget = staticmethod(_panel_widget)
    _pane_visible = staticmethod(_pane_visible)
    _panes_settings = staticmethod(_panes_settings)
    _set_panes = staticmethod(_set_panes)
    _set_pane_action = staticmethod(_set_pane_action)
    _pane_geometry = staticmethod(_pane_geometry)
    _fix_pane_size = staticmethod(_fix_pane_size)
    _ensure_pane_size = staticmethod(_ensure_pane_size)
    _delayed_pane_size = staticmethod(_delayed_pane_size)
    _toggle_pane = staticmethod(_toggle_pane)
    _hide_all_panels = staticmethod(_hide_all_panels)
    _toggle_bottom_panel = staticmethod(_toggle_bottom_panel)
    _toggle_side_panel = staticmethod(_toggle_side_panel)
    _handle_global_key = staticmethod(_handle_global_key)
    _on_window_key_press = staticmethod(_on_window_key_press)
