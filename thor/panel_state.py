# -*- coding: utf-8 -*-
"""Panel state persistence between runs."""

from __future__ import annotations

import json
import logging
import os
import tempfile

from . import xdg

logger = logging.getLogger(__name__)

DEFAULT_PANEL_STATE = {
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
}


def load_panel_state(path: str | None = None) -> dict:
    """Load saved panel state from XDG config, falling back to defaults."""
    state = dict(DEFAULT_PANEL_STATE)
    state_path = path or xdg.panel_state_path()
    try:
        if os.path.isfile(state_path):
            with open(state_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    for k, default_val in DEFAULT_PANEL_STATE.items():
                        if k in saved:
                            val = saved[k]
                            if isinstance(default_val, bool):
                                state[k] = bool(val)
                            elif isinstance(default_val, int):
                                try:
                                    state[k] = int(val)
                                except (TypeError, ValueError):
                                    pass
                            elif default_val is None:
                                if val is None:
                                    state[k] = None
                                else:
                                    try:
                                        state[k] = int(val)
                                    except (TypeError, ValueError):
                                        state[k] = None
                            else:
                                state[k] = val
    except Exception as e:
        logger.debug("load_panel_state failed: %r", e, exc_info=True)
    return state

def save_panel_state(state: dict, path: str | None = None) -> None:
    """Save panel state to XDG config atomically."""
    state_path = path or xdg.panel_state_path()
    tmp = None
    try:
        xdg.ensure_dir(os.path.dirname(state_path))
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(state_path) or ".", prefix=".panel-state-", suffix=".tmp")
        payload = dict(DEFAULT_PANEL_STATE)
        payload.update(state)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, state_path)
        tmp = None
    except Exception as e:
        logger.debug("save_panel_state failed: %r", e, exc_info=True)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
