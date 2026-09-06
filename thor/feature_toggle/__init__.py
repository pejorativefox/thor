# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

try:
    from gi.repository import GLib
except Exception:  # headless / no gi
    GLib = None  # type: ignore

GROUP = "FeatureToggle"
DEFAULTS = {
    "hide_documents_panel": True,
    "close_untitled_on_startup": True,
}


def _config_dir() -> str:
    if GLib is not None:
        try:
            base = GLib.get_user_config_dir()
        except Exception:
            base = os.path.expanduser("~/.config")
    else:
        base = os.path.expanduser("~/.config")
    return os.path.join(base, "thor", "plugins", "feature-toggle")


class SettingsStore:
    def __init__(self, path: str | None = None) -> None:
        config_dir = _config_dir()
        os.makedirs(config_dir, exist_ok=True)
        self._path = path or os.path.join(config_dir, "settings.ini")
        self._data = dict(DEFAULTS)
        self.load()

    def load(self) -> dict:
        data = dict(DEFAULTS)
        try:
            if os.path.exists(self._path):
                current_group = None
                with open(self._path, "r", encoding="utf-8") as f:
                    for raw in f:
                        line = raw.strip()
                        if not line or line.startswith(("#", ";")):
                            continue
                        if line.startswith("[") and line.endswith("]"):
                            current_group = line[1:-1]
                            continue
                        if current_group != GROUP or "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        if key in DEFAULTS:
                            data[key] = value.lower() in ("1", "true", "yes", "on")
        except Exception as e:
            logger.debug(f"settings load failed: {e!r}")
        self._data = data
        return dict(self._data)

    def save(self) -> None:
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(f"[{GROUP}]\n")
                for key in DEFAULTS:
                    value = self._data.get(key, DEFAULTS[key])
                    f.write(f"{key}={'true' if value else 'false'}\n")
            os.replace(tmp, self._path)
        except Exception as e:
            logger.debug(f"settings save failed: {e!r}")

    def get(self, key: str):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if key in DEFAULTS:
            self._data[key] = bool(value)


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


def _find_documents_widget(widget, skip: tuple = ()):
    try:
        if any(widget is owned for owned in skip):
            return None
    except Exception:
        pass
    try:
        if type(widget).__name__ == "ThorDocumentsPanel":
            return widget
    except Exception:
        return None
    try:
        children = widget.get_children()
    except Exception:
        return None
    for child in children or []:
        found = _find_documents_widget(child, skip)
        if found is not None:
            return found
    return None


def _safe(window, fn):
    try:
        return fn()
    except Exception as e:
        logger.debug(f"window accessor failed: {e!r}")
        return None


def _hide_documents_panel(window) -> None:
    try:
        if getattr(window, "_thor_feature_toggle_hidden", None) is not None:
            return
        settings = getattr(window, "_thor_feature_toggle_settings", None)
        try:
            enabled = bool(settings.get("hide_documents_panel")) if settings else True
        except Exception:
            enabled = True
        if not enabled:
            return
        side = _safe(window, lambda: window.get_side_panel())
        if side is None:
            return
        found = _find_documents_widget(side)
        if found is None:
            return
        try:
            removed = bool(side.remove_item(found))
        except Exception:
            removed = False
        if not removed:
            try:
                side.remove(found)
                removed = True
            except Exception as e:
                logger.debug(f"documents panel remove failed: {e!r}")
                return
        # store hidden widget on window
        try:
            window._thor_feature_toggle_hidden = found  # type: ignore[attr-defined]
        except Exception:
            pass
        logger.debug("documents panel hidden")
    except Exception as e:
        logger.debug(f"documents panel hide failed: {e!r}")


def _restore_documents_panel(window) -> None:
    try:
        widget = getattr(window, "_thor_feature_toggle_hidden", None)
        try:
            window._thor_feature_toggle_hidden = None  # type: ignore[attr-defined]
        except Exception:
            pass
        if widget is None:
            return
        side = _safe(window, lambda: window.get_side_panel())
        if side is None:
            return
        try:
            side.add_item(widget, "Documents", "text-x-generic")
        except Exception:
            try:
                side.add(widget)
            except Exception as e:
                logger.debug(f"documents panel restore failed: {e!r}")
    except Exception as e:
        logger.debug(f"documents panel restore failed: {e!r}")


def _close_untouched_starter_doc(window) -> None:
    try:
        settings = getattr(window, "_thor_feature_toggle_settings", None)
        if settings is not None and not bool(settings.get("close_untitled_on_startup")):
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


def attach(window, settings_path: str | None = None) -> bool:
    """Attach feature-toggle behaviour to a ThorWindow.

    - Hides the Documents side-panel if enabled.
    - Schedules closing of an untouched starter doc.
    - Hooks ``tab-added`` / ``active-tab-changed`` to re-hide panel.

    Soft-fails (returns False) when window is None or headless.
    """
    if window is None:
        return False
    try:
        # Use provided path for tests if given, otherwise default.
        if settings_path is not None:
            settings = SettingsStore(path=settings_path)
        else:
            # If window already has settings (re-attach), reuse.
            existing = getattr(window, "_thor_feature_toggle_settings", None)
            if isinstance(existing, SettingsStore):
                settings = existing
            else:
                settings = SettingsStore()
        try:
            window._thor_feature_toggle_settings = settings  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            window._thor_feature_toggle_hidden = getattr(window, "_thor_feature_toggle_hidden", None)
            if window._thor_feature_toggle_hidden is None:
                window._thor_feature_toggle_hidden = None  # type: ignore[attr-defined]
        except Exception:
            pass
        # Ensure signal list exists.
        if not hasattr(window, "_thor_feature_toggle_signal_ids"):
            try:
                window._thor_feature_toggle_signal_ids = []  # type: ignore[attr-defined]
            except Exception:
                pass

        _hide_documents_panel(window)

        # Schedule starter-doc close.
        try:
            if GLib is not None and hasattr(GLib, "idle_add"):
                try:
                    GLib.idle_add(lambda: (_close_untouched_starter_doc(window), False)[1])
                except Exception:
                    _close_untouched_starter_doc(window)
                # Flush pending idle for headless/mocked windows where no main loop runs.
                # In real Thor main loop this is harmless (idle already queued, iteration
                # will run it immediately); in headless tests it ensures deterministic close.
                try:
                    ctx = GLib.MainContext.default()  # type: ignore[union-attr]
                    # Iterate pending sources without blocking.
                    while ctx.pending():
                        ctx.iteration(False)
                except Exception:
                    # If iteration not available, fall back to direct call as safety.
                    try:
                        _close_untouched_starter_doc(window)
                    except Exception:
                        pass
            else:
                _close_untouched_starter_doc(window)
        except Exception:
            try:
                _close_untouched_starter_doc(window)
            except Exception:
                pass
        for signal in ("active-tab-changed", "tab-added"):
            try:
                handler_id = window.connect(signal, lambda *_a, w=window: _hide_documents_panel(w))
                try:
                    window._thor_feature_toggle_signal_ids.append((window, handler_id))  # type: ignore[attr-defined]
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"connect {signal} failed: {e!r}")
        return True
    except Exception as e:
        logger.debug(f"attach failed: {e!r}")
        return False


def detach(window) -> None:
    """Detach feature-toggle: restore panel and disconnect signals."""
    if window is None:
        return
    try:
        _restore_documents_panel(window)
    except Exception:
        pass
    # Disconnect signals.
    try:
        ids = list(getattr(window, "_thor_feature_toggle_signal_ids", []) or [])
        for obj, handler_id in ids:
            try:
                obj.disconnect(handler_id)  # type: ignore[attr-defined]
            except Exception:
                pass
        try:
            window._thor_feature_toggle_signal_ids = []  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception:
        pass


# Compatibility shim for plugin tests
class FeatureTogglePlugin:  # type: ignore[no-redef]
    DEFAULTS = DEFAULTS
    GROUP = GROUP
    _find_documents_widget = staticmethod(_find_documents_widget)
    _hide_documents_panel = staticmethod(_hide_documents_panel)
    _restore_documents_panel = staticmethod(_restore_documents_panel)
    _close_untouched_starter_doc = staticmethod(_close_untouched_starter_doc)
    _doc_path = staticmethod(_doc_path)
    _safe = staticmethod(_safe)
