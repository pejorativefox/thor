# -*- coding: utf-8 -*-
"""Thor shared utilities — headless-safe, no GTK imports."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
def tab_state_name(state) -> str:
    """Normalized TabState name for a raw ``get_state()`` value.

    Real GI enums stringify to ints (``str(STATE_SAVING) == "3"``), so
    substring checks for "SAVING"/"NORMAL" on ``str()`` never match in
    production. Prefer ``value_name``, then ``value_nick``, then bare ints
    (0 == NORMAL, 3 == SAVING — stable ABI values). Plain strings
    (tests, older bindings) pass through unchanged. Headless-safe.
    """
    try:
        name = getattr(state, "value_name", None)
        if isinstance(name, str) and name:
            return name
    except Exception:
        logger.debug("tab_state_name: value_name failed", exc_info=True)
    try:
        nick = getattr(state, "value_nick", None)
        if isinstance(nick, str) and nick:
            return nick
    except Exception:
        logger.debug("tab_state_name: value_nick failed", exc_info=True)
    try:
        num = int(state)  # type: ignore[arg-type]
    except Exception:
        logger.debug("tab_state_name: int() failed", exc_info=True)
        num = None
    if num == 0:
        return "THOR_TAB_STATE_NORMAL"
    if num == 3:
        return "THOR_TAB_STATE_SAVING"
    try:
        return str(state)
    except Exception:
        logger.debug("tab_state_name: str() failed", exc_info=True)
        return ""

def is_save_completed(previous, current) -> bool:
    """True on a SAVING -> NORMAL tab-state transition (save done)."""
    try:
        prev = tab_state_name(previous).upper()
        cur = tab_state_name(current).upper()
    except Exception:
        logger.debug("is_save_completed: normalize failed", exc_info=True)
        return False
    return "SAVING" in prev and "ERROR" not in prev and cur.endswith("NORMAL")

def doc_path(doc) -> str | None:
    """Best-effort file path (or URI for remote) for a document, else None."""
    if doc is None:
        return None
    if isinstance(doc, str):
        return doc or None
    try:
        location = doc.get_location()
    except Exception:
        logger.debug("doc_path: get_location failed", exc_info=True)
        location = None
    if location is None:
        try:
            maybe_file = doc.get_file()
            location = maybe_file.get_location() if maybe_file is not None else None
        except Exception:
            logger.debug("doc_path: get_file location failed", exc_info=True)
            location = None
    if location is None:
        return None
    if isinstance(location, str):
        return location or None
    try:
        path = location.get_path()
    except Exception:
        logger.debug("doc_path: get_path failed", exc_info=True)
        path = None
    if path:
        return path
    try:
        uri = location.get_uri()
    except Exception:
        logger.debug("doc_path: get_uri failed", exc_info=True)
        uri = None
    if uri:
        return uri
    return None


__all__ = ["tab_state_name", "is_save_completed", "doc_path"]
