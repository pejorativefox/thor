# -*- coding: utf-8 -*-
"""ThorDocument / ThorView / ThorTab — document/view/tab."""

from __future__ import annotations

import errno
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


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
                    logger.debug("ThorDocument init: set_location failed", exc_info=True)
            self._thor_untouched = True

        # ThorDocument API used by features
        def get_location(self):  # type: ignore[override]
            return self._thor_location

        def set_location(self, loc):  # type: ignore[override]
            self._thor_location = loc
            try:
                self._thor_file.set_location(loc)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("set_location failed", exc_info=True)

        def get_file(self):  # type: ignore[override]
            return self._thor_file

        def get_uri_for_display(self) -> str | None:
            loc = self._thor_location
            if loc is None:
                return None
            try:
                return loc.get_uri()
            except Exception:
                logger.debug("get_uri_for_display failed", exc_info=True)
                return None

        def get_short_name_for_display(self) -> str:
            loc = self._thor_location
            if loc is not None:
                try:
                    return loc.get_basename() or "Untitled"
                except Exception:
                    logger.debug("get_short_name_for_display failed", exc_info=True)
            return "Untitled"

        def is_untitled(self) -> bool:
            return self._thor_location is None

        def is_untouched(self) -> bool:
            return bool(getattr(self, "_thor_untouched", True))

        def is_local(self) -> bool:
            loc = self._thor_location
            if loc is None:
                return False
            try:
                return loc.is_native()
            except Exception:
                logger.debug("is_local query failed", exc_info=True)
                return True

        def get_deleted(self) -> bool:
            loc = self._thor_location
            if loc is None:
                return False
            try:
                return not loc.query_exists(None)
            except Exception:
                logger.debug("get_deleted query failed", exc_info=True)
                return False

        def get_readonly(self) -> bool:
            loc = self._thor_location
            if loc is None:
                return False
            try:
                info = loc.query_info("access::read-only", Gio.FileQueryInfoFlags.NONE, None)
                return bool(info.get_attribute_boolean("access::read-only"))
            except Exception:
                logger.debug("get_readonly query failed", exc_info=True)
                return False

        def goto_line(self, line: int) -> bool:
            try:
                count = self.get_line_count()
                clamped = min(max(0, line), max(0, count - 1))
                if clamped != line:
                    logger.debug("goto_line %d out of range (0..%d), clamped", line, max(0, count - 1))
                it = self.get_iter_at_line(clamped)
                self.place_cursor(it)
                return True
            except Exception:
                logger.debug("goto_line failed", exc_info=True)
                return False

        def goto_line_offset(self, line: int, offset: int) -> bool:
            try:
                count = self.get_line_count()
                clamped = min(max(0, line), max(0, count - 1))
                if clamped != line:
                    logger.debug("goto_line_offset %d out of range (0..%d), clamped", line, max(0, count - 1))
                it = self.get_iter_at_line_offset(clamped, max(0, offset))
                self.place_cursor(it)
                return True
            except Exception:
                logger.debug("goto_line_offset failed", exc_info=True)
                return False

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
                logger.warning("save aborted: get_text failed", exc_info=True)
                return False
            try:
                path = loc.get_path()  # type: ignore[union-attr]
            except Exception:
                logger.debug("save: get_path failed", exc_info=True)
                path = None
            try:
                if path:
                    try:
                        st_mode = os.stat(path).st_mode & 0o777
                    except OSError:
                        st_mode = None
                    dir_name = os.path.dirname(path) or "."
                    fd, tmp = tempfile.mkstemp(dir=dir_name)
                    try:
                        if st_mode is not None:
                            try:
                                os.fchmod(fd, st_mode)
                            except OSError:
                                logger.debug("save: fchmod failed for %s", path, exc_info=True)
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(text)
                            f.flush()
                            try:
                                os.fsync(f.fileno())
                            except OSError:
                                logger.debug("save: fsync failed for %s", path, exc_info=True)
                        os.replace(tmp, path)
                        try:
                            dir_fd = os.open(dir_name, os.O_RDONLY)
                        except OSError:
                            dir_fd = None
                        if dir_fd is not None:
                            try:
                                os.fsync(dir_fd)
                            except OSError:
                                logger.debug("save: dir fsync failed for %s", dir_name, exc_info=True)
                            finally:
                                os.close(dir_fd)
                    except Exception:
                        try:
                            os.unlink(tmp)
                        except OSError:
                            logger.debug("save: tmp unlink failed for %s", tmp, exc_info=True)
                        raise
                else:
                    # Gio fallback (e.g. remote)
                    try:
                        loc.replace_contents(text.encode("utf-8"), None, False, Gio.FileCreateFlags.NONE, None)  # type: ignore[attr-defined, union-attr]
                    except Exception as e:
                        err = getattr(e, "errno", None)
                        logger.warning("save failed (remote %r): %r (errno=%r)", loc, e, err)
                        return False
                try:
                    self.set_modified(False)
                except Exception:
                    logger.debug("save: set_modified failed", exc_info=True)
                self._thor_untouched = False
                return True
            except Exception as e:
                err = getattr(e, "errno", None) or (e.errno if isinstance(e, OSError) else None)
                logger.warning("save failed for %r: %r (errno=%r)", path, e, err)
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
                logger.debug("get_uri_for_display failed", exc_info=True)
                return None

        def get_short_name_for_display(self):
            loc = self._thor_location
            if loc is not None:
                try:
                    return loc.get_basename() or "Untitled"  # type: ignore[union-attr]
                except Exception:
                    logger.debug("get_short_name_for_display failed", exc_info=True)
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
                logger.debug("is_local query failed", exc_info=True)
                return True

        def get_deleted(self) -> bool:
            loc = self._thor_location
            if loc is None:
                return False
            try:
                if hasattr(loc, "query_exists"):
                    return not loc.query_exists(None)
                path = loc.get_path() if hasattr(loc, "get_path") else None
                return bool(path) and not os.path.exists(path)
            except Exception:
                logger.debug("get_deleted query failed", exc_info=True)
                return False

        def get_readonly(self) -> bool:
            loc = self._thor_location
            if loc is None:
                return False
            try:
                if hasattr(loc, "query_info"):
                    info = loc.query_info("access::read-only", 0, None)
                    return bool(info.get_attribute_boolean("access::read-only"))
                path = loc.get_path() if hasattr(loc, "get_path") else None
                return bool(path) and os.path.exists(path) and not os.access(path, os.W_OK)
            except Exception:
                logger.debug("get_readonly query failed", exc_info=True)
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
            loc = self._thor_location
            if loc is None:
                return False
            try:
                path = loc.get_path() if hasattr(loc, "get_path") else None  # type: ignore[union-attr]
            except Exception:
                logger.debug("save: get_path failed", exc_info=True)
                path = None
            if not path:
                logger.warning("save failed (remote %r): no local path in headless mode", loc)
                return False
            try:
                try:
                    st_mode = os.stat(path).st_mode & 0o777
                except OSError:
                    st_mode = None
                dir_name = os.path.dirname(path) or "."
                fd, tmp = tempfile.mkstemp(dir=dir_name)
                try:
                    if st_mode is not None:
                        try:
                            os.fchmod(fd, st_mode)
                        except OSError:
                            logger.debug("save: fchmod failed for %s", path, exc_info=True)
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(self._text)
                        f.flush()
                        try:
                            os.fsync(f.fileno())
                        except OSError:
                            logger.debug("save: fsync failed for %s", path, exc_info=True)
                    os.replace(tmp, path)
                    try:
                        dir_fd = os.open(dir_name, os.O_RDONLY)
                    except OSError:
                        dir_fd = None
                    if dir_fd is not None:
                        try:
                            os.fsync(dir_fd)
                        except OSError:
                            logger.debug("save: dir fsync failed for %s", dir_name, exc_info=True)
                        finally:
                            os.close(dir_fd)
                except Exception:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        logger.debug("save: tmp unlink failed for %s", tmp, exc_info=True)
                    raise
                self._modified = False
                self._thor_untouched = False
                return True
            except Exception as e:
                err = e.errno if isinstance(e, OSError) else getattr(e, "errno", None)
                logger.warning("save failed for %r: %r (errno=%r)", path, e, err)
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
                logger.debug("new_with_buffer: set_buffer failed", exc_info=True)
            # sane defaults — isolated so one failing setter keeps the rest
            for _name, _arg in (
                ("set_show_line_numbers", True),
                ("set_highlight_current_line", True),
                ("set_auto_indent", True),
                ("set_indent_on_tab", True),
                ("set_tab_width", 4),
                ("set_insert_spaces_instead_of_tabs", True),
                ("set_show_right_margin", False),
                ("set_monospace", True),
            ):
                try:
                    getattr(v, _name)(_arg)
                except Exception:
                    logger.debug("new_with_buffer: default %s failed", _name, exc_info=True)
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
                        logger.debug("ThorTab: scrolled remove failed", exc_info=True)
                    self._scrolled.add(self._view)
            except Exception:
                logger.debug("ThorTab: view parenting failed", exc_info=True)
                try:
                    # Fallback: ensure view is parented somewhere
                    if self._view.get_parent() is None:
                        self._scrolled.add(self._view)
                except Exception:
                    logger.debug("ThorTab: fallback parenting failed", exc_info=True)
            self.pack_start(self._scrolled, True, True, 0)
            # Track modified for title updates (id kept so window.close_tab can disconnect)
            self._modified_changed_id = None
            try:
                self._modified_changed_id = self._document.connect("modified-changed", lambda *_: self._on_modified_changed())
            except Exception:
                logger.debug("ThorTab: modified-changed connect failed", exc_info=True)
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
                logger.debug("ThorTab.save failed", exc_info=True)
                return False

        def _on_modified_changed(self) -> None:
            # placeholder — window will connect to update title
            pass

        def load_location(self, location: Gio.File) -> None:
            """Synchronous guarded load (stays sync; skips truncation-safe huge files)."""
            _MAX_LOAD_BYTES = 20 * 1024 * 1024
            self._document._thor_location = location  # type: ignore[attr-defined]
            try:
                self._document._thor_file.set_location(location)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("load_location: set_location failed", exc_info=True)
            # Language from filename
            try:
                lm = GtkSource.LanguageManager.get_default()
                lang = lm.guess_language(location.get_basename(), None)  # type: ignore[union-attr]
                if lang is not None:
                    self._document.set_language(lang)
            except Exception:
                logger.debug("load_location: guess_language failed", exc_info=True)
            # Size guard — skip files over 20MB with a warning
            try:
                info = location.query_file_info("standard::size", Gio.FileQueryInfoFlags.NONE, None)  # type: ignore[attr-defined]
                size = info.get_size()
            except Exception:
                size = None
            if size is None:
                try:
                    p = location.get_path()
                    size = os.path.getsize(p) if p else None
                except Exception:
                    logger.debug("load_location: size query failed", exc_info=True)
                    size = None
            if size is not None and size > _MAX_LOAD_BYTES:
                logger.warning("load_location: skipping %r (%d bytes > 20MB)", location, size)
                return
            # Load bytes synchronously; on failure keep the existing buffer
            # untouched (never wipe to "" + mark clean on a read error).
            text = None
            try:
                ok, contents, etag = location.load_contents(None)  # type: ignore[attr-defined]
                if ok and contents is not None:
                    if len(contents) > _MAX_LOAD_BYTES:
                        logger.warning("load_location: truncating %r to 20MB", location)
                        text = contents[:_MAX_LOAD_BYTES].decode("utf-8", errors="replace")
                    else:
                        text = contents.decode("utf-8", errors="replace")
            except Exception:
                logger.debug("load_location: load_contents failed, trying direct read", exc_info=True)
                try:
                    path = location.get_path()
                    with open(path, "r", encoding="utf-8", errors="replace") as f:  # type: ignore[arg-type]
                        text = f.read(_MAX_LOAD_BYTES + 1)
                    if len(text) > _MAX_LOAD_BYTES:
                        logger.warning("load_location: truncating %r to 20MB", location)
                        text = text[:_MAX_LOAD_BYTES]
                except Exception:
                    logger.warning("load_location: read failed for %r; keeping buffer", location, exc_info=True)
                    return
            if text is None:
                logger.warning("load_location: read failed for %r; keeping buffer", location)
                return
            try:
                self._document.begin_not_undoable_action()
                self._document.set_text(text)
                self._document.end_not_undoable_action()
                self._document.set_modified(False)
            except Exception:
                logger.debug("load_location: set_text failed", exc_info=True)
            self._document._thor_untouched = False  # type: ignore[attr-defined]

else:

    class ThorTab:  # type: ignore[no-redef]
        def __init__(self, document=None, location=None):
            if document is None and location is not None:
                try:
                    document = ThorDocument(location=location)
                except Exception:
                    logger.debug("ThorTab init failed", exc_info=True)
                    document = None
            self._document = document
            self._view = None
            self._state = 0
            self._modified_changed_id = None

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
                logger.debug("ThorTab.save failed", exc_info=True)
                return False

        def set_info_bar(self, bar):
            pass

        def load_location(self, location) -> None:
            _MAX_LOAD_BYTES = 20 * 1024 * 1024
            try:
                if self._document is not None:
                    self._document._thor_location = location  # type: ignore[attr-defined]
                    try:
                        self._document._thor_file.set_location(location)  # type: ignore[attr-defined]
                    except Exception:
                        logger.debug("load_location: set_location failed", exc_info=True)
                    try:
                        path = location.get_path() if hasattr(location, "get_path") else None  # type: ignore[union-attr]
                        if path and os.path.isfile(path):
                            try:
                                size = os.path.getsize(path)
                            except OSError:
                                size = None
                            if size is not None and size > _MAX_LOAD_BYTES:
                                logger.warning("load_location: skipping %r (%d bytes > 20MB)", path, size)
                                txt = ""
                            else:
                                with open(path, "r", encoding="utf-8", errors="replace") as f:
                                    txt = f.read(_MAX_LOAD_BYTES + 1)
                                if len(txt) > _MAX_LOAD_BYTES:
                                    logger.warning("load_location: truncating %r to 20MB", path)
                                    txt = txt[:_MAX_LOAD_BYTES]
                            self._document.set_text(txt)  # type: ignore[union-attr]
                            try:
                                self._document.set_modified(False)  # type: ignore[union-attr]
                            except Exception:
                                logger.debug("load_location: set_modified failed", exc_info=True)
                    except Exception:
                        logger.debug("load_location: read failed", exc_info=True)
                    try:
                        self._document._thor_untouched = False  # type: ignore[attr-defined]
                    except Exception:
                        logger.debug("load_location: untouched flag failed", exc_info=True)
            except Exception:
                logger.debug("load_location failed", exc_info=True)
