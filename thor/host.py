# -*- coding: utf-8 -*-
"""Attach built-in features to a ThorWindow.

Each feature lives in its own ``thor.*`` subpackage and exposes a plain
``attach(window)`` function.  This module is the only wiring layer: it
imports the features statically and calls their ``attach`` entry points
in a fixed order (the first window ``key-press-event`` handler to return
True wins, so order matters).  Failures are soft (log, never raise) so a
single broken feature cannot take down the editor.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkSource", "4")
    from gi.repository import Gtk  # type: ignore
except Exception:  # headless
    Gtk = None  # type: ignore

from . import autoreload as _autoreload_mod
from . import csharp as _csharp_mod
from . import feature_toggle as _feature_toggle_mod
from . import find as _find_mod
from . import fuzzy as _fuzzy_mod
from . import gitdiff as _gitdiff_mod
from . import keybinds as _keybinds_mod
from . import occurrences as _occurrences_mod
from . import palette as _palette_mod
from . import panel_hider as _panel_hider_mod
from . import project as _project_mod
from . import terminal as _terminal_mod

_project_attach = _project_mod.attach
_terminal_attach = _terminal_mod.attach
_csharp_attach = _csharp_mod.attach
_fuzzy_attach = _fuzzy_mod.attach
_palette_attach = _palette_mod.attach
_find_attach = _find_mod.attach
_gitdiff_attach = _gitdiff_mod.attach
_occurrences_attach = _occurrences_mod.attach
_autoreload_attach = _autoreload_mod.attach
_keybinds_attach = _keybinds_mod.attach
_panel_hider_attach = _panel_hider_mod.attach
_feature_toggle_attach = _feature_toggle_mod.attach

# ---------------------------------------------------------------------------
# Public entry — called once per new ThorWindow after construction
# ---------------------------------------------------------------------------
def attach_builtin_features(window, initial_folder: str | None = None) -> None:
    """Wire all built-in Thor features to *window*."""
    if window is None:
        return
    logger.debug("attach to window %r folder=%r", window, initial_folder)

    # Order matters: panels first, then features, then key handlers last
    # so window shortcuts register before feature key handlers.
    # Each attach is soft — log and continue on failure.
    for name, attach_fn, kwargs in (
        ("project", _project_attach, {"initial_folder": initial_folder}),
        ("terminal", _terminal_attach, {}),
        ("csharp", _csharp_attach, {"initial_folder": initial_folder}),
        ("fuzzy", _fuzzy_attach, {}),
        ("palette", _palette_attach, {}),
        ("find", _find_attach, {}),
        ("gitdiff", _gitdiff_attach, {}),
        ("occurrences", _occurrences_attach, {}),
        ("autoreload", _autoreload_attach, {}),
        ("keybinds", _keybinds_attach, {}),
        ("panel_hider", _panel_hider_attach, {}),
        ("feature_toggle", _feature_toggle_attach, {}),
    ):
        try:
            attach_fn(window, **kwargs)
            logger.debug("%s attached", name)
        except Exception as e:
            logger.exception("%s attach failed: %r", name, e)

def detach_builtin_features(window) -> None:
    """Undo attach_builtin_features: detach every feature in reverse order.

    Called from ThorWindow teardown so no feature leaks signal handlers,
    timers, or child processes after the window is destroyed. Failures
    are soft (log, never raise).
    """
    if window is None:
        return
    for name, mod in reversed([
        ("project", _project_mod),
        ("terminal", _terminal_mod),
        ("csharp", _csharp_mod),
        ("fuzzy", _fuzzy_mod),
        ("palette", _palette_mod),
        ("find", _find_mod),
        ("gitdiff", _gitdiff_mod),
        ("occurrences", _occurrences_mod),
        ("autoreload", _autoreload_mod),
        ("keybinds", _keybinds_mod),
        ("panel_hider", _panel_hider_mod),
        ("feature_toggle", _feature_toggle_mod),
    ]):
        fn = getattr(mod, "detach", None)
        if fn is None:
            continue
        try:
            fn(window)
            logger.debug("%s detached", name)
        except Exception as e:
            logger.debug("%s detach failed: %r", name, e, exc_info=True)


    # Restore loaded panel states (visibility, sizes, active tab)
    if hasattr(window, "_restore_panel_state"):
        try:
            window._restore_panel_state()
        except Exception as e:
            logger.debug("restore panel state failed: %r", e, exc_info=True)
    # Re-apply saved fonts (editor, terminal, side panel)
    try:
        from thor.fonts import apply_all

        apply_all(window)
    except Exception as e:
        logger.debug("restore fonts failed: %r", e, exc_info=True)

    # Always ensure the active editor view is focused on startup
    if hasattr(window, "focus_active_editor"):
        try:
            from gi.repository import GLib  # type: ignore

            GLib.idle_add(window.focus_active_editor)
        except Exception:
            try:
                window.focus_active_editor()
            except Exception:
                pass
