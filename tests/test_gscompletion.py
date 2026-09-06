"""GtkSource framework completion provider tests.

Headless parts (cache/timeout bridge) run anywhere; Gtk parts need a
display and follow the same Xvfb-or-skip pattern as test_completion_popup.
"""

import os
import sys

import pytest


from thor.csharp import gscompletion as gs_mod
from thor.csharp import intelligence as intel


def _gui():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkSource", "4")
        from gi.repository import Gtk, GtkSource

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        if not ok:
            return None
        return Gtk, GtkSource
    except Exception as e:
        print(f"SKIP gscompletion gui tests (no display: {e})")
        return None


_GUI = _gui()

_ITEMS_MSG = {
    "result": {
        "items": [
            {"label": "WriteLine", "detail": "void"},
            {"label": "Write", "detail": "void"},
        ]
    }
}


def _provider(send_request=None, ready=True, path="/tmp/A.cs"):
    if send_request is None:
        def send_request(method, params, callback):
            callback(dict(_ITEMS_MSG))
            return 1
    return gs_mod.RoslynCompletionProvider(
        is_ready=lambda: ready,
        resolve_path=lambda _buf: path,
        send_request=send_request,
    )


def test_import_without_gtksource_typelib():
    """Regression: module must load when GtkSource is missing.

    Previously the provider class statement inherited `object` twice in
    the fallback branch ("duplicate base class object" at import time).
    """
    import gi
    import importlib

    saved = sys.modules.pop("thor.csharp.gscompletion", None)
    original = gi.require_version

    def patched(namespace, version=None):
        if namespace == "GtkSource":
            raise ValueError("Namespace GtkSource not available")
        return original(namespace, version) if version else None

    gi.require_version = patched
    try:
        mod = importlib.import_module("thor.csharp.gscompletion")
        assert mod._AVAILABLE is False
        assert mod.RoslynProposal.__name__ == "RoslynProposal"
        assert mod.RoslynCompletionProvider.__name__ == "RoslynCompletionProvider"
    finally:
        gi.require_version = original
        sys.modules.pop("thor.csharp.gscompletion", None)
        if saved is not None:
            sys.modules["thor.csharp.gscompletion"] = saved

def test_trigger_context():
    assert gs_mod.trigger_context("a", 1, False) == (1, None)
    assert gs_mod.trigger_context("Console.", 8, False) == (2, ".")
    assert gs_mod.trigger_context("f(", 2, False) == (2, "(")
    assert gs_mod.trigger_context("x", 1, True) == (1, None)
    # VSCode re-query while extending an incomplete list.
    assert gs_mod.trigger_context("x", 1, False, True) == (3, None)


def test_cache_valid_reuse_vs_requery():
    import time

    items = [intel.CompletionItem(label="WriteLine")]
    entry = gs_mod._CacheEntry(
        items=list(items), is_incomplete=False, path="/tmp/A.cs",
        line=0, char=11, offset=11, prefix="Wri", time=time.monotonic(),
    )
    # Extending the same prefix on the same line reuses (local filter).
    assert gs_mod.cache_valid(entry, "/tmp/A.cs", 0, "Writ") is True
    # Trigger character resets the prefix -> member list differs.
    assert gs_mod.cache_valid(entry, "/tmp/A.cs", 0, "") is False
    assert gs_mod.cache_valid(entry, "/tmp/A.cs", 1, "Wri") is False
    assert gs_mod.cache_valid(entry, "/tmp/B.cs", 0, "Wri") is False
    incomplete = gs_mod._CacheEntry(
        items=list(items), is_incomplete=True, path="/tmp/A.cs",
        line=0, char=11, offset=11, prefix="Wri", time=time.monotonic(),
    )
    assert gs_mod.cache_valid(incomplete, "/tmp/A.cs", 0, "Writ") is False


def test_populate_async_never_blocks():
    """VSCode parity: populate returns cache/empty immediately (no 0.35s wait)."""
    if _GUI is None:
        pytest.skip("no display")
    import time

    Gtk, GtkSource = _GUI
    message = {"result": {"items": [
        {"label": "WriteLine", "detail": "void"},
        {"label": "Write", "detail": "void"},
    ]}}

    class FakeCtx:
        def __init__(self, it, user=False):
            self._it = it
            self._user = user
            self.calls = []

        def get_iter(self):
            return (True, self._it)

        def get_activation(self):
            return (
                GtkSource.CompletionActivation.USER_REQUESTED
                if self._user
                else GtkSource.CompletionActivation.INTERACTIVE
            )

        def add_proposals(self, _provider, proposals, finished):
            self.calls.append((list(proposals), finished))

    contexts = []

    def send_request(method, params, callback):
        contexts.append(params["context"])
        callback(dict(message))
        return 5

    provider = _provider(send_request=send_request)
    buf = GtkSource.Buffer()
    buf.set_text("Console.Wri")
    ctx = FakeCtx(buf.get_end_iter(), user=True)
    started = time.monotonic()
    provider.do_populate(ctx)
    assert time.monotonic() - started < 0.2, "populate blocked the main loop"
    # Empty unfinished first, async answer completes the same context.
    assert ctx.calls[0] == ([], False)
    assert len(ctx.calls) == 2 and ctx.calls[1][1] is True
    assert [p.get_label() for p in ctx.calls[1][0]] == ["WriteLine", "Write"]  # type: ignore[attr-defined]
    assert contexts[0] == {"triggerKind": 1, "triggerCharacter": None}
    # Same prefix on the same line: cache hit, no new request.
    before = len(contexts)
    ctx2 = FakeCtx(buf.get_end_iter())
    provider.do_populate(ctx2)
    assert len(contexts) == before
    assert ctx2.calls[0][1] is True and len(ctx2.calls) == 1


def test_populate_trigger_character_context():
    if _GUI is None:
        pytest.skip("no display")
    Gtk, GtkSource = _GUI

    class FakeCtx:
        def __init__(self, it):
            self._it = it
            self.calls = []

        def get_iter(self):
            return (True, self._it)

        def get_activation(self):
            return GtkSource.CompletionActivation.INTERACTIVE

        def add_proposals(self, _provider, proposals, finished):
            self.calls.append((list(proposals), finished))

    contexts = []

    def send_request(method, params, callback):
        contexts.append(params["context"])
        callback({"result": {"items": []}})
        return 1

    provider = _provider(send_request=send_request)
    buf = GtkSource.Buffer()
    buf.set_text("Console.")
    provider.do_populate(FakeCtx(buf.get_end_iter()))
    assert contexts[0] == {"triggerKind": 2, "triggerCharacter": "."}, contexts


def test_proposal_filter_text_and_icon():
    if _GUI is None:
        pytest.skip("no display")
    Gtk, GtkSource = _GUI
    provider = _provider()
    text = "Console.Wri"
    message = {"result": {"items": [
        {"label": "WriteLine", "detail": "void M()", "filterText": "WriteLine",
         "kind": 2,
         "textEdit": {
             "range": {"start": {"line": 0, "character": 8},
                       "end": {"line": 0, "character": 11}},
             "newText": "WriteLine"}},
    ]}}
    items = intel.parse_completion(message, text, len(text))
    proposals = provider._proposals_for(items)
    assert proposals[0].get_text() == "WriteLine"  # type: ignore[attr-defined]  # framework filters on this
    assert "void M()" in proposals[0].get_info()  # type: ignore[attr-defined]
    assert proposals[0].get_property("icon-name") == "completion-method"  # type: ignore[attr-defined]


def test_match_permissive_while_typing():
    """VSCode shows completion on the first char; the old char-probe gate did not."""
    if _GUI is None:
        pytest.skip("no display")
    Gtk, GtkSource = _GUI
    win = Gtk.Window()
    buf = GtkSource.Buffer()
    view = GtkSource.View.new_with_buffer(buf)
    win.add(view)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()
    try:
        buf.set_text("C")
        provider = _provider(path="/tmp/A.cs")
        ctx = view.get_completion().create_context(buf.get_end_iter())
        assert provider.do_match(ctx) is True
    finally:
        win.destroy()
    assert gs_mod.word_start_offset("Console.Wri", 11) == 8
    assert gs_mod.word_start_offset("Console.", 8) == 8
    assert gs_mod.word_start_offset("", 0) == 0
    assert gs_mod.word_start_offset("ab", 99) == 0


def test_fetch_sync_uses_cache():
    if not gs_mod._AVAILABLE:
        pytest.skip("no GtkSource")
    calls = []

    def send_request(method, params, callback):
        calls.append(method)
        callback(dict(_ITEMS_MSG))
        return 7

    provider = _provider(send_request=send_request)
    first = provider.fetch_sync("/tmp/A.cs", 0, 11, "Console.Wri", 11, timeout=1.0)
    assert [i.label for i in first] == ["WriteLine", "Write"]
    second = provider.fetch_sync("/tmp/A.cs", 0, 11, "Console.Wri", 11, timeout=1.0)
    assert [i.label for i in second] == ["WriteLine", "Write"]
    assert calls == ["textDocument/completion"], calls  # second hit cache


def test_fetch_timeout_then_late_response_drained():
    if not gs_mod._AVAILABLE:
        pytest.skip("no GtkSource")
    pending = []

    def send_request(method, params, callback):
        pending.append(callback)
        return 9

    provider = _provider(send_request=send_request)
    # Server too slow: returns stale (empty) cache without blocking.
    assert provider.fetch_sync("/tmp/A.cs", 0, 5, "Conso", 5, timeout=0.05) == []
    assert len(pending) == 1
    # Late arrival is stashed, not lost: next keystroke on the same line
    # picks it up with no new request.
    pending[0](dict(_ITEMS_MSG))
    got = provider.fetch_sync("/tmp/A.cs", 0, 6, "Consol", 6, timeout=0.05)
    assert [i.label for i in got] == ["WriteLine", "Write"]
    assert len(pending) == 1, pending


def test_fetch_no_transport_returns_cache():
    if not gs_mod._AVAILABLE:
        pytest.skip("no GtkSource")
    provider = _provider(send_request=lambda _m, _p, _c: None)
    assert provider.fetch_sync("/tmp/A.cs", 0, 3, "Con", 3, timeout=0.1) == []


def test_proposal_carries_insert_text():
    if _GUI is None:
        pytest.skip("no display")
    Gtk, GtkSource = _GUI
    item = gs_mod.RoslynProposal(label="WriteLine", insert_text="WriteLine()", info="void")
    assert item.get_label() == "WriteLine"
    assert item.insert_text == "WriteLine()"
    assert item.get_info() == "void"


def test_start_iter_and_activate_on_real_buffer():
    if _GUI is None:
        pytest.skip("no display")
    Gtk, GtkSource = _GUI
    provider = _provider()
    buf = GtkSource.Buffer()
    buf.set_text("Console.Wri")
    end = buf.get_end_iter()

    class FakeCtx:
        def get_iter(self):
            return (True, end)

    ok, start = provider.do_get_start_iter(FakeCtx(), None)
    assert ok is True
    assert start.get_offset() == 8
    assert buf.get_text(start, end, True) == "Wri"
    proposal = gs_mod.RoslynProposal(label="WriteLine", insert_text="WriteLine",
                                     replace_start=8, replace_end=11)
    ok, start = provider.do_get_start_iter(FakeCtx(), proposal)
    assert ok is True
    assert start.get_offset() == 8
    assert provider.do_activate_proposal(proposal, buf.get_end_iter()) is True
    assert buf.get_text(*buf.get_bounds(), True) == "Console.WriteLine"


def test_activate_appends_suffix_by_kind():
    if _GUI is None:
        pytest.skip("no display")
    Gtk, GtkSource = _GUI
    provider = _provider()
    buf = GtkSource.Buffer()
    buf.set_text("count")
    method = gs_mod.RoslynProposal(label="WriteLine", insert_text="WriteLine", kind=2)
    assert provider.do_activate_proposal(method, buf.get_end_iter()) is True
    assert buf.get_text(*buf.get_bounds(), True) == "WriteLine()"
    cursor = buf.get_iter_at_mark(buf.get_insert()).get_offset()
    assert cursor == len("WriteLine("), cursor  # between the parens
    buf.set_text("count")
    var = gs_mod.RoslynProposal(label="count", insert_text="count", kind=6)
    assert provider.do_activate_proposal(var, buf.get_end_iter()) is True
    assert buf.get_text(*buf.get_bounds(), True) == "count."
    # No doubling when the char is already there.
    buf.set_text("WriteLine(")
    again = gs_mod.RoslynProposal(label="WriteLine", insert_text="WriteLine", kind=2,
                                  replace_start=0, replace_end=9)
    assert provider.do_activate_proposal(again, buf.get_iter_at_offset(9)) is True
    assert buf.get_text(*buf.get_bounds(), True) == "WriteLine("


def test_match_gates_non_csharp():
    if _GUI is None:
        pytest.skip("no display")
    Gtk, GtkSource = _GUI
    win = Gtk.Window()
    buf = GtkSource.Buffer()
    view = GtkSource.View.new_with_buffer(buf)
    win.add(view)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()
    try:
        buf.set_text("Wri")
        provider = _provider(path="/tmp/A.cs")
        ctx = view.get_completion().create_context(buf.get_end_iter())
        assert provider.do_match(ctx) is True
        other = _provider(path=None)
        assert other.do_match(ctx) is False
        not_ready = _provider(path="/tmp/A.cs", ready=False)
        assert not_ready.do_match(ctx) is False
    finally:
        win.destroy()


def test_attach_detach_view():
    if _GUI is None:
        pytest.skip("no display")
    Gtk, GtkSource = _GUI
    win = Gtk.Window()
    view = GtkSource.View.new_with_buffer(GtkSource.Buffer())
    win.add(view)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()
    try:
        import types

        provider = _provider()
        attached: set = set()
        # Plain Gtk.Window lacks Thor's get_views(); emulate a Thor window.
        fake_window = types.SimpleNamespace(get_views=lambda: [view])
        before = len(view.get_completion().get_providers())
        assert gs_mod.attach_to_views(fake_window, provider, attached) == 1
        assert len(view.get_completion().get_providers()) == before + 1
        # Idempotent: second pass attaches nothing new.
        assert gs_mod.attach_to_views(fake_window, provider, attached) == 0
        gs_mod.detach_from_views(fake_window, provider, attached)
        assert len(view.get_completion().get_providers()) == before
        assert attached == set()
    finally:
        win.destroy()


def test_show_completion_uses_native_shape():
    """Programmatic invoke must mirror GtkSourceView's show-completion handler.

    Regression: starting with a caller-created context (explicit iter, single
    provider) returns True but the framework never adopts the context, so
    add_proposals hits "completion->priv->context == context" and the dialog
    never appears. The native shape (NULL position, all providers) drives
    match/populate on a framework-owned context.
    """
    if _GUI is None:
        pytest.skip("no display")
    import time

    Gtk, GtkSource = _GUI
    win = Gtk.Window()
    buf = GtkSource.Buffer()
    buf.set_text("Console.Wri")
    view = GtkSource.View.new_with_buffer(buf)
    win.add(view)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()
    try:
        requests: list = []

        def send_request(method, params, callback):
            requests.append(method)
            callback(dict(_ITEMS_MSG))
            return 3

        provider = gs_mod.RoslynCompletionProvider(
            is_ready=lambda: True,
            resolve_path=lambda _buf: "/tmp/A.cs",
            send_request=send_request,
        )
        view.get_completion().add_provider(provider)
        assert gs_mod.show_completion(view, provider) is True
        deadline = time.time() + 5
        while not requests and time.time() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration()
            time.sleep(0.05)
        assert requests == ["textDocument/completion"], requests
    finally:
        win.destroy()
