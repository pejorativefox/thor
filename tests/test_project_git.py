"""project-mode git status colors (headless)."""

import os
import shutil
import subprocess
import sys
import tempfile

import pytest


import thor.project as projectmode
from thor.project import gitstatus


def _has_git():
    return shutil.which("git") is not None


def test_vscode_default_colors():
    assert gitstatus.COLOR_MODIFIED == "#E2C08D"
    assert gitstatus.COLOR_UNTRACKED == "#73C991"
    assert gitstatus.COLOR_ADDED == "#81B88B"
    assert gitstatus.COLOR_DELETED == "#C74E39"
    assert gitstatus.COLOR_CONFLICTING == "#6C6CC4"
    assert gitstatus.COLOR_IGNORED == "#8C8C8C"


def test_status_to_color_full_palette():
    assert gitstatus.status_to_color("?", "?") == gitstatus.COLOR_UNTRACKED
    assert gitstatus.status_to_color(" ", "M") == gitstatus.COLOR_MODIFIED
    assert gitstatus.status_to_color("M", " ") == gitstatus.COLOR_MODIFIED
    assert gitstatus.status_to_color("M", "M") == gitstatus.COLOR_MODIFIED
    assert gitstatus.status_to_color("A", " ") == gitstatus.COLOR_ADDED
    assert gitstatus.status_to_color(" ", "D") == gitstatus.COLOR_DELETED
    assert gitstatus.status_to_color("D", " ") == gitstatus.COLOR_DELETED
    assert gitstatus.status_to_color("R", " ") == gitstatus.COLOR_RENAMED
    assert gitstatus.status_to_color("U", "U") == gitstatus.COLOR_CONFLICTING
    assert gitstatus.status_to_color("A", "A") == gitstatus.COLOR_CONFLICTING
    assert gitstatus.status_to_color("!", "!") == gitstatus.COLOR_IGNORED
    assert gitstatus.status_to_color(" ", " ") is None


def test_parse_porcelain_z_basic():
    with tempfile.TemporaryDirectory() as tmp:
        raw = b"?? new.txt\x00 M mod.txt\x00M  staged.txt\x00"
        out = gitstatus.parse_porcelain_z(raw, tmp)
        assert out[os.path.join(tmp, "new.txt")] == ("?", "?")
        assert out[os.path.join(tmp, "mod.txt")] == (" ", "M")
        assert out[os.path.join(tmp, "staged.txt")] == ("M", " ")


def test_parse_porcelain_z_rename_consumes_source():
    with tempfile.TemporaryDirectory() as tmp:
        raw = b"R  new.txt\x00old.txt\x00?? other.txt\x00"
        out = gitstatus.parse_porcelain_z(raw, tmp)
        assert out[os.path.join(tmp, "new.txt")][0] == "R"
        # source path is consumed, not a status entry itself
        assert os.path.join(tmp, "old.txt") not in out
        assert out[os.path.join(tmp, "other.txt")] == ("?", "?")


def test_parse_porcelain_z_empty():
    with tempfile.TemporaryDirectory() as tmp:
        assert gitstatus.parse_porcelain_z(b"", tmp) == {}


def test_aggregate_dir_color_priority():
    agg = gitstatus.aggregate_dir_color
    assert agg([]) is None
    assert agg([None, None]) is None
    # conflict beats everything, deleted beats modified, modified beats untracked
    assert agg([gitstatus.COLOR_UNTRACKED, gitstatus.COLOR_MODIFIED]) == gitstatus.COLOR_MODIFIED
    assert agg([gitstatus.COLOR_MODIFIED, gitstatus.COLOR_DELETED]) == gitstatus.COLOR_DELETED
    assert (
        agg([gitstatus.COLOR_ADDED, gitstatus.COLOR_CONFLICTING])
        == gitstatus.COLOR_CONFLICTING
    )
    assert agg([gitstatus.COLOR_UNTRACKED]) == gitstatus.COLOR_UNTRACKED


def test_color_for_path_unknown_is_none():
    assert gitstatus.color_for_path({}, "/no/such/file.txt") is None


def test_get_git_statuses_real_repo():
    if not _has_git():
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tracked = os.path.join(tmp, "tracked.txt")
        with open(tracked, "w") as f:
            f.write("one")
        subprocess.run(["git", "add", "tracked.txt"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(tracked, "w") as f:
            f.write("two")
        untracked = os.path.join(tmp, "new.txt")
        with open(untracked, "w") as f:
            f.write("new")

        statuses = gitstatus.get_git_statuses(tmp)
        assert statuses[os.path.abspath(tracked)][1] == "M"
        assert statuses[os.path.abspath(untracked)] == ("?", "?")
        assert gitstatus.color_for_path(statuses, tracked) == gitstatus.COLOR_MODIFIED
        assert gitstatus.color_for_path(statuses, untracked) == gitstatus.COLOR_UNTRACKED


def test_get_git_statuses_non_repo_is_empty():
    with tempfile.TemporaryDirectory() as tmp:
        # No `git init` here; must soft-fail to {} (no raise).
        assert gitstatus.get_git_statuses(tmp) == {}


def test_find_git_root_none_outside_repo():
    with tempfile.TemporaryDirectory() as tmp:
        # Plain temp dir: no .git here and /tmp has none above it.
        assert gitstatus.get_git_statuses(tmp) == {}
        assert gitstatus.find_git_root(tmp) is None
        assert gitstatus.find_git_root(os.path.join(tmp, "no-such-sub")) is None


def test_find_git_root_inside_repo():
    if not _has_git():
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sub = os.path.join(tmp, "a", "b")
        os.makedirs(sub)
        assert gitstatus.find_git_root(sub) == os.path.abspath(tmp)


def test_projectmode_exposes_gitstatus():
    assert projectmode._gitstatus is gitstatus
