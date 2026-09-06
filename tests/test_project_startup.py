"""project-mode startup folder + xed-code launcher (headless)."""

import inspect
import os
import sys
import tempfile
import time
import types


import thor.project as projectmode
IS_THOR = True  # thor standalone, skip plugin UI tests


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")


def test_has_project_markers():
    with tempfile.TemporaryDirectory() as tmp:
        assert projectmode.has_project_markers(tmp) is False
        _touch(os.path.join(tmp, ".git", "HEAD"))
        assert projectmode.has_project_markers(tmp) is True


def test_has_project_markers_git_file_and_solutions():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, ".git"))  # worktree-style file
        assert projectmode.has_project_markers(tmp) is True
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "App.sln"))
        assert projectmode.has_project_markers(tmp) is True
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "App.csproj"))
        assert projectmode.has_project_markers(tmp) is True
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "package.json"))
        assert projectmode.has_project_markers(tmp) is True


def test_has_project_markers_top_level_only():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "nested", "App.sln"))
        _touch(os.path.join(tmp, "plain.txt"))
        assert projectmode.has_project_markers(tmp) is False
    assert projectmode.has_project_markers(os.path.join(tmp, "nope")) is False


def test_is_unsafe_root():
    home = os.path.realpath(os.path.expanduser("~"))
    assert projectmode.is_unsafe_root(home) is True
    assert projectmode.is_unsafe_root("/") is True
    assert projectmode.is_unsafe_root(os.path.dirname(home)) is True
    with tempfile.TemporaryDirectory() as tmp:
        assert projectmode.is_unsafe_root(tmp) is False


def test_pending_roundtrip_consumes():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "proj")
        os.makedirs(target)
        pending = os.path.join(tmp, "pending-root")
        assert projectmode.write_pending_root(target, path=pending) == pending
        assert projectmode.take_pending_root(path=pending) == os.path.abspath(target)
        assert not os.path.exists(pending)
        assert projectmode.take_pending_root(path=pending) is None


def test_pending_stale_consumed_as_none():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "proj")
        os.makedirs(target)
        pending = os.path.join(tmp, "pending-root")
        projectmode.write_pending_root(target, path=pending)
        old = time.time() - 600
        os.utime(pending, (old, old))
        assert projectmode.take_pending_root(path=pending) is None
        assert not os.path.exists(pending)


def test_pending_empty_consumed_as_none():
    with tempfile.TemporaryDirectory() as tmp:
        pending = os.path.join(tmp, "pending-root")
        with open(pending, "w") as f:
            f.write("\n")
        assert projectmode.take_pending_root(path=pending) is None
        assert not os.path.exists(pending)


def test_resolve_startup_root_pending_wins():
    if not hasattr(projectmode, "ProjectModePlugin"):
        import pytest; pytest.skip("ProjectModePlugin not available in thor")
    with tempfile.TemporaryDirectory() as marker, tempfile.TemporaryDirectory() as plain:
        _touch(os.path.join(marker, ".git", "HEAD"))
        assert projectmode.resolve_startup_root(plain, marker) == ("load", os.path.abspath(marker))
        assert projectmode.resolve_startup_root(marker, plain) == ("prompt", os.path.abspath(plain))


def test_resolve_startup_root_pending_edge_cases():
    if not hasattr(projectmode, "ProjectModePlugin"):
        import pytest; pytest.skip("ProjectModePlugin not available in thor")
    home = os.path.realpath(os.path.expanduser("~"))
    assert projectmode.resolve_startup_root(None, os.path.join("/no/such/dir")) == ("none", None)
    action, folder = projectmode.resolve_startup_root(None, home)
    assert (action, folder) == ("prompt", os.path.abspath(home))
    assert projectmode.resolve_startup_root(None, None) == ("none", None)


def test_resolve_startup_root_cwd_fallback():
    if not hasattr(projectmode, "ProjectModePlugin"):
        import pytest; pytest.skip("ProjectModePlugin not available in thor")
    with tempfile.TemporaryDirectory() as marker, tempfile.TemporaryDirectory() as plain:
        _touch(os.path.join(marker, "App.sln"))
        assert projectmode.resolve_startup_root(marker, None) == ("load", os.path.abspath(marker))
        assert projectmode.resolve_startup_root(plain, None) == ("none", None)
    home = os.path.realpath(os.path.expanduser("~"))
    assert projectmode.resolve_startup_root(home, None) == ("none", None)


def _startup_ns():
    cls = projectmode.ProjectModePlugin
    ns = types.SimpleNamespace()
    for name in ("_startup_load", "_prompt_startup_root", "_set_root", "_choose_root"):
        setattr(ns, name, types.MethodType(getattr(cls, name), ns))
    return ns


def test_startup_load_auto_loads_markers():
    if IS_THOR:
        import pytest; pytest.skip("plugin UI test not applicable for thor")
    with tempfile.TemporaryDirectory() as marker:
        _touch(os.path.join(marker, "go.mod"))
        called = []
        ns = _startup_ns()
        ns._set_root = lambda folder: called.append(folder)
        saved_take = projectmode.take_pending_root
        saved_getcwd = os.getcwd
        projectmode.take_pending_root = lambda *a, **k: os.path.abspath(marker)
        os.getcwd = lambda: "/tmp"
        try:
            ns._startup_load()
        finally:
            projectmode.take_pending_root = saved_take
            os.getcwd = saved_getcwd
        assert called == [os.path.abspath(marker)]


def test_startup_load_prompts_for_markerless_pending():
    if not hasattr(projectmode, "ProjectModePlugin"):
        import pytest; pytest.skip("ProjectModePlugin not available in thor")
    with tempfile.TemporaryDirectory() as plain:
        prompted = []
        ns = _startup_ns()
        ns._set_root = lambda folder: prompted.append(("load", folder))
        ns._choose_root = lambda initial_folder=None: prompted.append(("prompt", initial_folder))
        saved_take = projectmode.take_pending_root
        saved_getcwd = os.getcwd
        saved_glib = projectmode.GLib
        projectmode.take_pending_root = lambda *a, **k: os.path.abspath(plain)
        os.getcwd = lambda: "/tmp"
        projectmode.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
        try:
            ns._startup_load()
        finally:
            projectmode.take_pending_root = saved_take
            os.getcwd = saved_getcwd
            projectmode.GLib = saved_glib
        assert prompted == [("prompt", os.path.abspath(plain))]


def test_choose_root_accepts_initial_folder():
    if not hasattr(projectmode, "ProjectModePlugin"):
        import pytest; pytest.skip("ProjectModePlugin not available in thor")
    sig = inspect.signature(projectmode.ProjectModePlugin._choose_root)
    assert "initial_folder" in sig.parameters
    assert sig.parameters["initial_folder"].default is None


def _code_module():
    from importlib.machinery import SourceFileLoader

    path = os.path.join(os.path.dirname(__file__), "..", "xed-code")
    return SourceFileLoader("xed_code", path).load_module()


def test_code_resolve_target():
    xed_code = _code_module()
    assert xed_code.resolve_target(["xed-code"]) == os.path.abspath(".")
    assert xed_code.resolve_target(["xed-code", "--new-window"]) == os.path.abspath(".")
    assert xed_code.resolve_target(["xed-code", "-h"]) is None
    assert xed_code.resolve_target(["xed-code", "--help"]) is None
    with tempfile.TemporaryDirectory() as tmp:
        sub = os.path.join(tmp, "proj")
        assert xed_code.resolve_target(["xed-code", sub]) == os.path.abspath(sub)


def test_code_main_help_and_bad_dir():
    xed_code = _code_module()
    assert xed_code.main(["xed-code", "--help"]) == 0
    assert xed_code.main(["xed-code", "/no/such/dir"]) == 2
    assert xed_code.launch("/no/such/dir") == 2


def test_code_launch_records_pending_and_opens_new_window():
    if IS_THOR:
        import pytest; pytest.skip("plugin UI test not applicable for thor")
    xed_code = _code_module()
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache:
        target = os.path.join(tmp, "proj")
        os.makedirs(target)
        calls = []
        saved_popen = xed_code.subprocess.Popen
        saved_env = dict(os.environ)
        xed_code.subprocess.Popen = lambda argv, **kw: calls.append((argv, kw))
        os.environ["XDG_CACHE_HOME"] = cache
        try:
            assert xed_code.launch(target) == 0
        finally:
            xed_code.subprocess.Popen = saved_popen
            os.environ.clear()
            os.environ.update(saved_env)
        assert calls and calls[0][0] == ["xed", "--new-window"]
        assert calls[0][1].get("cwd") == target
        assert calls[0][1].get("start_new_session") is True
        pending = os.path.join(cache, "xed", "project-mode", "pending-root")
        with open(pending, encoding="utf-8") as f:
            assert f.read().strip() == target