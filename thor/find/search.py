# -*- coding: utf-8 -*-
"""Headless document-find helpers (no GTK import).

Plain substring search, case-insensitive by default, matching
VS Code / mousepad Ctrl+F behaviour (not whole-word like occurrences).
"""

from __future__ import annotations


def find_all(
    text: str,
    query: str,
    case_sensitive: bool = False,
) -> list[tuple[int, int]]:
    """Return non-overlapping ``(start, end)`` hits of *query* in *text*.

    Empty query → ``[]``.
    """
    if not query:
        return []
    if not case_sensitive:
        hay = text.lower()
        needle = query.lower()
    else:
        hay = text
        needle = query
    hits: list[tuple[int, int]] = []
    qlen = len(needle)
    if qlen == 0:
        return []
    start = 0
    # guard against infinite loop on empty needle (already returned) and
    # ensure progress even if needle is empty string slicing edge
    while True:
        idx = hay.find(needle, start)
        if idx < 0:
            break
        hits.append((idx, idx + qlen))
        start = idx + qlen
        # avoid infinite if qlen == 0 (defensive)
        if qlen == 0:
            start += 1
            if start > len(hay):
                break
    return hits


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
