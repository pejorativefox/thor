# -*- coding: utf-8 -*-
"""Thor shared utilities — headless-safe, no GTK imports."""

from __future__ import annotations


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
        pass
    try:
        nick = getattr(state, "value_nick", None)
        if isinstance(nick, str) and nick:
            return nick
    except Exception:
        pass
    try:
        num = int(state)  # type: ignore[arg-type]
    except Exception:
        num = None
    if num == 0:
        return "THOR_TAB_STATE_NORMAL"
    if num == 3:
        return "THOR_TAB_STATE_SAVING"
    try:
        return str(state)
    except Exception:
        return ""


def is_save_completed(previous, current) -> bool:
    """True on a SAVING -> NORMAL tab-state transition (save done)."""
    try:
        prev = tab_state_name(previous).upper()
        cur = tab_state_name(current).upper()
    except Exception:
        return False
    return "SAVING" in prev and "ERROR" not in prev and cur.endswith("NORMAL")


def doc_path(doc) -> str | None:
    """Best-effort file path for a document, or None for untitled/remote."""
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


__all__ = ["tab_state_name", "is_save_completed", "doc_path"]
