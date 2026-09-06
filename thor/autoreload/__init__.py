# -*- coding: utf-8 -*-
"""autoreload: silently reload externally modified files without local edits.

Two triggers feed the same safe-reload path:

- externally-modified tab state (fires on focus/tab switch), and
- a Gio file monitor per open file (fires immediately, even unfocused).

A reload only happens when the buffer has no unsaved changes *and* the file
content actually differs from the buffer, so own saves and mtime-only touches
are harmless no-ops. Buffers with unsaved changes are left alone.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_RELOAD_DEBOUNCE_MS = 400
_COMPARE_CAP_BYTES = 1024 * 1024

try:
    import gi  # type: ignore

    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("GtkSource", "4")
    except Exception as e:
        logger.debug("GtkSource 4 unavailable: %r", e, exc_info=True)
        try:
            gi.require_version("GtkSource", "3.0")
        except Exception as e2:
            logger.debug("GtkSource 3 unavailable: %r", e2, exc_info=True)
    from gi.repository import Gio, GLib  # type: ignore

    try:
        from gi.repository import GtkSource  # type: ignore
    except Exception as e:
        logger.debug("GtkSource import failed: %r", e, exc_info=True)
        GtkSource = None  # type: ignore[assignment]
except Exception as e:
    logger.debug("GTK unavailable, autoreload headless: %r", e, exc_info=True)
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    GtkSource = None  # type: ignore[assignment]


#: Tab state meaning "file changed on disk since load". Numeric because the
#: host tab-state enum is not importable headless; tests pin value 13.
_EXTERNALLY_MODIFIED_STATE = 13
EXTERNALLY_MODIFIED_STATE = _EXTERNALLY_MODIFIED_STATE


def doc_location(doc):
    """Best-effort Gio location for a document, or None for untitled docs."""
    try:
        location = doc.get_location()
    except Exception:
        location = None
    if location is None:
        try:
            location = doc.get_file().get_location()
        except Exception:
            location = None
    return location


def should_reload(doc) -> bool:
    """True when a doc may be reloaded without losing user edits."""
    if doc_location(doc) is None:
        return False
    try:
        if doc.get_modified():
            return False
    except Exception:
        return False
    return True


def cursor_line(doc) -> int:
    """0-based cursor line, best effort."""
    try:
        return int(doc.get_iter_at_mark(doc.get_insert()).get_line())
    except Exception:
        return 0


def _loader_file(doc, location):
    """Thor's own GtkSource.File for a doc, so its mtime tracking updates."""
    try:
        gfile = doc.get_file()
    except Exception:
        gfile = None
    if gfile is not None:
        return gfile
    if GtkSource is None:
        return None
    gfile = GtkSource.File.new()
    gfile.set_location(location)
    return gfile


def doc_file_path(doc) -> str | None:
    """Local filesystem path for a document, or None for untitled/remote."""
    location = doc_location(doc)
    if location is None:
        return None
    try:
        if hasattr(location, "has_uri_scheme") and not location.has_uri_scheme("file"):
            return None
        path = location.get_path()
    except Exception:
        return None
    return path or None


def buffer_bytes(doc) -> bytes | None:
    """Buffer content as UTF-8 bytes, or None (soft-only)."""
    try:
        text = doc.get_text(doc.get_start_iter(), doc.get_end_iter(), False)
    except Exception:
        return None
    try:
        return text.encode("utf-8")
    except Exception:
        return None


def read_file_bytes(path: str, cap: int = _COMPARE_CAP_BYTES) -> bytes | None:
    """Raw file bytes up to cap, or None when missing/unreadable/too big."""
    try:
        size = os.path.getsize(path)
    except Exception as e:
        logger.debug("stat failed for %s: %r", path, e, exc_info=True)
        return None
    if size > cap:
        logger.debug("skip compare for %s: %d bytes > %d cap", path, size, cap)
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.debug("read failed for %s: %r", path, e, exc_info=True)
        return None


def file_differs(doc, path: str) -> bool | None:
    """True when file content differs from the buffer.

    None means "cannot tell" (unreadable file or buffer); callers treat that
    as "do not reload", except oversized files which compare as differing so
    large clean buffers still reload.

    Tolerates one trailing-newline difference: GtkSource keeps the final
    newline implicit, so a freshly loaded buffer always compares one ``\\n``
    short of the on-disk bytes.
    """
    try:
        size = os.path.getsize(path)
    except Exception as e:
        logger.debug("stat failed for %s: %r", path, e, exc_info=True)
        return None
    if size > _COMPARE_CAP_BYTES:
        # Keep the reload (a clean large buffer should still pick up an
        # external change) but say so instead of silently forcing True.
        logger.debug(
            "large file %s (%d bytes > %d cap): treating as differs without compare",
            path, size, _COMPARE_CAP_BYTES,
        )
        return True
    data = read_file_bytes(path)
    if data is None:
        return None
    content = buffer_bytes(doc)
    if content is None:
        return None
    if data == content or data == content + b"\n":
        return False
    return True


def _watched_events() -> set:
    """Gio monitor event codes that should trigger a reload check."""
    try:
        monitor_event = Gio.FileMonitorEvent
        names = ("CHANGED", "CHANGES_DONE_HINT", "CREATED",
                 "ATTRIBUTE_CHANGED", "RENAMED", "MOVED_IN", "MOVED_OUT")
        return {int(getattr(monitor_event, name)) for name in names}
    except Exception:
        try:
            return {int(Gio.FileMonitorEvent.CHANGES_DONE_HINT)}
        except Exception:
            return {1}


class AutoReloadManager:
    """Plain manager that wires autoreload to a ThorWindow.

    No GObject inheritance — instantiated via :func:`attach`.
    """

    def __init__(self, window) -> None:
        self._window = window
        self._signal_ids: list = []
        self._monitors: dict = {}
        self._pending: dict = {}
        self._loading: set = set()
        self._attach(window)

    def _attach(self, window) -> None:
        for signal, handler in (
            ("tab-added", self._on_tab_added),
            ("tab-removed", self._on_tab_removed),
            ("active-tab-changed", self._on_active_tab_changed),
            ("active-tab-state-changed", self._on_tab_state_changed),
        ):
            try:
                self._signal_ids.append((window, window.connect(signal, handler)))
            except Exception as e:
                logger.debug(f"connect {signal} failed: {e!r}")

    def _detach(self) -> None:
        for obj, handler_id in self._signal_ids:
            try:
                obj.disconnect(handler_id)
            except Exception as e:
                logger.debug("autoreload disconnect failed: %r", e, exc_info=True)
        self._signal_ids = []

    def _on_tab_added(self, window, _tab) -> None:
        self._sync_watches(window)

    def _on_tab_removed(self, window, _tab) -> None:
        self._sync_watches(window)

    def _on_active_tab_changed(self, window, *_args) -> None:
        self._sync_watches(window)
        self._sweep(window)

    def _on_tab_state_changed(self, window, *args) -> None:
        tab = None
        for candidate in args:
            if candidate is not None and hasattr(candidate, "get_document"):
                tab = candidate
                break
        if tab is None:
            return
        self._maybe_reload(tab, window)
        self._sync_watches(window)

    def _sweep(self, window) -> None:
        try:
            docs = list(window.get_documents())
        except Exception:
            return
        for doc in docs:
            try:
                location = doc_location(doc)
                tab = window.get_tab_from_location(location) if location is not None else None
            except Exception:
                tab = None
            if tab is None:
                continue
            self._maybe_reload(tab, window)

    def _maybe_reload(self, tab, window) -> bool:
        try:
            state = int(tab.get_state())
        except Exception:
            return False
        if state != _EXTERNALLY_MODIFIED_STATE:
            return False
        try:
            doc = tab.get_document()
        except Exception:
            return False
        return self._check_and_reload(window, doc, reason="externally modified")

    def _reload_doc(self, window, doc, location, reason: str) -> bool:
        if GtkSource is None or GLib is None:
            return False
        try:
            path = location.get_path()
        except Exception:
            path = None
        if path is not None and path in self._loading:
            return False
        try:
            gfile = _loader_file(doc, location)
            if gfile is None:
                return False
            loader = GtkSource.FileLoader.new(doc, gfile)
        except Exception as e:
            logger.debug(f"reload setup failed: {e!r}")
            return False
        line = cursor_line(doc)
        if path is not None:
            self._loading.add(path)
        holder = {"loader": loader}

        def _done(src, result, _user_data=None):
            holder.pop("loader", None)
            if path is not None:
                self._loading.discard(path)
            # TOCTOU re-check: the user may have typed while the async load
            # was in flight. Confirm the buffer is still clean before
            # finalising the load, otherwise keep the user's fresh edits.
            try:
                if not should_reload(doc):
                    logger.debug("reload done: buffer dirty now, keeping user edits")
                    return
            except Exception as e:
                logger.debug("reload done: should_reload re-check failed: %r", e, exc_info=True)
                return
            try:
                ok = loader.load_finish(result)
            except Exception as e:
                logger.debug(f"reload load failed: {e!r}")
                return
            if not ok:
                return
            try:
                last = max(0, int(doc.get_line_count()) - 1)
                doc.place_cursor(doc.get_iter_at_line(min(max(0, line), last)))
            except Exception as e:
                logger.debug("reload cursor restore failed: %r", e, exc_info=True)
            self._clear_modified_state(window, doc)
            logger.debug(f"reloaded {path if path is not None else location} (clean, {reason})")
        try:
            loader.load_async(GLib.PRIORITY_DEFAULT, None, None, None, _done, None)
        except Exception as e:
            holder.pop("loader", None)
            if path is not None:
                self._loading.discard(path)
            logger.debug(f"reload start failed: {e!r}")
            return False
        return True

    def _clear_modified_state(self, window, doc) -> None:
        """Best-effort dismissal of stale externally-modified flag."""
        # Thor uses 0 for normal state; thor's STATE_NORMAL maps similarly.
        try:
            normal = 0
        except Exception:
            return
        try:
            location = doc_location(doc)
            tab = window.get_tab_from_location(location) if location is not None else None
        except Exception:
            tab = None
        if tab is None:
            return
        try:
            tab.set_state(normal)
        except Exception as e:
            logger.debug(f"state reset failed: {e!r}")

    def _find_doc(self, window, path: str):
        try:
            docs = list(window.get_documents())
        except Exception:
            return None
        for doc in docs:
            try:
                if doc_file_path(doc) == path:
                    return doc
            except Exception:
                continue
        return None

    def _check_and_reload(self, window, doc, reason: str) -> bool:
        if not should_reload(doc):
            return False
        location = doc_location(doc)
        if location is None:
            return False
        try:
            path = doc_file_path(doc)
        except Exception:
            path = None
        if path is not None:
            try:
                differs = file_differs(doc, path)
            except Exception:
                differs = None
            if differs is False:
                return False
            if differs is None:
                logger.debug(f"skip reload of {path} (cannot compare)")
                return False
        return self._reload_doc(window, doc, location, reason)

    # -- file monitors (immediate reload, no focus needed) ----------------

    def _sync_watches(self, window) -> None:
        if Gio is None:
            return
        try:
            docs = list(window.get_documents())
        except Exception:
            return
        wanted: dict = {}
        for doc in docs:
            try:
                path = doc_file_path(doc)
            except Exception:
                path = None
            if path:
                wanted[path] = doc
        for path in list(self._monitors):
            if path not in wanted:
                self._unwatch(path)
        for path in wanted:
            if path not in self._monitors:
                self._watch(window, path)

    def _watch(self, window, path: str) -> None:
        if Gio is None:
            return
        try:
            flags = getattr(Gio.FileMonitorFlags, "WATCH_MTIME", 0)
            monitor = Gio.File.new_for_path(path).monitor_file(flags, None)
        except Exception as e:
            logger.debug(f"watch failed for {path}: {e!r}")
            return
        try:
            handler_id = monitor.connect("changed", self._on_file_event, window, path)
        except Exception as e:
            logger.debug(f"watch connect failed for {path}: {e!r}")
            try:
                monitor.cancel()
            except Exception:
                pass
            return
        self._monitors[path] = (monitor, handler_id)

    def _unwatch(self, path: str) -> None:
        entry = self._monitors.pop(path, None)
        if entry is not None:
            monitor, handler_id = entry
            try:
                monitor.disconnect(handler_id)
            except Exception:
                pass
            try:
                monitor.cancel()
            except Exception:
                pass
        timer_id = self._pending.pop(path, None)
        if timer_id is not None and GLib is not None:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass

    def _unwatch_all(self) -> None:
        for path in list(self._monitors):
            self._unwatch(path)
        self._monitors = {}
        self._pending = {}

    def _on_file_event(self, _monitor, _gfile, _other, event, window, path) -> None:
        try:
            code = int(event)
        except Exception:
            return
        if code not in _watched_events():
            return
        old_id = self._pending.pop(path, None)
        if old_id is not None and GLib is not None:
            try:
                GLib.source_remove(old_id)
            except Exception:
                pass
        if GLib is None:
            self._fire(window, path)
            return
        try:
            self._pending[path] = GLib.timeout_add(
                _RELOAD_DEBOUNCE_MS, self._fire, window, path
            )
        except Exception as e:
            logger.debug(f"debounce failed for {path}: {e!r}")

    def _fire(self, window, path) -> bool:
        self._pending.pop(path, None)
        try:
            doc = self._find_doc(window, path)
        except Exception:
            doc = None
        if doc is None:
            self._unwatch(path)
            return False
        self._check_and_reload(window, doc, reason="file changed on disk")
        return False


# -- module-level attach/detach for ThorWindow --------------------------

_managers: dict[int, AutoReloadManager] = {}


def attach(window) -> bool:
    """Wire autoreload to *window*. Soft-fails when GTK/GLib unavailable."""
    if window is None:
        return False
    key = id(window)
    if key in _managers:
        return True
    # soft-fail if window lacks required API (headless tests may pass dummy)
    if not hasattr(window, "connect") or not hasattr(window, "get_documents"):
        logger.debug("attach soft-fail: window missing connect/get_documents")
        return False
    try:
        mgr = AutoReloadManager(window)
    except Exception as e:
        logger.debug(f"attach failed: {e!r}")
        return False
    _managers[key] = mgr
    try:
        window._thor_autoreload = mgr  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        mgr._sync_watches(window)
        mgr._sweep(window)
    except Exception as e:
        logger.debug(f"initial sync failed: {e!r}")
    return True


def detach(window) -> bool:
    """Unwire autoreload from *window*."""
    if window is None:
        return False
    key = id(window)
    mgr = _managers.pop(key, None)
    if mgr is None:
        # also try attribute
        try:
            mgr = getattr(window, "_thor_autoreload", None)
        except Exception:
            mgr = None
        if mgr is None:
            return False
    try:
        mgr._detach()
    except Exception:
        pass
    try:
        mgr._unwatch_all()
    except Exception:
        pass
    try:
        if hasattr(window, "_thor_autoreload"):
            delattr(window, "_thor_autoreload")
    except Exception:
        pass
    return True


# Compatibility alias for plugin tests (thor standalone)
AutoReloadPlugin = AutoReloadManager
