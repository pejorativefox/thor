# -*- coding: utf-8 -*-
"""Whole-word occurrence search (headless, no GTK import).

Word characters are ASCII ``[A-Za-z0-9_]`` only; any unicode letter
outside that set acts as a delimiter.
"""
from __future__ import annotations

MIN_WORD_LEN = 2
MAX_MATCHES = 1000


def _is_word_char(ch: str) -> bool:
    return ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9")


def word_at(text: str, offset: int) -> tuple[str, int, int] | None:
    """Return ``(word, start, end)`` for the word containing ``offset``.

    ``offset`` is clamped into ``[0, len(text))``.  Returns ``None`` when
    the char under the cursor is not a word char or the word is shorter
    than ``MIN_WORD_LEN``.
    """
    if not text:
        return None
    if offset < 0:
        offset = 0
    elif offset >= len(text):
        offset = len(text) - 1
    if not _is_word_char(text[offset]):
        return None
    start = offset
    while start > 0 and _is_word_char(text[start - 1]):
        start -= 1
    end = offset
    while end < len(text) and _is_word_char(text[end]):
        end += 1
    word = text[start:end]
    if len(word) < MIN_WORD_LEN:
        return None
    return (word, start, end)


def find_occurrences(
    text: str, word: str, limit: int = MAX_MATCHES
) -> list[tuple[int, int]]:
    """Find whole-word, case-sensitive, non-overlapping hits of ``word``.

    Returns ``[]`` for ``""`` or words shorter than ``MIN_WORD_LEN``.
    Stops once ``len(hits) == limit``; beyond-cap matches are omitted.
    """
    if not word or len(word) < MIN_WORD_LEN:
        return []
    if limit <= 0:
        return []
    hits: list[tuple[int, int]] = []
    start = 0
    wlen = len(word)
    tlen = len(text)
    while True:
        idx = text.find(word, start)
        if idx < 0:
            break
        before_ok = idx == 0 or not _is_word_char(text[idx - 1])
        after = idx + wlen
        after_ok = after >= tlen or not _is_word_char(text[after])
        if before_ok and after_ok:
            hits.append((idx, after))
            if len(hits) >= limit:
                break
        start = idx + wlen
    return hits
