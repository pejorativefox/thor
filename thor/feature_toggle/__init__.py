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
    "close_untitled_on_startup": True,
}


def _config_dir() -> str:
    try:
        from thor import xdg
        base = xdg.config_home()
    except Exception:
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
        try:
            config_dir = _config_dir()
            os.makedirs(config_dir, exist_ok=True)
        except OSError:
            logger.debug("feature-toggle config dir unavailable", exc_info=True)
            config_dir = _config_dir()
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
        # Atomic + pid-unique tmp (see thor.csharp.settings): concurrent
        # editors never share a tmp file; last-saver-wins on destination.
        import tempfile

        try:
            parent = os.path.dirname(self._path) or "."
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                pass
            fd, tmp = tempfile.mkstemp(dir=parent, prefix=".settings-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"[{GROUP}]\n")
                    for key in DEFAULTS:
                        value = self._data.get(key, DEFAULTS[key])
                        f.write(f"{key}={'true' if value else 'false'}\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                os.replace(tmp, self._path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
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
    """Attach startup behaviour to a ThorWindow.

    - Loads the feature settings onto the window.
    - Schedules closing of an untouched starter doc via the main loop.

    Soft-fails (returns False) when window is None.
    """
    if window is None:
        return False
    try:
        if settings_path is not None:
            settings = SettingsStore(path=settings_path)
        elif isinstance(getattr(window, "_thor_feature_toggle_settings", None), SettingsStore):
            settings = window._thor_feature_toggle_settings
        else:
            settings = SettingsStore()
        try:
            window._thor_feature_toggle_settings = settings  # type: ignore[attr-defined]
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
    for attr in ("_thor_feature_toggle_settings", "_thor_feature_toggle_signal_ids"):
        try:
            if hasattr(window, attr):
                delattr(window, attr)
        except Exception:
            pass
