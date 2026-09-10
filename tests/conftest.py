"""Shared helpers for the tests/ suite.

Canonical home for the small duplicates that grew a copy in every test
module: tmp-file touching, throwaway git repos (every git spawn carries
``timeout=10`` so a wedged git can never hang the suite), silent GTK
probes, a polling ``wait_for``, and duck-typed Fake* stand-ins.

Test modules SHOULD import from here::

    from conftest import _touch, _init_repo, wait_for

NOTE: ``test_helpers.py`` is NOT this module — it holds real unit tests
for the dotnet helpers. It re-exports a few of these names for backward
compat.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

import pytest


# --- test-session state isolation -------------------------------------------
# thor.logging_config installs an always-on DEBUG file handler into
# $XDG_STATE_HOME/thor/logs/thor.log at the first thor import. Without this
# redirect, every pytest run sprays test-double noise (FakeWindow tracebacks,
# fixture paths, deliberate init-error tests) into the user's real log.
# This runs at conftest import time — before any test module imports thor —
# so the file handler lands in a per-session sandbox instead. Restored and
# removed when the session ends.
_TEST_STATE_HOME = tempfile.mkdtemp(prefix="thor-test-state-")
_SAVED_STATE_HOME = os.environ.get("XDG_STATE_HOME")
os.environ["XDG_STATE_HOME"] = _TEST_STATE_HOME


@pytest.fixture(scope="session", autouse=True)
def _restore_test_state_home():
    yield
    if _SAVED_STATE_HOME is None:
        os.environ.pop("XDG_STATE_HOME", None)
    else:
        os.environ["XDG_STATE_HOME"] = _SAVED_STATE_HOME
    shutil.rmtree(_TEST_STATE_HOME, ignore_errors=True)


def touch(path, content="x"):
    """Create ``path`` (plus parents) with ``content``; return ``path``."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return path


_touch = touch


def git_run(args, cwd, **kw):
    """Run ``git`` with sane test defaults (``timeout=10`` included)."""
    kw.setdefault("check", True)
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    kw.setdefault("timeout", 10)
    return subprocess.run(["git", *args], cwd=cwd, **kw)


def init_repo(path):
    """``git init`` + test identity in ``path``; return ``path``."""
    git_run(["init"], cwd=path)
    git_run(["config", "user.email", "t@t.t"], cwd=path)
    git_run(["config", "user.name", "t"], cwd=path)
    return path


_init_repo = init_repo


def has_git():
    return shutil.which("git") is not None


def wait_for(predicate, timeout_s=10.0, interval=0.05):
    """Poll ``predicate`` until true; bounded by ``timeout_s``."""
    end = time.time() + timeout_s
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def probe_gtk():
    """Return Gtk, or None when unavailable/headless. Never prints."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        return Gtk if ok else None
    except Exception:
        return None


def probe_gtk_gdk():
    """Return (Gtk, Gdk), or None when unavailable/headless. Never prints."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gtk, Gdk

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        return (Gtk, Gdk) if ok else None
    except Exception:
        return None


def probe_gtk_source():
    """Return (Gtk, GtkSource), or None. Never prints."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkSource", "4")
        from gi.repository import Gtk, GtkSource

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        return (Gtk, GtkSource) if ok else None
    except Exception:
        return None


class FakeLocation:
    def __init__(self, path="/tmp/Foo.cs"):
        self._path = path

    def get_path(self):
        return self._path

    def has_uri_scheme(self, scheme):
        return scheme == "file"


class FakeIter:
    def __init__(self, line=0):
        self._line = line

    def get_line(self):
        return self._line


class FakeDoc:
    """Duck-typed Thor document: location + dirty flag + text + signals."""

    def __init__(self, path="/tmp/Foo.cs", modified=False, text=""):
        self._location = FakeLocation(path)
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


class FakeWindow:
    """Duck-typed Thor window: document list + signal connect."""

    def __init__(self, docs=()):
        self._docs = list(docs)
        self.connected = []

    def get_documents(self):
        return list(self._docs)

    def connect(self, signal, _handler):
        self.connected.append(signal)
        return len(self.connected)


class FakeBuffer:
    """Duck-typed text buffer holding plain text."""

    def __init__(self, text=""):
        self._text = text

    def get_text(self):
        return self._text


# Backward-compat aliases for modules that defined their own _
_FakeDoc = FakeDoc
_FakeWindow = FakeWindow
_FakeBuffer = FakeBuffer
