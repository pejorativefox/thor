"""Project browser git monitor filtering (headless)."""

import os
import tempfile
import types

import pytest

import thor.project as projectmode


def test_git_noise_ignored():
    root = "/repo"
    assert projectmode.should_refresh_for_git_event(root, "/repo/.git/objects/ab/cd") is False
    assert projectmode.should_refresh_for_git_event(root, "/repo/.git/logs/HEAD") is False
    assert projectmode.should_refresh_for_git_event(root, "/repo/.git/index.lock") is False
    assert projectmode.should_refresh_for_git_event(root, "/repo/.git/hooks/x.tmp") is False


def test_git_relevant_allowed():
    root = "/repo"
    assert projectmode.should_refresh_for_git_event(root, "/repo/.git/HEAD") is True
    assert projectmode.should_refresh_for_git_event(root, "/repo/.git/index") is True
    assert projectmode.should_refresh_for_git_event(root, "/repo/.git/refs/heads/main") is True
    assert projectmode.should_refresh_for_git_event(root, "/repo/.git/packed-refs") is True
    assert projectmode.should_refresh_for_git_event(root, "/repo/src/a.cs") is True


def test_git_outside_root_ignored():
    assert projectmode.should_refresh_for_git_event("/repo", "/other/f") is False


def test_tree_rebuild_on_delete():
    assert projectmode.should_rebuild_tree_for_event("/repo", "/repo/src/a.cs") is True
    assert projectmode.should_rebuild_tree_for_event("/repo", None, None) is True
    assert projectmode.should_rebuild_tree_for_event("/repo", "/other/f") is False
    assert projectmode.should_rebuild_tree_for_event("/repo", "/repo/.git/HEAD") is False
    assert projectmode.should_rebuild_tree_for_event("/repo", "/repo/.git/objects/x") is False


def test_collect_watch_dirs_prunes():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "src", "sub"))
        os.makedirs(os.path.join(tmp, ".git"))
        os.makedirs(os.path.join(tmp, "bin"))
        with open(os.path.join(tmp, "src", "a.txt"), "w") as f:
            f.write("x")
        watched = projectmode.collect_watch_dirs(tmp)
        assert os.path.abspath(tmp) in watched
        assert os.path.abspath(os.path.join(tmp, "src")) in watched
        assert os.path.abspath(os.path.join(tmp, "src", "sub")) in watched
        assert all("/.git" not in w and "/bin" not in w for w in watched)


def test_git_event_paths_headless():
    class Fake:
        def __init__(self, p):
            self._p = p

        def get_path(self):
            return self._p

    assert projectmode.git_event_paths(Fake("/a"), Fake("/b")) == ("/a", "/b")
    assert projectmode.git_event_paths(None, None) == (None, None)


def test_working_tree_refresh_bypasses_storm_gate():
    import time

    if not hasattr(projectmode, "ProjectBrowser"):
        pytest.skip("no Gtk")
    saved_glib = projectmode.GLib
    timers = []
    projectmode.GLib = types.SimpleNamespace(
        timeout_add=lambda ms, cb, *a: timers.append((ms, cb, a)) or len(timers),
        source_remove=lambda i: None,
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ns = types.SimpleNamespace(
                _root_dir=tmp,
                _git_generation=0,
                _last_git_refresh=time.monotonic(),
                _refresh_timer=None,
                _refresh_interval_s=projectmode.GIT_REFRESH_MIN_INTERVAL_S,
                _query_git_thread=lambda *a: None,
            )
            ns.refresh_git_statuses = types.MethodType(
                projectmode.ProjectBrowser.refresh_git_statuses, ns)
            ns._arm_git_timer = types.MethodType(
                projectmode.ProjectBrowser._arm_git_timer, ns)
            ns._on_git_changed_fire = lambda *a: False
            # Default gate: refreshed a moment ago -> re-arm, no new query.
            ns.refresh_git_statuses()
            assert ns._git_generation == 0
            assert timers, "gated refresh must re-arm a timer"
            # Working-tree edit: zero interval proceeds immediately.
            queries = []
            ns._query_git_thread = lambda *a: queries.append(a)
            ns.refresh_git_statuses(min_interval_s=0.0)
            assert ns._git_generation == 1
    finally:
        projectmode.GLib = saved_glib


def test_git_timer_preserves_interval_through_fire():
    if not hasattr(projectmode, "ProjectBrowser"):
        pytest.skip("no Gtk")
    saved_glib = projectmode.GLib
    projectmode.GLib = types.SimpleNamespace(
        timeout_add=lambda ms, cb, *a: 7,
        source_remove=lambda i: None,
    )
    try:
        ns = types.SimpleNamespace(
            _git_generation=5,
            _refresh_timer=None,
            _refresh_interval_s=projectmode.GIT_REFRESH_MIN_INTERVAL_S,
        )
        ns._arm_git_timer = types.MethodType(
            projectmode.ProjectBrowser._arm_git_timer, ns)
        ns._on_git_changed_fire = types.MethodType(
            projectmode.ProjectBrowser._on_git_changed_fire, ns)
        calls = []
        ns.refresh_git_statuses = lambda *a, **k: calls.append(k)
        ns._arm_git_timer(500, 5, 0.0)
        assert ns._refresh_interval_s == 0.0
        ns._on_git_changed_fire(5)
        assert calls == [{"min_interval_s": 0.0}]
        ns._on_git_changed_fire(4)  # stale generation: ignored
        assert len(calls) == 1
    finally:
        projectmode.GLib = saved_glib


def test_git_monitor_target_resolves_repo_root():
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        sub = os.path.join(tmp, "src", "deep")
        os.makedirs(sub)
        assert projectmode.git_monitor_target(sub) == os.path.join(tmp, ".git")
        assert projectmode.git_monitor_target(tmp) == os.path.join(tmp, ".git")


def test_git_monitor_target_falls_back_without_repo():
    with tempfile.TemporaryDirectory() as tmp:
        assert projectmode.git_monitor_target(tmp) == os.path.join(tmp, ".git")
        assert projectmode.git_monitor_target("") is None

class _FakeEnumState:
    """Mimics a real GI Thor.TabState: str() is an int, names are rich."""

    def __init__(self, num, name, nick):
        self._num = num
        self.value_name = name
        self.value_nick = nick

    def __str__(self):
        return str(self._num)

    def __int__(self):
        return self._num


FAKE_SAVING = _FakeEnumState(3, "THOR_TAB_STATE_SAVING", "state-saving")
FAKE_NORMAL = _FakeEnumState(0, "THOR_TAB_STATE_NORMAL", "state-normal")
FAKE_SAVING_ERROR = _FakeEnumState(10, "THOR_TAB_STATE_SAVING_ERROR", "state-saving-error")


def test_tab_state_name_handles_real_gi_enums():
    # Regression: str(STATE_SAVING) == "3" in production, so substring
    # checks on str() never matched and save refresh never fired.
    assert projectmode.tab_state_name(FAKE_SAVING) == "THOR_TAB_STATE_SAVING"
    assert projectmode.tab_state_name(FAKE_NORMAL) == "THOR_TAB_STATE_NORMAL"
    assert projectmode.tab_state_name("THOR_TAB_STATE_SAVING") == "THOR_TAB_STATE_SAVING"
    assert projectmode.tab_state_name(3) == "THOR_TAB_STATE_SAVING"
    assert projectmode.tab_state_name(0) == "THOR_TAB_STATE_NORMAL"


def test_is_save_completed_transitions():
    assert projectmode.is_save_completed(FAKE_SAVING, FAKE_NORMAL) is True
    assert projectmode.is_save_completed("THOR_TAB_STATE_SAVING", "THOR_TAB_STATE_NORMAL") is True
    assert projectmode.is_save_completed(3, 0) is True
    assert projectmode.is_save_completed(FAKE_NORMAL, FAKE_NORMAL) is False
    assert projectmode.is_save_completed(FAKE_SAVING_ERROR, FAKE_NORMAL) is False
    assert projectmode.is_save_completed("", FAKE_NORMAL) is False


def test_git_event_filter_resolves_subdir_root():
    """Events in the real .git count when a subdirectory is open."""
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".git", "refs", "heads"))
        os.makedirs(os.path.join(tmp, "src", "deep"))
        head = os.path.join(tmp, ".git", "refs", "heads", "main")
        with open(head, "w") as f:
            f.write("x")
        sub = os.path.join(tmp, "src", "deep")
        assert projectmode.should_refresh_for_git_event(sub, head) is True
        noise = os.path.join(tmp, ".git", "objects", "ab", "cdef")
        os.makedirs(os.path.dirname(noise))
        with open(noise, "w") as f:
            f.write("x")
        assert projectmode.should_refresh_for_git_event(sub, noise) is False
        assert projectmode.should_refresh_for_git_event(sub, os.path.join(sub, "a.cs")) is True
        assert projectmode.should_refresh_for_git_event(sub, "/other/f") is False


def test_collect_watch_dirs_is_breadth_first():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "a", "deep", "deeper"))
        os.makedirs(os.path.join(tmp, "b"))
        full = projectmode.collect_watch_dirs(tmp)
        assert os.path.abspath(tmp) in full
        assert os.path.join(os.path.abspath(tmp), "a", "deep", "deeper") in full
        # With room for only 3 dirs, the shallowest levels win.
        tight = projectmode.collect_watch_dirs(tmp, max_dirs=3)
        assert tight[0] == os.path.abspath(tmp)
        assert os.path.join(os.path.abspath(tmp), "a") in tight
        assert os.path.join(os.path.abspath(tmp), "b") in tight