# -*- coding: utf-8 -*-
"""Command palette (Ctrl+Shift+P) — VSCode-style command picker."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GObject, Gtk, Gdk, Gio, GLib  # type: ignore
except Exception:  # headless
    GObject = Gtk = Gdk = Gio = GLib = None  # type: ignore

from thor.fuzzy.matcher import fuzzy_match, markup_highlight
from thor.keys import decode_key_event

SETTINGS_FILENAME = "state.toml"

(COL_LABEL, COL_MARKUP) = range(2)


def default_settings_path() -> str:
    """Main config file path (``$XDG_CONFIG_HOME/thor/state.toml``).

    Thor has exactly one user config file: flat keys hold window state,
    TOML sections hold feature settings (see ``thor.state``).
    """
    try:
        from thor import xdg

        return xdg.state_path()
    except Exception:
        return os.path.join(os.path.expanduser("~/.config"), "thor", SETTINGS_FILENAME)


def open_settings(window, path: str | None = None):
    """Open the main config file in *window*'s editor."""
    target = path or default_settings_path()
    try:
        return window.open_file(target, jump_to=True)
    except Exception as e:
        logger.debug(f"open settings failed: {e!r}")
        return None


def get_commands(window) -> list[dict]:
    """Command registry — add new palette items here."""
    try:
        from thor.fonts import choose_all
    except Exception:
        choose_all = None  # type: ignore[assignment]

    def _fonts_command():
        if choose_all is None:
            return None
        return {
            "label": "Select Fonts",
            "detail": "Pick editor, terminal and side panel fonts",
            "run": lambda: choose_all(window),
        }

    commands = [
        {
            "label": "Edit Settings file",
            "detail": "Open the default settings file in the editor",
            "run": lambda: open_settings(window),
        },
    ]
    try:
        _cmd = _fonts_command()
    except Exception:
        _cmd = None
    if _cmd is not None:
        commands.append(_cmd)
    return commands


def filter_commands(query: str, labels: list[str]) -> list[tuple[str, list[int]]]:
    """Fuzzy-filter *labels* by *query*; empty query returns all, no positions."""
    if not (query or "").strip():
        return [(label, []) for label in labels]
    scored: list[tuple[float, str, list[int]]] = []
    for label in labels:
        try:
            hit = fuzzy_match(query, label)
        except Exception:
            hit = None
        if hit is not None:
            scored.append((hit[0], label, list(hit[1])))
    # ponytail: O(n log n) rescore per keystroke, fine for a handful of commands
    scored.sort(key=lambda t: (-t[0], len(t[1]), t[1].lower()))
    return [(label, pos) for _, label, pos in scored]


if Gtk is not None:

    class CommandPaletteDialog(Gtk.Dialog):  # type: ignore[misc]
        __gsignals__ = {
            "activate-command": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
        }

        def __init__(self, parent=None) -> None:
            super().__init__(title="Command Palette")
            self._destroyed = False
            try:
                self.connect("destroy", lambda *_a: setattr(self, "_destroyed", True))
            except Exception:
                pass
            try:
                self.set_modal(True)
            except Exception:
                pass
            if parent is not None:
                try:
                    self.set_transient_for(parent)
                except Exception as e:
                    logger.debug(f"palette transient_for failed: {e!r}")
            self.set_default_size(560, 300)
            self._commands: list[dict] = []
            self._entry = Gtk.Entry()
            try:
                self._entry.set_placeholder_text("Type a command…")
            except Exception:
                pass
            self._entry.connect("changed", lambda _e: self._refilter())
            self._entry.connect("key-press-event", self._on_entry_key)
            self._store = Gtk.ListStore(str, str)
            self._view = Gtk.TreeView.new_with_model(self._store)
            self._view.set_headers_visible(False)
            cell = Gtk.CellRendererText()
            cell.set_property("ellipsize", 2)
            self._view.append_column(Gtk.TreeViewColumn("Command", cell, markup=COL_MARKUP))
            self._view.connect("row-activated", lambda _v, _p, _c: self._activate_selected())
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(self._view)
            area = self.get_content_area()
            area.pack_start(self._entry, False, False, 0)
            area.pack_start(scrolled, True, True, 0)
            self.show_all()

        def set_commands(self, commands: list[dict]) -> None:
            self._commands = list(commands)
            self._refilter()
            try:
                self._entry.grab_focus()
            except Exception:
                pass

        def _refilter(self) -> None:
            if getattr(self, "_destroyed", False):
                return
            try:
                query = self._entry.get_text()
            except Exception:
                return
            try:
                self._store.clear()
            except Exception:
                return
            labels = [c["label"] for c in self._commands]
            for label, positions in filter_commands(query, labels):
                if getattr(self, "_destroyed", False):
                    return
                try:
                    self._store.append([label, markup_highlight(label, positions)])
                except Exception:
                    pass
            self._select_row(0)

        def _select_row(self, index: int) -> None:
            if getattr(self, "_destroyed", False):
                return
            try:
                if len(self._store) == 0:
                    return
                index = max(0, min(len(self._store) - 1, index))
                path = Gtk.TreePath.new_from_indices([index])
                self._view.get_selection().select_path(path)
                self._view.scroll_to_cell(path, None, False, 0, 0)
            except Exception:
                return

        def _selected_label(self) -> str | None:
            if getattr(self, "_destroyed", False):
                return None
            try:
                model, tree_iter = self._view.get_selection().get_selected()
            except Exception:
                return None
            if tree_iter is None:
                try:
                    if len(self._store) == 0:
                        return None
                    tree_iter = self._store.get_iter_first()
                except Exception:
                    return None
            try:
                return model.get_value(tree_iter, COL_LABEL)
            except Exception:
                return None

        def _current_index(self) -> int:
            if getattr(self, "_destroyed", False):
                return 0
            try:
                _model, tree_iter = self._view.get_selection().get_selected()
            except Exception:
                return 0
            if tree_iter is not None:
                try:
                    return self._store.get_path(tree_iter).get_indices()[0]
                except Exception:
                    pass
            return 0

        def _activate_selected(self) -> None:
            label = self._selected_label()
            if label is None:
                return
            try:
                self.emit("activate-command", label)
            except Exception as e:
                logger.debug("palette activate emit failed: %r", e, exc_info=True)

        def _on_entry_key(self, _entry, event) -> bool:
            try:
                name = Gdk.keyval_name(event.keyval) or ""
            except Exception:
                return False
            if name in ("Up", "KP_Up", "Down", "KP_Down"):
                self._select_row(self._current_index() + (-1 if "Up" in name else 1))
                return True
            if name in ("Return", "KP_Enter"):
                self._activate_selected()
                return True
            if name == "Escape":
                self.destroy()
                return True
            return False

else:

    class CommandPaletteDialog:  # type: ignore[no-redef]
        pass


class _PaletteManager:
    def __init__(self, window) -> None:
        self.window = window
        self._window_key_id = None

    def attach(self) -> None:
        if Gtk is None:
            return
        try:
            self._window_key_id = self.window.connect("key-press-event", self._on_window_key_press)
        except Exception as e:
            logger.debug(f"palette keys connect failed: {e!r}")
            self._window_key_id = None

    def detach(self) -> None:
        if self._window_key_id is not None:
            try:
                self.window.disconnect(self._window_key_id)
            except Exception:
                pass
        self._window_key_id = None

    def _handle_global_key(self, keyname: str, ctrl: bool, shift: bool, alt: bool) -> bool:
        if ctrl and shift and not alt and (keyname or "").lower() == "p":
            self._show()
            return True
        return False

    def _on_window_key_press(self, _window, event) -> bool:
        parts = decode_key_event(event)
        if parts is None:
            return False
        keyname, ctrl, shift, alt = parts
        return self._handle_global_key(keyname, ctrl, shift, alt)

    def _show(self) -> None:
        if Gtk is None:
            return
        try:
            dialog = CommandPaletteDialog(parent=self.window)
        except Exception as e:
            logger.debug(f"palette dialog create failed: {e!r}")
            return
        commands = get_commands(self.window)
        dialog.set_commands(commands)
        by_label = {c["label"]: c for c in commands}

        def _on_activate(_w, label: str):
            try:
                by_label[label]["run"]()
            except Exception as e:
                logger.debug(f"palette command {label!r} failed: {e!r}")
            finally:
                try:
                    dialog.destroy()
                except Exception:
                    pass

        dialog.connect("activate-command", _on_activate)
        try:
            dialog.run()
        finally:
            try:
                dialog.destroy()
            except Exception:
                pass


def attach(window) -> _PaletteManager | None:
    """Wire palette to *window*. Idempotent; headless-safe (None without Gtk)."""
    if Gtk is None:
        return None
    existing = getattr(window, "_thor_palette_mgr", None)
    if isinstance(existing, _PaletteManager):
        return existing
    manager = _PaletteManager(window)
    try:
        window._thor_palette_mgr = manager  # type: ignore[attr-defined]
    except Exception:
        pass
    manager.attach()
    return manager


def detach(window) -> None:
    try:
        manager = getattr(window, "_thor_palette_mgr", None)
    except Exception:
        manager = None
    if isinstance(manager, _PaletteManager):
        try:
            manager.detach()
        except Exception:
            pass
        try:
            delattr(window, "_thor_palette_mgr")
        except Exception:
            pass


__all__ = [
    "SETTINGS_FILENAME",
    "CommandPaletteDialog",
    "default_settings_path",
    "open_settings",
    "get_commands",
    "filter_commands",
    "attach",
    "detach",
]
