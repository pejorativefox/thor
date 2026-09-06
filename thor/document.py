# -*- coding: utf-8 -*-
"""ThorDocument / ThorView / ThorTab — document/view/tab."""

from __future__ import annotations

import os
import tempfile


try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkSource", "4")
    from gi.repository import GObject, Gtk, Gio, GtkSource  # type: ignore
except Exception:  # headless
    GObject = Gtk = Gio = GtkSource = None  # type: ignore

# ---------------------------------------------------------------------------
# Document — GtkSource.Buffer subclass with ThorDocument API
# ---------------------------------------------------------------------------
if GtkSource is not None:

    class ThorDocument(GtkSource.Buffer):  # type: ignore[misc]
        __gtype_name__ = "ThorDocument"

        def __init__(self, location: Gio.File | None = None) -> None:  # type: ignore[name-defined]
            super().__init__()
            self._thor_location: Gio.File | None = location  # type: ignore[assignment]
            self._thor_file = GtkSource.File()
            if location is not None:
                try:
                    self._thor_file.set_location(location)  # type: ignore[attr-defined]
                except Exception:
                    pass
            self._thor_untouched = True

        # ThorDocument API used by features
        def get_location(self):  # type: ignore[override]
            return self._thor_location

        def set_location(self, loc):  # type: ignore[override]
            self._thor_location = loc
            try:
                self._thor_file.set_location(loc)  # type: ignore[attr-defined]
            except Exception:
                pass

        def get_file(self):  # type: ignore[override]
            return self._thor_file

        def get_uri_for_display(self) -> str | None:
            loc = self._thor_location
            if loc is None:
                return None
            try:
                return loc.get_uri()
            except Exception:
                return None

        def get_short_name_for_display(self) -> str:
            loc = self._thor_location
            if loc is not None:
                try:
                    return loc.get_basename() or "Untitled"
                except Exception:
                    pass
            return "Untitled"

        def is_untitled(self) -> bool:
            return self._thor_location is None

        def is_untouched(self) -> bool:
            return self._thor_untouched

        def is_local(self) -> bool:
            loc = self._thor_location
            if loc is None:
                return False
            try:
                return loc.is_native()
            except Exception:
                return True

        def get_deleted(self) -> bool:
            return False

        def get_readonly(self) -> bool:
            return False

        def goto_line(self, line: int) -> bool:
            try:
                it = self.get_iter_at_line(max(0, line))
                self.place_cursor(it)
                return True
            except Exception:
                return False

        def goto_line_offset(self, line: int, offset: int) -> bool:
            try:
                it = self.get_iter_at_line_offset(max(0, line), max(0, offset))
                self.place_cursor(it)
                return True
            except Exception:
                return False

        def set_language(self, lang):  # type: ignore[override]
            try:
                super().set_language(lang)
            except Exception:
                pass

        def get_language(self):  # type: ignore[override]
            try:
                return super().get_language()
            except Exception:
                return None

        def save(self) -> bool:
            loc = self._thor_location
            if loc is None:
                return False
            try:
                start, end = self.get_bounds()
                text = self.get_text(start, end, True)
            except Exception:
                text = ""
            try:
                path = loc.get_path()  # type: ignore[union-attr]
                if path:
                    dir_name = os.path.dirname(path) or "."
                    fd, tmp = tempfile.mkstemp(dir=dir_name)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(text)
                            f.flush()
                            try:
                                os.fsync(f.fileno())
                            except Exception:
                                pass
                        os.replace(tmp, path)
                    except Exception:
                        try:
                            os.unlink(tmp)
                        except Exception:
                            pass
                        raise
                else:
                    # Gio fallback (e.g. remote)
                    try:
                        loc.replace_contents(text.encode("utf-8"), None, False, Gio.FileCreateFlags.NONE, None)  # type: ignore[attr-defined, union-attr]
                    except Exception:
                        return False
                try:
                    self.set_modified(False)
                except Exception:
                    pass
                self._thor_untouched = False
                return True
            except Exception:
                return False

        # track untouched flag
        def set_text(self, text, *args, **kwargs):  # type: ignore[override]
            super().set_text(text, *args, **kwargs)
            self._thor_untouched = False

else:

    class ThorDocument:  # type: ignore[no-redef]
        def __init__(self, location=None):
            self._thor_location = location
            self._text = ""
            self._thor_untouched = True
            self._modified = False

        def get_location(self):
            return self._thor_location

        def set_location(self, loc):
            self._thor_location = loc

        def get_file(self):
            class _F:
                def get_location(self_inner):
                    return self._thor_location

            return _F()

        def get_uri_for_display(self):
            loc = self._thor_location
            if loc is None:
                return None
            try:
                return loc.get_uri() if hasattr(loc, "get_uri") else None  # type: ignore[union-attr]
            except Exception:
                return None

        def get_short_name_for_display(self):
            loc = self._thor_location
            if loc is not None:
                try:
                    return loc.get_basename() or "Untitled"  # type: ignore[union-attr]
                except Exception:
                    pass
            return "Untitled"

        def is_untitled(self):
            return self._thor_location is None

        def is_untouched(self):
            return bool(self._thor_untouched)

        def is_local(self):
            loc = self._thor_location
            if loc is None:
                return False
            try:
                return bool(loc.is_native()) if hasattr(loc, "is_native") else True  # type: ignore[union-attr]
            except Exception:
                return True

        def get_deleted(self) -> bool:
            return False

        def get_readonly(self) -> bool:
            return False

        def get_language(self):
            return None

        def set_language(self, lang) -> None:
            pass

        def goto_line(self, line: int) -> bool:
            return False

        def goto_line_offset(self, line: int, offset: int) -> bool:
            return False

        def get_bounds(self):
            return (None, None)

        def get_text(self, *a, **kw):
            return self._text

        def set_text(self, t, *a, **kw):
            self._text = t
            self._thor_untouched = False
            self._modified = True

        def get_modified(self):
            return bool(self._modified)

        def set_modified(self, v):
            self._modified = bool(v)

        def save(self) -> bool:
            try:
                loc = self._thor_location
                if loc is None:
                    return False
                path = loc.get_path() if hasattr(loc, "get_path") else None  # type: ignore[union-attr]
                if path:
                    dir_name = os.path.dirname(path) or "."
                    fd, tmp = tempfile.mkstemp(dir=dir_name)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(self._text)
                            f.flush()
                            try:
                                os.fsync(f.fileno())
                            except Exception:
                                pass
                        os.replace(tmp, path)
                    except Exception:
                        try:
                            os.unlink(tmp)
                        except Exception:
                            pass
                        return False
                    self._modified = False
                    self._thor_untouched = False
                    return True
            except Exception:
                pass
            return False

# ---------------------------------------------------------------------------
# View — thin GtkSource.View subclass
# ---------------------------------------------------------------------------
if GtkSource is not None and Gtk is not None:

    class ThorView(GtkSource.View):  # type: ignore[misc]
        __gtype_name__ = "ThorView"

        @classmethod
        def new_with_buffer(cls, buf: ThorDocument) -> "ThorView":  # type: ignore[override]
            v = cls()
            try:
                v.set_buffer(buf)
            except Exception:
                pass
            # sane defaults
            try:
                v.set_show_line_numbers(True)
                v.set_highlight_current_line(True)
                v.set_auto_indent(True)
                v.set_indent_on_tab(True)
                v.set_tab_width(4)
                v.set_insert_spaces_instead_of_tabs(True)
                v.set_show_right_margin(False)
                v.set_monospace(True)
            except Exception:
                pass
            return v

else:

    class ThorView:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass

        @classmethod
        def new_with_buffer(cls, buf):
            return cls()

# ---------------------------------------------------------------------------
# Tab — Gtk.Box containing scrolled ThorView + ThorDocument
# ---------------------------------------------------------------------------
if Gtk is not None and GtkSource is not None:

    class ThorTab(Gtk.Box):  # type: ignore[misc]
        __gtype_name__ = "ThorTab"

        def __init__(self, document: ThorDocument | None = None, location: Gio.File | None = None) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            if document is None:
                document = ThorDocument(location=location)
            self._document = document
            self._view = ThorView.new_with_buffer(document)
            self._state = 0  # THOR_TAB_STATE_NORMAL
            # Wrap view in scrolled window (avoid double-add critical: view may already be parented)
            self._scrolled = Gtk.ScrolledWindow()
            self._scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            try:
                if self._view.get_parent() is None and self._scrolled.get_child() is None:
                    self._scrolled.add(self._view)
                elif self._view.get_parent() is None:
                    # Scrolled already has a child (should not happen) — remove first
                    try:
                        old = self._scrolled.get_child()
                        if old is not None:
                            self._scrolled.remove(old)
                    except Exception:
                        pass
                    self._scrolled.add(self._view)
            except Exception:
                try:
                    # Fallback: ensure view is parented somewhere
                    if self._view.get_parent() is None:
                        self._scrolled.add(self._view)
                except Exception:
                    pass
            self.pack_start(self._scrolled, True, True, 0)
            # Track modified for title updates
            try:
                self._document.connect("modified-changed", lambda *_: self._on_modified_changed())
            except Exception:
                pass
            self.show_all()

        def get_view(self):
            return self._view

        def get_document(self):
            return self._document

        def get_state(self) -> int:
            return self._state

        def set_state(self, v: int) -> None:
            self._state = v

        def get_auto_save_enabled(self) -> bool:
            return False

        def set_auto_save_enabled(self, v: bool) -> None:
            pass

        def get_auto_save_interval(self) -> int:
            return 0

        def set_auto_save_interval(self, v: int) -> None:
            pass

        def set_info_bar(self, bar) -> None:
            # Tabs can show infobar; Thor ignores
            pass

        def save(self) -> bool:
            try:
                return bool(self._document.save())  # type: ignore[attr-defined]
            except Exception:
                return False

        def _on_modified_changed(self) -> None:
            # placeholder — window will connect to update title
            pass

        def load_location(self, location: Gio.File) -> None:
            """Synchronous load (MVP). Async FileLoader for later."""
            self._document._thor_location = location  # type: ignore[attr-defined]
            try:
                self._document._thor_file.set_location(location)  # type: ignore[attr-defined]
            except Exception:
                pass
            # Language from filename
            try:
                lm = GtkSource.LanguageManager.get_default()
                lang = lm.guess_language(location.get_basename(), None)  # type: ignore[union-attr]
                if lang is not None:
                    self._document.set_language(lang)
            except Exception:
                pass
            # Load bytes synchronously
            try:
                ok, contents, etag = location.load_contents(None)  # type: ignore[attr-defined]
                text = contents.decode("utf-8", errors="replace") if ok else ""
            except Exception:
                try:
                    path = location.get_path()
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except Exception:
                    text = ""
            try:
                self._document.begin_not_undoable_action()
                self._document.set_text(text)
                self._document.end_not_undoable_action()
                self._document.set_modified(False)
            except Exception:
                pass
            self._document._thor_untouched = False  # type: ignore[attr-defined]

else:

    class ThorTab:  # type: ignore[no-redef]
        def __init__(self, document=None, location=None):
            if document is None and location is not None:
                try:
                    document = ThorDocument(location=location)
                except Exception:
                    document = None
            self._document = document
            self._view = None
            self._state = 0

        def get_view(self):
            return self._view

        def get_document(self):
            return self._document

        def get_state(self):
            return getattr(self, '_state', 0)

        def set_state(self, v):
            self._state = v

        def get_auto_save_enabled(self) -> bool:
            return False

        def set_auto_save_enabled(self, v: bool) -> None:
            pass

        def get_auto_save_interval(self) -> int:
            return 0

        def set_auto_save_interval(self, v: int) -> None:
            pass

        def save(self):
            try:
                return bool(self._document.save()) if self._document else False
            except Exception:
                return False

        def set_info_bar(self, bar):
            pass

        def load_location(self, location) -> None:
            try:
                if self._document is not None:
                    self._document._thor_location = location  # type: ignore[attr-defined]
                    try:
                        self._document._thor_file.set_location(location)  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    try:
                        path = location.get_path() if hasattr(location, "get_path") else None  # type: ignore[union-attr]
                        if path and os.path.isfile(path):
                            with open(path, "r", encoding="utf-8", errors="replace") as f:
                                txt = f.read()
                            self._document.set_text(txt)  # type: ignore[union-attr]
                            try:
                                self._document.set_modified(False)  # type: ignore[union-attr]
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        self._document._thor_untouched = False  # type: ignore[attr-defined]
                    except Exception:
                        pass
            except Exception:
                pass
