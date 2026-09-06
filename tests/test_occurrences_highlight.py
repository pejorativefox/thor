"""Occurrences-highlight pure search logic (headless)."""

import thor.occurrences.search as s
import thor.occurrences as occurrenceshighlight


def test_word_at_mid_word():
    assert s.word_at("foo bar", 1) == ("foo", 0, 3)


def test_word_at_word_start():
    assert s.word_at("foo bar", 4) == ("bar", 4, 7)


def test_word_at_whitespace_returns_none():
    assert s.word_at("foo bar", 3) is None


def test_word_at_single_char_returns_none():
    assert s.word_at("a bc", 0) is None


def test_find_occurrences_finds_two():
    assert s.find_occurrences("foo bar foo", "foo") == [(0, 3), (8, 11)]


def test_find_occurrences_excludes_substring():
    assert s.find_occurrences("foobar foo", "foo") == [(7, 10)]


def test_find_occurrences_case_sensitive():
    assert s.find_occurrences("Foo foo", "foo") == [(4, 7)]


def test_find_occurrences_empty_returns_empty():
    assert s.find_occurrences("foo bar", "") == []


def test_find_occurrences_cap_respected():
    text = "foo foo foo foo foo"
    assert s.find_occurrences(text, "foo", limit=2) == [(0, 3), (4, 7)]


def test_constants():
    assert occurrenceshighlight.TAG_NAME == "occurrences-highlight"
    assert occurrenceshighlight.MARK_CATEGORY == "occurrences-highlight"
