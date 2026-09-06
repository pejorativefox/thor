# -*- coding: utf-8 -*-
"""ThorPanel — Side/bottom container."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GObject, Gtk  # type: ignore
except Exception:  # headless tests
    GObject = Gtk = None  # type: ignore

if Gtk is not None:

    class ThorPanel(Gtk.Box):
        """Drop-in panel.

        Backed by Gtk.Notebook (label + icon per page). Mirrors:
          thor_panel_add_item(panel, widget, name, icon_name)
          thor_panel_remove_item(panel, widget)
          thor_panel_activate_item(panel, widget)
          thor_panel_item_is_active(panel, widget)
          thor_panel_get_n_items(panel)
          thor_panel_get_orientation(panel)
        """

        __gtype_name__ = "ThorPanel"

        def __init__(self, orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL) -> None:  # type: ignore[name-defined]
            super().__init__(orientation=orientation, spacing=0)
            self._orientation = orientation
            self._notebook = Gtk.Notebook()
            self._notebook.set_scrollable(True)
            self._notebook.set_show_border(False)
            # Hide tabs when empty — mimic panel auto-hide
            try:
                self._notebook.set_show_tabs(True)
            except Exception:
                logger.debug("set_show_tabs failed", exc_info=True)
            self._target_visible = True
            self.pack_start(self._notebook, True, True, 0)
            self.show_all()
            # Start hidden when empty
            self.set_no_show_all(True)
            self.hide()

        # -- ThorPanel API --

        def add_item(self, widget: Gtk.Widget, name: str, icon_name: str) -> None:  # type: ignore[override]
            # Wrap widget in a labeled tab
            label = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            try:
                icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            except Exception:
                icon = Gtk.Image.new_from_icon_name("text-x-generic", Gtk.IconSize.MENU)
            lbl = Gtk.Label(label=name)
            lbl.set_xalign(0.0)
            label.pack_start(icon, False, False, 0)
            label.pack_start(lbl, False, False, 0)
            label.show_all()
            self._notebook.append_page(widget, label)
            widget.show_all()
            self._notebook.set_tab_reorderable(widget, True)
            self._notebook.show()
            # Auto-activate first item
            if self._notebook.get_n_pages() == 1:
                self._notebook.set_current_page(0)
            self._sync_visibility()

        def remove_item(self, widget: Gtk.Widget) -> bool:  # type: ignore[override]
            n = self._notebook.get_n_pages()
            for i in range(n):
                if self._notebook.get_nth_page(i) is widget:
                    self._notebook.remove_page(i)
                    self._sync_visibility()
                    return True
            return False

        def activate_item(self, widget: Gtk.Widget) -> bool:
            n = self._notebook.get_n_pages()
            for i in range(n):
                if self._notebook.get_nth_page(i) is widget:
                    self._notebook.set_current_page(i)
                    return True
            return False

        def item_is_active(self, widget: Gtk.Widget) -> bool:
            cur = self._notebook.get_current_page()
            if cur < 0:
                return False
            return self._notebook.get_nth_page(cur) is widget

        def get_n_items(self) -> int:
            return self._notebook.get_n_pages()

        def get_orientation(self) -> Gtk.Orientation:
            return self._orientation

        def set_target_visible(self, visible: bool) -> None:
            self._target_visible = bool(visible)
            self._sync_visibility()

        def get_target_visible(self) -> bool:
            return bool(getattr(self, "_target_visible", True))

        def _sync_visibility(self) -> None:
            if self.get_n_items() == 0 or not getattr(self, "_target_visible", True):
                self.hide()
            else:
                self.show()
                self._notebook.show()

        # Compatibility shims (functions call these)
        def get_n_pages(self):  # for debugging
            return self.get_n_items()

else:

    class ThorPanel:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass

        def add_item(self, *a, **kw):
            return None

        def remove_item(self, *a, **kw):
            return False

        def activate_item(self, *a, **kw):
            return False

        def item_is_active(self, *a, **kw):
            return False

        def get_n_items(self):
            return 0

        def get_orientation(self):
            return 0
