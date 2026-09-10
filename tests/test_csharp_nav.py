# -*- coding: utf-8 -*-
"""Regression tests for the C# navigation outage (go-to-definition / references).

Root cause: the ``CSharpManager._dotnet()`` helper was deleted while its
callers (solution refresh, build/test/run, Roslyn startup) and the new
``self._setting(...)`` reads were kept — every one raised ``AttributeError``,
so solution discovery never ran, Roslyn never reached ``ready``, and the
definition/references handlers early-returned forever.
"""

import types

import thor.csharp as csharp_mod
from thor.csharp import intelligence as intel


def _manager():
    mgr = csharp_mod.CSharpManager(window=None)
    # Never touch the real window/panels in tests.
    mgr.window = types.SimpleNamespace(get_documents=lambda: [])
    mgr.output = None
    mgr.explorer = None
    mgr.completion_popup = None
    return mgr


def test_setting_and_dotnet_helpers_exist_and_fall_back():
    mgr = _manager()
    assert isinstance(mgr._dotnet(), str) and mgr._dotnet()
    assert mgr._setting("dotnet_executable", "dotnet") is not None
    # A missing store must degrade to defaults, never raise.
    mgr.settings = None
    assert mgr._setting("roslyn_server", "DEF") == "DEF"
    assert isinstance(mgr._dotnet(), str) and mgr._dotnet()


def test_refresh_cb_runs_discovery_without_attribute_error(monkeypatch, tmp_path):
    """The full debounced refresh path exercises ``_dotnet`` + publish."""
    import thor.csharp.solution as solution_mod

    mgr = _manager()
    model = solution_mod.SolutionModel(path=None, root_dir=str(tmp_path), projects=[])
    monkeypatch.setattr(solution_mod, "load_solution", lambda start, dotnet: model)
    # Run the worker inline and deliver idle callbacks synchronously.
    monkeypatch.setattr(csharp_mod.GLib, "idle_add", lambda fn, *a, **k: fn(*a, **k))

    class _InlineThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(csharp_mod.threading, "Thread", _InlineThread)
    mgr._refresh_cb()
    assert mgr._model is model


def _ready_manager():
    mgr = _manager()
    recorded = []

    class FakeRoslyn:
        state = "ready"
        open_docs = {}

        def request(self, method, params, callback):
            recorded.append((method, params, callback))
            return 1

    mgr.roslyn = FakeRoslyn()
    return mgr, recorded


def test_goto_definition_sends_lsp_request_and_jumps():
    mgr, recorded = _ready_manager()
    jumps = []
    mgr._jump_to = lambda p, l, c=0: jumps.append((p, l, c))
    mgr.output = types.SimpleNamespace(
        set_status=lambda *a: None,
        set_problems=lambda *a: None,
        show_problems=lambda: None,
    )
    mgr._on_goto_definition(None, "/tmp/a.cs", 1, 2)
    assert len(recorded) == 1
    method, params, callback = recorded[0]
    assert method == "textDocument/definition"
    assert params["position"] == {"line": 1, "character": 2}
    assert params["textDocument"]["uri"].endswith("a.cs")
    # Simulate the server answer: cursor must land on the target.
    callback({"result": {"uri": "file:///tmp/b.cs",
                         "range": {"start": {"line": 5, "character": 3}}}})
    assert jumps == [("/tmp/b.cs", 5, 3)]


def test_find_references_includes_declaration_and_lists():
    mgr, recorded = _ready_manager()
    problems = []
    mgr.output = types.SimpleNamespace(
        set_status=lambda *a: None,
        set_problems=lambda rows: problems.extend(rows),
        show_problems=lambda: None,
    )
    mgr._on_find_references(None, "/tmp/a.cs", 0, 0)
    assert len(recorded) == 1
    method, params, _cb = recorded[0]
    assert method == "textDocument/references"
    assert params["context"] == {"includeDeclaration": True}
    _cb({"result": [
        {"uri": "file:///tmp/a.cs",
         "range": {"start": {"line": 0, "character": 0}}},
        {"uri": "file:///tmp/b.cs",
         "range": {"start": {"line": 4, "character": 1}}},
    ]})
    assert len(problems) == 2
    assert problems[1][1] == "b.cs" and problems[1][2] == 5


def test_nav_when_roslyn_down_warns_instead_of_silence():
    mgr, _recorded = _ready_manager()
    mgr.roslyn.state = "stopped"
    status = []
    mgr.output = types.SimpleNamespace(
        set_status=lambda text: status.append(text),
        append=lambda *a: None,
    )
    mgr._on_goto_definition(None, "/tmp/a.cs", 0, 0)
    mgr._on_find_references(None, "/tmp/a.cs", 0, 0)
    assert status, "expected a visible 'Roslyn not ready' status"
    assert any("Roslyn not ready" in s for s in status)


def test_parse_locations_still_covers_link_shapes():
    assert intel.parse_locations({"result": None}) == []
