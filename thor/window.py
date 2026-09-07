# -*- coding: utf-8 -*-
"""ThorWindow — central editor window."""

from __future__ import annotations

import logging
import os
import pathlib

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkSource", "4")
    from gi.repository import GObject, Gtk, Gdk, Gio, GLib, GtkSource  # type: ignore
except Exception:  # headless
    GObject = Gtk = Gdk = Gio = GLib = GtkSource = None  # type: ignore

from .panel import ThorPanel
from .document import ThorDocument, ThorTab
from .keys import CTRL_PLUGIN_KEYS, CTRL_SHIFT_PLUGIN_KEYS, decode_key_event
from .state import load_state as load_panel_state, save_state as save_panel_state

if Gtk is not None and GObject is not None:

    class ThorWindow(Gtk.ApplicationWindow):  # type: ignore[misc]
        __gtype_name__ = "ThorWindow"

        __gsignals__ = {
            "tab-added": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
            "tab-removed": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
            "tabs-reordered": (GObject.SignalFlags.RUN_LAST, None, ()),
            "active-tab-changed": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
            "active-tab-state-changed": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
        }

        def __init__(self, app: Gtk.Application, initial_folder: str | None = None) -> None:  # type: ignore[name-defined]
            super().__init__(application=app, title="Thor")

            # Load persistent panel / window state
            self._panel_state = load_panel_state()
            self._word_wrap = bool(self._panel_state.get("word_wrap", False))
            side_vis = bool(self._panel_state.get("side_panel_visible", True))
            bottom_vis = bool(self._panel_state.get("bottom_panel_visible", False))
            side_size = max(100, int(self._panel_state.get("side_panel_size", 260)))
            bottom_size = max(80, int(self._panel_state.get("bottom_panel_size", 200)))

            win_w = max(400, int(self._panel_state.get("window_width", 1280)))
            win_h = max(300, int(self._panel_state.get("window_height", 800)))
            self.set_default_size(win_w, win_h)

            win_x = self._panel_state.get("window_x")
            win_y = self._panel_state.get("window_y")
            if win_x is not None and win_y is not None:
                try:
                    self.move(int(win_x), int(win_y))
                except Exception:
                    pass

            if bool(self._panel_state.get("window_maximized", False)):
                try:
                    self.maximize()
                except Exception:
                    pass
            try:
                self.set_icon_name("dev.thor.Editor")
            except Exception:
                pass
            # XFCE: SSD via xfwm4 — no HeaderBar, follow mousepad/gedit.
            self._app = app
            self._initial_folder = initial_folder

            # Main layout: H paned (side | center) + V paned (center | bottom)
            self._hpaned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)

            # Side panel — directly in Paned so hide() reclaims space (xfce/mousepad style)
            self._side_panel = ThorPanel(orientation=Gtk.Orientation.VERTICAL)
            self._side_panel.set_target_visible(side_vis)
            self._side_panel.set_size_request(side_size, -1)
            self._hpaned.pack1(self._side_panel, False, True)  # shrink True so hide reclaims

            # Center vertical split: editor notebook on top, bottom panel below
            self._vpaned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
            self._hpaned.pack2(self._vpaned, True, False)

            # Editor notebook
            self._notebook = Gtk.Notebook()
            self._notebook.set_scrollable(True)
            self._notebook.set_show_border(False)
            self._notebook.connect("switch-page", self._on_switch_page)
            self._notebook.connect("page-removed", self._on_page_removed)
            self._notebook.connect("page-reordered", self._on_page_reordered)
            self._vpaned.pack1(self._notebook, True, False)

            # Bottom panel — directly in Paned
            self._bottom_panel = ThorPanel(orientation=Gtk.Orientation.HORIZONTAL)
            self._bottom_panel.set_target_visible(bottom_vis)
            self._bottom_panel.set_size_request(-1, bottom_size)
            self._vpaned.pack2(self._bottom_panel, False, True)  # shrink True so hide reclaims

            # Wrap hpaned (clean frameless content)
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self._vbox = vbox  # exposed for find bar (thor.find)
            self._menubar = None
            vbox.pack_start(self._hpaned, True, True, 0)
            self.add(vbox)

            # Do after show so allocation exists; use idle
            def _set_initial_positions():
                if getattr(self, "_destroyed", False):
                    return False
                try:
                    self._restore_panel_state()
                    self.focus_active_editor()
                except Exception:
                    logger.debug("initial paned positions failed", exc_info=True)
                return False

            try:
                if GLib is not None:
                    GLib.idle_add(_set_initial_positions)
            except Exception:
                logger.debug("initial positions idle_add failed", exc_info=True)

            vbox.show_all()
            if self._side_panel.get_n_items() == 0 or not side_vis:
                self._side_panel.hide()
            if self._bottom_panel.get_n_items() == 0 or not bottom_vis:
                self._bottom_panel.hide()

            # Sync Paned positions with panel visibility so hidden panels reclaim space
            def _sync_side(*_a):
                try:
                    is_vis = self._side_panel.get_visible()
                    n = self._side_panel.get_n_items()
                    if is_vis and n == 0:
                        # Guard: never show an empty panel
                        self._side_panel.hide()
                        return
                    if not is_vis or n == 0:
                        self._side_panel.set_size_request(0, -1)
                        self._hpaned.set_position(0)
                        self._hpaned.queue_resize()
                        self.queue_resize()
                    else:
                        size = max(100, int(self._panel_state.get("side_panel_size", 260)))
                        self._side_panel.set_size_request(size, -1)
                        if self._hpaned.get_position() == 0:
                            self._hpaned.set_position(size)
                        self._hpaned.queue_resize()
                        self.queue_resize()
                    if n > 0:
                        self._panel_state["side_panel_visible"] = is_vis
                        self._save_panel_state()
                except Exception:
                    logger.debug("side panel sync failed", exc_info=True)

            def _sync_bottom(*_a):
                try:
                    is_vis = self._bottom_panel.get_visible()
                    n = self._bottom_panel.get_n_items()
                    if is_vis and n == 0:
                        # Guard: never show an empty panel
                        self._bottom_panel.hide()
                        return
                    alloc = self.get_allocation()
                    h = alloc.height if alloc.height > 0 else 800
                    if not is_vis or n == 0:
                        self._bottom_panel.set_size_request(-1, 0)
                        self._vpaned.set_position(h)
                        self._vpaned.queue_resize()
                        self.queue_resize()
                    else:
                        size = max(80, int(self._panel_state.get("bottom_panel_size", 200)))
                        self._bottom_panel.set_size_request(-1, size)
                        self._vpaned.set_position(max(100, h - size))
                        self._vpaned.queue_resize()
                        self.queue_resize()
                    if n > 0:
                        self._panel_state["bottom_panel_visible"] = is_vis
                        self._save_panel_state()
                except Exception:
                    logger.debug("bottom panel sync failed", exc_info=True)

            try:
                self._side_panel.connect("notify::visible", _sync_side)
                self._bottom_panel.connect("notify::visible", _sync_bottom)
            except Exception:
                logger.debug("panel notify wiring failed", exc_info=True)

            def _on_hpaned_pos(*_a):
                try:
                    if self._side_panel.get_visible() and self._side_panel.get_n_items() > 0:
                        pos = self._hpaned.get_position()
                        if pos > 80:
                            self._panel_state["side_panel_size"] = pos
                            self._save_panel_state()
                except Exception:
                    pass

            def _on_vpaned_pos(*_a):
                try:
                    if self._bottom_panel.get_visible() and self._bottom_panel.get_n_items() > 0:
                        alloc = self.get_allocation()
                        h = alloc.height if alloc.height > 0 else 800
                        bottom_h = h - self._vpaned.get_position()
                        if bottom_h > 50:
                            self._panel_state["bottom_panel_size"] = bottom_h
                            self._save_panel_state()
                except Exception:
                    pass

            try:
                self._hpaned.connect("notify::position", _on_hpaned_pos)
                self._vpaned.connect("notify::position", _on_vpaned_pos)
            except Exception:
                pass

            def _on_side_page_switch(_nb, _page, page_num):
                try:
                    self._panel_state["side_panel_active_page"] = int(page_num)
                    self._save_panel_state()
                except Exception:
                    pass

            def _on_bottom_page_switch(_nb, _page, page_num):
                try:
                    self._panel_state["bottom_panel_active_page"] = int(page_num)
                    self._save_panel_state()
                except Exception:
                    pass

            try:
                self._side_panel._notebook.connect("switch-page", _on_side_page_switch)
                self._bottom_panel._notebook.connect("switch-page", _on_bottom_page_switch)
            except Exception:
                pass
            # Track tabs
            self._tabs: list = []
            self._css_provider = None
            self._save_state_timeout_id = None
            self._session_save_timeout_id = None
            self._destroyed = False
            self._saving_panel_state = False

            def _debounced_save_state():
                self._save_state_timeout_id = None
                if getattr(self, "_destroyed", False):
                    return False
                self._save_panel_state()
                return False

            def _on_configure_event(_w, _event):
                try:
                    is_max = self.is_maximized()
                    self._panel_state["window_maximized"] = bool(is_max)
                    if not is_max:
                        x, y = self.get_position()
                        w, h = self.get_size()
                        if w > 100 and h > 100:
                            self._panel_state["window_width"] = int(w)
                            self._panel_state["window_height"] = int(h)
                            self._panel_state["window_x"] = int(x)
                            self._panel_state["window_y"] = int(y)
                    if GLib is not None:
                        if self._save_state_timeout_id is not None:
                            GLib.source_remove(self._save_state_timeout_id)
                        self._save_state_timeout_id = GLib.timeout_add(200, _debounced_save_state)
                except Exception:
                    pass
                return False

            def _on_window_state_event(_w, event):
                try:
                    if Gdk is not None:
                        is_max = bool(event.new_window_state & Gdk.WindowState.MAXIMIZED)
                        self._panel_state["window_maximized"] = is_max
                        if not is_max:
                            x, y = self.get_position()
                            w, h = self.get_size()
                            if w > 100 and h > 100:
                                self._panel_state["window_width"] = int(w)
                                self._panel_state["window_height"] = int(h)
                                self._panel_state["window_x"] = int(x)
                                self._panel_state["window_y"] = int(y)
                        self._save_panel_state()
                except Exception:
                    pass
                return False

            def _on_unmap(_w):
                try:
                    self._save_panel_state()
                except Exception:
                    pass
                return False

            try:
                self.connect("configure-event", _on_configure_event)
                self.connect("window-state-event", _on_window_state_event)
                self.connect("unmap", _on_unmap)
            except Exception:
                pass

            # Key handling (panel-hider style, etc. plugins hook here too)
            self.connect("key-press-event", self._on_key_press)
            self.connect("delete-event", self._on_delete_event)
            self.connect("destroy", self._on_destroy)

            # Apply atom-one-dark if available
            self._apply_color_scheme()

            self.show_all()
            # Re-hide panels if still empty or not target-visible after show_all
            if self._side_panel.get_n_items() == 0 or not side_vis:
                self._side_panel.hide()
            if self._bottom_panel.get_n_items() == 0 or not bottom_vis:
                self._bottom_panel.hide()

        def _save_panel_state(self) -> None:
            # Re-entrancy guard: notify handlers above call back in here while
            # the state dict is mid-update; nested saves would recurse.
            if getattr(self, "_saving_panel_state", False):
                return
            self._saving_panel_state = True
            try:
                if hasattr(self, "is_maximized"):
                    is_max = bool(self.is_maximized())
                    self._panel_state["window_maximized"] = is_max
                    if not is_max:
                        try:
                            if hasattr(self, "get_size"):
                                w, h = self.get_size()
                                if w > 100 and h > 100:
                                    self._panel_state["window_width"] = int(w)
                                    self._panel_state["window_height"] = int(h)
                            if hasattr(self, "get_position"):
                                x, y = self.get_position()
                                if x is not None and y is not None:
                                    self._panel_state["window_x"] = int(x)
                                    self._panel_state["window_y"] = int(y)
                        except Exception:
                            pass
                save_panel_state(self._panel_state)
            except Exception as e:
                logger.debug("save_panel_state failed: %r", e, exc_info=True)
            finally:
                self._saving_panel_state = False

        def _session_root(self):
            """Canonical project root for session save/restore, or None."""
            try:
                from .project import session as _session

                return _session.get_window_root(self)
            except Exception:
                logger.debug("_session_root failed", exc_info=True)
                return None

        def _schedule_session_save(self) -> None:
            """Debounced continuous session save (tab add/remove/reorder/switch)."""
            try:
                if getattr(self, "_destroyed", False):
                    return
                if GLib is None:
                    self._save_session_now()
                    return
                if getattr(self, "_session_save_timeout_id", None) is not None:
                    try:
                        GLib.source_remove(self._session_save_timeout_id)
                    except Exception:
                        pass
                    self._session_save_timeout_id = None

                def _fire():
                    self._session_save_timeout_id = None
                    if getattr(self, "_destroyed", False):
                        return False
                    try:
                        self._save_session_now()
                    except Exception:
                        logger.debug("session debounced save failed", exc_info=True)
                    return False

                try:
                    from .project.session import SESSION_SAVE_DEBOUNCE_MS

                    delay = SESSION_SAVE_DEBOUNCE_MS
                except Exception:
                    delay = 500
                self._session_save_timeout_id = GLib.timeout_add(delay, _fire)
            except Exception:
                logger.debug("_schedule_session_save failed", exc_info=True)

        def _save_session_now(self) -> None:
            """Synchronously persist open tabs for the current project root."""
            try:
                root = self._session_root()
                if not root:
                    return
                from .project import session as _session

                _session.save_for_root(root, self)
            except Exception:
                logger.debug("_save_session_now failed", exc_info=True)

        def _restore_panel_state(self) -> None:
            """Apply loaded panel state (visibility, active pages, sizes) to panels."""
            try:
                side_vis = bool(self._panel_state.get("side_panel_visible", True))
                bottom_vis = bool(self._panel_state.get("bottom_panel_visible", False))
                self._side_panel.set_target_visible(side_vis)
                self._bottom_panel.set_target_visible(bottom_vis)

                side_page = int(self._panel_state.get("side_panel_active_page", 0))
                if 0 <= side_page < self._side_panel.get_n_items():
                    self._side_panel._notebook.set_current_page(side_page)

                bottom_page = int(self._panel_state.get("bottom_panel_active_page", 0))
                if 0 <= bottom_page < self._bottom_panel.get_n_items():
                    self._bottom_panel._notebook.set_current_page(bottom_page)

                if side_vis and self._side_panel.get_n_items() > 0:
                    side_size = max(100, int(self._panel_state.get("side_panel_size", 260)))
                    self._hpaned.set_position(side_size)
                else:
                    self._hpaned.set_position(0)

                if bottom_vis and self._bottom_panel.get_n_items() > 0:
                    bottom_size = max(80, int(self._panel_state.get("bottom_panel_size", 200)))
                    alloc = self.get_allocation()
                    h = alloc.height if alloc.height > 0 else 800
                    self._vpaned.set_position(max(100, h - bottom_size))
                else:
                    alloc = self.get_allocation()
                    h = alloc.height if alloc.height > 0 else 800
                    self._vpaned.set_position(h)
            except Exception as e:
                logger.debug("_restore_panel_state failed: %r", e, exc_info=True)

        def focus_active_editor(self) -> bool:
            """Always place focus on the active editor view on startup or focus requests."""
            try:
                tab = self.get_active_tab()
                if tab is not None:
                    view = tab.get_view()
                    if view is not None:
                        view.grab_focus()
                        return True
            except Exception:
                logger.debug("focus_active_editor failed", exc_info=True)
            return False

        def toggle_word_wrap(self) -> bool:
            """Flip the window-wide word-wrap setting; returns the new state.

            Applies to every open editor view, persists to state.toml, and
            is inherited by tabs created later via _add_tab.
            """
            new_state = not bool(getattr(self, "_word_wrap", False))
            self._word_wrap = new_state
            try:
                self._panel_state["word_wrap"] = new_state
            except Exception:
                logger.debug("toggle_word_wrap: state store failed", exc_info=True)
            self._apply_word_wrap()
            try:
                self._save_panel_state()
            except Exception:
                logger.debug("toggle_word_wrap: save failed", exc_info=True)
            return new_state

        def _apply_word_wrap(self) -> None:
            """Apply the current word-wrap flag to all open editor views."""
            if Gtk is None:
                return
            try:
                mode = Gtk.WrapMode.WORD if getattr(self, "_word_wrap", False) else Gtk.WrapMode.NONE
            except Exception:
                logger.debug("_apply_word_wrap: mode resolve failed", exc_info=True)
                return
            for tab in list(getattr(self, "_tabs", None) or []):
                try:
                    view = tab.get_view()
                    if view is not None and hasattr(view, "set_wrap_mode"):
                        view.set_wrap_mode(mode)
                except Exception:
                    logger.debug("_apply_word_wrap: view failed", exc_info=True)
                    continue

        def _focus_in_terminal(self) -> bool:
            """True when keyboard focus sits inside the embedded terminal.

            Ctrl+R is readline reverse-search there and must not be stolen
            by the window-level word-wrap toggle.
            """
            try:
                panel = getattr(self, "_thor_terminal_panel", None)
                if panel is None:
                    return False
                focus = self.get_focus() if hasattr(self, "get_focus") else None
                ancestor = focus
                for _ in range(8):
                    if ancestor is None:
                        break
                    if ancestor is panel:
                        return True
                    ancestor = ancestor.get_parent() if hasattr(ancestor, "get_parent") else None
            except Exception:
                logger.debug("_focus_in_terminal probe failed", exc_info=True)
            return False

        def _on_destroy(self, *_args) -> None:
            # Mark first so pending idle/timeout callbacks bail instead of
            # emitting on a dead window. Real teardown: project monitors,
            # CssProvider. Plugin detaches beyond project use their own
            # detach() via their owners.
            try:
                self._destroyed = True
            except Exception:
                pass
            if getattr(self, "_save_state_timeout_id", None) is not None and GLib is not None:
                try:
                    GLib.source_remove(self._save_state_timeout_id)
                    self._save_state_timeout_id = None
                except Exception:
                    pass
            if getattr(self, "_session_save_timeout_id", None) is not None and GLib is not None:
                try:
                    GLib.source_remove(self._session_save_timeout_id)
                    self._session_save_timeout_id = None
                except Exception:
                    pass
            try:
                self._save_panel_state()
            except Exception:
                pass
            try:
                self._save_session_now()
            except Exception:
                pass
            try:
                from .project import detach as _detach_project

                try:
                    _detach_project(self)
                except Exception:
                    logger.debug("destroy: project detach failed", exc_info=True)
            except Exception:
                logger.debug("destroy: project detach import failed", exc_info=True)
            prov = getattr(self, "_css_provider", None)
            if prov is not None:
                try:
                    screen = Gdk.Screen.get_default() if Gdk is not None else None
                    if screen is not None:
                        Gtk.StyleContext.remove_provider_for_screen(screen, prov)  # type: ignore[attr-defined]
                except Exception:
                    logger.debug("destroy: css provider removal failed", exc_info=True)
                finally:
                    try:
                        self._css_provider = None
                    except Exception:
                        logger.debug("destroy: css provider clear failed", exc_info=True)

        def get_menubar(self):
            return None

        # ------------------------------------------------------------------
        # ThorWindow API
        # ------------------------------------------------------------------
        def get_side_panel(self):
            return self._side_panel

        def get_bottom_panel(self):
            return self._bottom_panel

        def get_statusbar(self):
            return None

        def get_searchbar(self):
            # Thor's document find bar (Ctrl+F)
            mgr = getattr(self, "_thor_find_mgr", None)
            if mgr is not None and getattr(mgr, "bar", None) is not None:
                return mgr.bar
            return getattr(self, "_find_bar", None)

        def get_message_bus(self):
            return None

        def get_state(self) -> int:
            return 0

        def get_group(self):
            return None

        def get_ui_manager(self):
            return None

        def _thor_window_get_notebook(self):  # for plugins that poke private
            return self._notebook

        def get_active_tab(self):
            n = self._notebook.get_current_page()
            if n < 0 or n >= len(self._tabs):
                return None
            # notebook order and _tabs must stay in sync
            try:
                return self._notebook.get_nth_page(n)  # ThorTab
            except Exception:
                logger.debug("get_active_tab fallback", exc_info=True)
                return self._tabs[n] if self._tabs else None

        def get_active_view(self):
            tab = self.get_active_tab()
            if tab is None:
                return None
            try:
                return tab.get_view()
            except Exception:
                logger.debug("get_active_view failed", exc_info=True)
                return None

        def get_active_document(self):
            tab = self.get_active_tab()
            if tab is None:
                return None
            try:
                return tab.get_document()
            except Exception:
                logger.debug("get_active_document failed", exc_info=True)
                return None

        def get_documents(self):
            return [t.get_document() for t in self._tabs]

        def get_views(self):
            return [t.get_view() for t in self._tabs]

        def get_unsaved_documents(self):
            out = []
            for t in self._tabs:
                try:
                    if t.get_document().get_modified():
                        out.append(t.get_document())
                except Exception:
                    logger.debug("unsaved check failed", exc_info=True)
            return out

        def get_tab_from_location(self, location: Gio.File):  # type: ignore[name-defined]
            for t in self._tabs:
                try:
                    loc = t.get_document().get_location()
                    if loc is not None and loc.equal(location):
                        return t
                except Exception:
                    logger.debug("tab location compare failed", exc_info=True)
                    continue
            return None

        def open_file(self, path: str, line_pos: int = -1, col_pos: int = -1, jump_to: bool = True):
            """Open an out-of-tree or in-tree file path in a tab."""
            if not path or Gio is None:
                return None
            try:
                abspath = os.path.abspath(os.path.expanduser(path))
                loc = Gio.File.new_for_path(abspath)
                return self.create_tab_from_location(
                    loc, line_pos=line_pos, col_pos=col_pos, create=True, jump_to=jump_to
                )
            except Exception as e:
                logger.warning("open_file %s failed: %r", path, e)
                return None

        def prompt_open_file(self) -> list[str]:
            """Prompt user with standard Open File dialog to open out-of-tree or in-tree files."""
            if Gtk is None:
                return []
            dlg = None
            opened: list[str] = []
            try:
                dlg = Gtk.FileChooserDialog(
                    title="Open File",
                    transient_for=self,
                    action=Gtk.FileChooserAction.OPEN,
                )
                dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
                dlg.set_select_multiple(True)
                if dlg.run() == Gtk.ResponseType.OK:
                    files = dlg.get_filenames()
                    for fp in files or []:
                        tab = self.open_file(fp, jump_to=True)
                        if tab is not None:
                            opened.append(fp)
            except Exception as e:
                logger.warning("prompt_open_file failed: %r", e)
            finally:
                if dlg is not None:
                    try:
                        dlg.destroy()
                    except Exception:
                        pass
            return opened

        def create_tab(self, jump_to: bool = True):
            doc = ThorDocument(location=None)
            tab = ThorTab(document=doc)
            self._add_tab(tab, jump_to=jump_to, title="Untitled")
            return tab

        def create_tab_from_location(
            self,
            location: Gio.File,  # type: ignore[name-defined]
            encoding=None,
            line_pos: int = -1,
            col_pos: int = -1,
            create: bool = True,
            jump_to: bool = True,
        ):
            # Reuse existing tab if already open
            existing = self.get_tab_from_location(location)
            if existing is not None:
                if jump_to:
                    self.set_active_tab(existing)
                if line_pos >= 0:
                    self._jump_to_line(existing, line_pos, col=col_pos)
                return existing
            if not create:
                try:
                    if not location.query_exists(None):
                        return None
                except Exception:
                    logger.debug("create_tab: query_exists failed", exc_info=True)
                    return None
            tab = ThorTab(location=location)
            try:
                tab.load_location(location)
            except Exception:
                logger.debug("create_tab_from_location: load failed", exc_info=True)
            name = self._display_name(location)
            self._add_tab(tab, jump_to=jump_to, title=name)
            if line_pos >= 0:
                self._jump_to_line(tab, line_pos, col=col_pos)
            return tab

        def close_tab(self, tab) -> None:
            if tab is None:
                return
            # Drop from _tabs first so a remove_page failure (or the
            # page-removed resync below) can't leave a stale entry behind.
            try:
                if tab in self._tabs:
                    self._tabs.remove(tab)
            except Exception:
                logger.debug("close_tab: _tabs remove failed", exc_info=True)
            # Remove the notebook page; a tab missing from the notebook (e.g.
            # removed directly via remove_page) still gets cleanup + emit below.
            try:
                n = self._notebook.get_n_pages()
                for i in range(n):
                    if self._notebook.get_nth_page(i) is tab:
                        try:
                            self._notebook.remove_page(i)
                        except Exception:
                            logger.debug("close_tab: remove_page failed", exc_info=True)
                        break
            except Exception:
                logger.debug("close_tab: page lookup failed", exc_info=True)
            try:
                doc = tab.get_document()
                for attr in ("_thor_label_handler_id", "_modified_changed_id"):
                    hid = getattr(tab, attr, None)
                    if hid is not None:
                        try:
                            if doc.handler_is_connected(hid):
                                doc.disconnect(hid)
                        except Exception:
                            logger.debug("close_tab: disconnect %s failed", attr, exc_info=True)
                        finally:
                            try:
                                setattr(tab, attr, None)
                            except Exception:
                                logger.debug("close_tab: clear %s failed", attr, exc_info=True)
            except Exception:
                logger.debug("close_tab: handler disconnect failed", exc_info=True)
            try:
                self.emit("tab-removed", tab)
            except Exception:
                logger.debug("close_tab: emit tab-removed failed", exc_info=True)
            try:
                tab.destroy()
            except Exception:
                logger.debug("close_tab: destroy failed", exc_info=True)

        def close_all_tabs(self) -> None:
            for tab in list(self._tabs):
                self.close_tab(tab)

        def close_tabs(self, tabs) -> None:
            for t in list(tabs):
                self.close_tab(t)

        def set_active_tab(self, tab) -> None:
            n = self._notebook.get_n_pages()
            for i in range(n):
                if self._notebook.get_nth_page(i) is tab:
                    self._notebook.set_current_page(i)
                    return

        # ------------------------------------------------------------------
        # Internals
        # ------------------------------------------------------------------
        def _add_tab(self, tab: ThorTab, jump_to: bool = True, title: str = "Untitled") -> None:
            # Apply preferred scheme so gutter isn't white (screenshot fix)
            try:
                pref = getattr(self, "_preferred_scheme", None)
                if pref is not None:
                    tab.get_document().set_style_scheme(pref)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("_add_tab: scheme failed", exc_info=True)
            # New tabs inherit the window-wide word-wrap setting
            try:
                if Gtk is not None:
                    view = tab.get_view()
                    if view is not None and hasattr(view, "set_wrap_mode"):
                        view.set_wrap_mode(
                            Gtk.WrapMode.WORD if getattr(self, "_word_wrap", False)
                            else Gtk.WrapMode.NONE
                        )
            except Exception:
                logger.debug("_add_tab: wrap mode failed", exc_info=True)
            # Build notebook label with close button
            label_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            lbl = Gtk.Label(label=title)
            lbl.set_xalign(0.0)
            # modified dot
            dot = Gtk.Label(label="•")
            dot.set_no_show_all(True)
            dot.hide()
            # keep refs for updates
            tab._thor_label = lbl  # type: ignore[attr-defined]
            tab._thor_dot = dot  # type: ignore[attr-defined]
            # Track modified signal to show dot (id kept for close_tab disconnect)
            try:
                tab._thor_label_handler_id = tab.get_document().connect("modified-changed", lambda *_: self._update_tab_label(tab))  # type: ignore[attr-defined]
            except Exception:
                logger.debug("_add_tab: modified-changed connect failed", exc_info=True)
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_focus_on_click(False)
            try:
                img = Gtk.Image.new_from_icon_name("window-close", Gtk.IconSize.MENU)
                btn.add(img)
            except Exception:
                logger.debug("_add_tab: close icon failed", exc_info=True)
                btn.set_label("×")
            btn.connect("clicked", lambda *_: self.close_tab(tab))
            btn.set_tooltip_text("Close")
            label_box.pack_start(lbl, True, True, 0)
            label_box.pack_start(dot, False, False, 0)
            label_box.pack_start(btn, False, False, 0)
            label_box.show_all()
            dot.hide()
            idx = self._notebook.append_page(tab, label_box)
            try:
                self._notebook.set_tab_reorderable(tab, True)
            except Exception:
                logger.debug("_add_tab: set_tab_reorderable failed", exc_info=True)
            # Notebook and _tabs must mutate together — a failure between them
            # desyncs get_active_tab(). Each emit is isolated so one failing
            # listener can't skip the other signal.
            self._tabs.append(tab)
            tab.show_all()
            try:
                self.emit("tab-added", tab)
            except Exception:
                logger.debug("_add_tab: emit tab-added failed", exc_info=True)
            if jump_to:
                try:
                    self._notebook.set_current_page(idx)
                except Exception:
                    logger.debug("_add_tab: set_current_page failed", exc_info=True)
                try:
                    self.emit("active-tab-changed", tab)
                except Exception:
                    logger.debug("_add_tab: emit active-tab-changed failed", exc_info=True)
            self._update_header()

        def _on_page_reordered(self, *args):
            # Keep _tabs in notebook order — drag-reorder otherwise desyncs
            try:
                ordered: list = []
                for i in range(self._notebook.get_n_pages()):
                    w = self._notebook.get_nth_page(i)
                    if w is not None:
                        ordered.append(w)
                existing = set(ordered)
                for t in list(self._tabs):
                    if t not in existing:
                        ordered.append(t)
                self._tabs = ordered
            except Exception:
                logger.debug("page reordered sync failed", exc_info=True)
            try:
                self.emit("tabs-reordered")
            except Exception:
                logger.debug("tabs-reordered emit failed", exc_info=True)

        def _on_switch_page(self, nb, page, idx):
            # idle so get_current_page reflects new page; resolve the page
            # inside the callback (captured idx may be stale after reorder/close)
            def _emit():
                if getattr(self, "_destroyed", False):
                    return False
                try:
                    cur = nb.get_current_page()
                    tab = nb.get_nth_page(cur)
                except Exception:
                    logger.debug("switch page resolve failed", exc_info=True)
                    tab = None
                try:
                    self.emit("active-tab-changed", tab)
                except Exception:
                    logger.debug("switch page emit failed", exc_info=True)
                self._update_header()
                return False

            try:
                GLib.idle_add(_emit)
            except Exception:
                logger.debug("switch page idle_add failed", exc_info=True)
                _emit()

        def _on_page_removed(self, *args):
            # A page removed without close_tab (direct remove_page) would
            # otherwise leave a stale entry in _tabs forever — resync.
            try:
                pages = []
                for i in range(self._notebook.get_n_pages()):
                    try:
                        pages.append(self._notebook.get_nth_page(i))
                    except Exception:
                        logger.debug("page removed sync lookup failed", exc_info=True)
                self._tabs = [t for t in list(self._tabs) if any(p is t for p in pages)]
            except Exception:
                logger.debug("page removed sync failed", exc_info=True)
            self._update_header()

        def _update_tab_label(self, tab) -> None:
            try:
                doc = tab.get_document()
                modified = doc.get_modified()
                dot = getattr(tab, "_thor_dot", None)
                if dot is not None:
                    dot.set_visible(bool(modified))
                lbl = getattr(tab, "_thor_label", None)
                if lbl is not None and modified and not lbl.get_text().endswith(" •"):
                    # dot already shows, keep label clean
                    pass
            except Exception:
                logger.debug("update tab label failed", exc_info=True)
            self._update_header()

        def _update_header(self) -> None:
            try:
                tab = self.get_active_tab()
                if tab is None:
                    self.set_title("Thor")
                    return
                doc = tab.get_document()
                loc = doc.get_location()
                if loc is not None:
                    try:
                        name = loc.get_basename() or "Untitled"
                        path = loc.get_path() or loc.get_uri()
                    except Exception:
                        logger.debug("header location names failed", exc_info=True)
                        name = doc.get_short_name_for_display()
                        path = name
                else:
                    name = "Untitled"
                    path = ""
                mod = " •" if doc.get_modified() else ""
                # XFCE: WM title carries path (like mousepad), no headerbar subtitle
                title = f"{name}{mod} — Thor" if path else f"{name}{mod}"
                self.set_title(title)
            except Exception:
                logger.debug("update header failed", exc_info=True)

        def _display_name(self, loc: Gio.File) -> str:  # type: ignore[name-defined]
            try:
                return loc.get_basename() or "Untitled"
            except Exception:
                logger.debug("display name failed", exc_info=True)
                return "Untitled"

        def _jump_to_line(self, tab, line: int, col: int = -1) -> None:
            if tab is None:
                return
            try:
                doc = tab.get_document()
                if doc is None:
                    return
                try:
                    count = int(doc.get_line_count())
                except Exception:
                    count = None
                if count is None:
                    # Unknown length: pin only the floor, let the buffer clamp.
                    clamped = max(0, line)
                else:
                    # Empty docs report 0/1 lines — never jump past the last one.
                    clamped = min(max(0, line), max(0, count - 1))
                if col >= 0:
                    try:
                        it = doc.get_iter_at_line_offset(clamped, max(0, col))
                    except Exception:
                        # Offset past end-of-line (or empty doc): line start.
                        it = doc.get_iter_at_line(clamped)
                else:
                    it = doc.get_iter_at_line(clamped)
                doc.place_cursor(it)
                view = tab.get_view()
                if view is None:
                    return
                view.scroll_to_iter(it, 0.0, False, 0, 0)
                view.grab_focus()
            except Exception:
                logger.debug("jump to line failed", exc_info=True)

        def _apply_color_scheme(self) -> None:
            # Thor ships atom-one-dark (styles/atom-one-dark.xml) which
            # defines line-numbers background as gutter-bg #21252B. Use it
            # by default so the gutter isn't stark white (screenshot bug).
            # Still respects THOR_DARK=0 to force classic if user wants.
            try:
                mgr = GtkSource.StyleSchemeManager.get_default()
                try:
                    from . import xdg
                    for s_dir in xdg.styles_dirs():
                        if os.path.isdir(s_dir):
                            mgr.append_search_path(s_dir)
                except Exception:
                    here = pathlib.Path(__file__).resolve().parents[1]
                    style_dir = here / "styles"
                    if style_dir.is_dir():
                        mgr.append_search_path(str(style_dir))
                force_classic = os.environ.get("THOR_DARK", "").strip().lower() in ("0", "false", "no", "off")
                if force_classic:
                    return
                scheme = mgr.get_scheme("atom-one-dark")
                if scheme is not None:
                    self._preferred_scheme = scheme  # type: ignore[attr-defined]
                    for tab in self._tabs:
                        try:
                            tab.get_document().set_style_scheme(scheme)  # type: ignore[attr-defined]
                        except Exception:
                            logger.debug("apply scheme failed", exc_info=True)
                    # Also set view gutter background via CSS fallback for any view
                    try:
                        old = getattr(self, "_css_provider", None)
                        if old is not None:
                            try:
                                screen = Gdk.Screen.get_default() if Gdk is not None else None
                                if screen is not None:
                                    Gtk.StyleContext.remove_provider_for_screen(screen, old)  # type: ignore[attr-defined]
                            except Exception:
                                logger.debug("css provider replace-remove failed", exc_info=True)
                            self._css_provider = None
                        css = b"textview, textview text, .view, GtkSourceView { background-color: #282C34; } .gutter, GtkSourceGutter { background-color: #21252B; }"
                        prov = Gtk.CssProvider()
                        prov.load_from_data(css)
                        screen = Gdk.Screen.get_default() if Gdk is not None else None
                        if screen is None:
                            return
                        Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)  # type: ignore[attr-defined]
                        self._css_provider = prov
                    except Exception:
                        logger.debug("css provider install failed", exc_info=True)
            except Exception:
                logger.debug("apply color scheme failed", exc_info=True)

        def save_tab(self, tab, save_as: bool = False) -> bool:
            if tab is None:
                return False
            try:
                doc = tab.get_document()
            except Exception:
                logger.debug("save_tab: get_document failed", exc_info=True)
                return False
            if doc is None:
                return False
            try:
                loc = doc.get_location() if hasattr(doc, 'get_location') else None
            except Exception:
                logger.debug("save_tab: get_location failed", exc_info=True)
                loc = None
            if loc is None or save_as:
                # Save As — prompt
                dlg = None
                try:
                    dlg = Gtk.FileChooserDialog(title="Save File", transient_for=self, action=Gtk.FileChooserAction.SAVE)  # type: ignore[attr-defined]
                    dlg.set_do_overwrite_confirmation(True)
                    if loc is not None:
                        try:
                            dlg.set_file(loc)
                        except Exception:
                            logger.debug("save dialog set_file failed", exc_info=True)
                        try:
                            dlg.set_current_name(loc.get_basename() or "Untitled")  # type: ignore[union-attr]
                        except Exception:
                            logger.debug("save dialog name failed", exc_info=True)
                    dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
                    resp = dlg.run()
                    if resp != Gtk.ResponseType.OK:
                        return False
                    # Prefer the dialog's Gio.File: get_filename() is None for
                    # remote (GVFS/URI) locations even though OK was pressed.
                    chosen = None
                    try:
                        chosen = dlg.get_file()
                    except Exception:
                        logger.debug("save dialog get_file failed", exc_info=True)
                        chosen = None
                    filename = None
                    if chosen is not None:
                        loc = chosen
                        try:
                            filename = chosen.get_path()
                        except Exception:
                            logger.debug("save dialog chosen path failed", exc_info=True)
                    if loc is None or (chosen is None):
                        try:
                            filename = dlg.get_filename()
                        except Exception:
                            logger.debug("save dialog get_filename failed", exc_info=True)
                            filename = None
                        if not filename:
                            return False
                        loc = Gio.File.new_for_path(filename)  # type: ignore[union-attr]
                    if loc is None:
                        return False
                    doc.set_location(loc)  # type: ignore[attr-defined]
                    # Display name falls back to the Gio basename so remote
                    # (no local path) saves still relabel the tab.
                    display_name = filename
                    if not display_name:
                        try:
                            display_name = loc.get_basename() or loc.get_uri()
                        except Exception:
                            logger.debug("save display name failed", exc_info=True)
                            display_name = None
                    try:
                        # update language
                        import gi
                        gi.require_version("GtkSource","4")
                        from gi.repository import GtkSource as _GS  # type: ignore
                        lm = _GS.LanguageManager.get_default()
                        lang = lm.guess_language(display_name, None)
                        if lang:
                            doc.set_language(lang)  # type: ignore[attr-defined]
                    except Exception:
                        logger.debug("save language guess failed", exc_info=True)
                    # update tab label
                    try:
                        lbl = getattr(tab, "_thor_label", None)
                        if lbl and display_name:
                            lbl.set_text(os.path.basename(display_name))
                    except Exception:
                        logger.debug("save label update failed", exc_info=True)
                except Exception:
                    logger.debug("save dialog failed", exc_info=True)
                    return False
                finally:
                    if dlg is not None:
                        try:
                            dlg.destroy()
                        except Exception:
                            logger.debug("save dialog destroy failed", exc_info=True)
            # Autoreload save guard: dirty buffer + file changed on disk since
            # baseline means saving would silently overwrite external edits.
            if loc is not None and not save_as:
                try:
                    from .autoreload import has_save_conflict, note_saved as _ar_note_saved
                    if has_save_conflict(self, doc):
                        if Gtk is None:
                            logger.warning("save blocked: file changed on disk")
                            return False
                        try:
                            dlg = Gtk.MessageDialog(
                                transient_for=self,
                                flags=Gtk.DialogFlags.MODAL,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.NONE,
                                text="File changed on disk — overwrite?",
                            )
                            dlg.format_secondary_text(
                                "Your unsaved changes conflict with external edits. "
                                "Saving now will overwrite the changes on disk."
                            )
                            dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
                            dlg.add_button("Overwrite Anyway", Gtk.ResponseType.OK)
                            try:
                                resp = dlg.run()
                            finally:
                                dlg.destroy()
                            if resp != Gtk.ResponseType.OK:
                                return False
                        except Exception:
                            logger.debug("save conflict dialog failed", exc_info=True)
                            return False
                except ImportError:
                    pass
                except Exception:
                    logger.debug("save conflict check failed", exc_info=True)
            try:
                tab.set_state(3)  # SAVING
                self.emit("active-tab-state-changed", tab)
            except Exception:
                logger.debug("save SAVING emit failed", exc_info=True)
            ok = False
            try:
                ok = bool(tab.save())  # type: ignore[attr-defined]
            except Exception:
                logger.debug("tab.save failed", exc_info=True)
                ok = False
            if ok:
                try:
                    from .autoreload import note_saved as _ar_note_saved_ok
                    _ar_note_saved_ok(self, doc)
                except Exception:
                    pass
                try:
                    tab.set_state(0)  # NORMAL
                    self.emit("active-tab-state-changed", tab)
                    self._update_tab_label(tab)
                    self._update_header()
                except Exception:
                    logger.debug("save NORMAL emit failed", exc_info=True)
            else:
                from .util import doc_path
                try:
                    tab.set_state(0)  # NORMAL — never leave a stuck SAVING spinner
                    self.emit("active-tab-state-changed", tab)
                except Exception:
                    logger.debug("save NORMAL emit failed", exc_info=True)
                logger.warning("save failed for %r; keeping dirty state", doc_path(doc))
            return ok

        def save_active_tab(self, save_as: bool = False) -> bool:
            return self.save_tab(self.get_active_tab(), save_as=save_as)

        def _emit_tab_state(self, tab, state: int) -> None:
            try:
                tab.set_state(state)
                self.emit("active-tab-state-changed", tab)
            except Exception:
                logger.debug("emit tab state failed", exc_info=True)

        def _on_key_press(self, widget, event) -> bool:
            # Thor-native save handling (XFCE traditional). Plugins also listen.
            # Plugin-owned keys fall through explicitly (return False) so this
            # handler can never swallow them, regardless of later edits below:
            # panel_hider Ctrl+B/J/E, fuzzy Ctrl+P, find Ctrl+F/G, palette
            # Ctrl+Shift+P, terminal Ctrl+` and Ctrl+Shift+T/W.
            parts = decode_key_event(event)
            if parts is None:
                return False
            keyname, ctrl, shift, _alt = parts
            if ctrl and keyname:
                if not shift and keyname in CTRL_PLUGIN_KEYS:
                    return False
                if shift and keyname in CTRL_SHIFT_PLUGIN_KEYS:
                    return False
            try:
                if ctrl and not shift and keyname == "s":
                    self.save_active_tab(save_as=False)
                    return True
                if ctrl and shift and keyname.lower() == "s":
                    self.save_active_tab(save_as=True)
                    return True
                if ctrl and not shift and keyname == "o":
                    self.prompt_open_file()
                    return True
                # lower(): with Shift held GDK reports "O"/"S" (uppercase).
                if ctrl and shift and keyname.lower() == "o":
                    if hasattr(self._app, "_prompt_open_folder"):
                        self._app._prompt_open_folder()
                    return True
                if ctrl and not shift and keyname == "n":
                    self.create_tab(jump_to=True)
                    return True
                # Unified exit path: Ctrl+Q closes this window via the same
                # delete-event handler as the WM close button (unsaved
                # prompt + saves). Each window is its own process, so this
                # quits exactly this window — never a sibling. Explicit
                # fallback: VTE/terminal focus can swallow the app accel.
                if ctrl and not shift and keyname.lower() == "q":
                    try:
                        self.close()
                    except Exception:
                        logger.debug("key press quit-close failed", exc_info=True)
                    return True
                # Window-level word-wrap toggle — but never steal the
                # terminal's reverse-i-search.
                if ctrl and not shift and keyname.lower() == "r":
                    try:
                        if self._focus_in_terminal():
                            return False
                    except Exception:
                        logger.debug("key press terminal guard failed", exc_info=True)
                    try:
                        self.toggle_word_wrap()
                    except Exception:
                        logger.debug("key press wrap toggle failed", exc_info=True)
                    return True
            except Exception:
                logger.debug("key press save failed", exc_info=True)
            # Let plugins / window handle other shortcuts; keep default propagation
            # Panel-hider: Ctrl+B etc will be handled by plugin signal handlers attached to window.
            # Do not swallow.
            return False

        def _on_delete_event(self, widget, event) -> bool:
            if getattr(self, "_save_state_timeout_id", None) is not None and GLib is not None:
                try:
                    GLib.source_remove(self._save_state_timeout_id)
                    self._save_state_timeout_id = None
                except Exception:
                    pass
            if getattr(self, "_session_save_timeout_id", None) is not None and GLib is not None:
                try:
                    GLib.source_remove(self._session_save_timeout_id)
                    self._session_save_timeout_id = None
                except Exception:
                    pass
            try:
                self._save_panel_state()
            except Exception:
                pass
            try:
                # Save session before the unsaved-changes prompt so "Close
                # Anyway" still restores dirty buffers via backup stash.
                self._save_session_now()
            except Exception:
                pass
            # Prompt for unsaved? MVP: allow close, plugins may intercept
            unsaved = self.get_unsaved_documents()
            if not unsaved:
                return False
            # Simple dialog
            try:
                dlg = Gtk.MessageDialog(
                    transient_for=self,
                    flags=Gtk.DialogFlags.MODAL,
                    message_type=Gtk.MessageType.WARNING,
                    buttons=Gtk.ButtonsType.NONE,
                    text=f"{len(unsaved)} file(s) have unsaved changes — close anyway?",
                )
                dlg.format_secondary_text("Changes will be lost if you close without saving.")
                dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
                dlg.add_button("Close Anyway", Gtk.ResponseType.OK)
                try:
                    resp = dlg.run()
                finally:
                    dlg.destroy()
                return resp != Gtk.ResponseType.OK
            except Exception:
                logger.debug("delete event dialog failed", exc_info=True)
                return True


else:

    class ThorWindow:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass
