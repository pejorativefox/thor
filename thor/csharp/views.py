"""Per-view integration: doc sync, shortcuts, context menu, hover, completion triggers.

GTK-only; imported lazily by __init__. All LSP round-trips are delegated to
the plugin via GObject signals so this module never touches the network.
"""

from __future__ import annotations

import logging
import os

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
    is_identifier_char,
    offset_to_position,
    prefix_at,
    should_trigger_completion,
    xy_of,
)

def doc_path(doc) -> str | None:
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


def is_csharp_doc(doc) -> bool:
    path = doc_path(doc)
    return bool(path and path.endswith(".cs"))

try:
    from thor.util import is_save_completed, tab_state_name
except Exception:  # headless fallback
    def tab_state_name(state) -> str:  # type: ignore[no-redef]
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
            num = int(state)
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
        try:
            prev = tab_state_name(previous).upper()
            cur = tab_state_name(current).upper()
        except Exception:
            return False
        return "SAVING" in prev and "ERROR" not in prev and cur.endswith("NORMAL")

def buffer_text(doc) -> str:
    try:
        start, end = doc.get_bounds()
        return doc.get_text(start, end, True)
    except Exception:
        return ""

def cursor_offset(doc) -> int:
    try:
        return doc.get_iter_at_mark(doc.get_insert()).get_offset()
    except Exception:
        return 0

def cursor_line0(doc) -> int:
    try:
        return doc.get_iter_at_mark(doc.get_insert()).get_line()
    except Exception:
        return 0

if not _GTK_AVAILABLE:
    class ViewTracker:  # type: ignore
        def __init__(self, *a, **kw): pass
        def attach(self, *a, **kw): pass
        def detach(self, *a, **kw): pass
        def connect(self, *a, **kw): return 0
        def show_hover_text(self, *a, **kw): pass
        framework_completion = False
        __gsignals__ = {}
else:
    class ViewTracker(GObject.Object):
        __gsignals__ = {
            "doc-changed": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "doc-saved": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "doc-closed": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "completion-request": (
                GObject.SignalFlags.RUN_LAST,
                None,
                (GObject.TYPE_STRING, GObject.TYPE_INT, GObject.TYPE_INT, GObject.TYPE_STRING),
            ),
            "goto-definition": (
                GObject.SignalFlags.RUN_LAST,
                None,
                (GObject.TYPE_STRING, GObject.TYPE_INT, GObject.TYPE_INT),
            ),
            "find-references": (
                GObject.SignalFlags.RUN_LAST,
                None,
                (GObject.TYPE_STRING, GObject.TYPE_INT, GObject.TYPE_INT),
            ),
            "hover-request": (
                GObject.SignalFlags.RUN_LAST,
                None,
                (GObject.TYPE_STRING, GObject.TYPE_INT, GObject.TYPE_INT),
            ),
            "format-request": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "code-action-request": (
                GObject.SignalFlags.RUN_LAST,
                None,
                (GObject.TYPE_STRING, GObject.TYPE_INT, GObject.TYPE_INT),
            ),
        }

        def __init__(self) -> None:
            super().__init__()
            self._window = None
            self.framework_completion = False
            self._tracked: dict[int, dict] = {}
            self._change_sources: dict[int, int] = {}
            self._tab_states: dict[int, str] = {}
            self._pending_tooltip = None

        # -- lifecycle ---------------------------------------------------
        def attach(self, window) -> None:
            self._window = window
            try:
                for view in window.get_views():
                    self._track_view(view)
            except Exception as e:
                logger.debug(f"ViewTracker initial views failed: {e!r}")
            for signal, handler in (
                ("tab-added", self._on_tab_added),
                ("tab-removed", self._on_tab_removed),
                ("active-tab-changed", self._on_active_tab_changed),
                ("active-tab-state-changed", self._on_tab_state_changed),
            ):
                try:
                    window.connect(signal, handler)
                except Exception as e:
                    logger.debug(f"ViewTracker connect {signal} failed: {e!r}")

        def detach(self) -> None:
            for record in list(self._tracked.values()):
                self._untrack_record(record)
            self._tracked.clear()
            self._change_sources.clear()
            self._tab_states.clear()
            self._window = None

        # -- view tracking -----------------------------------------------
        def _track_view(self, view) -> None:
            try:
                doc = view.get_buffer()
            except Exception:
                return
            key = hash(doc)
            if key in self._tracked:
                return
            record = {"view": view, "doc": doc, "ids": []}

            def _connect(obj, signal, handler):
                try:
                    record["ids"].append((obj, obj.connect(signal, handler)))
                except Exception as e:
                    logger.debug(f"ViewTracker connect {signal} failed: {e!r}")

            _connect(doc, "changed", lambda _b: self._on_buffer_changed(doc))
            _connect(view, "key-press-event", lambda v, e: self._on_key_press(v, e, doc))
            _connect(view, "key-release-event", lambda v, e: self._on_key_release(v, e, doc))
            _connect(view, "populate-popup", lambda v, m: self._on_populate_popup(v, m, doc))
            try:
                view.set_has_tooltip(True)
            except Exception:
                pass
            _connect(view, "query-tooltip", lambda v, x, y, kb, t: self._on_query_tooltip(v, x, y, kb, t, doc))
            self._tracked[key] = record

        def _untrack_record(self, record: dict) -> None:
            for obj, handler_id in record.get("ids", []):
                try:
                    obj.disconnect(handler_id)
                except Exception:
                    pass

        def _on_tab_added(self, _window, *args) -> None:
            tab = args[0] if args else None
            if tab is None:
                return
            try:
                self._track_view(tab.get_view())
            except Exception as e:
                logger.debug(f"tab-added track failed: {e!r}")

        def _on_tab_removed(self, _window, *args) -> None:
            tab = args[0] if args else None
            if tab is None:
                return
            try:
                doc = tab.get_document()
            except Exception:
                return
            path = doc_path(doc)
            key = hash(doc)
            record = self._tracked.pop(key, None)
            if record is not None:
                self._untrack_record(record)
            if path:
                self.emit("doc-closed", path)

        def _on_active_tab_changed(self, window, *_args) -> None:
            try:
                view = window.get_active_view()
                if view is not None:
                    self._track_view(view)
            except Exception:
                pass

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
                    self.emit("doc-saved", path)

        # -- buffer changes ----------------------------------------------
        def _on_buffer_changed(self, doc) -> None:
            if not is_csharp_doc(doc):
                return
            path = doc_path(doc)
            if not path:
                return
            key = hash(doc)
            old = self._change_sources.pop(key, None)
            if old is not None:
                try:
                    GLib.source_remove(old)
                except Exception:
                    pass
            try:
                self._change_sources[key] = GLib.timeout_add(400, self._emit_changed, key, path)
            except Exception:
                self.emit("doc-changed", path)

        def _emit_changed(self, key: int, path: str) -> bool:
            self._change_sources.pop(key, None)
            self.emit("doc-changed", path)
            return False

        # -- keyboard ----------------------------------------------------
        def _cursor_lsp(self, doc) -> tuple[int, int]:
            return offset_to_position(buffer_text(doc), cursor_offset(doc))

        def _on_key_press(self, view, event, doc) -> bool:
            try:
                mods = event.state & Gtk.accelerator_get_default_mod_mask()
                keyval = event.keyval
                keyname = Gdk.keyval_name(keyval) or ""
                ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)
                shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
                alt = bool(mods & Gdk.ModifierType.MOD1_MASK)
            except Exception:
                return False
            if not is_csharp_doc(doc):
                return False
            path = doc_path(doc)
            if not path:
                return False
            if ctrl or keyname == "F12" or keyname.lower() == "space":
                logger.debug(f"key: name={keyname} ctrl={ctrl} shift={shift} alt={alt} "
                      f"path={path} framework={self.framework_completion}")
            line, char = self._cursor_lsp(doc)
            plain = mods in (0, Gdk.ModifierType.SHIFT_MASK)
            if keyname == "F12" and plain:
                if shift:
                    self.emit("find-references", path, line, char)
                else:
                    self.emit("goto-definition", path, line, char)
                return True
            if ctrl and not shift and not alt and keyname.lower() == "space":
                if self.framework_completion:
                    # GtkSourceView binds Ctrl+Space to its own show-completion
                    # signal, which creates a framework-owned context and drives
                    # every registered provider (ours included). Let the binding
                    # run: consuming the key here would suppress the native flow
                    # that add_proposals requires (foreign contexts are rejected).
                    logger.debug(f"completion invoke passthrough path={path} (native binding)")
                    return False
                self.emit("completion-request", path, line, char, "invoke")
                return True
            if alt and not ctrl and keyname.lower() in ("return", "enter", "kp_enter"):
                self.emit("code-action-request", path, line, char)
                return True
            if shift and alt and not ctrl and keyname.lower() == "f":
                self.emit("format-request", path)
                return True
            return False

        def _on_key_release(self, _view, event, doc) -> bool:
            if not is_csharp_doc(doc):
                return False
            if self.framework_completion:
                # GtkSource interactive activation drives populate/filtering
                # itself (like wordcompletion + VSCode). Manual emissions here
                # only caused duplicate requests and stale (path,line) cache
                # warming — stay out of the way.
                return False
            try:
                mods = event.state & Gtk.accelerator_get_default_mod_mask()
                if mods & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
                    return False
                typed = Gdk.keyval_to_unicode(event.keyval)
                char = chr(typed) if typed else ""
            except Exception:
                return False
            if not char:
                return False
            path = doc_path(doc)
            if not path:
                return False
            line, charpos = self._cursor_lsp(doc)
            if should_trigger_completion(char):
                # '.', '(' etc: member-access triggers always fire.
                logger.debug(f"completion trigger char={char!r} path={path} line={line} char={charpos} "
                      f"framework={self.framework_completion}")
                self.emit("completion-request", path, line, charpos, char)
                return False
            if is_identifier_char(char):
                # Auto-popup while typing words, like VSCode: from the first
                # character. The plugin refilters locally when visible and
                # requests once when hidden.
                try:
                    prefix, _start = prefix_at(buffer_text(doc), cursor_offset(doc))
                except Exception:
                    prefix = char
                if len(prefix) >= 1:
                    logger.debug(f"completion auto path={path} line={line} char={charpos} prefix={prefix!r}")
                    self.emit("completion-request", path, line, charpos, "auto:" + prefix)

        # -- context menu -------------------------------------------------
        def _on_populate_popup(self, _view, menu, doc) -> None:
            if not is_csharp_doc(doc):
                return
            path = doc_path(doc)
            if not path:
                return
            try:
                line, char = self._cursor_lsp(doc)
                sep = Gtk.SeparatorMenuItem()
                menu.append(sep)
                entries = (
                    ("Go to Definition  (F12)", "goto-definition", (path, line, char)),
                    ("Find References  (Shift+F12)", "find-references", (path, line, char)),
                    ("Quick Fix…  (Alt+Enter)", "code-action-request", (path, line, char)),
                    ("Format Document", "format-request", (path,)),
                )
                for label, signal, args in entries:
                    item = Gtk.MenuItem.new_with_label(label)
                    item.connect("activate", lambda _i, s=signal, a=args: self.emit(s, *a))
                    menu.append(item)
                menu.show_all()
            except Exception as e:
                logger.debug(f"populate-popup failed: {e!r}")

        # -- hover --------------------------------------------------------
        def _on_query_tooltip(self, view, x, y, _keyboard, tooltip, doc) -> bool:
            if not is_csharp_doc(doc):
                return False
            try:
                bx, by = xy_of(view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, x, y))
                it = view.get_iter_at_location(bx, by)
                if it is None:
                    return False
                if isinstance(it, tuple):
                    it = it[0] if it[0] is not None else None
                if it is None:
                    return False
                text = buffer_text(doc)
                line, char = offset_to_position(text, it.get_offset())
            except Exception:
                return False
            path = doc_path(doc)
            if not path:
                return False
            self._pending_tooltip = tooltip
            try:
                tooltip.set_text("Loading…")
            except Exception:
                pass
            self.emit("hover-request", path, line, char)
            return True

        def show_hover_text(self, text: str) -> None:
            tooltip = self._pending_tooltip
            self._pending_tooltip = None
            if tooltip is None:
                return
            try:
                if text:
                    tooltip.set_text(text)
                else:
                    tooltip.set_text("No documentation available.")
            except Exception:
                pass
