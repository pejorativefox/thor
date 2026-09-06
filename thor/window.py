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
            self.set_default_size(1280, 800)
            try:
                self.set_icon_name("dev.thor.Editor")
            except Exception:
                pass
            # XFCE: SSD via xfwm4 — no HeaderBar, follow mousepad/gedit.
            self._app = app
            self._initial_folder = initial_folder

            # Traditional menu bar (not Gio app-menu/headerbar)
            menubar = self._build_menubar()

            # Main layout: H paned (side | center) + V paned (center | bottom)
            # (don't add to window yet — batch into vbox to avoid remove dance
            # that conflicts with GtkApplication's app-menu child)
            self._hpaned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)

            # Side panel — directly in Paned so hide() reclaims space (xfce/mousepad style)
            self._side_panel = ThorPanel(orientation=Gtk.Orientation.VERTICAL)
            self._side_panel.set_size_request(260, -1)
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
            self._bottom_panel.set_size_request(-1, 200)
            self._vpaned.pack2(self._bottom_panel, False, True)  # shrink True so hide reclaims
            # Wrap menubar + hpaned (XFCE SSD, not CSD headerbar)
            # Single add — avoids GtkApplication's extra app-menu child confusion.
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self._vbox = vbox  # exposed for find bar (thor.find)
            self._menubar = menubar
            if menubar is not None:
                vbox.pack_start(menubar, False, False, 0)
            vbox.pack_start(self._hpaned, True, True, 0)
            self.add(vbox)
            # Do after show so allocation exists; use idle
            def _set_initial_positions():
                try:
                    if self._side_panel.get_visible() and self._side_panel.get_n_items() != 0:
                        self._hpaned.set_position(260)
                    else:
                        self._hpaned.set_position(0)
                    alloc = self.get_allocation()
                    h = alloc.height if alloc.height > 0 else 800
                    if self._bottom_panel.get_visible() and self._bottom_panel.get_n_items() != 0:
                        self._vpaned.set_position(max(200, h - 240))
                    else:
                        self._vpaned.set_position(h)
                except Exception:
                    logger.debug("initial paned positions failed", exc_info=True)
                return False
            try:
                GLib.idle_add(_set_initial_positions)
            except Exception:
                logger.debug("initial positions idle_add failed", exc_info=True)
            vbox.show_all()
            if self._side_panel.get_n_items() == 0:
                self._side_panel.hide()
            if self._bottom_panel.get_n_items() == 0:
                self._bottom_panel.hide()
            # Sync Paned positions with panel visibility so hidden panels reclaim space
            # Gtk.Paned/Box doesn't reclaim hidden child's size_request without explicit queue_resize (minimal test).
            def _sync_side(*_a):
                try:
                    if self._side_panel.get_visible() and self._side_panel.get_n_items() == 0:
                        # Guard: never show an empty panel
                        self._side_panel.hide()
                        return
                    if not self._side_panel.get_visible() or self._side_panel.get_n_items() == 0:
                        self._side_panel.set_size_request(0, -1)
                        self._hpaned.set_position(0)
                        self._hpaned.queue_resize()
                        self.queue_resize()
                    else:
                        self._side_panel.set_size_request(260, -1)
                        if self._hpaned.get_position() == 0:
                            self._hpaned.set_position(260)
                        self._hpaned.queue_resize()
                        self.queue_resize()
                except Exception:
                    logger.debug("side panel sync failed", exc_info=True)
            def _sync_bottom(*_a):
                try:
                    if self._bottom_panel.get_visible() and self._bottom_panel.get_n_items() == 0:
                        # Guard: never show an empty panel
                        self._bottom_panel.hide()
                        return
                    if not self._bottom_panel.get_visible() or self._bottom_panel.get_n_items() == 0:
                        self._bottom_panel.set_size_request(-1, 0)
                        alloc = self.get_allocation()
                        h = alloc.height if alloc.height > 0 else 800
                        self._vpaned.set_position(h)
                        self._vpaned.queue_resize()
                        self.queue_resize()
                    else:
                        self._bottom_panel.set_size_request(-1, 200)
                        alloc = self.get_allocation()
                        h = alloc.height if alloc.height > 0 else 800
                        self._vpaned.set_position(max(200, h - 240))
                        self._vpaned.queue_resize()
                        self.queue_resize()
                except Exception:
                    logger.debug("bottom panel sync failed", exc_info=True)
            try:
                # Single notify::visible each — hide/show fire it; ThorPanel's
                # _sync_visibility -> hide/show covers n_items changes.
                self._side_panel.connect("notify::visible", _sync_side)
                self._bottom_panel.connect("notify::visible", _sync_bottom)
            except Exception:
                logger.debug("panel notify wiring failed", exc_info=True)
            # Track tabs
            self._tabs: list = []
            self._css_provider = None
            # Key handling (panel-hider style, etc. plugins hook here too)
            self.connect("key-press-event", self._on_key_press)
            self.connect("delete-event", self._on_delete_event)
            self.connect("destroy", self._on_destroy)

            # Apply atom-one-dark if available
            self._apply_color_scheme()

            self.show_all()
            # Re-hide panels if still empty after show_all
            if self._side_panel.get_n_items() == 0:
                self._side_panel.hide()
            if self._bottom_panel.get_n_items() == 0:
                self._bottom_panel.hide()

        def _on_destroy(self, *_args) -> None:
            # Real teardown: project monitors, CssProvider. Plugin detaches
            # beyond project use their own detach() via their owners.
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
                    Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), prov)  # type: ignore[attr-defined]
                except Exception:
                    logger.debug("destroy: css provider removal failed", exc_info=True)
                finally:
                    try:
                        self._css_provider = None
                    except Exception:
                        logger.debug("destroy: css provider clear failed", exc_info=True)


        def _build_menubar(self):
            # XFCE-native traditional menu bar (not HeaderBar/Gio app-menu).
            # Mirrors mousepad — File, Edit, View, Help. Uses xfwm4 decorations.
            try:
                menubar = Gtk.MenuBar()

                # File
                file_menu = Gtk.Menu()
                file_item = Gtk.MenuItem(label="File")
                file_item.set_submenu(file_menu)
                menubar.append(file_item)

                def _add(label, cb):
                    it = Gtk.MenuItem(label=label)
                    it.connect("activate", cb)
                    file_menu.append(it)
                    return it

                _add("New Window", lambda *_: self._app._new_window() if hasattr(self._app, "_new_window") else None)
                _add("Open Folder…", lambda *_: self._app._prompt_open_folder() if hasattr(self._app, "_prompt_open_folder") else None)  # type: ignore[attr-defined]
                _add("Open File…", lambda *_: self._app._prompt_open_file() if hasattr(self._app, "_prompt_open_file") else None)  # type: ignore[attr-defined]
                file_menu.append(Gtk.SeparatorMenuItem())
                _add("Close Tab", lambda *_: self.close_tab(self.get_active_tab()) if self.get_active_tab() else None)
                file_menu.append(Gtk.SeparatorMenuItem())
                _add("Quit", lambda *_: self._app.quit() if hasattr(self._app, "quit") else Gtk.main_quit())

                # Edit
                edit_menu = Gtk.Menu()
                edit_item = Gtk.MenuItem(label="Edit")
                edit_item.set_submenu(edit_menu)
                menubar.append(edit_item)

                def _find_show(*_a):
                    try:
                        mgr = getattr(self, "_thor_find_mgr", None)
                        if mgr is not None:
                            mgr.show()
                            return
                        # Lazy attach if host hasn't wired yet (e.g. early menu open)
                        try:
                            from thor.find import attach as _attach_find  # type: ignore
                            m = _attach_find(self)
                            if m is not None:
                                m.show()
                        except Exception:
                            logger.debug("lazy find attach failed", exc_info=True)
                    except Exception:
                        logger.debug("find show failed", exc_info=True)

                def _find_next(*_a):
                    try:
                        mgr = getattr(self, "_thor_find_mgr", None)
                        if mgr is not None:
                            mgr._go_next()  # type: ignore[attr-defined]
                    except Exception:
                        logger.debug("find next failed", exc_info=True)

                def _find_prev(*_a):
                    try:
                        mgr = getattr(self, "_thor_find_mgr", None)
                        if mgr is not None:
                            mgr._go_prev()  # type: ignore[attr-defined]
                    except Exception:
                        logger.debug("find prev failed", exc_info=True)

                it = Gtk.MenuItem(label="Find…")
                it.connect("activate", _find_show)
                edit_menu.append(it)
                it = Gtk.MenuItem(label="Find Next")
                it.connect("activate", _find_next)
                edit_menu.append(it)
                it = Gtk.MenuItem(label="Find Previous")
                it.connect("activate", _find_prev)
                edit_menu.append(it)

                # View
                view_menu = Gtk.Menu()
                view_item = Gtk.MenuItem(label="View")
                view_item.set_submenu(view_menu)
                menubar.append(view_item)
                # Toggle panels — like View → Side/Bottom Pane
                def _toggle_side(*_):
                    try:
                        vis = self._side_panel.get_visible()
                        if vis:
                            self._side_panel.hide()
                        elif self._side_panel.get_n_items() != 0:
                            self._side_panel.show()
                    except Exception:
                        logger.debug("toggle side panel failed", exc_info=True)
                def _toggle_bottom(*_):
                    try:
                        vis = self._bottom_panel.get_visible()
                        if vis:
                            self._bottom_panel.hide()
                        elif self._bottom_panel.get_n_items() != 0:
                            self._bottom_panel.show()
                    except Exception:
                        logger.debug("toggle bottom panel failed", exc_info=True)
                it = Gtk.MenuItem(label="Side Panel")
                it.connect("activate", _toggle_side)
                view_menu.append(it)
                it = Gtk.MenuItem(label="Bottom Panel")
                it.connect("activate", _toggle_bottom)
                view_menu.append(it)
                # Help
                help_menu = Gtk.Menu()
                help_item = Gtk.MenuItem(label="Help")
                help_item.set_submenu(help_menu)
                menubar.append(help_item)
                it = Gtk.MenuItem(label="About Thor")
                it.connect("activate", lambda *_: self._app._show_about() if hasattr(self._app, "_show_about") else None)
                help_menu.append(it)

                menubar.show_all()
                return menubar
            except Exception as e:
                logger.warning("menubar failed: %r", e)
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
            create: bool = True,
            jump_to: bool = True,
        ):
            # Reuse existing tab if already open
            existing = self.get_tab_from_location(location)
            if existing is not None:
                if jump_to:
                    self.set_active_tab(existing)
                if line_pos >= 0:
                    self._jump_to_line(existing, line_pos)
                return existing
            if not create and not location.query_exists(None):
                return None
            tab = ThorTab(location=location)
            try:
                tab.load_location(location)
            except Exception:
                logger.debug("create_tab_from_location: load failed", exc_info=True)
            name = self._display_name(location)
            self._add_tab(tab, jump_to=jump_to, title=name)
            if line_pos >= 0:
                self._jump_to_line(tab, line_pos)
            return tab

        def close_tab(self, tab) -> None:
            # Find notebook index of this tab widget
            n = self._notebook.get_n_pages()
            for i in range(n):
                if self._notebook.get_nth_page(i) is tab:
                    self._notebook.remove_page(i)
                    if tab in self._tabs:
                        self._tabs.remove(tab)
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
                        tab.destroy()
                    except Exception:
                        logger.debug("close_tab: destroy failed", exc_info=True)
                    self.emit("tab-removed", tab)
                    return

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
            self._notebook.set_tab_reorderable(tab, True)
            self._tabs.append(tab)
            tab.show_all()
            if jump_to:
                self._notebook.set_current_page(idx)
                self.emit("active-tab-changed", tab)
            self.emit("tab-added", tab)
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

        def _jump_to_line(self, tab, line: int) -> None:
            try:
                doc = tab.get_document()
                it = doc.get_iter_at_line(max(0, line))
                doc.place_cursor(it)
                view = tab.get_view()
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
                        css = b"textview, textview text, .view, GtkSourceView { background-color: #282C34; } .gutter, GtkSourceGutter { background-color: #21252B; }"
                        prov = Gtk.CssProvider()
                        prov.load_from_data(css)
                        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)  # type: ignore[attr-defined]
                        self._css_provider = prov
                    except Exception:
                        logger.debug("css provider install failed", exc_info=True)
            except Exception:
                logger.debug("apply color scheme failed", exc_info=True)
        def save_tab(self, tab, save_as: bool = False) -> bool:
            if tab is None:
                return False
            doc = tab.get_document()
            loc = doc.get_location() if hasattr(doc, 'get_location') else None
            if loc is None or save_as:
                # Save As — prompt
                dlg = None
                try:
                    dlg = Gtk.FileChooserDialog(title="Save File", transient_for=self, action=Gtk.FileChooserAction.SAVE)  # type: ignore[attr-defined]
                    dlg.set_do_overwrite_confirmation(True)
                    if loc is not None:
                        try:
                            dlg.set_current_name(loc.get_basename() or "Untitled")  # type: ignore[union-attr]
                        except Exception:
                            logger.debug("save dialog name failed", exc_info=True)
                    dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
                    resp = dlg.run()
                    if resp != Gtk.ResponseType.OK:
                        return False
                    filename = dlg.get_filename()
                    if not filename:
                        return False
                    loc = Gio.File.new_for_path(filename)  # type: ignore[union-attr]
                    doc.set_location(loc)  # type: ignore[attr-defined]
                    try:
                        # update language
                        import gi
                        gi.require_version("GtkSource","4")
                        from gi.repository import GtkSource as _GS  # type: ignore
                        lm = _GS.LanguageManager.get_default()
                        lang = lm.guess_language(filename, None)
                        if lang:
                            doc.set_language(lang)  # type: ignore[attr-defined]
                    except Exception:
                        logger.debug("save language guess failed", exc_info=True)
                    # update tab label
                    try:
                        lbl = getattr(tab, "_thor_label", None)
                        if lbl:
                            lbl.set_text(os.path.basename(filename))
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
            # Emit SAVING for plugins like autoreload/git-inline-diff; NORMAL only on success
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
                    tab.set_state(0)  # NORMAL
                    self.emit("active-tab-state-changed", tab)
                    self._update_tab_label(tab)
                    self._update_header()
                except Exception:
                    logger.debug("save NORMAL emit failed", exc_info=True)
            else:
                from .util import doc_path

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
            try:
                mods = event.state & Gtk.accelerator_get_default_mod_mask()  # type: ignore[union-attr]
                keyval = event.keyval
                keyname = (Gdk.keyval_name(keyval) or "").lower()  # type: ignore[union-attr]
                ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)  # type: ignore[union-attr]
                shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)  # type: ignore[union-attr]
                if ctrl and not shift and keyname == "s":
                    self.save_active_tab(save_as=False)
                    return True
                if ctrl and shift and keyname == "s":
                    self.save_active_tab(save_as=True)
                    return True
            except Exception:
                logger.debug("key press save failed", exc_info=True)
            # Let plugins / window handle other shortcuts; keep default propagation
            # Panel-hider: Ctrl+B etc will be handled by plugin signal handlers attached to window.
            # Do not swallow.
            return False

        def _on_delete_event(self, widget, event) -> bool:
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
                resp = dlg.run()
                dlg.destroy()
                return resp != Gtk.ResponseType.OK
            except Exception:
                logger.debug("delete event dialog failed", exc_info=True)
                return False


else:

    class ThorWindow:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass
