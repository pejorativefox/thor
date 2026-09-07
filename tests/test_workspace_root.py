"""Workspace scoping: never hand Roslyn a root that crawls /proc via symlinks."""

import os
import sys
import tempfile


from thor.csharp import roslyn, solution


def test_is_home_root():
    assert solution.is_home_root(os.path.expanduser("~")) is True
    assert solution.is_home_root("/") is True
    assert solution.is_home_root("/tmp/definitely-not-home-xyz") is False


def test_fallback_refuses_home():
    assert solution.find_projects_fallback(os.path.expanduser("~")) == []


def test_fallback_skips_symlinked_dirs():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as elsewhere:
        real = os.path.join(tmp, "real")
        os.makedirs(real)
        with open(os.path.join(real, "A.csproj"), "w") as f:
            f.write("<Project/>")
        with open(os.path.join(elsewhere, "B.csproj"), "w") as f:
            f.write("<Project/>")
        os.symlink(elsewhere, os.path.join(tmp, "link"))
        found = solution.find_projects_fallback(tmp)
        assert found == [os.path.join(real, "A.csproj")]


def test_startup_dir_tracks_launch_directory():
    import thor.csharp as thorcsharp

    previous = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            assert thorcsharp.CSharpManager._startup_dir() == tmp
        finally:
            os.chdir(previous)


def test_open_doc_dir_fallback():
    import types

    import thor.csharp as thorcsharp

    cls = thorcsharp.CSharpManager
    loc = types.SimpleNamespace(
        has_uri_scheme=lambda s: s == "file",
        get_path=lambda: "/repo/src/App/Program.cs",
    )
    doc = types.SimpleNamespace(get_location=lambda: loc)

    def _no_active():
        raise ValueError("no active document")

    def _self(window):
        # Unbound call with a stand-in self: GObject refuses attribute
        # assignment on uninitialized instances, and these helpers only
        # need .window and ._safe.
        return types.SimpleNamespace(window=window, _safe=cls._safe)

    with_docs = _self(types.SimpleNamespace(
        get_active_document=_no_active,
        get_documents=lambda: [doc],
    ))
    assert cls._active_path(with_docs) is None
    assert cls._open_doc_dir(with_docs) == "/repo/src/App"
    without_docs = _self(types.SimpleNamespace(
        get_active_document=_no_active,
        get_documents=lambda: [],
    ))
    assert cls._open_doc_dir(without_docs) is None


def test_load_solution_from_launch_dir_finds_slnx():
    with tempfile.TemporaryDirectory() as tmp:
        sub = os.path.join(tmp, "src", "App")
        os.makedirs(sub)
        slnx = os.path.join(tmp, "App.slnx")
        with open(slnx, "w") as f:
            f.write("<Solution />")
        model = solution.load_solution(sub, dotnet="definitely-not-dotnet-xyz")
        assert model.path == slnx, model.path
        assert model.root_dir == tmp


def test_load_solution_tightens_root_to_projects():
    with tempfile.TemporaryDirectory() as tmp:
        for sub in ("svc-a", "svc-b"):
            d = os.path.join(tmp, "src", sub)
            os.makedirs(d)
            with open(os.path.join(d, f"{sub}.csproj"), "w") as f:
                f.write("<Project/>")
        model = solution.load_solution(tmp, dotnet="definitely-not-dotnet-xyz")
        assert model.path is None
        assert len(model.projects) == 2
        assert model.root_dir == os.path.join(tmp, "src")


class _FakeTransport:
    def __init__(self, head, tail) -> None:
        self._head = head
        self._tail = tail

    def stderr_head(self):
        return list(self._head)

    def stderr_tail(self):
        return list(self._tail)


def test_proc_crash_gets_targeted_hint():
    errors: list = []
    mgr = roslyn.RoslynManager(on_error=errors.append)
    mgr.transport = _FakeTransport(  # type: ignore[assignment]
        ["Unhandled exception. System.IO.IOException: No such process : '/x/wine/dosdevices/z:/proc/5925/cwd'"],
        ["Language server child exited with code 134."],
    )
    mgr._on_transport_exit(134)
    assert mgr.state == "error"
    assert errors and "Wine" in errors[0] and "/proc" in errors[0]
