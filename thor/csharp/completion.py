"""Completion popup (pure Gtk3, no GtkSource version coupling).

Behaves like VSCode's suggest widget:

* The editor keeps keyboard focus; the popup never grabs it. All
  navigation is driven by key forwarding from the editor view.
* Typing filters the list (prefix, then substring, then subsequence —
  ``wrLn`` matches ``WriteLine`` — on filterText, like VSCode).
* Up/Down (wrapping) and PageUp/PageDown move, Home/End jump to
  first/last, Tab/Enter accept, Escape dismisses. Shift+Tab steps back.
* Commit characters accept the current item and then insert normally
  (per-item LSP commitCharacters when present, else the C# defaults).

Shown near the cursor when Roslyn answers a completion request.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import GObject, Gtk, Gdk, GLib  # type: ignore

    _GTK_AVAILABLE = True
except Exception:  # headless
    GObject = Gtk = Gdk = GLib = None  # type: ignore
    _GTK_AVAILABLE = False

from .intelligence import (
    COMMIT_CHARACTERS,
    CompletionItem,
    best_initial_index,
    completion_commit_chars,
    filter_completion,
    xy_of,
)



if not _GTK_AVAILABLE:
    NAV_KEYS = frozenset()
    COMMIT_CHARS = frozenset({".", "(", "[", "<", ";", ","})

    class CompletionPopup:  # type: ignore
        def __init__(self, *a, **kw): pass
        def set_items(self, *a, **kw): pass
        def has_items(self, *a, **kw): return False
        def show_at_view(self, *a, **kw): pass
        def hide(self, *a, **kw): pass
        def get_visible(self, *a, **kw): return False
        def update_filter(self, *a, **kw): pass
        def handle_nav_key(self, *a, **kw): return False
        def selected_commit_chars(self, *a, **kw): return frozenset()
        def activate_selected(self, *a, **kw): pass
        def dismiss(self, *a, **kw): pass
        def connect(self, *a, **kw): return 0
        def destroy(self, *a, **kw): pass
else:
    NAV_KEYS = frozenset(
        {
            "Up", "KP_Up", "Down", "KP_Down",
            "Page_Up", "KP_Page_Up", "Page_Down", "KP_Page_Down",
            "Home", "KP_Home", "End", "KP_End",
            "Return", "KP_Enter", "Tab", "ISO_Left_Tab", "Escape",
        }
    )

    #: Fallback accept-then-insert characters (``Console.Wri`` + ``.`` ->
    #: ``Console.Write.`` + member list refresh). Per-item LSP commitCharacters
    #: win when present; this set only covers items that send none.
    COMMIT_CHARS = frozenset(COMMIT_CHARACTERS)


    class CompletionPopup(Gtk.Window):
        __gsignals__ = {
            "item-activated": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
            "dismissed": (GObject.SignalFlags.RUN_LAST, None, ()),
        }

        def __init__(self) -> None:
            # POPUP = override-redirect: the window manager never gives it
            # keyboard focus, so typing always lands in the editor and filters.
            # (TOPLEVEL + accept_focus=False still gets focused by some WMs.)
            super().__init__(type=Gtk.WindowType.POPUP)
            try:
                self.set_accept_focus(False)
                self.set_focus_on_map(False)
                self.set_can_focus(False)
                self.set_resizable(False)
                self.set_type_hint(Gdk.WindowTypeHint.COMBO)
            except Exception as e:
                logger.debug(f"completion window hints failed: {e!r}")
            self._anchor_view = None
            try:
                self.connect("focus-in-event", self._on_focus_in)
            except Exception as e:
                logger.debug(f"completion focus hook failed: {e!r}")
            self.set_default_size(420, 260)
            self.set_border_width(1)
            self._all_items: list[CompletionItem] = []
            self._items: list[CompletionItem] = []
            self._prefix: str = ""

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.add(box)

            self.store = Gtk.ListStore(str, str, object)
            self.tree = Gtk.TreeView.new_with_model(self.store)
            # No column headers: completion lists show values, not titles.
            self.tree.set_headers_visible(False)
            self.tree.set_can_focus(False)
            for index in (0, 1):
                col = Gtk.TreeViewColumn()
                cell = Gtk.CellRendererText()
                col.pack_start(cell, True)
                col.add_attribute(cell, "text", index)
                self.tree.append_column(col)
            self.tree.connect("row-activated", self._on_row_activated)
            # Button clicks still accept even though keys stay in the editor.
            try:
                self.tree.connect("button-press-event", self._on_button_press)
            except Exception as e:
                logger.debug(f"completion button hook failed: {e!r}")

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            try:
                scrolled.set_can_focus(False)
            except Exception as e:
                logger.debug(f"completion scrolled focus failed: {e!r}")
            scrolled.add(self.tree)
            box.pack_start(scrolled, True, True, 0)

            self.doc_label = Gtk.Label(label="")
            self.doc_label.set_xalign(0.0)
            self.doc_label.set_line_wrap(True)
            self.doc_label.set_max_width_chars(60)
            self.doc_label.set_ellipsize(3)  # END
            try:
                self.doc_label.set_can_focus(False)
            except Exception as e:
                logger.debug(f"completion doc focus failed: {e!r}")
            box.pack_start(self.doc_label, False, False, 0)
            self.tree.get_selection().connect("changed", self._on_selection_changed)

        # -- model -------------------------------------------------------
        def set_items(self, items: list[CompletionItem], prefix: str = "") -> None:
            """Replace the full (unfiltered) result set, then filter by prefix."""
            self._all_items = list(items)
            self.update_filter(prefix)

        def update_filter(self, prefix: str) -> list[CompletionItem]:
            """Narrow the visible list to what matches the typed prefix.

            Returns the filtered items. The server-preselected item wins when
            visible, else the top match is auto-selected so Tab/Enter accepts
            it, like VSCode.
            """
            self._prefix = prefix or ""
            self._items = filter_completion(self._all_items, self._prefix)
            self.store.clear()
            for item in self._items:
                self.store.append([item.label, item.detail, item])
            if self._items:
                initial = best_initial_index(self._items)
                self._select_index(initial)
                self._show_doc(self._items[initial])
            else:
                self._show_doc(None)
            return list(self._items)

        @property
        def all_items(self) -> list[CompletionItem]:
            return list(self._all_items)

        @property
        def current_prefix(self) -> str:
            return self._prefix

        def has_items(self) -> bool:
            return bool(self._items)

        def _show_doc(self, item: CompletionItem | None) -> None:
            try:
                self.doc_label.set_text((item.documentation or item.detail or "")[:500] if item else "")
            except Exception as e:
                logger.debug(f"completion doc show failed: {e!r}")

        def _on_selection_changed(self, selection) -> None:
            try:
                _model, tree_iter = selection.get_selected()
                item = self.store.get_value(tree_iter, 2) if tree_iter else None
                self._show_doc(item)
            except Exception as e:
                logger.debug(f"completion selection failed: {e!r}")

        def _on_button_press(self, _tree, event) -> bool:
            try:
                if event.button == 1 and event.type == Gdk.EventType._2BUTTON_PRESS:
                    self.activate_selected()
                    return True
                if event.button == 1:
                    # Single click just moves the selection; keep the popup open
                    # and focus in the editor (single click does not commit).
                    path_info = self.tree.get_path_at_pos(int(event.x), int(event.y))
                    if path_info:
                        path, _col, _x, _y = path_info
                        indices = path.get_indices() if path is not None else []
                        if indices:
                            self._select_index(indices[0])
                    return True
            except Exception as e:
                logger.debug(f"completion button press failed: {e!r}")
            return False

        def selected_item(self) -> CompletionItem | None:
            if not self._items:
                return None
            try:
                selection = self.tree.get_selection()
                _model, tree_iter = selection.get_selected()
                if tree_iter is None:
                    return self._items[0] if self._items else None
                return self.store.get_value(tree_iter, 2)
            except Exception as e:
                logger.debug(f"completion selected item failed: {e!r}")
                return None

        def _select_index(self, index: int) -> None:
            """Select a row WITHOUT grabbing focus.

            TreeView.set_cursor() moves keyboard focus into the list (stealing
            it from the editor), so selection here uses TreeSelection + scroll
            instead — focus never leaves the editor view.
            """
            try:
                count = len(self._items)
                if count == 0:
                    return
                index = max(0, min(count - 1, index))
                path = Gtk.TreePath.new_from_indices([index])
                self.tree.get_selection().select_path(path)
                self.tree.scroll_to_cell(path, None, False, 0.0, 0.0)
            except Exception as e:
                logger.debug(f"completion select scroll failed: {e!r}")

            try:
                self._show_doc(self._items[index])
            except Exception as e:
                logger.debug(f"completion select doc failed: {e!r}")

        # -- placement ---------------------------------------------------
        def show_at_view(self, view, line0: int, char0: int) -> None:
            # POPUP windows are override-redirect: no transient parent, no
            # keep_above, no present() — none of which apply, and present()
            # would hand keyboard focus to the list.
            placed = False
            try:
                buf = view.get_buffer()
                it = buf.get_iter_at_line_offset(max(0, line0), max(0, char0))
                rect = view.get_iter_location(it)
                bx, by = xy_of(view.buffer_to_window_coords(Gtk.TextWindowType.TEXT, rect.x, rect.y + rect.height))
                win = view.get_window(Gtk.TextWindowType.TEXT)
                ox, oy = xy_of(win.get_origin())
                self.move(ox + bx, oy + by)
                placed = True
            except Exception as e:
                logger.debug(f"completion placement failed: {e!r}")
            if not placed:
                try:
                    _screen, px, py = Gdk.Display.get_default().get_pointer()[:3]
                    self.move(px + 10, py + 10)
                except Exception as e:
                    logger.debug(f"completion pointer fallback failed: {e!r}")
            # NOTE: never call present()/present_with_time() here:
            # gtk_window_present() explicitly moves keyboard focus to the popup,
            # which steals keystrokes from the editor so typing can't filter.
            # show_all() maps the override-redirect window in place; focus never
            # leaves the editor (selection uses select_path, never set_cursor).
            self._anchor_view = view
            self.show_all()
            # IMMEDIATE grab + idle regrab: show_all() maps the override-redirect
            # window asynchronously and some WMs/X servers move input focus to the
            # newly mapped window after grab_focus() returns. The idle ensures the
            # editor regains focus once the map is processed. Keep both for
            # robustness on real displays (xfwm, mutter) and Xvfb (no WM).
            try:
                view.grab_focus()
            except Exception as e:
                logger.debug(f"completion grab focus failed: {e!r}")
            try:
                GLib.idle_add(self._refocus_editor)
            except Exception as e:
                logger.debug(f"completion refocus schedule failed: {e!r}")
        def _refocus_editor(self) -> bool:
            """One-shot idle callback: hand focus back to the editor view."""
            try:
                view = self._anchor_view
                if view is not None:
                    # Re-present the editor's toplevel so win.is_active stays True;
                    # POPUP is override-redirect and stays above even when its
                    # transient parent is presented. This handles WMs that make the
                    # popup the active window despite accept_focus=False.
                    try:
                        toplevel = view.get_toplevel()
                        if isinstance(toplevel, Gtk.Window):
                            toplevel.present()
                    except Exception as e:
                        logger.debug(f"completion toplevel present failed: {e!r}")
                    view.grab_focus()
            except Exception as e:
                logger.debug(f"completion refocus failed: {e!r}")
            return False

        def _on_focus_in(self, _window, _event) -> bool:
            """Defensive: some WMs focus the popup anyway; bounce it back."""
            try:
                GLib.idle_add(self._refocus_editor)
            except Exception as e:
                logger.debug(f"completion focus-in refocus failed: {e!r}")
            return False

        # -- events ------------------------------------------------------
        def _on_row_activated(self, _tree, _path, _col) -> None:
            self.activate_selected()

        def _on_key_press(self, _widget, event) -> bool:
            # Only reachable if some WM focuses the popup anyway; keep it
            # consistent with view-level forwarding. Unhandled keys bounce
            # focus back so the next keystroke lands in the editor.
            try:
                name = Gdk.keyval_name(event.keyval) or ""
            except Exception:
                return False
            handled = self.handle_nav_key(name)
            if not handled:
                try:
                    GLib.idle_add(self._refocus_editor)
                except Exception as e:
                    logger.debug(f"completion key refocus failed: {e!r}")
            return handled

        # -- programmatic navigation (driven by view key forwarding) --
        def move_selection(self, delta: int) -> bool:
            """Move the selection, wrapping at the ends like VSCode."""
            try:
                count = len(self._items)
                if count == 0:
                    return False
                index = 0
                _model, tree_iter = self.tree.get_selection().get_selected()
                if tree_iter is not None:
                    path = self.store.get_path(tree_iter)
                    indices = path.get_indices() if path is not None else []
                    if indices:
                        index = indices[0]
                index = (index + delta) % count
                self._select_index(index)
                return True
            except Exception as e:
                logger.debug(f"completion move selection failed: {e!r}")
                return False

        def select_first(self) -> bool:
            if not self._items:
                return False
            self._select_index(0)
            return True

        def select_last(self) -> bool:
            if not self._items:
                return False
            self._select_index(len(self._items) - 1)
            return True

        def selected_commit_chars(self) -> frozenset:
            """Commit chars for the selected row (per-item LSP wins)."""
            try:
                item = self.selected_item()
            except Exception as e:
                logger.debug(f"completion selected for commits failed: {e!r}")
                item = None
            if item is not None:
                try:
                    return frozenset(completion_commit_chars(item))
                except Exception as e:
                    logger.debug(f"completion commit chars failed: {e!r}")
            return COMMIT_CHARS

        def activate_selected(self) -> bool:
            item = self.selected_item()
            if item is None:
                return False
            self.emit("item-activated", item)
            return True

        def handle_nav_key(self, name: str) -> bool:
            """Handle one navigation key. Returns True if consumed."""
            if name in ("Up", "KP_Up"):
                return self.move_selection(-1)
            if name in ("Down", "KP_Down"):
                return self.move_selection(1)
            if name in ("Page_Up", "KP_Page_Up"):
                return self.move_selection(-10)
            if name in ("Page_Down", "KP_Page_Down"):
                return self.move_selection(10)
            if name in ("Home", "KP_Home"):
                return self.select_first()
            if name in ("End", "KP_End"):
                return self.select_last()
            if name == "ISO_Left_Tab":
                # Shift+Tab steps to the previous item instead of accepting.
                return self.move_selection(-1)
            if name in ("Return", "KP_Enter", "Tab"):
                return self.activate_selected()
            if name == "Escape":
                self.dismiss()
                return True
            return False

        def dismiss(self) -> None:
            try:
                self.hide()
            except Exception as e:
                logger.debug(f"completion hide failed: {e!r}")
            self.emit("dismissed")
