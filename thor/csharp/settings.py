"""C# feature settings — stored in the ``[csharp]`` section of state.toml.

Thor keeps one user config file (``$XDG_CONFIG_HOME/thor/state.toml``):
window state in the flat keys, feature settings in TOML sections. This
store is a thin view over the ``[csharp]`` section; values from the
legacy plugin-era INI (``~/.config/thor/plugins/thor-csharp/settings.ini``)
are honored as fallback defaults until the user saves anything new.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SECTION = "csharp"

DEFAULTS = {
    "dotnet_executable": "dotnet",
    "roslyn_server": "~/.dotnet/tools/roslyn-language-server",
    "roslyn_log_level": "Information",
    "auto_restore": True,
    "format_on_save": False,
    "test_framework_filter": "",
}


def _legacy_ini_path() -> str:
    try:
        from thor import xdg

        base = xdg.config_home()
    except Exception:
        base = os.path.expanduser("~/.config")
    return os.path.join(base, "thor", "plugins", "thor-csharp", "settings.ini")


def _load_legacy_ini(path: str) -> dict:
    """Read the plugin-era INI (best effort) so old settings survive."""
    data: dict = {}
    try:
        if not os.path.exists(path):
            return data
        current_group = None
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current_group = line[1:-1]
                    continue
                if (current_group or "").lower() != SECTION:
                    continue
                if "=" not in line:
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
        logger.debug(f"legacy settings read failed: {e!r}")
    return data


class SettingsStore:
    """View over the ``[csharp]`` section of the main state.toml config."""

    def __init__(self, path: str | None = None) -> None:
        from .. import state as _state

        self._state = _state
        self._path = path  # optional state.toml override (tests)
        self._data = self.load()

    @property
    def path(self) -> str:
        return self._path or self._state.xdg.state_path()

    def load(self) -> dict:
        data = dict(DEFAULTS)
        try:
            sections = self._state.load_sections(self._path)
            section = sections.get(SECTION)
            if isinstance(section, dict):
                for key in DEFAULTS:
                    if key in section:
                        data[key] = section[key]
            else:
                # One-time fallback: honor legacy INI values until the
                # section is written for the first time.
                data.update(_load_legacy_ini(_legacy_ini_path()))
        except Exception as e:
            logger.debug(f"settings load failed: {e!r}", exc_info=True)
        self._data = data
        return dict(self._data)

    def save(self) -> None:
        """Write the current values into the ``[csharp]`` section."""
        try:
            sections = self._state.load_sections(self._path)
            sections[SECTION] = {
                k: self._data.get(k, DEFAULTS[k]) for k in DEFAULTS
            }
            self._state.save_state(self._state.load_state(self._path),
                                   path=self._path, sections=sections)
        except Exception as e:
            logger.debug(f"settings save failed: {e!r}", exc_info=True)

    def get(self, key: str):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if key in DEFAULTS:
            self._data[key] = value
