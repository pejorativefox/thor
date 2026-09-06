# -*- coding: utf-8 -*-
"""Headless document-find helpers (no GTK import).

Plain substring search, case-insensitive by default, matching
VS Code / mousepad Ctrl+F behaviour (not whole-word like occurrences).
"""

from __future__ import annotations


import re

_MAX_HITS = 5000

def find_all(
    text: str,
    query: str,
    case_sensitive: bool = False,
) -> list[tuple[int, int]]:
    """Return non-overlapping ``(start, end)`` hits of *query* in *text*.

    Empty query → ``[]``.

    Case-insensitive matching uses :func:`re.finditer` with
    ``re.IGNORECASE`` on the original text so offsets always refer to
    *text*. ``str.lower()``/``casefold()`` can change string length
    (e.g. ``İ`` → ``i̇``, ``ß`` → ``ss``), which skews offsets when the
    search runs on the lowered copy.
    """
    if not isinstance(text, str) or not isinstance(query, str) or not query:
        return []
    if case_sensitive:
        hits: list[tuple[int, int]] = []
        qlen = len(query)
        start = 0
        while True:
            idx = text.find(query, start)
            if idx < 0:
                break
            hits.append((idx, idx + qlen))
            start = idx + qlen
            if len(hits) >= _MAX_HITS:
                break
        return hits
    try:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
    except re.error:
        return []
    hits = [(m.start(), m.end()) for m in pattern.finditer(text) if m.end() > m.start()]
    return hits[:_MAX_HITS]


def next_index(hits: list[tuple[int, int]], cursor_offset: int, wrap: bool = True) -> int | None:
    """Index of the first hit strictly after *cursor_offset*.

    If none after, wraps to 0 when *wrap* else None.
    Empty hits → None.
    """
    if not hits:
        return None
    for i, (s, _e) in enumerate(hits):
        if s > cursor_offset:
            return i
        # cursor inside a hit → consider that hit as current, so next is i+1
        # but we already handled s > cursor; if cursor inside [s, e) we skip
        if s <= cursor_offset < _e:
            nxt = i + 1
            if nxt < len(hits):
                return nxt
            return 0 if wrap else None
    # no hit after cursor → wrap to first
    return 0 if wrap else None


def prev_index(hits: list[tuple[int, int]], cursor_offset: int, wrap: bool = True) -> int | None:
    """Index of the last hit strictly before *cursor_offset*."""
    if not hits:
        return None
    # iterate reverse to find last hit before cursor
    for i in range(len(hits) - 1, -1, -1):
        _s, e = hits[i]
        if e <= cursor_offset:
            return i
        if _s <= cursor_offset < e:
            prv = i - 1
            if prv >= 0:
                return prv
            return len(hits) - 1 if wrap else None
    return len(hits) - 1 if wrap else None


def current_index(hits: list[tuple[int, int]], cursor_offset: int) -> int | None:
    """Index of hit containing *cursor_offset*, or None."""
    for i, (s, e) in enumerate(hits):
        if s <= cursor_offset < e:
            return i
        if s == cursor_offset:
            return i
    return None
