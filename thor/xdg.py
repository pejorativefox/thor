# -*- coding: utf-8 -*-
"""Central XDG Base Directory resolution for Thor.

Follows the FreeDesktop XDG Base Directory Specification:
- XDG_DATA_HOME:   ~/.local/share
- XDG_CONFIG_HOME: ~/.config
- XDG_CACHE_HOME:  ~/.cache
- XDG_STATE_HOME:  ~/.local/state
- XDG_BIN_HOME:    ~/.local/bin

Safe for headless imports (no GUI or DISPLAY required). Uses GLib when
available and falls back to environment variables and standard defaults.
"""

from __future__ import annotations

import logging
import os
import pathlib

logger = logging.getLogger(__name__)

try:
    from gi.repository import GLib  # type: ignore
except Exception:
    GLib = None  # type: ignore

APP_ID = "dev.thor.Editor"


def _home() -> str:
    return os.path.expanduser("~")


def _abs_env(name: str) -> str | None:
    """Absolute XDG override or None (relative values rejected per spec)."""
    env = os.environ.get(name, "").strip()
    if env and os.path.isabs(env):
        return env
    return None


def data_home() -> str:
    """Return canonical user data directory ($XDG_DATA_HOME or ~/.local/share)."""
    env = _abs_env("XDG_DATA_HOME")
    if env:
        return env
    if GLib is not None:
        try:
            base = GLib.get_user_data_dir()
            if base:
                return str(base)
        except Exception:
            logger.debug("GLib.get_user_data_dir() failed", exc_info=True)
    return os.path.join(_home(), ".local", "share")


def config_home() -> str:
    """Return canonical user configuration directory ($XDG_CONFIG_HOME or ~/.config)."""
    env = _abs_env("XDG_CONFIG_HOME")
    if env:
        return env
    if GLib is not None:
        try:
            base = GLib.get_user_config_dir()
            if base:
                return str(base)
        except Exception:
            logger.debug("GLib.get_user_config_dir() failed", exc_info=True)
    return os.path.join(_home(), ".config")


def cache_home() -> str:
    """Return canonical user cache directory ($XDG_CACHE_HOME or ~/.cache)."""
    env = _abs_env("XDG_CACHE_HOME")
    if env:
        return env
    if GLib is not None:
        try:
            base = GLib.get_user_cache_dir()
            if base:
                return str(base)
        except Exception:
            logger.debug("GLib.get_user_cache_dir() failed", exc_info=True)
    return os.path.join(_home(), ".cache")


def state_home() -> str:
    """Return canonical user state/logs directory ($XDG_STATE_HOME or ~/.local/state)."""
    env = _abs_env("XDG_STATE_HOME")
    if env:
        return env
    if GLib is not None and hasattr(GLib, "get_user_state_dir"):
        try:
            base = GLib.get_user_state_dir()
            if base:
                return str(base)
        except Exception:
            logger.debug("GLib.get_user_state_dir() failed", exc_info=True)
    return os.path.join(_home(), ".local", "state")


def bin_home() -> str:
    """Return canonical user binary directory ($XDG_BIN_HOME or ~/.local/bin)."""
    env = _abs_env("XDG_BIN_HOME")
    if env:
        return env
    return os.path.join(_home(), ".local", "bin")


def ensure_dir(path: str) -> str:
    """Create directory and its parents if missing (best-effort, idempotent)."""
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            logger.warning("ensure_dir failed for %s", path, exc_info=True)
        else:
            try:
                if not os.path.isdir(path):
                    logger.warning("ensure_dir: %s is not a directory", path)
            except Exception:
                logger.debug("ensure_dir check failed for %s", path, exc_info=True)
    return path


def desktop_dir() -> str:
    """Directory for user desktop launchers (~/.local/share/applications)."""
    return os.path.join(data_home(), "applications")


def icon_hicolor_dir(size: str = "scalable", category: str = "apps") -> str:
    """Directory for user hicolor icons (e.g. ~/.local/share/icons/hicolor/scalable/apps)."""
    return os.path.join(data_home(), "icons", "hicolor", size, category)


def styles_dirs() -> list[str]:
    """Search path candidate directories for GtkSourceView style schemes."""
    dirs: list[str] = []
    # 1. Custom env override
    env = os.environ.get("THOR_STYLE_DIR", "").strip()
    if env:
        dirs.append(os.path.abspath(env))
    # 2. Thor-specific XDG data styles dir
    dirs.append(os.path.join(data_home(), "thor", "styles"))
    # 3. GtkSourceView 4 standard XDG data styles dir
    dirs.append(os.path.join(data_home(), "gtksourceview-4", "styles"))
    # 4. Repo / bundled styles dir
    repo_styles = pathlib.Path(__file__).resolve().parents[1] / "styles"
    if repo_styles.is_dir():
        dirs.append(str(repo_styles))
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


def lang_dirs() -> list[str]:
    """Search path candidate directories for GtkSourceView language specs."""
    dirs: list[str] = []
    # 1. Custom env override
    env = os.environ.get("THOR_LANG_DIR", "").strip()
    if env:
        dirs.append(os.path.abspath(env))
    # 2. GtkSourceView 4 standard XDG data language-specs dir
    dirs.append(os.path.join(data_home(), "gtksourceview-4", "language-specs"))
    # 3. Repo / bundled lang dir
    repo_lang = pathlib.Path(__file__).resolve().parents[1] / "lang"
    if repo_lang.is_dir():
        dirs.append(str(repo_lang))
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


def log_dir() -> str:
    """Directory for Thor log files ($XDG_STATE_HOME/thor/logs)."""
    path = os.path.join(state_home(), "thor", "logs")
    ensure_dir(path)
    return path


def marker_log_path() -> str:
    """Canonical path for the thor-csharp marker debug log."""
    # Single canonical path under XDG state; no /tmp fallback — a
    # predictable world-writable fallback is a symlink-hijack vector.
    # Callers fail open (log write already best-effort) if unwritable.
    return os.path.join(log_dir(), "thor-csharp.log")


def roslyn_log_dir() -> str:
    """Directory for Roslyn language server stderr and LSP trace logs."""
    return log_dir()


def state_path() -> str:
    """Path to the persistent window/panel state TOML file in XDG config."""
    path = os.path.join(config_home(), "thor", "state.toml")
    ensure_dir(os.path.dirname(path))
    return path


def sessions_dir() -> str:
    """Directory for per-project session files ($XDG_CACHE_HOME/thor/sessions)."""
    path = os.path.join(cache_home(), "thor", "sessions")
    ensure_dir(path)
    return path


__all__ = [
    "APP_ID",
    "data_home",
    "config_home",
    "cache_home",
    "state_home",
    "bin_home",
    "ensure_dir",
    "desktop_dir",
    "icon_hicolor_dir",
    "styles_dirs",
    "lang_dirs",
    "log_dir",
    "marker_log_path",
    "roslyn_log_dir",
    "state_path",
    "sessions_dir",
]

