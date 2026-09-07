# -*- coding: utf-8 -*-
"""Startup behaviour: close the untouched starter document.

Settings live in the ``[features]`` section of the main config file
(``$XDG_CONFIG_HOME/thor/state.toml``); values from the legacy
plugin-era INI (``~/.config/thor/plugins/feature-toggle/settings.ini``)
are honored as a one-time fallback until the section exists.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

try:
    from gi.repository import GLib
except Exception:  # headless / no gi
    GLib = None  # type: ignore

SECTION = "features"

DEFAULTS = {
    "close_untitled_on_startup": True,
}


def _load_settings() -> dict:
    """Settings merged over defaults, with legacy INI fallback."""
    data = dict(DEFAULTS)
    try:
        from .. import state as _state

        section = _state.load_sections().get(SECTION)
        if isinstance(section, dict):
            for key in DEFAULTS:
                if key in section:
                    data[key] = bool(section[key])
            return data
    except Exception as e:
        logger.debug(f"feature settings load failed: {e!r}")
    # Legacy plugin-era INI (read-only migration source).
    try:
        from .. import xdg as _xdg

        base = _xdg.config_home()
    except Exception:
        base = os.path.expanduser("~/.config")
    path = os.path.join(base, "thor", "plugins", "feature-toggle", "settings.ini")
    try:
        if os.path.exists(path):
            group = None
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith(("#", ";")):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        group = line[1:-1]
                        continue
                    if group != "FeatureToggle" or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() in DEFAULTS:
                        data[key.strip()] = value.strip().lower() in ("1", "true", "yes", "on")
    except Exception as e:
        logger.debug(f"legacy feature settings read failed: {e!r}")
    return data


def _doc_path(doc) -> str | None:
    try:
        location = doc.get_location()
    except Exception:
        location = None
    if location is None:
        try:
            location = doc.get_file().get_location()
        except Exception:
            location = None
    if location is None:
        return None
    try:
        if not location.has_uri_scheme("file"):
            return None
        return location.get_path()
    except Exception:
        return None


def _close_untouched_starter_doc(window) -> None:
    try:
        settings = getattr(window, "_thor_feature_settings", None)
        if settings is not None and not bool(settings.get("close_untitled_on_startup", True)):
            return
    except Exception:
        pass
    try:
        docs = list(window.get_documents())
    except Exception:
        return
    if len(docs) != 1:
        return
    doc = docs[0]
    try:
        if not doc.is_untouched() or _doc_path(doc) is not None:
            return
    except Exception:
        return
    try:
        tab = window.get_active_tab()
    except Exception:
        tab = None
    if tab is None:
        return
    try:
        window.close_tab(tab)
        logger.debug("closed untouched starter doc")
    except Exception as e:
        logger.debug(f"starter doc close failed: {e!r}")


def attach(window, settings: dict | None = None) -> bool:
    """Attach startup behaviour to a ThorWindow.

    Stores the resolved settings dict on the window and schedules closing
    of an untouched starter doc via the main loop. Soft-fails (returns
    False) when window is None.
    """
    if window is None:
        return False
    try:
        try:
            window._thor_feature_settings = settings if settings is not None else _load_settings()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            if GLib is not None and hasattr(GLib, "idle_add"):
                GLib.idle_add(lambda: (_close_untouched_starter_doc(window), False)[1])
            else:
                _close_untouched_starter_doc(window)
        except Exception:
            try:
                _close_untouched_starter_doc(window)
            except Exception:
                pass
        return True
    except Exception as e:
        logger.debug(f"attach failed: {e!r}")
        return False


def detach(window) -> None:
    """Detach feature-toggle: drop window attributes."""
    if window is None:
        return
    try:
        if hasattr(window, "_thor_feature_settings"):
            delattr(window, "_thor_feature_settings")
    except Exception:
        pass
