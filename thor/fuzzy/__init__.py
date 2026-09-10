# -*- coding: utf-8 -*-
"""Fuzzy file opener (Ctrl+P) for the folder loaded in project-mode.

Thor-native port of ``plugins/fuzzy-finder/fuzzyfinder`` — wired via
``attach(window)`` to a :class:`thor.window.ThorWindow` with no plugin-era
interface slop.

Soft runtime dependency on project-mode: the project root is read from
the ProjectBrowser widget living in the side panel (duck-typed, no
import of projectmode). When project-mode is disabled or no folder is
loaded, a hint dialog is shown instead of raising.
"""

from __future__ import annotations

import collections
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

#: Explicit selected-row style for the results list. The search entry always
#: holds focus, so GTK paints the treeview with its *unfocused* selection
#: colour (a faint grey bar); we override both states with the same vivid
#: accent so the file that Enter will open is unmistakable.
_SELECTION_CSS = (
    "treeview.view:selected, treeview.view:selected:focus {"
    " background-color: #3d78c2;"
    " background-image: none;"
    " color: #ffffff;"
    "}"
)


def selection_css() -> str:
    """CSS for the fuzzy finder result selection (pure string, headless-safe)."""
    return _SELECTION_CSS

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
    "selection_css",
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
    """Locate the project feature's loaded root via the project browser.

    Returns an absolute directory path or None when no project browser
    is attached (or it has no valid root loaded).
    """
    try:
        from ..project import get_browser

        browser = get_browser(window)
    except Exception as e:
        logger.debug("find_project_root: project lookup failed: %r", e, exc_info=True)
        return None
    if browser is None:
        return None
    try:
        root = getattr(browser, "_root_dir", None)
    except Exception as e:
        logger.debug("find_project_root attr failed: %r", e, exc_info=True)
        return None
    if isinstance(root, str) and root and os.path.isdir(root):
        return os.path.abspath(root)
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
            self._destroyed = False
            try:
                self.connect("destroy", lambda *_a: setattr(self, "_destroyed", True))
            except Exception as e:
                logger.debug("fuzzy destroy hook failed: %r", e, exc_info=True)
            try:
                self.set_modal(True)
            except Exception as e:
                logger.debug("fuzzy set_modal failed: %r", e, exc_info=True)
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
            self._selection_css = None
            try:
                provider = Gtk.CssProvider()
                provider.load_from_data(_SELECTION_CSS.encode("utf-8"))
                self._view.get_style_context().add_provider(
                    provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION  # type: ignore[attr-defined]
                )
                self._selection_css = provider
            except Exception as e:
                logger.debug(f"fuzzy selection css failed: {e!r}")
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
            if getattr(self, "_destroyed", False):
                return
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
            except Exception as e:
                logger.debug("fuzzy grab_focus failed: %r", e, exc_info=True)

        def set_indexing(self, on: bool) -> None:
            """Show or clear the background-indexing indicator."""
            if getattr(self, "_destroyed", False):
                return
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
            if getattr(self, "_destroyed", False):
                return
            if total == 0 and not self._indexing:
                if query and shown > 0:
                    text = f"{shown} match{'es' if shown != 1 else ''}"
                elif query:
                    text = "No matches"
                else:
                    text = "Type a path or search files"
            elif shown == 0 and query:
                text = "No matches"
            else:
                text = f"{shown} of {total} files"
            if self._indexing:
                text = f"Indexing…  {text}" if text else "Indexing…"
            try:
                self._status.set_text(text)
            except Exception as e:
                logger.debug("fuzzy status failed: %r", e, exc_info=True)

        def _refilter(self) -> None:
            # Guard use-after-destroy: background indexing completes via
            # idle_add after the dialog may have been closed.
            if getattr(self, "_destroyed", False):
                return
            try:
                query = self._entry.get_text()
            except Exception as e:
                logger.debug("fuzzy query read failed: %r", e, exc_info=True)
                return
            labels = self._labels
            try:
                self._store.clear()
            except Exception as e:
                logger.debug("fuzzy store clear failed: %r", e, exc_info=True)
                return

            query_trimmed = query.strip()
            direct_file = None
            if query_trimmed:
                try:
                    expanded = os.path.abspath(os.path.expanduser(query_trimmed))
                    if os.path.isfile(expanded):
                        direct_file = expanded
                except Exception:
                    direct_file = None

            try:
                # If query points to an existing file on disk, offer it at top
                if direct_file is not None:
                    display_text = f"{direct_file} (Open file)"
                    markup = f"<b>{GLib.markup_escape_text(direct_file)}</b> <i>(file on disk)</i>" if GLib else direct_file
                    self._store.append([display_text, markup, direct_file])

                if self._index is not None:
                    # Single DP per candidate: scores AND positions come back
                    # together; never re-run fuzzy_match per row.
                    for display, _score, positions in self._index.search_scored(query, limit=MAX_ROWS):
                        if getattr(self, "_destroyed", False):
                            return
                        path = labels.get(display, display)
                        if direct_file and os.path.abspath(path) == direct_file:
                            continue
                        self._store.append(
                            [display, markup_highlight(display, positions), path]
                        )
                else:
                    displays = [display for display, _path in self._files]
                    for display in fuzzy_find(query, displays, limit=MAX_ROWS):
                        if getattr(self, "_destroyed", False):
                            return
                        path = labels.get(display, display)
                        if direct_file and os.path.abspath(path) == direct_file:
                            continue
                        positions: list[int] = []
                        if query.strip():
                            try:
                                hit = fuzzy_match(query, display)
                                positions = list(hit[1]) if hit is not None else []
                            except Exception as e:
                                logger.debug("fuzzy highlight failed: %r", e, exc_info=True)
                                positions = []
                        self._store.append(
                            [display, markup_highlight(display, positions), path]
                        )
            except Exception as e:
                logger.debug(f"fuzzy refilter failed: {e!r}")
            self._update_status(len(self._store), len(self._files), query)
            self._select_row(0)

        # -- selection -------------------------------------------------
        def _select_row(self, index: int) -> None:
            if getattr(self, "_destroyed", False):
                return
            count = len(self._store)
            if count == 0:
                return
            index = max(0, min(count - 1, index))
            path = Gtk.TreePath.new_from_indices([index])
            self._view.get_selection().select_path(path)
            self._view.scroll_to_cell(path, None, False, 0, 0)

        def _selected_path(self) -> str | None:
            if getattr(self, "_destroyed", False):
                return None
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
            if not path:
                try:
                    query = self._entry.get_text().strip()
                    if query:
                        expanded = os.path.abspath(os.path.expanduser(query))
                        if os.path.isfile(expanded):
                            path = expanded
                except Exception:
                    path = None
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
        self._registered = False
        self._file_cache: dict[str, list[str]] = {}
        self._file_cache_mtime: dict[str, float] = {}
        self._recent: list[str] = []

    # -- lifecycle -----------------------------------------------------

    def attach(self) -> None:
        try:
            self.window.register_key_handler(self._handle_key)
            self._registered = True
        except Exception as e:
            logger.debug(f"window keys register failed: {e!r}")
            self._registered = False

    def detach(self) -> None:
        if self._registered:
            try:
                self.window.unregister_key_handler(self._handle_key)
            except Exception:
                pass
        self._registered = False

    # -- key handling --------------------------------------------------

    def _handle_global_key(self, keyname: str, ctrl: bool, shift: bool, alt: bool) -> bool:
        if ctrl and not shift and not alt and (keyname or "").lower() == "p":
            logger.debug("key: Ctrl+P fuzzy-finder")
            self._show_finder()
            return True
        return False

    def _handle_key(self, window, keyname: str, ctrl: bool, shift: bool, alt: bool) -> bool:
        return self._handle_global_key(keyname, ctrl, shift, alt)

    # -- file cache ----------------------------------------------------

    def _cached_files(self, root: str) -> list[str] | None:
        try:
            key = os.path.abspath(root)
        except Exception:
            return None
        cached = self._file_cache.get(key)
        if cached is None:
            return None
        try:
            mtime = os.path.getmtime(key)
        except OSError:
            return cached
        if self._file_cache_mtime.get(key) != mtime:
            # Root changed on disk since the entry was stored: drop it so
            # the caller paints recents + re-indexes instead of a stale list.
            # (Top-level mtime only catches direct-child changes; deeper
            # edits are picked up by the background reload every show.)
            self._file_cache.pop(key, None)
            self._file_cache_mtime.pop(key, None)
            return None
        return cached

    @staticmethod
    def _idle_guarded(dialog, fn, *args):
        """Wrap an idle callback so a destroyed dialog is never touched.

        The background index thread outlives the dialog when the user
        closes it mid-scan, so the destroyed check must run at fire time,
        not at schedule time (a pre-schedule check alone still races).
        """
        def _fire(*_a):
            try:
                if getattr(dialog, "_destroyed", False):
                    return False
            except Exception:
                return False
            try:
                fn(*args)
            except Exception as e:
                logger.debug(f"fuzzy background update failed: {e!r}")
            return False
        return _fire

    def _load_in_background(self, root: str, dialog) -> None:
        try:
            files = list_project_files(root)
        except Exception as e:
            logger.debug(f"file index failed: {e!r}")
            try:
                if GLib is not None:
                    GLib.idle_add(self._idle_guarded(dialog, dialog.set_indexing, False))
                elif not getattr(dialog, "_destroyed", False):
                    dialog.set_indexing(False)
            except Exception:
                pass
            return
        try:
            key = os.path.abspath(root)
        except Exception:
            key = root
        if files or os.path.isdir(root):
            self._file_cache[key] = files
            try:
                self._file_cache_mtime[key] = os.path.getmtime(key)
            except OSError:
                pass
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
                GLib.idle_add(self._idle_guarded(dialog, dialog.set_files, ordered))
            elif not getattr(dialog, "_destroyed", False):
                dialog.set_files(ordered)
        except Exception as e:
            logger.debug(f"fuzzy background update failed: {e!r}")
    # -- dialog --------------------------------------------------------

    def _show_finder(self) -> None:
        if Gtk is None:
            return
        root = find_project_root(self.window)
        try:
            dialog = FuzzyFinderDialog(parent=self.window)
        except Exception as e:
            logger.debug(f"fuzzy dialog create failed: {e!r}")
            return

        if root:
            cached = self._cached_files(root)
            if cached is not None:
                # [] (empty project) is a valid hit: paint recents now; the
                # background reload below still refreshes afterwards.
                items: list[tuple[str, str]] = []
                for path in cached:
                    try:
                        display = os.path.relpath(path, root)
                    except Exception:
                        display = path
                    items.append((display, path))
                dialog.set_files(order_with_recent(items, self._recent))
            else:
                recent_items = [
                    (os.path.basename(p), p)
                    for p in self._recent
                    if os.path.isfile(p)
                ]
                if recent_items:
                    dialog.set_files(recent_items)
                dialog.set_indexing(True)
            try:
                thread = threading.Thread(
                    target=self._load_in_background, args=(root, dialog), daemon=True
                )
                thread.start()
            except Exception as e:
                logger.debug(f"fuzzy background index failed: {e!r}")
        else:
            # No project root: populate with recent files on disk (if any)
            recent_items = [
                (os.path.basename(p), p)
                for p in self._recent
                if os.path.isfile(p)
            ]
            dialog.set_files(recent_items)

        dialog.connect("open-file", lambda _w, p: (self._open_file(p), dialog.destroy()))
        try:
            dialog.run()
        finally:
            try:
                dialog.destroy()
            except Exception:
                pass

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
