# -*- coding: utf-8 -*-
"""Window + panel state persistence between runs (TOML)."""

from __future__ import annotations

import json
import logging
import os
import tempfile

try:
    import tomllib
except ImportError:  # Python 3.10: no stdlib tomllib
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

from . import xdg

logger = logging.getLogger(__name__)

DEFAULT_STATE = {
    "side_panel_visible": True,
    "bottom_panel_visible": False,
    "side_panel_size": 260,
    "bottom_panel_size": 200,
    "side_panel_active_page": 0,
    "bottom_panel_active_page": 0,
    "window_x": None,
    "window_y": None,
    "window_width": 1280,
    "window_height": 800,
    "window_maximized": False,
    "editor_font": "",
    "terminal_font": "",
    "panel_font": "",
}


def _legacy_path() -> str:
    try:
        base = xdg.config_home()
    except Exception:
        base = os.path.expanduser("~/.config")
    return os.path.join(base, "thor", "panel_state.json")


def _coerce(state: dict, saved: dict) -> dict:
    for k, default_val in DEFAULT_STATE.items():
        if k not in saved:
            continue
        val = saved[k]
        if isinstance(default_val, bool):
            # ponytail: strict bool check first — bool is an int subclass
            state[k] = bool(val) if isinstance(val, (bool, int)) else state[k]
        elif isinstance(default_val, int):
            if isinstance(val, bool):
                continue
            if isinstance(val, float) and not val.is_integer():
                continue
            try:
                state[k] = int(val)
            except (TypeError, ValueError):
                pass
        elif default_val is None:
            if val is None:
                state[k] = None
            elif isinstance(val, bool):
                pass  # no bool<->None mixing; keep default
            else:
                try:
                    state[k] = int(val)
                except (TypeError, ValueError):
                    state[k] = None
        else:
            state[k] = val
    return state


def _encode(state: dict) -> str:
    """Flat TOML for the state dict; None values are omitted (no TOML null)."""
    lines = []
    for k, default_val in DEFAULT_STATE.items():
        val = state.get(k, default_val)
        if val is None:
            continue
        if isinstance(val, bool):
            lines.append(f"{k} = {'true' if val else 'false'}")
        elif isinstance(val, int):
            lines.append(f"{k} = {val}")
        elif isinstance(val, str):
            lines.append(f"{k} = {json.dumps(val)}")
        else:
            logger.debug("state encode: skipping non-scalar %r", k)
    return "\n".join(lines) + "\n"

def _fallback_parse(data: bytes) -> dict:
    """Minimal flat `key = value` parser for our schema (no TOML lib)."""
    saved: dict = {}
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return saved
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k not in DEFAULT_STATE or not v:
            continue
        if v == "true":
            saved[k] = True
        elif v == "false":
            saved[k] = False
        elif v.startswith('"'):
            try:
                saved[k] = json.loads(v)
            except ValueError:
                continue
        else:
            try:
                saved[k] = int(v, 10)
            except ValueError:
                continue
    return saved


def _toml_loads(data: bytes) -> dict:
    if tomllib is not None:
        try:
            import io

            saved = tomllib.load(io.BytesIO(data))
        except Exception:
            logger.debug("state TOML decode failed; using defaults", exc_info=True)
            return {}
        return saved if isinstance(saved, dict) else {}
    return _fallback_parse(data)


def load_state(path: str | None = None) -> dict:
    """Load saved window/panel state from XDG config, falling back to defaults."""
    state = dict(DEFAULT_STATE)
    state_path = path or xdg.state_path()
    try:
        try:
            with open(state_path, "rb") as f:
                data = f.read()
        except OSError:
            data = None
        if data is not None:
            saved = _toml_loads(data)
            if saved:
                return _coerce(state, saved)
            return state
        elif path is None:
            # One-time migration from the legacy JSON file.
            legacy = _legacy_path()
            if os.path.isfile(legacy):
                try:
                    with open(legacy, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                    if isinstance(saved, dict):
                        state = _coerce(state, saved)
                except Exception as e:
                    logger.debug("legacy state migration failed: %r", e, exc_info=True)
    except Exception as e:
        logger.debug("load_state failed: %r", e, exc_info=True)
    return state


def save_state(state: dict, path: str | None = None) -> None:
    """Save window/panel state to XDG config atomically (TOML)."""
    state_path = path or xdg.state_path()
    tmp = None
    try:
        xdg.ensure_dir(os.path.dirname(state_path))
        payload = dict(DEFAULT_STATE)
        payload.update(state)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(state_path) or ".", prefix=".state-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_encode(payload))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, state_path)
        tmp = None
        try:
            dir_fd = os.open(os.path.dirname(state_path) or ".", os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                logger.debug("save_state: dir fsync failed", exc_info=True)
            finally:
                os.close(dir_fd)
    except Exception as e:
        logger.warning("save_state failed: %r", e, exc_info=True)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
