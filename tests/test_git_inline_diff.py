"""git-inline-diff plugin behavior (headless)."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading

import pytest


import thor.gitdiff as gitinline
from thor.gitdiff import diffparse
IS_THOR = True  # thor standalone, skip plugin UI tests


def _has_git():
    return shutil.which("git") is not None


def _init_repo(tmp):
    subprocess.run(["git", "init"], cwd=tmp, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_palette_matches_tree():
    assert diffparse.COLOR_ADDED == "#73C991"
    assert diffparse.COLOR_MODIFIED == "#E2C08D"
    assert diffparse.COLOR_DELETED == "#C74E39"
    assert diffparse.CATEGORY_ADDED == "gitinline-added"
    assert diffparse.CATEGORY_MODIFIED == "gitinline-modified"
    assert diffparse.CATEGORY_DELETED == "gitinline-deleted"


def test_parse_pure_addition():
    text = (
        "diff --git a/f.txt b/f.txt\n"
        "index 1111111..2222222 100644\n"
        "--- a/f.txt\n"
        "+++ b/f.txt\n"
        "@@ -1,0 +2,2 @@\n"
    )
    out = diffparse.parse_unified_diff(text)
    assert out["added"] == [1, 2]
    assert out["modified"] == []
    assert out["deleted"] == []


def test_parse_pure_deletion_marks_line_above_gap():
    text = "@@ -3,2 +2,0 @@\n"
    out = diffparse.parse_unified_diff(text)
    assert out["deleted"] == [1]
    assert out["added"] == [] and out["modified"] == []


def test_parse_deletion_at_start_floors_at_zero():
    out = diffparse.parse_unified_diff("@@ -1,2 +0,0 @@\n")
    assert out["deleted"] == [0]


def test_parse_modification():
    out = diffparse.parse_unified_diff("@@ -5,1 +5,3 @@\n")
    assert out["modified"] == [4, 5, 6]
    assert out["added"] == [] and out["deleted"] == []


def test_parse_multi_hunk_and_empty():
    text = "@@ -1,1 +1,1 @@\n@@ -10,0 +11,2 @@\n@@ -20,3 +23,0 @@\n"
    out = diffparse.parse_unified_diff(text)
    assert out["modified"] == [0]
    assert out["added"] == [10, 11]
    assert out["deleted"] == [22]
    empty = diffparse.parse_unified_diff("")
    assert empty == {"added": [], "modified": [], "deleted": []}


def test_parse_binary_diff_yields_nothing():
    text = (
        "diff --git a/logo.png b/logo.png\n"
        "index 1111111..2222222 100644\n"
        "GIT binary patch\n"
    )
    assert diffparse.parse_unified_diff(text) == {"added": [], "modified": [], "deleted": []}


def test_clamp_lines():
    assert diffparse.clamp_lines([0, 5, 99, -3, 5], 10) == [0, 5, 9]
    assert diffparse.clamp_lines([3], 0) == []
    assert diffparse.clamp_lines([], 10) == []


def test_untracked_marks():
    assert diffparse.untracked_marks(3) == {"added": [0, 1, 2], "modified": [], "deleted": []}
    assert diffparse.untracked_marks(0)["added"] == []

def test_untracked_marks_caps_large_file():
    assert diffparse.untracked_marks(5000)["added"] == list(range(2000))

def test_buffer_diff_staged_new_yields_added():
    if not _has_git():
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        with open(os.path.join(tmp, "new.txt"), "w") as f:
            f.write("a\nb\nc\n")
        subprocess.run(["git", "add", "new.txt"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        text = diffparse.get_buffer_diff_text(tmp, "new.txt", "a\nb\nc\nd\n")
        assert diffparse.parse_unified_diff(text)["added"] == [0, 1, 2, 3]


def test_refresh_path_skips_spawn_while_in_flight():
    plugin = _plugin()
    plugin._generations = {"/tmp/x.cs": 1}
    plugin._in_flight = {"/tmp/x.cs"}
    plugin._snapshot_doc = lambda path: {"modified": False, "line_count": 1, "text": None}  # type: ignore[method-assign]
    spawned = []
    real_thread = threading.Thread

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            spawned.append(args)

        def start(self):
            pass

    threading.Thread = _FakeThread  # type: ignore[assignment]
    try:
        plugin.refresh_path("/tmp/x.cs")
        assert spawned == []
        plugin._in_flight = set()
        plugin.refresh_path("/tmp/x.cs")
        assert len(spawned) == 1
    finally:
        threading.Thread = real_thread  # type: ignore[assignment]


def test_status_and_diff_real_repo():
    if not _has_git():
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        tracked = os.path.join(tmp, "f.txt")
        with open(tracked, "w") as f:
            f.write("one\ntwo\nthree\n")
        subprocess.run(["git", "add", "f.txt"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert diffparse.file_status_short(tmp, tracked) == ""
        assert diffparse.get_diff_text(tmp, tracked) == ""

        with open(tracked, "w") as f:
            f.write("one\nTWO\nthree\nfour\n")
        status = diffparse.file_status_short(tmp, tracked)
        assert status[1] == "M"
        hunks = diffparse.parse_unified_diff(diffparse.get_diff_text(tmp, tracked))
        assert 1 in hunks["modified"]  # line 2 changed (0-based 1)
        assert 3 in hunks["added"]  # appended line 4 (0-based 3)

        new = os.path.join(tmp, "new.txt")
        with open(new, "w") as f:
            f.write("hello\n")
        assert diffparse.file_status_short(tmp, new) == "??"
        assert diffparse.find_git_root(os.path.dirname(new)) == os.path.abspath(tmp)


def test_non_repo_soft_fails():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "f.txt")
        with open(target, "w") as f:
            f.write("x\n")
        assert diffparse.file_status_short(tmp, target) == ""
        assert diffparse.get_diff_text(tmp, target) == ""
        assert diffparse.find_git_root(tmp) is None


def test_plugin_imports_headless():
    assert gitinline._diffparse is diffparse
    assert gitinline._REFRESH_DEBOUNCE_MS == 300


def test_rgba_pixel_packs_rgb_with_full_alpha():
    assert gitinline.rgba_pixel("#73C991") == 0x73C991FF
    assert gitinline.rgba_pixel("#E2C08D") == 0xE2C08DFF
    assert gitinline.rgba_pixel("#C74E39") == 0xC74E39FF
    assert gitinline.rgba_pixel("not-a-color") is None
    assert gitinline.rgba_pixel("#FFF") is None


def test_color_pixbuf_is_small_solid_square():
    pixbuf = gitinline.color_pixbuf("#73C991")
    if gitinline.GdkPixbuf is None:
        assert pixbuf is None
        pytest.skip("no GdkPixbuf")
    assert pixbuf.get_width() == gitinline._MARK_ICON_SIZE
    assert pixbuf.get_height() == gitinline._MARK_ICON_SIZE
    assert pixbuf.get_has_alpha() is True
    assert gitinline.color_pixbuf("bogus") is None

def test_mark_colors_single_sourced_from_diffparse():
    assert gitinline._MARK_COLORS == (
        (diffparse.CATEGORY_ADDED, diffparse.COLOR_ADDED),
        (diffparse.CATEGORY_MODIFIED, diffparse.COLOR_MODIFIED),
        (diffparse.CATEGORY_DELETED, diffparse.COLOR_DELETED),
    )


def test_deleted_pixbuf_is_transparent_cornered_triangle():
    if not hasattr(gitinline, "GitInlineDiffPlugin"):
        import pytest; pytest.skip("GitInlineDiffPlugin not available in thor")
    assert gitinline.deleted_pixbuf("bogus") is None
    pixbuf = gitinline.deleted_pixbuf(diffparse.COLOR_DELETED)
    if gitinline.GdkPixbuf is None:
        assert pixbuf is None
        pytest.skip("no GdkPixbuf")
    assert pixbuf.get_width() == gitinline._MARK_ICON_SIZE
    assert pixbuf.get_height() == gitinline._MARK_ICON_SIZE

    def alpha(x, y):
        pixels = pixbuf.get_pixels()
        stride = pixbuf.get_rowstride()
        return pixels[y * stride + x * 4 + 3]

    size = gitinline._MARK_ICON_SIZE
    assert alpha(size // 2, 0) == 255
    assert alpha(0, size - 1) == 0
    assert alpha(size - 1, size - 1) == 0
    opaque = sum(
        1 for y in range(size) for x in range(size) if alpha(x, y) == 255
    )
    assert 0 < opaque < size * size


def test_configure_marks_never_sets_line_background():
    if not hasattr(gitinline, "GitInlineDiffPlugin"):
        import pytest; pytest.skip("GitInlineDiffPlugin not available in thor")
    import inspect

    source = inspect.getsource(gitinline.GitInlineDiffPlugin._configure_marks)
    assert "set_background" not in source
    assert "set_pixbuf" in source


def test_buffer_diff_tracks_unsaved_edits():
    if not _has_git():
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        tracked = os.path.join(tmp, "f.txt")
        with open(tracked, "w") as f:
            f.write("one\ntwo\nthree\n")
        subprocess.run(["git", "add", "f.txt"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Disk is clean, but the in-memory buffer has unsaved edits.
        assert diffparse.get_diff_text(tmp, tracked) == ""
        hunks = diffparse.parse_unified_diff(
            diffparse.get_buffer_diff_text(tmp, "f.txt", "one\nTWO\nthree\nfour\n")
        )
        assert 1 in hunks["modified"]
        assert 3 in hunks["added"]
        assert diffparse.parse_unified_diff(diffparse.get_buffer_diff_text(tmp, "missing.txt", "x\n"))["added"] == [0]
        assert diffparse.get_buffer_diff_text(tmp, "f.txt", "one\ntwo\nthree\n") == ""


class _FakeLocation:
    def __init__(self, path):
        self._path = path

    def has_uri_scheme(self, scheme):
        return scheme == "file"

    def get_path(self):
        return self._path


class _FakeIter:
    def __init__(self, line=0):
        self._line = line

    def get_line(self):
        return self._line


class _FakeDoc:
    def __init__(self, path, modified=False, text=""):
        self._location = _FakeLocation(path)
        self._modified = modified
        self._text = text
        self.connected = []
        self.disconnected = []

    def get_location(self):
        return self._location

    def get_file(self):
        raise AttributeError("no file")

    def get_modified(self):
        return self._modified

    def get_line_count(self):
        return len(self._text.splitlines())

    def get_bounds(self):
        return object(), object()

    def get_text(self, _start, _end, _hidden):
        return self._text

    def connect(self, signal, _handler):
        self.connected.append(signal)
        return len(self.connected)

    def disconnect(self, handler_id):
        self.disconnected.append(handler_id)


def _plugin():
    plugin = gitinline.GitInlineDiffPlugin.__new__(gitinline.GitInlineDiffPlugin)
    plugin._signal_ids = []
    plugin._mark_views_configured = set()
    plugin._generations = {}
    plugin._tab_states = {}
    plugin._debounce_timer = None
    plugin._pending_paths = set()
    plugin._doc_handlers = {}
    plugin._in_flight = set()
    plugin._git_monitors = []
    plugin._root_monitors = {}
    return plugin


def test_doc_watch_connects_once_and_schedules_on_change():
    doc = _FakeDoc("/tmp/F.cs")
    plugin = _plugin()
    scheduled = []
    plugin._schedule_paths = lambda paths: scheduled.append(list(paths))  # type: ignore[method-assign]
    plugin._watch_doc(doc)
    plugin._watch_doc(doc)
    assert doc.connected == ["changed"]
    plugin._on_doc_changed(doc)
    assert scheduled == [["/tmp/F.cs"]]


def test_doc_unwatch_disconnects():
    doc = _FakeDoc("/tmp/F.cs")
    plugin = _plugin()
    plugin._watch_doc(doc)
    plugin._unwatch_doc(doc)
    assert doc.disconnected == [1]
    assert plugin._doc_handlers == {}


def test_snapshot_captures_buffer_text_when_dirty():
    doc = _FakeDoc("/tmp/F.cs", modified=True, text="a\nb\n")
    plugin = _plugin()
    plugin._find_doc = lambda path: doc if path == "/tmp/F.cs" else None  # type: ignore[method-assign]
    snapshot = plugin._snapshot_doc("/tmp/F.cs")
    assert snapshot["modified"] is True
    assert snapshot["text"] == "a\nb\n"
    assert snapshot["line_count"] == 2
    clean = _FakeDoc("/tmp/G.cs", modified=False, text="a\n")
    plugin._find_doc = lambda path: clean if path == "/tmp/G.cs" else None  # type: ignore[method-assign]
    snapshot = plugin._snapshot_doc("/tmp/G.cs")
    assert snapshot["modified"] is False
    assert snapshot["text"] is None


def test_buffer_matches_head_defers_to_git():
    if not _has_git():
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        with open(os.path.join(tmp, "f.txt"), "w") as f:
            f.write("one\ntwo\n")
        subprocess.run(["git", "add", "f.txt"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert diffparse.buffer_matches_head(tmp, "f.txt", "one\ntwo") is True
        assert diffparse.buffer_matches_head(tmp, "f.txt", "one\ntwo\n") is True
        assert diffparse.buffer_matches_head(tmp, "f.txt", "one\nTWO") is False
        assert diffparse.buffer_matches_head(tmp, "missing.txt", "x\n") is False
        assert diffparse.buffer_matches_head(tmp, "f.txt", None) is False


def test_query_thread_prefers_disk_when_buffer_matches_it():
    if not hasattr(gitinline, "GitInlineDiffPlugin"):
        import pytest; pytest.skip("GitInlineDiffPlugin not available in thor")
    if not _has_git():
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        tracked = os.path.join(tmp, "f.txt")
        with open(tracked, "w") as f:
            f.write("one\n")
        subprocess.run(["git", "add", "f.txt"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        plugin = gitinline.GitInlineDiffPlugin()
        results = []
        plugin._apply_result = lambda p, r, g: results.append(r) or False  # type: ignore[method-assign]

        def run_query(snapshot):
            results.clear()
            plugin._query_thread(tracked, 0, snapshot)
            if gitinline.GLib is not None:
                ctx = gitinline.GLib.MainContext.default()
                for _ in range(200):
                    if not ctx.pending():
                        break
                    ctx.iteration(False)

        # Transient loader state: dirty flag, text one newline short, disk clean.
        run_query({"modified": True, "line_count": 1, "text": "one"})
        assert results == [{"added": [], "modified": [], "deleted": []}]
        # Genuine unsaved edit still uses the buffer diff.
        with open(tracked, "w") as f:
            f.write("one\nTWO\n")
        run_query({"modified": True, "line_count": 1, "text": "uno\n"})
        assert results[-1]["modified"] == [0]


def _display():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        if not ok:
            return None
        return Gtk
    except Exception as e:
        print(f"SKIP mark-clear test (no display: {e})")
        return None


def test_apply_result_removes_stale_marks():
    if not hasattr(gitinline, "GitInlineDiffPlugin"):
        import pytest; pytest.skip("GitInlineDiffPlugin not available in thor")
    """Empty refresh must clear previously applied marks.

    Needs a display; skipped headless. Regression: remove_source_marks
    was called category-first (TypeError, silently swallowed), so marks
    stuck forever — e.g. the ghost mark after an external revert.
    """
    Gtk = _display()
    if Gtk is None or gitinline.GtkSource is None:
        pytest.skip("no display")
    buf = gitinline.GtkSource.Buffer.new(None)
    buf.set_text("a\nb\nc\n", -1)
    buf.create_source_mark(
        None, diffparse.CATEGORY_ADDED, buf.get_iter_at_line(0))
    assert len(buf.get_source_marks_at_line(0, None)) == 1
    plugin = gitinline.GitInlineDiffPlugin()
    plugin._generations = {"/tmp/x.cs": 0}
    plugin._find_doc = lambda path: buf  # type: ignore[method-assign]
    plugin._configure_marks = lambda doc: None  # type: ignore[method-assign]
    plugin._apply_result("/tmp/x.cs", {"added": [], "modified": [], "deleted": []}, 0)
    assert buf.get_source_marks_at_line(0, None) == []
    plugin._apply_result("/tmp/x.cs", {"added": [1], "modified": [], "deleted": []}, 0)
    assert len(buf.get_source_marks_at_line(1, None)) == 1


def test_tab_state_helpers_detect_gi_enum_save():
    """Regression: str(STATE_SAVING) == "3", so str() checks never fired."""

    class _Enum:
        def __init__(self, num, name, nick):
            self._num = num
            self.value_name = name
            self.value_nick = nick

        def __str__(self):
            return str(self._num)

        def __int__(self):
            return self._num
    saving = _Enum(3, "THOR_TAB_STATE_SAVING", "state-saving")
    normal = _Enum(0, "THOR_TAB_STATE_NORMAL", "state-normal")
    assert gitinline.tab_state_name(saving) == "THOR_TAB_STATE_SAVING"
    assert gitinline.is_save_completed(saving, normal) is True
    assert gitinline.is_save_completed(normal, normal) is False
    assert gitinline.is_save_completed("THOR_TAB_STATE_SAVING", "THOR_TAB_STATE_NORMAL") is True