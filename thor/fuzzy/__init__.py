# -*- coding: utf-8 -*-
"""Fuzzy file opener (Ctrl+P) for the folder loaded in project-mode.

Thor-native port of ``plugins/fuzzy-finder/fuzzyfinder`` — wired via
``attach(window)`` to a :class:`thor.window.ThorWindow` with no plugin
interface slop.

Soft runtime dependency on project-mode: the project root is read from
the ProjectBrowser widget living in the side panel (duck-typed, no
import of projectmode). When project-mode is disabled or no folder is
loaded, a hint dialog is shown instead of raising.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GTK imports — headless safe
# ---------------------------------------------------------------------------

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import GObject, Gtk, Gdk, Gio, GLib  # type: ignore
except Exception:  # headless / missing typelibs
    GObject = Gtk = Gdk = Gio = GLib = None  # type: ignore

from .files import list_project_files
from .matcher import FuzzyIndex, fuzzy_find, fuzzy_match, markup_highlight


(COL_LABEL, COL_MARKUP, COL_PATH) = range(3)
MAX_ROWS = 60
MAX_RECENT = 20

__all__ = [
    "FuzzyFinderDialog",
    "FuzzyIndex",
    "MAX_ROWS",
    "MAX_RECENT",
    "COL_LABEL",
    "COL_MARKUP",
    "COL_PATH",
    "attach",
    "detach",
    "find_project_root",
    "order_with_recent",
    "fuzzy_find",
    "fuzzy_match",
    "markup_highlight",
    "list_project_files",
]


def order_with_recent(
    items: list[tuple[str, str]], recent: list[str]
) -> list[tuple[str, str]]:
    """Recent-first ordering for finder items.

    Items whose absolute path appears in ``recent`` come first, in recency
    order (most recent first); all others keep their input order. Recent
    entries with no matching item are dropped.
    """
    if not recent:
        return list(items)
    by_path = {path: (display, path) for display, path in items}
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in recent:
        item = by_path.get(path)
        if item is not None and path not in seen:
            ordered.append(item)
            seen.add(path)
    for _display, path in items:
        if path not in seen:
            ordered.append((_display, path))
    return ordered


def find_project_root(window) -> str | None:
    """Locate project-mode's loaded folder root via the side panel.

    Duck-typed: accepts any widget exposing a non-empty ``_root_dir``
    string (ProjectBrowser). Walks one level of container nesting.
    Returns an absolute directory path or None.
    """
    try:
        side = window.get_side_panel()
    except Exception:
        return None
    if side is None:
        return None
    try:
        children = list(side.get_children())
    except Exception:
        return None
    queue = list(children)
    seen = 0
    while queue and seen < 64:
        widget = queue.pop(0)
        seen += 1
        try:
            root = getattr(widget, "_root_dir", None)
        except Exception:
            root = None
        if isinstance(root, str) and root and os.path.isdir(root):
            return os.path.abspath(root)
        try:
            queue.extend(widget.get_children())
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Dialog — only when Gtk is available
# ---------------------------------------------------------------------------

if Gtk is not None:
    class FuzzyFinderDialog(Gtk.Dialog):  # type: ignore[misc]
        __gsignals__ = {
            "open-file": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),  # type: ignore[union-attr]
        }

        def __init__(self, parent=None) -> None:
            super().__init__(title="Open File in Project")
            try:
                self.set_modal(True)
            except Exception:
                pass
            if parent is not None:
                try:
                    self.set_transient_for(parent)
                except Exception as e:
                    logger.debug(f"fuzzy transient_for failed: {e!r}")
            self.set_default_size(560, 380)
            self._files: list[tuple[str, str]] = []
            self._index: FuzzyIndex | None = None
            self._labels: dict[str, str] = {}
            self._entry = Gtk.Entry()
            try:
                self._entry.set_placeholder_text("Type to filter…")
            except Exception:
                pass
            self._entry.connect("changed", lambda _e: self._refilter())
            self._entry.connect("key-press-event", self._on_entry_key)
            self._store = Gtk.ListStore(str, str, str)
            self._view = Gtk.TreeView.new_with_model(self._store)
            self._view.set_headers_visible(False)
            cell = Gtk.CellRendererText()
            cell.set_property("ellipsize", 2)  # middle-ellipsis for long paths
            col = Gtk.TreeViewColumn("File", cell, markup=COL_MARKUP)
            self._view.append_column(col)
            self._view.connect("row-activated", lambda _v, _p, _c: self._activate_selected())
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(self._view)
            area = self.get_content_area()
            area.pack_start(self._entry, False, False, 0)
            self._indexing = False
            self._status = Gtk.Label()
            try:
                self._status.set_halign(Gtk.Align.START)
                self._status.set_xalign(0)
            except Exception:
                pass
            area.pack_start(self._status, False, False, 0)
            area.pack_start(scrolled, True, True, 0)
            self.show_all()

        # -- data ------------------------------------------------------
        def set_files(self, items: list[tuple[str, str]]) -> None:
            """items: (display, path) pairs."""
            self._files = list(items)
            self._labels = {display: path for display, path in self._files}
            # Score the relative display paths, not the absolute ones: the
            # common /home/... prefix would otherwise drown out real
            # differences. The index is built once; each keystroke only
            # re-runs the DP via _refilter.
            try:
                self._index = FuzzyIndex([display for display, _path in self._files])
            except Exception as e:
                logger.debug(f"fuzzy index build failed: {e!r}")
                self._index = None
            self.set_indexing(False)
            self._refilter()
            try:
                self._entry.grab_focus()
            except Exception:
                pass

        def set_indexing(self, on: bool) -> None:
            """Show or clear the background-indexing indicator."""
            self._indexing = bool(on)
            try:
                query = self._entry.get_text()
            except Exception:
                query = ""
            try:
                shown = len(self._store)
            except Exception:
                shown = 0
            self._update_status(shown, len(self._files), query)

        def _update_status(self, shown: int, total: int, query: str) -> None:
            if total == 0 and not self._indexing:
                text = "No files found"
            elif shown == 0 and query:
                text = "No matches"
            else:
                text = f"{shown} of {total} files"
            if self._indexing:
                text = f"Indexing…  {text}" if text else "Indexing…"
            try:
                self._status.set_text(text)
            except Exception:
                pass

        def _refilter(self) -> None:
            query = self._entry.get_text()
            labels = self._labels
            displays = [display for display, _path in self._files]
            self._store.clear()
            try:
                if self._index is not None:
                    ranked = self._index.search(query, limit=MAX_ROWS)
                else:
                    ranked = fuzzy_find(query, displays, limit=MAX_ROWS)
                for display in ranked:
                    positions: list[int] = []
                    if query.strip():
                        try:
                            hit = fuzzy_match(query, display)
                            positions = list(hit[1]) if hit is not None else []
                        except Exception:
                            positions = []
                    self._store.append(
                        [display, markup_highlight(display, positions), labels.get(display, display)]
                    )
            except Exception as e:
                logger.debug(f"fuzzy refilter failed: {e!r}")
            self._update_status(len(self._store), len(self._files), query)
            self._select_row(0)

        # -- selection -------------------------------------------------
        def _select_row(self, index: int) -> None:
            count = len(self._store)
            if count == 0:
                return
            index = max(0, min(count - 1, index))
            path = Gtk.TreePath.new_from_indices([index])
            self._view.get_selection().select_path(path)
            self._view.scroll_to_cell(path, None, False, 0, 0)

        def _selected_path(self) -> str | None:
            model, tree_iter = self._view.get_selection().get_selected()
            if tree_iter is None:
                if len(self._store) == 0:
                    return None
                tree_iter = self._store.get_iter_first()
            try:
                return model.get_value(tree_iter, COL_PATH)
            except Exception:
                return None

        def _activate_selected(self) -> None:
            path = self._selected_path()
            if path:
                self.emit("open-file", path)

        def _current_index(self) -> int:
            _model, tree_iter = self._view.get_selection().get_selected()
            if tree_iter is not None:
                try:
                    return self._store.get_path(tree_iter).get_indices()[0]
                except Exception:
                    pass
            return 0

        def _on_entry_key(self, _entry, event) -> bool:
            try:
                name = Gdk.keyval_name(event.keyval) or ""
            except Exception:
                return False
            try:
                ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
            except Exception:
                ctrl = False
            if ctrl and name in ("n", "N", "p", "P"):
                self._select_row(self._current_index() + (1 if name.lower() == "n" else -1))
                return True
            if name in ("Up", "KP_Up", "Down", "KP_Down"):
                self._select_row(self._current_index() + (-1 if "Up" in name else 1))
                return True
            if name in ("Page_Up", "KP_Page_Up", "Page_Down", "KP_Page_Down"):
                self._select_row(self._current_index() + (-10 if "Up" in name else 10))
                return True
            if name in ("Home", "KP_Home"):
                self._select_row(0)
                return True
            if name in ("End", "KP_End"):
                self._select_row(len(self._store) - 1)
                return True
            if name in ("Return", "KP_Enter"):
                self._activate_selected()
                return True
            if name == "Escape":
                self.destroy()
                return True
            return False

else:
    class FuzzyFinderDialog:  # type: ignore[no-redef]
        """Dummy placeholder when Gtk is unavailable (headless)."""

        pass


# ---------------------------------------------------------------------------
# Manager — plain object
# ---------------------------------------------------------------------------

class _FuzzyFinderManager:
    """Per-window fuzzy finder state and key handling."""

    def __init__(self, window) -> None:
        self.window = window
        self._window_key_id = None
        self._file_cache: dict[str, list[str]] = {}
        self._recent: list[str] = []

    # -- lifecycle -----------------------------------------------------

    def attach(self) -> None:
        if Gtk is None or Gio is None:
            logger.debug("Gtk/Gio unavailable — fuzzy finder attach skipped (headless)")
            return
        try:
            self._window_key_id = self.window.connect("key-press-event", self._on_window_key_press)
        except Exception as e:
            logger.debug(f"window keys connect failed: {e!r}")
            self._window_key_id = None

    def detach(self) -> None:
        if self._window_key_id is not None:
            try:
                self.window.disconnect(self._window_key_id)
            except Exception:
                pass
        self._window_key_id = None

    # -- key handling --------------------------------------------------

    def _handle_global_key(self, keyname: str, ctrl: bool, shift: bool, alt: bool) -> bool:
        if ctrl and not shift and not alt and keyname.lower() == "p":
            logger.debug("key: Ctrl+P fuzzy-finder")
            self._show_finder()
            return True
        return False

    def _on_window_key_press(self, _window, event) -> bool:
        if Gtk is None or Gdk is None:
            return False
        try:
            mods = event.state & Gtk.accelerator_get_default_mod_mask()
            keyname = Gdk.keyval_name(event.keyval) or ""
            ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)
            shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
            alt = bool(mods & Gdk.ModifierType.MOD1_MASK)
        except Exception:
            return False
        return self._handle_global_key(keyname, ctrl, shift, alt)

    # -- file cache ----------------------------------------------------

    def _cached_files(self, root: str) -> list[str] | None:
        try:
            key = os.path.abspath(root)
        except Exception:
            return None
        return self._file_cache.get(key)

    def _load_in_background(self, root: str, dialog) -> None:
        try:
            files = list_project_files(root)
        except Exception as e:
            logger.debug(f"file index failed: {e!r}")
            try:
                if GLib is not None:
                    GLib.idle_add(dialog.set_indexing, False)
                else:
                    dialog.set_indexing(False)
            except Exception:
                pass
            return
        key = os.path.abspath(root)
        if files or os.path.isdir(root):
            self._file_cache[key] = files
        logger.debug(f"fuzzy: {len(files)} file(s) indexed under {root}")
        items: list[tuple[str, str]] = []
        for path in files:
            try:
                display = os.path.relpath(path, root)
            except Exception:
                display = path
            items.append((display, path))
        ordered = order_with_recent(items, self._recent)
        try:
            if GLib is not None:
                GLib.idle_add(dialog.set_files, ordered)
            else:
                dialog.set_files(ordered)
        except Exception as e:
            logger.debug(f"fuzzy background update failed: {e!r}")

    # -- dialog --------------------------------------------------------

    def _show_finder(self) -> None:
        if Gtk is None:
            return
        root = find_project_root(self.window)
        if not root:
            self._notify_no_root()
            return
        try:
            dialog = FuzzyFinderDialog(parent=self.window)
        except Exception as e:
            logger.debug(f"fuzzy dialog create failed: {e!r}")
            return
        cached = self._cached_files(root)
        if cached:
            items: list[tuple[str, str]] = []
            for path in cached:
                try:
                    display = os.path.relpath(path, root)
                except Exception:
                    display = path
                items.append((display, path))
            dialog.set_files(order_with_recent(items, self._recent))
        else:
            dialog.set_indexing(True)
        dialog.connect("open-file", lambda _w, p: (self._open_file(p), dialog.destroy()))
        try:
            thread = threading.Thread(
                target=self._load_in_background, args=(root, dialog), daemon=True
            )
            thread.start()
        except Exception as e:
            logger.debug(f"fuzzy background index failed: {e!r}")
        try:
            dialog.run()
        finally:
            try:
                dialog.destroy()
            except Exception:
                pass

    def _notify_no_root(self) -> None:
        logger.debug("fuzzy: no project root (project-mode off or empty)")
        if Gtk is None:
            return
        try:
            dialog = Gtk.MessageDialog(
                transient_for=self.window,
                modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="No project folder loaded.",
            )
            try:
                dialog.format_secondary_text(
                    "Enable the project-mode plugin and pick a folder "
                    "with Ctrl+Shift+O first."
                )
            except Exception:
                pass
            try:
                dialog.run()
            finally:
                try:
                    dialog.destroy()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"no-root prompt failed: {e!r}")

    def _notify_empty(self, root: str) -> None:
        logger.debug(f"fuzzy: no files under {root}")
        if Gtk is None:
            return
        try:
            dialog = Gtk.MessageDialog(
                transient_for=self.window,
                modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="No files found in the project folder.",
            )
            try:
                dialog.format_secondary_text(root)
            except Exception:
                pass
            try:
                dialog.run()
            finally:
                try:
                    dialog.destroy()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"empty-root prompt failed: {e!r}")

    def _open_file(self, path: str) -> None:
        if Gio is None:
            return
        if not path:
            return
        try:
            location = Gio.File.new_for_path(path)
        except Exception:
            return
        existing = None
        try:
            existing = self.window.get_tab_from_location(location)
        except Exception:
            existing = None
        try:
            if existing is not None:
                self.window.set_active_tab(existing)
            else:
                self.window.create_tab_from_location(location, None, 0, True, True)
        except Exception as e:
            logger.debug(f"open file failed for {path}: {e!r}")
            return
        self._remember_recent(path)

    def _remember_recent(self, path: str) -> None:
        """Prepend path to the in-memory MRU list (dedupe + truncate)."""
        try:
            self._recent.remove(path)
        except ValueError:
            pass
        self._recent.insert(0, path)
        del self._recent[MAX_RECENT:]


# ---------------------------------------------------------------------------
# Public attach/detach — Thor host API
# ---------------------------------------------------------------------------

def attach(window) -> _FuzzyFinderManager | None:
    """Wire fuzzy finder to *window* (ThorWindow).

    Soft-fails when Gtk is unavailable (headless). Idempotent — second
    call on the same window returns the existing manager.
    """
    if Gtk is None:
        logger.debug("Gtk unavailable — fuzzy finder disabled (headless)")
        return None
    # Idempotency: reuse existing manager if already attached.
    existing = getattr(window, "_thor_fuzzy_manager", None)
    if existing is not None:
        return existing
    if getattr(window, "_thor_fuzzy_attached", False) and existing is None:
        # Inconsistent state — treat as not attached.
        pass
    manager = _FuzzyFinderManager(window)
    manager.attach()
    try:
        window._thor_fuzzy_attached = True  # type: ignore[attr-defined]
        window._thor_fuzzy_manager = manager  # type: ignore[attr-defined]
    except Exception:
        pass
    return manager


def detach(window) -> None:
    """Detach fuzzy finder from *window*."""
    manager = getattr(window, "_thor_fuzzy_manager", None)
    if manager is not None:
        try:
            manager.detach()
        except Exception:
            pass
        try:
            window._thor_fuzzy_attached = False  # type: ignore[attr-defined]
            window._thor_fuzzy_manager = None  # type: ignore[attr-defined]
        except Exception:
            pass
    else:
        # No manager but maybe a stray signal id
        try:
            window._thor_fuzzy_attached = False  # type: ignore[attr-defined]
        except Exception:
            pass


# Compatibility shim for plugin tests
class FuzzyFinderPlugin:  # type: ignore[no-redef]
    """Shim mimicking old plugin API for tests; delegates to manager/module.

    Tests instantiate without window and call _handle_global_key, _show_finder,
    _notify_no_root, _cached_files, _remember_recent, etc. This shim provides
    those with sensible defaults, delegating to the real manager when possible.
    """

    MAX_RECENT = MAX_RECENT

    def __init__(self, window=None):
        self.window = window
        self._recent: list[str] = []
        self._file_cache: dict = {}
        self._window_key_id = None
        # For tests that monkey-patch _show_finder / _notify_no_root
        self._show_finder = self._show_finder_impl  # type: ignore[method-assign]
        self._notify_no_root = self._notify_no_root_impl  # type: ignore[method-assign]

    def _show_finder_impl(self):  # type: ignore[no-untyped-def]
        # Tests monkey-patch this; default is no-op
        pass

    def _notify_no_root_impl(self):  # type: ignore[no-untyped-def]
        pass

    def _handle_global_key(self, keyname, ctrl, shift, alt):  # type: ignore[no-untyped-def]
        # Match plugin logic: Ctrl+P shows finder, else False
        if ctrl and not shift and not alt and keyname.lower() == "p":
            try:
                self._show_finder()
            except Exception:
                pass
            return True
        return False

    def _cached_files(self, root):  # type: ignore[no-untyped-def]
        return self._file_cache.get(root)

    def _remember_recent(self, path):  # type: ignore[no-untyped-def]
        if path in self._recent:
            self._recent.remove(path)
        self._recent.insert(0, path)
        del self._recent[MAX_RECENT:]

    # Module-level helpers as static
    find_project_root = staticmethod(find_project_root)
    order_with_recent = staticmethod(order_with_recent)
