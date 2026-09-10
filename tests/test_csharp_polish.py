# -*- coding: utf-8 -*-
"""Tests for the deferred C# polish pass (all headless, no display needed).

Covers: Problems message cosmetics (workspace-relative, no path dup),
completion ranking parity (order-preserving cull, preselect promotion
without re-ranking, truncation survival), and Output sanitization
(ANSI/CR stripping) + test-run status summaries.
"""

import types

import thor.csharp as csharp_mod
from thor.csharp import intelligence as intel
from thor.csharp import output as out_mod


def _manager():
    mgr = csharp_mod.CSharpManager(window=None)
    mgr.window = types.SimpleNamespace(get_documents=lambda: [])
    mgr.output = None
    mgr.explorer = None
    mgr.completion_popup = None
    return mgr


# -- Problems message -------------------------------------------------

def test_problems_message_is_workspace_relative(tmp_path):
    mgr = _manager()
    mgr._model = types.SimpleNamespace(root_dir=str(tmp_path))
    target = str(tmp_path / "src" / "Foo.cs")
    assert mgr._problems_message(target) == "src/Foo.cs"


def test_problems_message_falls_back_outside_workspace(tmp_path):
    mgr = _manager()
    mgr._model = types.SimpleNamespace(root_dir=str(tmp_path))
    assert mgr._problems_message("/elsewhere/src/Foo.cs") == "src/Foo.cs"
    mgr._model = None
    assert mgr._problems_message("/elsewhere/src/Foo.cs") == "src/Foo.cs"


def test_reference_rows_carry_message_not_full_path(tmp_path):
    mgr, recorded = _manager(), []
    mgr._model = types.SimpleNamespace(root_dir=str(tmp_path))
    mgr.output = types.SimpleNamespace(
        set_status=lambda *a: None,
        set_problems=lambda rows: recorded.extend(rows),
        show_problems=lambda: None,
    )
    mgr._on_references_response({"result": [
        {"uri": "file:///other/src/B.cs",
         "range": {"start": {"line": 4, "character": 1}}},
    ]})
    assert len(recorded) == 1
    sev, basename, line1, message, path = recorded[0]
    assert (sev, basename, line1) == ("reference", "B.cs", 5)
    assert message == "src/B.cs" and path == "/other/src/B.cs"


# -- Completion ranking parity -----------------------------------------

def test_completion_matches_culls_without_reranking():
    items = [
        intel.CompletionItem(label="Write", kind=2),
        intel.CompletionItem(label="Printer", kind=2),
        intel.CompletionItem(label="Rinse", kind=2),
        intel.CompletionItem(label="zzz-nope", kind=2),
    ]
    kept = [it for it in items if intel.completion_matches(it, "ri")]
    # Server order kept (rank_for_prefix would lead with the prefix-tier hit).
    assert [i.label for i in kept] == ["Write", "Printer", "Rinse"]
    assert [i.label for i in intel.rank_for_prefix(items, "ri")][0] == "Rinse"
    assert intel.completion_matches(items[0], "") is True


def test_proposals_keep_server_order_and_promote_preselect():
    from thor.csharp import gscompletion as gs_mod

    provider = gs_mod.RoslynCompletionProvider(
        is_ready=lambda: True,
        resolve_path=lambda buf: None,
        send_request=lambda *a: None,
    )
    items = [
        intel.CompletionItem(label="b", kind=2),
        intel.CompletionItem(label="pre", kind=2, preselect=True),
        intel.CompletionItem(label="a", kind=2),
    ]
    proposals = provider._proposals_for(items)
    assert [p.label_text if hasattr(p, "label_text") else p.filter_text for p in proposals][0] == "pre"
    # Non-preselected rows keep server order behind the lead.
    rest = [p.label_text if hasattr(p, "label_text") else p.filter_text for p in proposals][1:]
    assert rest == ["b", "a"]


def test_proposals_truncation_never_drops_preselect():
    from thor.csharp import gscompletion as gs_mod

    provider = gs_mod.RoslynCompletionProvider(
        is_ready=lambda: True,
        resolve_path=lambda buf: None,
        send_request=lambda *a: None,
    )
    items = [intel.CompletionItem(label=f"item{i:03d}", kind=2) for i in range(gs_mod.MAX_PROPOSALS + 10)]
    items[-1].preselect = True
    proposals = provider._proposals_for(items)
    assert len(proposals) == gs_mod.MAX_PROPOSALS
    first = proposals[0]
    assert (first.label_text if hasattr(first, "label_text") else first.filter_text) == items[-1].label


# -- Output sanitization + test summary --------------------------------

def test_sanitize_strips_ansi_and_spinner_rewrites():
    assert out_mod.sanitize_output_text("\x1b[32mPassed\x1b[0m Foo [1s]") == "Passed Foo [1s]"
    assert out_mod.sanitize_output_text("10%\r20%\rDone") == "Done"
    assert out_mod.sanitize_output_text("a\r\nb") == "a\nb"
    assert out_mod.sanitize_output_text("") == ""


def test_test_run_status_carries_counts(monkeypatch):
    from thor.csharp import testing as testing_mod

    mgr = _manager()
    status = []
    mgr.output = types.SimpleNamespace(
        set_status=lambda text: status.append(text),
        append=lambda *a: None,
    )
    mgr.testpanel = types.SimpleNamespace(
        mark_running=lambda *a: None,
        apply_results=lambda *a: None,
        set_status=lambda *a: None,
    )
    mgr._model = types.SimpleNamespace(root_dir="/tmp")
    box = {}

    class Handle:
        pass

    def fake_stream(argv, cwd, on_line, on_done):
        # Production fires callbacks after the handle exists; capture and
        # drive them after _run_test_project returns, same order.
        box["argv"] = argv
        box["cb"] = (on_line, on_done)
        return Handle()

    monkeypatch.setattr(csharp_mod.dotnet_cli, "run_streaming", fake_stream)
    monkeypatch.setattr(csharp_mod.GLib, "idle_add", lambda fn, *a, **k: fn(*a, **k))
    mgr._run_test_project("/tmp/T.csproj")
    on_line, on_done = box["cb"]
    on_line("stdout", "Passed Foo.Bar [1s]\n")
    on_line("stdout", "Failed Foo.Baz [2s]\n")
    on_done(1)
    assert box["argv"][1] == "test"
    assert any("1 passed, 1 failed" in s and "exit 1" in s for s in status), status
    run = testing_mod.parse_test_output("Passed Foo.Bar [1s]\nFailed Foo.Baz [2s]\n")
    assert (run.passed, run.failed, run.total) == (1, 1, 2)
