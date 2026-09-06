# -*- coding: utf-8 -*-
"""fzy-style fuzzy matcher for the quick-open finder.

Replaces the old greedy subsequence scorer with a Smith-Waterman-style
dynamic program (after jhawthorn/fzy), which finds the *optimal* alignment
instead of the first greedy one. Matches earn bonuses for landing on word
starts (after ``/`` ``-`` ``_`` `` `` ``.``), camelCase boundaries and
consecutive runs; gaps pay affine penalties (leading/trailing vs inner).

Layered on top, fzf-style UX semantics:

- multi-term AND: whitespace-separated terms must all match; scores sum.
- smart case: a query containing uppercase matches case-sensitively,
  otherwise case-insensitively.
- exact-basename boost: a single term equal to the file's basename wins.

Headless-safe (stdlib only) so unit tests run without a display.
"""

from __future__ import annotations

import heapq
import html
import os

_NEG_INF = float("-inf")

# fzy constants (config.def.h): bonuses for good match positions, penalties
# for gaps. Higher total score is better.
SCORE_GAP_LEADING = -0.005
SCORE_GAP_TRAILING = -0.005
SCORE_GAP_INNER = -0.01
SCORE_MATCH_CONSECUTIVE = 1.0
SCORE_MATCH_SLASH = 0.9
SCORE_MATCH_WORD = 0.8
SCORE_MATCH_CAPITAL = 0.7
SCORE_MATCH_DOT = 0.6

#: Bonus when a single-term query exactly equals the candidate's basename.
SCORE_BASENAME_EXACT = 2.0

_WORD_SEPARATORS = frozenset("-_ ")


def _compute_bonus(prev: str, cur: str) -> float:
    if prev == "/":
        return SCORE_MATCH_SLASH
    if prev in _WORD_SEPARATORS:
        return SCORE_MATCH_WORD
    if prev == ".":
        return SCORE_MATCH_DOT
    if prev.islower() and cur.isupper():
        return SCORE_MATCH_CAPITAL
    return 0.0


def _bonuses(haystack: str) -> list[float]:
    """Per-position match bonus; position 0 counts as after ``/``."""
    out: list[float] = []
    prev = "/"
    for ch in haystack:
        out.append(_compute_bonus(prev, ch))
        prev = ch
    return out


def _fzy_core(
    needle_lower: str,
    needle_orig: str,
    hay_lower: str,
    hay_orig: str,
    bonus: list[float],
    case_sensitive: bool,
    want_positions: bool,
) -> tuple[float, list[int]] | None:
    """fzy DP core. Returns (score, match positions) or None."""
    n = len(needle_lower)
    m = len(hay_lower)
    if n == 0:
        return (0.0, [])
    if n > m:
        return None
    if n == m:
        if hay_lower == needle_lower and (not case_sensitive or hay_orig == needle_orig):
            return (float("inf"), list(range(n)))
        return None

    d_prev = [_NEG_INF] * m
    m_prev = [_NEG_INF] * m
    d_rows: list[list[float]] | None = [] if want_positions else None
    m_rows: list[list[float]] | None = [] if want_positions else None

    for i in range(n):
        nc = needle_lower[i]
        no = needle_orig[i]
        gap = SCORE_GAP_TRAILING if i == n - 1 else SCORE_GAP_INNER
        d_cur = [_NEG_INF] * m
        m_cur = [_NEG_INF] * m
        prev_score = _NEG_INF
        for j in range(m):
            if nc == hay_lower[j] and (not case_sensitive or no == hay_orig[j]):
                if i == 0:
                    score = j * SCORE_GAP_LEADING + bonus[j]
                elif j == 0:
                    score = _NEG_INF
                else:
                    score = m_prev[j - 1] + bonus[j]
                    consec = d_prev[j - 1] + SCORE_MATCH_CONSECUTIVE
                    if consec > score:
                        score = consec
                d_cur[j] = score
                stepped = prev_score + gap
                prev_score = score if score > stepped else stepped
                m_cur[j] = prev_score
            else:
                prev_score = prev_score + gap
                m_cur[j] = prev_score
        if want_positions:
            assert d_rows is not None and m_rows is not None
            d_rows.append(d_cur)
            m_rows.append(m_cur)
        d_prev, m_prev = d_cur, m_cur

    final = m_prev[m - 1]
    if final == _NEG_INF:
        return None
    positions: list[int] = []
    if want_positions:
        assert d_rows is not None and m_rows is not None
        positions = [0] * n
        match_required = False
        j = m - 1
        for i in range(n - 1, -1, -1):
            while j >= 0:
                if d_rows[i][j] != _NEG_INF and (match_required or d_rows[i][j] == m_rows[i][j]):
                    match_required = bool(
                        i and j and m_rows[i][j] == d_rows[i - 1][j - 1] + SCORE_MATCH_CONSECUTIVE
                    )
                    positions[i] = j
                    j -= 1
                    break
                j -= 1
    return (final, positions)


def split_terms(query: str) -> list[str]:
    """Whitespace-separated search terms (empty query -> [])."""
    return (query or "").split()


def _score_terms(
    terms: list[str], haystack: str, hay_lower: str, bonus: list[float]
) -> tuple[float, list[int]] | None:
    """Score pre-split terms against precomputed candidate state."""
    total = 0.0
    positions: list[int] = []
    for term in terms:
        case_sensitive = any(ch.isupper() for ch in term)
        if len(term) > len(haystack) or term[0].lower() not in hay_lower:
            return None
        hit = _fzy_core(
            term.lower(), term, hay_lower, haystack, bonus, case_sensitive, True
        )
        if hit is None:
            return None
        score, pos = hit
        total += score
        positions.extend(pos)
    if len(terms) == 1 and haystack and terms[0].lower() == os.path.basename(haystack).lower():
        total += SCORE_BASENAME_EXACT
    return (total, sorted(positions))


def fuzzy_match(query: str, candidate: str) -> tuple[float, list[int]] | None:
    """Match a (possibly multi-term) query against a candidate.

    Returns ``(score, positions)`` with higher scores better, or None for
    no match. All whitespace-separated terms must match (AND semantics).
    """
    terms = split_terms(query)
    if not terms:
        return (0.0, [])
    haystack = candidate or ""
    return _score_terms(terms, haystack, haystack.lower(), _bonuses(haystack))


def fuzzy_score(query: str, candidate: str) -> float | None:
    """Single score for query vs candidate (higher is better), None if no match."""
    hit = fuzzy_match(query, candidate)
    return None if hit is None else hit[0]


def markup_highlight(display: str, positions: list[int]) -> str:
    """Pango markup for a display path with matched runs bolded.

    Characters are escaped with :mod:`html` so filenames containing
    ``<``/``&`` never break markup; maximal contiguous runs of valid,
    deduplicated match indices are wrapped in ``<b>...</b>``.
    """
    try:
        valid = set(p for p in positions if 0 <= p < len(display))
    except Exception:
        valid = set()
    if not valid:
        return html.escape(display, quote=False)
    parts: list[str] = []
    for i, ch in enumerate(display):
        esc = html.escape(ch, quote=False)
        if i in valid:
            if (i - 1) not in valid:
                parts.append("<b>")
            parts.append(esc)
            if (i + 1) not in valid:
                parts.append("</b>")
        else:
            parts.append(esc)
    return "".join(parts)


class FuzzyIndex:
    """Precomputed per-candidate state so repeated searches are cheap.

    Lowercase forms and bonus arrays are built once in ``__init__``; each
    :meth:`search` then only runs the DP, with a first-character + length
    prefilter to skip hopeless candidates without entering the DP.
    """

    def __init__(self, paths: list[str]) -> None:
        self._entries: list[tuple[str, str, list[float]]] = []
        for path in paths:
            lowered = path.lower()
            self._entries.append((path, lowered, _bonuses(path)))

    def _score_entry(
        self, terms: list[str], path: str, hay_lower: str, bonus: list[float], haystack: str
    ) -> tuple[float, list[int]] | None:
        return _score_terms(terms, haystack, hay_lower, bonus)

    def search(self, query: str, limit: int = 50) -> list[str]:
        """Rank paths by score; ties break shorter-then-alphabetical."""
        terms = split_terms((query or "").strip())
        paths = [path for path, _, _ in self._entries]
        if not terms:
            return paths[:limit]
        if limit <= 0:
            return []
        scored: list[tuple[float, int, str]] = []
        for path, hay_lower, bonus in self._entries:
            hit = self._score_entry(terms, path, hay_lower, bonus, path)
            if hit is not None:
                scored.append((hit[0], len(path), path))
        if len(scored) <= limit:
            scored.sort(key=lambda item: (-item[0], item[1], item[2]))
            return [path for _, _, path in scored]
        top = heapq.nsmallest(limit, scored, key=lambda item: (-item[0], item[1], item[2]))
        return [path for _, _, path in top]


def fuzzy_find(query: str, paths: list[str], limit: int = 50) -> list[str]:
    """Rank paths by fuzzy score; ties break shorter-then-alphabetical."""
    return FuzzyIndex(paths).search(query, limit=limit)
