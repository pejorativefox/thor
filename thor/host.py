# -*- coding: utf-8 -*-
"""Attach built-in features to a ThorWindow.

Thor bakes plugins directly in-process, without libpeas
or the typelib.  Each feature lives in its own ``thor.*`` subpackage
and exposes a plain ``attach(window)`` function — no ``GObject``/
``WindowActivatable`` indirection, no dummy ``gi.repository.Thor``,
no ``importlib.util.spec_from_file_location`` hacks.

This module is the only wiring layer: it imports the scoped ``thor.*``
features normally and calls their ``attach`` entry points.  Failures are
soft (stderr log, never raise) so a single broken feature cannot take
down the editor.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkSource", "4")
    from gi.repository import Gtk  # type: ignore
except Exception:  # headless
    Gtk = None  # type: ignore

# ---------------------------------------------------------------------------
# Public entry — called once per new ThorWindow after construction
# ---------------------------------------------------------------------------
def attach_builtin_plugins(window, initial_folder: str | None = None) -> None:
    """Wire all built-in Thor features to *window*."""
    if window is None:
        return
    logger.debug("attach to window %r folder=%r", window, initial_folder)

    # Order matters a bit: side panel first, then bottom, then key handlers.
    # Each attach is soft — log and continue on failure.
    _features: list[tuple[str, str, dict]] = [
        ("project", "thor.project", {"initial_folder": initial_folder}),
        ("terminal", "thor.terminal", {}),
        ("csharp", "thor.csharp", {"initial_folder": initial_folder}),
        ("fuzzy", "thor.fuzzy", {}),
        ("palette", "thor.palette", {}),
        ("find", "thor.find", {}),
        ("gitdiff", "thor.gitdiff", {}),
        ("occurrences", "thor.occurrences", {}),
        ("autoreload", "thor.autoreload", {}),
        ("keybinds", "thor.keybinds", {}),
        ("panel_hider", "thor.panel_hider", {}),
        ("feature_toggle", "thor.feature_toggle", {}),
    ]
    for name, module, kwargs in _features:
        try:
            mod = importlib.import_module(module)
            mod.attach(window, **kwargs)
            logger.debug("%s attached", name)
        except Exception as e:
            logger.exception("%s attach failed: %r", name, e)

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
