"""Unit tests for full-document sync with ranges (headless, no GTK).

Regression tests for the Roslyn 5.9.0 server abort (SIGABRT, NRE in
RangeToTextSpan) triggered by rangeless textDocument/didChange events.
"""

import os
import sys


from thor.csharp import roslyn


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def send_notification(self, method: str, params: dict) -> None:
        self.sent.append((method, params))


def _manager():
    mgr = roslyn.RoslynManager()
    fake = _FakeTransport()
    mgr.transport = fake  # type: ignore[assignment]
    return mgr, fake


def test_full_document_range_single_line():
    assert roslyn.full_document_range("abc") == {
        "start": {"line": 0, "character": 0},
        "end": {"line": 0, "character": 3},
    }


def test_full_document_range_multiline():
    assert roslyn.full_document_range("ab\ncdef\n") == {
        "start": {"line": 0, "character": 0},
        "end": {"line": 2, "character": 0},
    }


def test_did_change_carries_range_of_previous_text():
    mgr, fake = _manager()
    old = "class A {\n    int x;\n}\n"
    new = "class A {\n    int y;\n}\n"
    mgr.did_open("/tmp/F.cs", "csharp", 1, old)
    mgr.did_change("/tmp/F.cs", 2, new)
    assert [m for m, _ in fake.sent] == ["textDocument/didOpen", "textDocument/didChange"]
    _method, params = fake.sent[1]
    (change,) = params["contentChanges"]
    assert change["text"] == new
    assert change["range"] == roslyn.full_document_range(old), change["range"]
    assert change["rangeLength"] == len(old)
    assert params["textDocument"]["version"] == 2


def test_did_change_covering_deletion():
    """The range must span the OLD text, so deletions are representable."""
    mgr, fake = _manager()
    old = "line1\nline2\nline3\n"
    mgr.did_open("/tmp/G.cs", "csharp", 1, old)
    mgr.did_change("/tmp/G.cs", 2, "line1\n")
    (_method, params) = fake.sent[1]
    (change,) = params["contentChanges"]
    assert change["range"]["end"] == {"line": 3, "character": 0}
    assert change["rangeLength"] == len(old)
    assert change["text"] == "line1\n"


def test_did_change_without_open_still_ranged():
    mgr, fake = _manager()
    mgr.did_change("/tmp/H.cs", 1, "hello")
    (_method, params) = fake.sent[0]
    (change,) = params["contentChanges"]
    assert "range" in change and "rangeLength" in change


def test_did_close_drops_tracked_text():
    mgr, fake = _manager()
    mgr.did_open("/tmp/I.cs", "csharp", 1, "x")
    assert len(mgr._doc_text) == 1
    mgr.did_close("/tmp/I.cs")
    assert mgr._doc_text == {}
    assert mgr.open_docs == {}

def test_did_close_without_open_sends_nothing():
    """didClose for a never-opened URI kills this Roslyn version
    (Contract.Fail -> SIGABRT, exit -6). It must be a local no-op."""
    mgr, fake = _manager()
    mgr.did_close("/tmp/NeverOpened.md")
    assert fake.sent == []
    assert mgr.open_docs == {}


def test_non_csharp_docs_never_sent():
    """Roslyn SIGABRTs (exit -6) on didClose for files it never tracked.

    It ignores didOpen for non-C# extensions server-side, so sending any
    sync notification for them diverges client/server tracking and the
    later didClose is fatal. All four must be local no-ops (repro:
    closing config.toml killed the server)."""
    mgr, fake = _manager()
    mgr.did_open("/proj/config.toml", "toml", 1, "x = 1\n")
    mgr.did_change("/proj/config.toml", 2, "x = 2\n")
    mgr.did_save("/proj/config.toml", "x = 2\n")
    mgr.did_close("/proj/config.toml")
    assert fake.sent == []
    assert mgr.open_docs == {}
    assert mgr._doc_text == {}


def test_toml_close_with_cs_open_sends_nothing():
    """The exact crash: a .cs doc is tracked, then an untracked .toml
    tab closes — no didClose may go out for the .toml."""
    mgr, fake = _manager()
    mgr.did_open("/proj/A.cs", "csharp", 1, "class A {}\n")
    mgr.did_close("/proj/config.toml")
    assert [m for m, _ in fake.sent] == ["textDocument/didOpen"]
    assert list(mgr.open_docs) == [roslyn.file_uri("/proj/A.cs")]
