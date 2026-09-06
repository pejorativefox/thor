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

    # Project browser (side panel)
    try:
        from thor.project import attach as attach_project
        attach_project(window, initial_folder=initial_folder)
        logger.debug("project attached")
    except Exception as e:
        logger.exception("project attach failed: %r", e)
    # Terminal (bottom panel)
    try:
        from thor.terminal import attach as attach_terminal
        attach_terminal(window)
        logger.debug("terminal attached")
    except Exception as e:
        logger.exception("terminal attach failed: %r", e)
    # C# support (side + bottom, Roslyn optional)
    try:
        from thor.csharp import attach as attach_csharp
        attach_csharp(window, initial_folder=initial_folder)
        logger.debug("csharp attached")
    except Exception as e:
        logger.exception("csharp attach failed: %r", e)
    try:
        from thor.fuzzy import attach as attach_fuzzy
        attach_fuzzy(window)
        logger.debug("fuzzy attached")
    except Exception as e:
        logger.exception("fuzzy attach failed: %r", e)
    # Document find (Ctrl+F)
    try:
        from thor.find import attach as attach_find
        attach_find(window)
        logger.debug("find attached")
    except Exception as e:
        logger.exception("find attach failed: %r", e)
    try:
        from thor.gitdiff import attach as attach_gitdiff
        attach_gitdiff(window)
        logger.debug("gitdiff attached")
    except Exception as e:
        logger.exception("gitdiff attach failed: %r", e)
    try:
        from thor.occurrences import attach as attach_occ
        attach_occ(window)
        logger.debug("occurrences attached")
    except Exception as e:
        logger.exception("occurrences attach failed: %r", e)
    try:
        from thor.autoreload import attach as attach_autoreload
        attach_autoreload(window)
        logger.debug("autoreload attached")
    except Exception as e:
        logger.exception("autoreload attach failed: %r", e)
    try:
        from thor.keybinds import attach as attach_keybinds
        attach_keybinds(window)
        logger.debug("keybinds attached")
    except Exception as e:
        logger.exception("keybinds attach failed: %r", e)
    try:
        from thor.panel_hider import attach as attach_panel_hider
        attach_panel_hider(window)
        logger.debug("panel_hider attached")
    except Exception as e:
        logger.exception("panel_hider attach failed: %r", e)
    try:
        from thor.feature_toggle import attach as attach_feature_toggle
        attach_feature_toggle(window)
        logger.debug("feature_toggle attached")
    except Exception as e:
        logger.exception("feature_toggle attach failed: %r", e)
