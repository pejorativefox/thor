# -*- coding: utf-8 -*-
"""thor.gitdiff — VS Code-style git gutter marks (added/modified/deleted).

Display-only: diffs the saved file vs HEAD, refreshed on save and tab
switch. Untracked files show every line as added. Headless-safe helpers
are importable without a display; UI wiring degrades gracefully when Gtk
or GtkSource is unavailable.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

try:
    from . import diffparse as _diffparse
except Exception:
    try:
        import diffparse as _diffparse  # type: ignore[no-redef,import-not-found]
    except Exception:
        _diffparse = None  # type: ignore[assignment]

_REFRESH_DEBOUNCE_MS = 300

try:
    from thor.util import is_save_completed, tab_state_name
except Exception:  # headless fallback — keep local copies
    def tab_state_name(state) -> str:  # type: ignore[no-redef]
        """Normalized TabState name for a raw ``get_state()`` value."""
        try:
            name = getattr(state, "value_name", None)
            if isinstance(name, str) and name:
                return name
        except Exception:
            pass
        try:
            nick = getattr(state, "value_nick", None)
            if isinstance(nick, str) and nick:
                return nick
        except Exception:
            pass
        try:
            num = int(state)  # type: ignore[arg-type]
        except Exception:
            num = None
        if num == 0:
            return "THOR_TAB_STATE_NORMAL"
        if num == 3:
            return "THOR_TAB_STATE_SAVING"
        try:
            return str(state)
        except Exception:
            return ""

    def is_save_completed(previous, current) -> bool:  # type: ignore[no-redef]
        """True on a SAVING -> NORMAL tab-state transition (save done)."""
        try:
            prev = tab_state_name(previous).upper()
            cur = tab_state_name(current).upper()
        except Exception:
            return False
        return "SAVING" in prev and "ERROR" not in prev and cur.endswith("NORMAL")


def doc_path(doc) -> str | None:
    """Best-effort file path for a document, or None for untitled/remote."""
    try:
        location = doc.get_location()
    except Exception:
        location = None
    if location is None:
        try:
            location = doc.get_file().get_location()
        except Exception:
            location = None
    if location is None:
        return None
    try:
        if not location.has_uri_scheme("file"):
            return None
        return location.get_path()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GTK / GtkSource imports — headless-safe (None when unavailable)
# ---------------------------------------------------------------------------
try:
    import gi

    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("Gdk", "3.0")
    except Exception:
        pass
    try:
        gi.require_version("GdkPixbuf", "2.0")
    except Exception:
        pass
    try:
        gi.require_version("GtkSource", "4")
    except Exception:
        try:
            gi.require_version("GtkSource", "3.0")
        except Exception:
            pass

    from gi.repository import Gtk, Gdk, Gio, GLib  # type: ignore

    try:
        from gi.repository import GdkPixbuf  # type: ignore
    except Exception:
        GdkPixbuf = None  # type: ignore[assignment]

    try:
        from gi.repository import GtkSource  # type: ignore
    except Exception:
        GtkSource = None  # type: ignore[assignment]

except Exception:
    Gtk = Gdk = Gio = GLib = GtkSource = GdkPixbuf = None  # type: ignore[assignment]

_GTKSOURCE_AVAILABLE = GtkSource is not None
GTKSOURCE_AVAILABLE = _GTKSOURCE_AVAILABLE

try:
    if _diffparse is not None:
        _MARK_COLORS = (
            (_diffparse.CATEGORY_ADDED, _diffparse.COLOR_ADDED),
            (_diffparse.CATEGORY_MODIFIED, _diffparse.COLOR_MODIFIED),
            (_diffparse.CATEGORY_DELETED, _diffparse.COLOR_DELETED),
        )
    else:
        raise AttributeError("_diffparse missing")
except Exception:
    _MARK_COLORS = (
        ("gitinline-added", "#73C991"),
        ("gitinline-modified", "#E2C08D"),
        ("gitinline-deleted", "#C74E39"),
    )

#: Gutter icon size in px. Deliberately small: the pixbuf renders only in
#: the marks gutter, leaving the text area untouched.
_MARK_ICON_SIZE = 12


def rgba_pixel(color: str) -> int | None:
    """Pack '#RRGGBB' into a 0xRRGGBBFF pixel (headless-safe)."""
    try:
        text = color.strip().lstrip("#")
        if len(text) != 6:
            return None
        return (int(text, 16) << 8) | 0xFF
    except Exception:
        return None


def color_pixbuf(color: str, size: int = _MARK_ICON_SIZE):
    """Solid-color square pixbuf for a gutter mark, or None (soft-only).

    Rendered via MarkAttributes.set_pixbuf so only the gutter is painted —
    never the line background.
    """
    if GdkPixbuf is None:
        return None
    pixel = rgba_pixel(color)
    if pixel is None:
        return None
    try:
        from gi.repository import GdkPixbuf as _Pb  # type: ignore

        pixbuf = _Pb.Pixbuf.new(_Pb.Colorspace.RGB, True, 8, size, size)
        pixbuf.fill(pixel)
        return pixbuf
    except Exception as e:
        logger.debug(f"gutter pixbuf failed: {e!r}")
        return None


def deleted_pixbuf(color: str, size: int = _MARK_ICON_SIZE):
    """Downward-triangle pixbuf for deleted-line marks, or None (soft-only).

    Same gutter-only rendering as color_pixbuf; the triangle sets deleted
    marks apart from the solid added/modified squares. Falls back to
    color_pixbuf when cairo or GdkPixbuf is unavailable.
    """
    if GdkPixbuf is None:
        return None
    if rgba_pixel(color) is None:
        return None
    try:
        import cairo  # type: ignore

        from gi.repository import GdkPixbuf as _Pb  # type: ignore

        text = color.strip().lstrip("#")
        red = int(text[0:2], 16) / 255.0
        green = int(text[2:4], 16) / 255.0
        blue = int(text[4:6], 16) / 255.0
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surface)
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)
        ctx.set_source_rgba(red, green, blue, 1.0)
        ctx.move_to(0, 0)
        ctx.line_to(size, 0)
        ctx.line_to(size / 2.0, size)
        ctx.close_path()
        ctx.fill()
        surface.flush()
        stride = surface.get_stride()
        raw = bytes(surface.get_data())
        # cairo ARGB32 is premultiplied, byte order B,G,R,A (LE):
        # unpremultiply + swizzle to GdkPixbuf's straight RGBA.
        out = bytearray(len(raw))
        for i in range(0, len(raw), 4):
            b, g, r, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
            if a == 0:
                continue
            if a == 255:
                out[i], out[i + 1], out[i + 2], out[i + 3] = r, g, b, a
            else:
                out[i] = min(255, (r * 255 + a // 2) // a)
                out[i + 1] = min(255, (g * 255 + a // 2) // a)
                out[i + 2] = min(255, (b * 255 + a // 2) // a)
                out[i + 3] = a
        data = bytes(out)
        pixbuf = _Pb.Pixbuf.new_from_data(
            data, _Pb.Colorspace.RGB, True, 8, size, size, stride
        )
        # new_from_data references (not copies) the bytes: keep alive.
        try:
            pixbuf._owned_data = data  # type: ignore[attr-defined]
        except Exception:
            pass
        return pixbuf
    except Exception as e:
        logger.debug(f"deleted pixbuf failed: {e!r}")
        try:
            return color_pixbuf(color, size)
        except Exception:
            return None


class GitDiffManager:
    """Manages git gutter marks for a ThorWindow.

    Plain Python object that replaces the former plugin system.
    Create via :func:`attach` or directly::

        mgr = GitDiffManager(window)
        mgr.attach()
        ...
        mgr.detach()
    """
    def __init__(self, window=None) -> None:
        self.window = window
        self._signal_ids: list = []
        self._mark_views_configured: set = set()
        self._generations: dict = {}
        self._tab_states: dict = {}
        self._debounce_timer = None
        self._pending_paths: set = set()
        self._doc_handlers: dict = {}
        self._in_flight: set = set()
        self._git_monitors: list = []
        self._root_monitors: dict = {}
        # Last-applied gutter line sets per path: (added, modified,
        # deleted) as tuples. Identical results skip the buffer entirely —
        # every remove/create_source_mark sweep invalidates the gutter.
        self._applied: dict = {}

    # -- lifecycle ---------------------------------------------------

    def attach(self) -> None:
        """Wire window signals and schedule initial refresh."""
        if Gtk is None or _diffparse is None:
            return
        if self.window is None:
            return
        for signal, handler in (
            ("tab-added", self._on_tab_added),
            ("tab-removed", self._on_tab_removed),
            ("active-tab-changed", self._on_active_tab_changed),
            ("active-tab-state-changed", self._on_tab_state_changed),
        ):
            try:
                self._signal_ids.append(
                    (self.window, self.window.connect(signal, handler))
                )
            except Exception as e:
                logger.debug(f"connect {signal} failed: {e!r}")
        self._schedule_active()

    def detach(self) -> None:
        """Remove gutter marks and disconnect all signals."""
        try:
            dp = _diffparse
            categories = (
                (dp.CATEGORY_ADDED, dp.CATEGORY_MODIFIED, dp.CATEGORY_DELETED)
                if dp is not None
                else ()
            )
            for path in list(self._generations):
                try:
                    doc = self._find_doc(path)
                except Exception:
                    doc = None
                if doc is None:
                    continue
                try:
                    start, end = doc.get_bounds()
                except Exception:
                    continue
                for category in categories:
                    try:
                        doc.remove_source_marks(start, end, category)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            for monitor, hid in list(getattr(self, "_git_monitor_ids", [])):
                try:
                    monitor.disconnect(hid)
                except Exception as e:
                    logger.debug("git monitor disconnect failed: %r", e, exc_info=True)
                try:
                    monitor.cancel()
                except Exception as e:
                    logger.debug("git monitor cancel failed: %r", e, exc_info=True)
        except Exception as e:
            logger.debug("git monitor teardown failed: %r", e, exc_info=True)
        try:
            for monitor in list(getattr(self, "_git_monitors", [])):
                try:
                    monitor.cancel()
                except Exception as e:
                    logger.debug("git monitor cancel failed: %r", e, exc_info=True)
        except Exception as e:
            logger.debug("git monitor cancel loop failed: %r", e, exc_info=True)
        try:
            self._git_monitors = []
            self._git_monitor_ids = []
            self._root_monitors = {}
            self._in_flight = set()
        except Exception as e:
            logger.debug("git state reset failed: %r", e, exc_info=True)
        try:
            self._generations.clear()
            self._pending_paths.clear()
            self._applied.clear()
        except Exception:
            pass
        for _key, entry in list(self._doc_handlers.items()):
            try:
                watched_doc, handler_id = entry
                watched_doc.disconnect(handler_id)
            except Exception:
                pass
        self._doc_handlers = {}
        if GLib is not None and self._debounce_timer is not None:
            try:
                GLib.source_remove(self._debounce_timer)
            except Exception:
                pass
        self._debounce_timer = None
        for obj, handler_id in self._signal_ids:
            try:
                obj.disconnect(handler_id)
            except Exception:
                pass
        self._signal_ids = []
        self._mark_views_configured = set()
        self._tab_states = {}

    # -- signals -----------------------------------------------------

    def _on_tab_added(self, _window, tab) -> None:
        try:
            doc = tab.get_document()
        except Exception:
            doc = None
        if doc is not None:
            self._watch_doc(doc)
            try:
                path = doc_path(doc)
            except Exception:
                path = None
            if path:
                self._schedule_paths([path])

    def _on_tab_removed(self, _window, tab) -> None:
        try:
            doc = tab.get_document()
        except Exception:
            doc = None
        if doc is not None:
            self._unwatch_doc(doc)
        try:
            path = doc_path(tab.get_document())
        except Exception:
            path = None
        if path:
            try:
                self._generations.pop(path, None)
            except Exception:
                pass
            try:
                self._applied.pop(path, None)
            except Exception:
                pass
        try:
            views = self.window.get_views()
            live = {id(v) for v in views}
            self._mark_views_configured = set(self._mark_views_configured) & live
        except Exception:
            pass

    def _on_active_tab_changed(self, window, *_args) -> None:
        # Active doc only: scheduling every open doc on each tab switch
        # multiplies git subprocesses and gutter rewrites into a redraw
        # storm (2026-09-11 freeze). Background docs refresh lazily when
        # activated; saves still schedule their own path explicitly.
        try:
            doc = window.get_active_document()
        except Exception:
            doc = None
        if doc is None:
            return
        try:
            self._watch_doc(doc)
        except Exception as e:
            logger.debug(f"active-tab watch failed: {e!r}")
        try:
            path = doc_path(doc)
        except Exception:
            path = None
        if path:
            self._schedule_paths([path])

    def _on_tab_state_changed(self, window, *args) -> None:
        """Detect save completion via SAVING -> NORMAL transitions."""
        tab = None
        for candidate in args:
            if candidate is not None and hasattr(candidate, "get_document"):
                tab = candidate
                break
        if tab is None:
            try:
                tab = window.get_active_tab()
            except Exception:
                return
        if tab is None:
            return
        try:
            state = tab_state_name(tab.get_state())
        except Exception:
            return
        try:
            key = hash(tab)
        except Exception:
            return
        previous = self._tab_states.get(key, "")
        self._tab_states[key] = state
        if is_save_completed(previous, state):
            try:
                path = doc_path(tab.get_document())
            except Exception:
                path = None
            if path:
                self._schedule_paths([path])

    # -- per-doc change tracking (live gutter while typing) ---------------

    def _watch_doc(self, doc) -> None:
        try:
            key = id(doc)
        except Exception:
            return
        if key in self._doc_handlers:
            return
        try:
            handler_id = doc.connect("changed", self._on_doc_changed)
        except Exception as e:
            logger.debug(f"doc watch failed: {e!r}")
            return
        self._doc_handlers[key] = (doc, handler_id)

    def _unwatch_doc(self, doc) -> None:
        try:
            key = id(doc)
        except Exception:
            return
        entry = self._doc_handlers.pop(key, None)
        if entry is None:
            return
        watched_doc, handler_id = entry
        try:
            watched_doc.disconnect(handler_id)
        except Exception:
            pass

    def _on_doc_changed(self, doc) -> None:
        try:
            path = doc_path(doc)
        except Exception:
            path = None
        if path:
            self._schedule_paths([path])

    # -- scheduling --------------------------------------------------

    def _schedule_active(self) -> None:
        try:
            docs = list(self.window.get_documents())
        except Exception:
            return
        paths = []
        for doc in docs:
            self._watch_doc(doc)
            try:
                path = doc_path(doc)
            except Exception:
                path = None
            if path:
                paths.append(path)
        if paths:
            self._schedule_paths(paths)

    def _schedule_paths(self, paths: list) -> None:
        if _diffparse is None:
            return
        for path in paths:
            try:
                self._pending_paths.add(path)
                self._generations[path] = self._generations.get(path, 0) + 1
            except Exception:
                continue
        if GLib is None:
            pending = list(self._pending_paths)
            self._pending_paths = set()
            for path in pending:
                self.refresh_path(path)
            return
        try:
            if self._debounce_timer is not None:
                try:
                    GLib.source_remove(self._debounce_timer)
                except Exception:
                    pass
            self._debounce_timer = GLib.timeout_add(
                _REFRESH_DEBOUNCE_MS, self._on_debounce_fire
            )
        except Exception as e:
            logger.debug(f"debounce failed: {e!r}")

    def _on_debounce_fire(self) -> bool:
        self._debounce_timer = None
        try:
            pending = list(self._pending_paths)
        except Exception:
            pending = []
        self._pending_paths = set()
        for path in pending:
            self.refresh_path(path)
        return False

    def _monitor_root(self, root: str) -> None:
        """Watch .git/HEAD + .git/index for external changes (soft-only)."""
        try:
            if not root:
                return
            if Gio is None:
                return
            if root in self._root_monitors:
                return
            # Prune roots no longer backing any open file so monitors do not
            # accumulate across repo switches; disconnect their signals first.
            try:
                live_dirs = set()
                for p in list(self._generations):
                    try:
                        live_dirs.add(os.path.dirname(p))
                    except Exception:
                        continue
                for old_root in list(self._root_monitors):
                    if old_root == root:
                        continue
                    still_live = False
                    try:
                        for d in live_dirs:
                            try:
                                if os.path.commonpath([d, old_root]) == old_root or d.startswith(old_root + os.sep):
                                    still_live = True
                                    break
                            except Exception:
                                continue
                    except Exception:
                        still_live = True
                    if not still_live:
                        self._drop_root_monitor(old_root)
            except Exception as e:
                logger.debug("git monitor prune failed: %r", e, exc_info=True)
            monitors = []
            handler_ids: list = []
            for name in ("HEAD", "index"):
                try:
                    watched = Gio.File.new_for_path(os.path.join(root, ".git", name))
                    monitor = watched.monitor_file(Gio.FileMonitorFlags.NONE, None)
                    hid = monitor.connect("changed", self._on_git_dir_changed)
                    handler_ids.append((monitor, hid))
                    monitors.append(monitor)
                except Exception as e:
                    logger.debug(f"git monitor {name} failed: {e!r}")
            if monitors:
                self._root_monitors[root] = monitors
                self._git_monitors.extend(monitors)
                try:
                    self._git_monitor_ids = getattr(self, "_git_monitor_ids", [])
                    self._git_monitor_ids.extend(handler_ids)
                except Exception as e:
                    logger.debug("monitor id stash failed: %r", e, exc_info=True)
        except Exception as e:
            logger.debug(f"git monitor setup failed: {e!r}")

    def _drop_root_monitor(self, root: str) -> None:
        """Disconnect + cancel monitors for *root* (no accumulation)."""
        mons = self._root_monitors.pop(root, [])
        for mon in mons:
            try:
                mon.cancel()
            except Exception as e:
                logger.debug("git monitor cancel failed: %r", e, exc_info=True)
            try:
                self._git_monitors.remove(mon)
            except ValueError:
                pass
            except Exception as e:
                logger.debug("git monitor list drop failed: %r", e, exc_info=True)

    def _on_git_dir_changed(self, *args) -> None:
        try:
            paths = list(self._generations)
        except Exception:
            return
        if not paths:
            return
        try:
            self._schedule_paths(paths)
        except Exception as e:
            logger.debug(f"git change refresh failed: {e!r}")

    def refresh_path(self, path: str) -> None:
        """Re-query git for one file off the UI thread, then repaint."""
        if _diffparse is None or not path:
            return
        try:
            in_flight = self._in_flight
        except AttributeError:
            in_flight = None
        if in_flight is not None and path in in_flight:
            return
        if in_flight is not None:
            in_flight.add(path)
        generation = self._generations.get(path, 0)
        snapshot = self._snapshot_doc(path)
        try:
            thread = threading.Thread(
                target=self._query_thread,
                args=(path, generation, snapshot),
                daemon=True,
            )
            thread.start()
        except Exception as e:
            logger.debug(f"refresh spawn failed: {e!r}")
            try:
                if in_flight is not None:
                    in_flight.discard(path)
            except Exception:
                pass

    def _snapshot_doc(self, path: str):
        """UI-thread snapshot: buffer text when dirty, else just a line count.

        Gtk buffers must only be touched on the UI thread; the worker thread
        below consumes this snapshot so marks align with unsaved edits.
        """
        doc = self._find_doc(path)
        if doc is None:
            return None
        try:
            modified = bool(doc.get_modified())
        except Exception:
            modified = False
        try:
            line_count = int(doc.get_line_count())
        except Exception:
            line_count = 0
        if line_count <= 0:
            try:
                start, end = doc.get_bounds()
                line_count = end.get_line() + 1
            except Exception:
                line_count = 0
        text = None
        if modified:
            try:
                start, end = doc.get_bounds()
                text = doc.get_text(start, end, False)
            except Exception:
                text = None
        return {"modified": modified, "line_count": line_count, "text": text}

    def _query_thread(self, path: str, generation: int, snapshot) -> None:
        dp = _diffparse
        if isinstance(snapshot, dict) and snapshot.get("line_count"):
            line_count = snapshot["line_count"]
        else:
            line_count = self._doc_line_count(path)
        result: dict = {"added": [], "modified": [], "deleted": []}
        try:
            folder = os.path.dirname(path)
            try:
                root = dp._cached_git_root(folder)  # type: ignore[attr-defined]
            except AttributeError:
                root = dp.find_git_root(folder)
            current = self._generations.get(path, generation)
            if generation != current:
                return
            if root is not None and GLib is not None:
                try:
                    GLib.idle_add(self._monitor_root, root)
                except Exception:
                    pass
            if root is not None:
                status = dp.file_status_short(root, path)
                current = self._generations.get(path, generation)
                if generation != current:
                    return
                if status == "??":
                    result = dp.untracked_marks(line_count)
                elif status:
                    relpath = os.path.relpath(path, root)
                    matches = dp.buffer_matches_head(root, relpath, snapshot["text"]) if (isinstance(snapshot, dict) and snapshot.get("modified") and snapshot.get("text") is not None) else True
                    current = self._generations.get(path, generation)
                    if generation != current:
                        return
                    use_buffer = (
                        isinstance(snapshot, dict)
                        and snapshot.get("modified")
                        and snapshot.get("text") is not None
                        and not matches
                    )
                    if use_buffer:
                        text = dp.get_buffer_diff_text(root, relpath, snapshot["text"])
                    else:
                        text = dp.get_diff_text(root, path)
                    hunks = dp.parse_unified_diff(text)
                    result = {
                        "added": dp.clamp_lines(hunks["added"], line_count),
                        "modified": dp.clamp_lines(hunks["modified"], line_count),
                        "deleted": dp.clamp_lines(hunks["deleted"], line_count),
                    }
        except Exception as e:
            logger.debug(f"git diff failed for {path}: {e!r}")
            result = {"added": [], "modified": [], "deleted": []}
        finally:
            try:
                self._in_flight.discard(path)
            except Exception:
                pass
        try:
            if GLib is not None:
                GLib.idle_add(self._apply_result, path, result, generation)
            else:
                self._apply_result(path, result, generation)
        except Exception as e:
            logger.debug(f"apply schedule failed: {e!r}")

    # -- rendering ---------------------------------------------------

    def _find_doc(self, path: str):
        try:
            for doc in self.window.get_documents():
                try:
                    if doc_path(doc) == path:
                        return doc
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _doc_line_count(self, path: str) -> int:
        doc = self._find_doc(path)
        if doc is None:
            return 0
        try:
            return doc.get_line_count()
        except Exception:
            pass
        try:
            start, end = doc.get_bounds()
            return end.get_line() + 1
        except Exception:
            return 0

    def _apply_result(self, path: str, result: dict, generation: int) -> bool:
        if generation != self._generations.get(path):
            return False
        doc = self._find_doc(path)
        if doc is None:
            return False
        try:
            fingerprint = (
                tuple(result.get("added", [])),
                tuple(result.get("modified", [])),
                tuple(result.get("deleted", [])),
            )
        except Exception as e:
            logger.debug(f"gutter fingerprint failed for {path}: {e!r}")
            fingerprint = None
        if fingerprint is not None:
            try:
                if self._applied.get(path) == fingerprint:
                    logger.debug(f"gutter marks unchanged for {path}, skipping")
                    return False
            except Exception as e:
                logger.debug(f"gutter fingerprint compare failed: {e!r}")
        categories = (
            (_diffparse.CATEGORY_ADDED, result.get("added", [])),
            (_diffparse.CATEGORY_MODIFIED, result.get("modified", [])),
            (_diffparse.CATEGORY_DELETED, result.get("deleted", [])),
        )
        try:
            start, end = doc.get_bounds()
        except Exception:
            return False
        for category, _lines in categories:
            try:
                # NOTE: argument order is (start, end, category) — the
                # category-first order raises TypeError (silently ignored
                # below), which used to make stale marks stick forever.
                doc.remove_source_marks(start, end, category)
            except Exception as e:
                logger.debug(f"remove marks {category} failed: {e!r}")
        try:
            line_count = doc.get_line_count()
        except Exception:
            line_count = 0
        for category, lines in categories:
            for line in lines:
                try:
                    if line_count and not 0 <= line < line_count:
                        continue
                    it = doc.get_iter_at_line(line)
                    doc.create_source_mark(None, category, it)
                except Exception:
                    continue
        if fingerprint is not None:
            try:
                self._applied[path] = fingerprint
            except Exception as e:
                logger.debug(f"gutter fingerprint store failed: {e!r}")
        self._configure_marks(doc)
        return False

    def _configure_marks(self, doc) -> None:
        """Enable gutter marks on views showing this doc (once per view)."""
        if not _GTKSOURCE_AVAILABLE:
            return
        try:
            views = self.window.get_views()
        except Exception:
            return
        for view in views:
            try:
                if view.get_buffer() is not doc:
                    continue
                if id(view) in self._mark_views_configured:
                    continue
                view.set_show_line_marks(True)
                try:
                    deleted_category = _diffparse.CATEGORY_DELETED
                except Exception:
                    deleted_category = "gitinline-deleted"
                for category, color in _MARK_COLORS:
                    try:
                        attrs = GtkSource.MarkAttributes()
                        # Gutter icon only: painting the line background is
                        # deliberately never used here (it washes out text).
                        if category == deleted_category:
                            pixbuf = deleted_pixbuf(color)
                        else:
                            pixbuf = color_pixbuf(color)
                        if pixbuf is not None:
                            attrs.set_pixbuf(pixbuf)
                        view.set_mark_attributes(category, attrs, 10)
                    except Exception as e:
                        logger.debug(f"mark attributes {category} failed: {e!r}")
                self._mark_views_configured.add(id(view))
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Public attach/detach — used by thor.host
# ---------------------------------------------------------------------------

def attach(window) -> GitDiffManager:
    """Attach git gutter marks to *window* and return the manager.

    The manager is retained on the window (`_thor_gitdiff_manager`) so it
    is not garbage-collected while the window lives.
    """
    mgr = GitDiffManager(window)
    try:
        window._thor_gitdiff_manager = mgr  # type: ignore[attr-defined]
    except Exception:
        pass
    mgr.attach()
    return mgr


def detach(window) -> None:
    """Detach git gutter marks from *window* if previously attached."""
    try:
        mgr = getattr(window, "_thor_gitdiff_manager", None)
        if mgr is not None:
            mgr.detach()
            try:
                delattr(window, "_thor_gitdiff_manager")
            except Exception:
                try:
                    window._thor_gitdiff_manager = None  # type: ignore[attr-defined]
                except Exception:
                    pass
            return
    except Exception:
        pass
    # Fallback: try to detach a manager passed directly
    try:
        if isinstance(window, GitDiffManager):
            window.detach()
    except Exception:
        pass


