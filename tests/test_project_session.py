"""Per-project session save/restore (headless)."""

from __future__ import annotations

import json
import os
import tempfile
import types

from thor.project import session


class _Loc:
    def __init__(self, path):
        self._path = path

    def get_path(self):
        return self._path


class _Doc:
    def __init__(self, path=None, modified=False, text="", line=0, col=0):
        self._loc = _Loc(path) if path else None
        self._modified = modified
        self._text = text
        self._line = line
        self._col = col

    def get_location(self):
        return self._loc

    def get_modified(self):
        return self._modified

    def set_modified(self, v):
        self._modified = bool(v)

    def get_insert_mark(self):
        return object()

    def get_iter_at_mark(self, _mark):
        doc = self

        class _It:
            def get_line(self):
                return doc._line

            def get_line_offset(self):
                return doc._col

        return _It()

    def get_bounds(self):
        return object(), object()

    def get_text(self, _s, _e, _hidden):
        return self._text

    def set_text(self, t):
        self._text = t


class _Tab:
    def __init__(self, doc):
        self._doc = doc

    def get_document(self):
        return self._doc


class _Win:
    """Duck-typed window for collect + restore tests."""

    def __init__(self, tabs=(), active=0):
        self._tabs = list(tabs)
        self._active = active
        self._notebook = types.SimpleNamespace(
            get_current_page=lambda: self._active,
            set_current_page=lambda i: setattr(self, "_active", i),
            get_n_pages=lambda: len(self._tabs),
        )
        self.open_calls = []

    def get_active_tab(self):
        if 0 <= self._active < len(self._tabs):
            return self._tabs[self._active]
        return None

    def get_documents(self):
        return [t.get_document() for t in self._tabs]

    def open_file(self, path, line_pos=-1, col_pos=-1, jump_to=False):
        doc = _Doc(path=path, line=max(0, line_pos), col=max(0, col_pos))
        tab = _Tab(doc)
        self._tabs.append(tab)
        self.open_calls.append((path, line_pos, col_pos))
        return tab

    def create_tab(self, jump_to=False):
        doc = _Doc()
        tab = _Tab(doc)
        self._tabs.append(tab)
        return tab


def _touch(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return path


def test_session_key_stable_and_unique():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        assert session.session_key(a) == session.session_key(a)
        assert len(session.session_key(a)) == 32
        assert session.session_key(a) != session.session_key(b)


def test_save_load_roundtrip_paths_cursor_active():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
        fa = _touch(os.path.join(root, "a.py"), "a")
        fb = _touch(os.path.join(root, "b.py"), "b")
        win = _Win([_Tab(_Doc(fa, line=3, col=5)), _Tab(_Doc(fb, line=10, col=2))], active=1)
        # cursor probe uses get_insert_mark/get_iter_at_mark
        assert session.save_for_root(root, win, base=cache) is not None
        loaded = session.load_for_root(root, base=cache)
        assert loaded is not None
        assert [f["path"] for f in loaded["files"]] == [os.path.abspath(fa), os.path.abspath(fb)]
        assert loaded["files"][0]["line"] == 3 and loaded["files"][0]["col"] == 5
        assert loaded["active"] == 1


def test_unsaved_content_backup_roundtrip_and_prune():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
        fa = _touch(os.path.join(root, "a.py"), "saved")
        win = _Win([_Tab(_Doc(fa, modified=True, text="dirty!"))])
        assert session.save_for_root(root, win, base=cache) is not None
        loaded = session.load_for_root(root, base=cache)
        assert loaded is not None
        assert loaded["files"][0]["backup"] is not None
        backup = loaded["files"][0]["backup"]
        assert isinstance(backup, str)
        assert session.read_backup(root, backup, base=cache) == "dirty!"
        # Save again clean -> stale backup pruned
        win2 = _Win([_Tab(_Doc(fa))])
        session.save_for_root(root, win2, base=cache)
        assert os.listdir(session.unsaved_dir_for_root(root, base=cache)) == []


def test_load_corrupt_returns_none():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
        path = session.session_path_for_root(root, base=cache)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not json")
        assert session.load_for_root(root, base=cache) is None


def test_merge_extras_dedupe_and_win_active():
    ordered, active = session.merge_file_lists(
        [{"path": "/r/a.py", "line": 1, "col": 0, "backup": None}], ["/r/b.py", "/r/a.py"])
    assert [f["path"] for f in ordered] == ["/r/a.py", os.path.abspath("/r/b.py")]
    assert active == 0  # last extra (deduped a.py) wins -> its index


def test_restore_merges_session_and_extras():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
        fa = _touch(os.path.join(root, "a.py"), "a")
        fb = _touch(os.path.join(root, "b.py"), "b")
        fc = _touch(os.path.join(root, "c.py"), "c")
        src = _Win([_Tab(_Doc(fa, line=4, col=1)), _Tab(_Doc(fb))], active=0)
        session.save_for_root(root, src, base=cache)
        assert session.load_for_root(root, base=cache) is not None
        dst = _Win()
        assert session.restore_into_window(dst, root, extra_files=[fc], base=cache) is True
        opened = [c[0] for c in dst.open_calls]
        assert opened == [os.path.abspath(fa), os.path.abspath(fb), os.path.abspath(fc)]
        assert dst._active == 2  # explicit extra wins active
        assert dst.open_calls[0][1] == 4  # cursor line restored


def test_restore_skips_vanished_without_backup():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
        fa = _touch(os.path.join(root, "a.py"), "a")
        src = _Win([_Tab(_Doc(fa))])
        session.save_for_root(root, src, base=cache)
        os.unlink(fa)
        dst = _Win()
        assert session.restore_into_window(dst, root, base=cache) is False
        assert dst.open_calls == []


def test_restore_applies_backup_for_missing_file():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
        fa = _touch(os.path.join(root, "a.py"), "saved")
        src = _Win([_Tab(_Doc(fa, modified=True, text="hot-exit"))])
        session.save_for_root(root, src, base=cache)
        os.unlink(fa)
        dst = _Win()
        assert session.restore_into_window(dst, root, base=cache) is True
        assert dst._tabs[0].get_document()._text == "hot-exit"
        assert dst._tabs[0].get_document()._modified is True


def test_unsafe_root_never_saved():
    home = os.path.realpath(os.path.expanduser("~"))
    with tempfile.TemporaryDirectory() as cache:
        win = _Win([_Tab(_Doc("/tmp/x.py"))])
        assert session.save_for_root(home, win, base=cache) is None


def test_get_window_root_prefers_browser():
    with tempfile.TemporaryDirectory() as root:
        browser = types.SimpleNamespace(_root_dir=root)
        win = types.SimpleNamespace(
            _thor_project_browser=browser, _initial_folder="/tmp")
        assert session.get_window_root(win) == os.path.realpath(root)


def test_collect_skips_empty_untitled():
    win = _Win([_Tab(_Doc())])
    entries, _active = session.collect_tabs(win)
    assert entries == []
