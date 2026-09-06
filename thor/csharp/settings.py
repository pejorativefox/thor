"""INI settings store. Avoids GSettings schemas (same rationale as terminal)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

try:
    from gi.repository import GLib
except Exception:  # headless unit tests
    GLib = None  # type: ignore

GROUP = "CSharp"

DEFAULTS = {
    "dotnet_executable": "dotnet",
    "roslyn_server": "~/.dotnet/tools/roslyn-language-server",
    "roslyn_log_level": "Information",
    "auto_restore": True,
    "format_on_save": False,
    "test_framework_filter": "",
}


def _config_dir() -> str:
    if GLib is not None:
        try:
            base = GLib.get_user_config_dir()
        except Exception:
            base = os.path.expanduser("~/.config")
    else:
        base = os.path.expanduser("~/.config")
    return os.path.join(base, "thor", "plugins", "thor-csharp")


class SettingsStore:
    def __init__(self, path: str | None = None) -> None:
        config_dir = _config_dir()
        os.makedirs(config_dir, exist_ok=True)
        self._path = path or os.path.join(config_dir, "settings.ini")
        self._data = dict(DEFAULTS)
        self.load()

    @property
    def path(self) -> str:
        return self._path

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
                            default = DEFAULTS[key]
                            if isinstance(default, bool):
                                data[key] = value.lower() in ("1", "true", "yes", "on")
                            else:
                                data[key] = value
        except Exception as e:
            logger.debug(f"settings load failed: {e!r}", exc_info=True)
        self._data = data
        return dict(self._data)

    def save(self) -> None:
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(f"[{GROUP}]\n")
                for key in DEFAULTS:
                    value = self._data.get(key, DEFAULTS[key])
                    if isinstance(value, bool):
                        value = "true" if value else "false"
                    f.write(f"{key}={value}\n")
            os.replace(tmp, self._path)
        except Exception as e:
            logger.debug(f"settings save failed: {e!r}", exc_info=True)

    def get(self, key: str):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if key in DEFAULTS:
            self._data[key] = value
