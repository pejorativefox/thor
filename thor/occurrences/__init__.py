# -*- coding: utf-8 -*-
"""Occurrences highlight — Thor-native."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk, Gdk, GLib  # type: ignore

    try:
        gi.require_version("GtkSource", "4")
        from gi.repository import GtkSource  # type: ignore

        _GTKSOURCE_AVAILABLE = True
    except Exception:
        try:
            gi.require_version("GtkSource", "3.0")
            from gi.repository import GtkSource  # type: ignore

            _GTKSOURCE_AVAILABLE = True
        except Exception as _e:
            GtkSource = None  # type: ignore
            _GTKSOURCE_AVAILABLE = False
            logger.debug(f"missing optional dependency: GtkSource typelib ({_e}). Gutter styling skipped.")
except Exception:
    Gtk = Gdk = GLib = GtkSource = None  # type: ignore
    _GTKSOURCE_AVAILABLE = False

TAG_NAME = "occurrences-highlight"
MARK_CATEGORY = "occurrences-highlight"
HIGHLIGHT_COLOR = "#37332a"
DEBOUNCE_MS = 150
MAX_MATCHES = 1000
MIN_WORD_LEN = 2

from .search import find_occurrences, word_at  # noqa: E402

class OccurrencesManager:
    """Highlight all occurrences of the word under cursor.

    Wires to a ThorWindow via :func:`attach`.
    Soft-fails when GTK is unavailable.
    """

    def __init__(self, window) -> None:
        self._window = window
        self._tracked: dict[int, dict] = {}
        self._window_ids: list = []
        self._mark_views_configured: set = set()

    # -- lifecycle ---------------------------------------------------

    def attach(self) -> None:
        if Gtk is None:
            return
        for signal, handler in (
            ("tab-added", self._on_tab_added),
            ("tab-removed", self._on_tab_removed),
            ("active-tab-changed", self._on_active_tab_changed),
        ):
            try:
                hid = self._window.connect(signal, handler)
                self._window_ids.append((self._window, hid))
            except Exception as e:
                logger.debug(f"window connect {signal} failed: {e!r}")
        try:
            for view in self._window.get_views():
                try:
                    self._track_view(view)
                except Exception as e:
                    logger.debug(f"track view failed: {e!r}")
        except Exception as e:
            logger.debug(f"initial views failed: {e!r}")

    def detach(self) -> None:
        try:
            for record in list(self._tracked.values()):
                try:
                    self._untrack_record(record)
                except Exception as e:
                    logger.debug(f"untrack failed: {e!r}")
        finally:
            self._tracked.clear()
            self._mark_views_configured.clear()
        for obj, hid in self._window_ids:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._window_ids = []

    # -- window signals ----------------------------------------------
    def _on_tab_added(self, window, tab) -> None:
        try:
            try:
                view = tab.get_view()
            except Exception:
                view = None
            if view is not None:
                self._track_view(view)
        except Exception as e:
            logger.debug(f"tab-added failed: {e!r}")

    def _on_tab_removed(self, window, tab) -> None:
        try:
            self._reap_dead_docs()
        except Exception as e:
            logger.debug(f"tab-removed failed: {e!r}")

    def _on_active_tab_changed(self, window, tab) -> None:
        try:
            try:
                view = tab.get_view() if tab is not None else None
            except Exception:
                view = None
            if view is not None:
                self._track_view(view)
                try:
                    doc = view.get_buffer()
                    self._schedule(doc)
                except Exception as e:
                    logger.debug(f"active-tab update failed: {e!r}")
        except Exception as e:
            logger.debug(f"active-tab-changed failed: {e!r}")

    def _doc_key(self, doc) -> int | None:
        # id(), never hash(): distinct buffers can share a hash (aliasing)
        # and hash() may invoke overloaded __hash__ on mocks. id() is
        # unique per live object, which is exactly the tracking scope.
        try:
            return id(doc)
        except Exception as e:
            logger.debug("doc key failed: %r", e, exc_info=True)
            return None
    def _track_view(self, view) -> None:
        try:
            doc = view.get_buffer()
        except Exception as e:
            logger.debug(f"get_buffer failed: {e!r}")
            return
        key = self._doc_key(doc)
        if key is None:
            return
        if key in self._tracked:
            return
        ids: list = []
        try:
            hid = doc.connect("mark-set", self._on_mark_set, key)
            ids.append((doc, hid))
        except Exception as e:
            logger.debug(f"mark-set connect failed: {e!r}")
        try:
            hid = doc.connect("changed", self._on_buffer_changed, key)
            ids.append((doc, hid))
        except Exception as e:
            logger.debug(f"changed connect failed: {e!r}")
        record = {"view": view, "doc": doc, "ids": ids, "pending": None,
                  "strip": None, "strip_ids": [], "lines": [], "line_count": 1}
        self._tracked[key] = record
        try:
            self._configure_marks(view)
        except Exception as e:
            logger.debug(f"configure marks failed: {e!r}")
        try:
            strip = self._attach_strip(view, key)
            record["strip"] = strip
        except Exception as e:
            logger.debug(f"attach strip failed: {e!r}")
        self._schedule(doc)

    def _untrack_record(self, record: dict) -> None:
        try:
            pending = record.get("pending")
            if pending is not None:
                try:
                    if GLib is not None:
                        GLib.source_remove(pending)
                except Exception:
                    pass
                record["pending"] = None
        except Exception:
            pass
        try:
            doc = record.get("doc")
            if doc is not None:
                try:
                    self._clear_doc(doc, record)
                except Exception:
                    pass
        except Exception:
            pass
        for obj, hid in list(record.get("ids", [])):
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        record["ids"] = []
        for obj, hid in list(record.get("strip_ids", [])):
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        record["strip_ids"] = []
        strip = record.get("strip")
        record["strip"] = None
        if strip is not None:
            try:
                strip.destroy()
            except Exception:
                pass

    def _reap_dead_docs(self) -> None:
        try:
            live: set = set()
            for view in self._window.get_views():
                try:
                    live.add(self._doc_key(view.get_buffer()))
                except Exception:
                    continue
        except Exception:
            return
        for key in list(self._tracked.keys()):
            if key not in live:
                record = self._tracked.pop(key)
                try:
                    self._untrack_record(record)
                except Exception as e:
                    logger.debug(f"reap untrack failed: {e!r}")

    # -- buffer signals ----------------------------------------------
    def _on_mark_set(self, doc, it, mark, doc_key) -> None:
        try:
            try:
                if mark != doc.get_insert():
                    return
            except Exception:
                pass
            self._schedule(doc)
        except Exception as e:
            logger.debug(f"mark-set failed: {e!r}")

    def _on_buffer_changed(self, doc, doc_key=None) -> None:
        try:
            self._schedule(doc)
        except Exception as e:
            logger.debug(f"buffer changed failed: {e!r}")

    def _schedule(self, doc) -> None:
        try:
            key = self._doc_key(doc)
            record = self._tracked.get(key) if key is not None else None
            if record is None:
                return
            old = record.get("pending")
            if old is not None:
                try:
                    if GLib is not None:
                        GLib.source_remove(old)
                except Exception:
                    pass
                record["pending"] = None
            if GLib is None:
                return
            try:
                record["pending"] = GLib.timeout_add(DEBOUNCE_MS, self._fire, key)
            except Exception as e:
                logger.debug(f"debounce schedule failed: {e!r}")
        except Exception as e:
            logger.debug(f"schedule failed: {e!r}")

    def _fire(self, doc_key) -> bool:
        try:
            record = self._tracked.get(doc_key)
            if record is None:
                return False
            record["pending"] = None
            try:
                self._update(record)
            except Exception as e:
                logger.debug(f"update failed: {e!r}")
        except Exception as e:
            logger.debug(f"fire failed: {e!r}")
        return False

    # -- highlight ---------------------------------------------------
    def _update(self, record: dict) -> None:
        doc = record.get("doc")
        if doc is None or word_at is None or find_occurrences is None:
            return
        # Cheap pre-check via word iters: skip the full-buffer snapshot when
        # the cursor is not on a word (whitespace stops are the common case).
        # Debounce (see _schedule) already coalesces rapid cursor moves.
        try:
            mark = doc.get_insert()
            cursor_iter = doc.get_iter_at_mark(mark)
            offset = cursor_iter.get_offset()
        except Exception as e:
            logger.debug(f"cursor offset failed: {e!r}")
            return
        try:
            if not self._iter_on_word(cursor_iter):
                self._clear_doc(doc, record)
                return
        except Exception as e:
            logger.debug("iter word pre-check failed: %r", e, exc_info=True)
            # fall through to the snapshot path
        try:
            start, end = doc.get_bounds()
            text = doc.get_text(start, end, True)
        except Exception:
            try:
                text = doc.get_text(doc.get_start_iter(), doc.get_end_iter(), True)
            except Exception as e:
                logger.debug(f"text snapshot failed: {e!r}")
                return
        try:
            found = word_at(text, offset)
        except Exception as e:
            logger.debug(f"word_at failed: {e!r}")
            return
        if found is None:
            try:
                self._clear_doc(doc, record)
            except Exception as e:
                logger.debug(f"clear failed: {e!r}")
            return
        word = found[0]
        try:
            hits = find_occurrences(text, word, MAX_MATCHES)
        except Exception as e:
            logger.debug(f"find_occurrences failed: {e!r}")
            return
        try:
            self._apply(doc, record, hits)
        except Exception as e:
            logger.debug(f"apply failed: {e!r}")

    @staticmethod
    def _iter_on_word(cursor_iter) -> bool:
        """True when the iter looks like it sits on/inside a word char."""
        try:
            ch = cursor_iter.get_char()
            if ch and (ch == "_" or ch.isalnum() and ch.isascii()):
                return True
        except Exception:
            pass
        # At EOF get_char() is empty; check the preceding char instead.
        try:
            prev = cursor_iter.copy()
            if prev.backward_char():
                ch = prev.get_char()
                if ch and (ch == "_" or ch.isalnum() and ch.isascii()):
                    return True
        except Exception:
            pass
        return False

    def _ensure_tag(self, doc):
        try:
            table = doc.get_tag_table()
        except Exception:
            return None
        try:
            tag = table.lookup(TAG_NAME)
        except Exception:
            tag = None
        if tag is not None:
            return tag
        try:
            return doc.create_tag(TAG_NAME, background=HIGHLIGHT_COLOR)
        except Exception as e:
            logger.debug(f"tag create failed: {e!r}")
            return None

    def _apply(self, doc, record: dict, hits: list) -> None:
        tag = self._ensure_tag(doc)
        try:
            start, end = doc.get_bounds()
        except Exception as e:
            logger.debug(f"bounds failed: {e!r}")
            return
        if tag is not None:
            try:
                doc.remove_tag(tag, start, end)
            except Exception:
                pass
            for s, e in hits:
                try:
                    doc.apply_tag(tag, doc.get_iter_at_offset(s), doc.get_iter_at_offset(e))
                except Exception:
                    continue
        # gutter marks: one per matched line
        try:
            doc.remove_source_marks(start, end, MARK_CATEGORY)
        except Exception as e:
            logger.debug(f"remove marks failed: {e!r}")
        lines: list[int] = []
        try:
            line_count = doc.get_line_count()
        except Exception:
            line_count = 0
        seen: set[int] = set()
        for s, _e in hits:
            try:
                line = doc.get_iter_at_offset(s).get_line()
            except Exception:
                continue
            if line in seen:
                continue
            seen.add(line)
            try:
                if line_count and not 0 <= line < line_count:
                    continue
                doc.create_source_mark(None, MARK_CATEGORY, doc.get_iter_at_line(line))
                lines.append(line)
            except Exception:
                continue
        record["lines"] = lines
        try:
            record["line_count"] = line_count if line_count else 1
        except Exception:
            record["line_count"] = 1
        strip = record.get("strip")
        if strip is not None:
            try:
                strip.queue_draw()
            except Exception:
                pass

    def _clear_doc(self, doc, record: dict | None = None) -> None:
        try:
            start, end = doc.get_bounds()
        except Exception:
            return
        try:
            table = doc.get_tag_table()
            tag = table.lookup(TAG_NAME)
        except Exception:
            tag = None
        if tag is not None:
            try:
                doc.remove_tag(tag, start, end)
            except Exception:
                pass
        try:
            doc.remove_source_marks(start, end, MARK_CATEGORY)
        except Exception as e:
            logger.debug(f"clear marks failed: {e!r}")
        if record is not None:
            record["lines"] = []
            strip = record.get("strip")
            if strip is not None:
                try:
                    strip.queue_draw()
                except Exception:
                    pass

    def _configure_marks(self, view) -> None:
        if not _GTKSOURCE_AVAILABLE:
            return
        try:
            if id(view) in self._mark_views_configured:
                return
        except Exception as e:
            logger.debug("mark configured check failed: %r", e, exc_info=True)
            return
        try:
            view.set_show_line_marks(True)
            try:
                attrs = GtkSource.MarkAttributes()
                rgba = Gdk.RGBA()
                if rgba.parse(HIGHLIGHT_COLOR):
                    attrs.set_background(rgba)
                view.set_mark_attributes(MARK_CATEGORY, attrs, 10)
            except Exception as e:
                logger.debug(f"mark attributes {MARK_CATEGORY} failed: {e!r}")
            try:
                self._mark_views_configured.add(id(view))
            except Exception as e:
                logger.debug("mark configured stash failed: %r", e, exc_info=True)
        except Exception as e:
            logger.debug(f"configure marks failed: {e!r}")

    # -- scrollbar tick strip (best-effort) ---------------------------
    def _attach_strip(self, view, doc_key):
        if Gtk is None:
            return None
        try:
            sw = None
            p = None
            try:
                p = view.get_parent()
            except Exception:
                return None
            for _ in range(8):
                if p is None:
                    break
                try:
                    p.get_vscrollbar()
                    sw = p
                    break
                except Exception:
                    pass
                try:
                    p = p.get_parent()
                except Exception:
                    break
            if sw is None:
                logger.debug("no ScrolledWindow found; gutter marks only")
                return None
            strip = Gtk.DrawingArea()
            try:
                strip.set_size_request(8, -1)
            except Exception:
                pass
            try:
                strip.connect("draw", self._on_strip_draw, doc_key)
            except Exception as e:
                logger.debug(f"strip draw connect failed: {e!r}")
                return None
            record = self._tracked.get(doc_key)
            try:
                sb = sw.get_vscrollbar()
            except Exception:
                sb = None
            placed = False
            if sb is not None:
                try:
                    parent = sb.get_parent()
                except Exception:
                    parent = None
                if parent is not None:
                    for meth in ("pack_start", "pack_end", "add"):
                        try:
                            fn = getattr(parent, meth, None)
                            if fn is None:
                                continue
                            if meth.startswith("pack"):
                                fn(strip, False, False, 0)
                            else:
                                fn(strip)
                            placed = True
                            break
                        except Exception:
                            continue
                    if not placed:
                        try:
                            parent.add(strip)
                            placed = True
                        except Exception as e:
                            logger.debug(f"strip pack failed: {e!r}")
                try:
                    hid = sb.connect("value-changed", self._on_scroll_changed, doc_key)
                    if record is not None:
                        record.setdefault("strip_ids", []).append((sb, hid))
                except Exception:
                    pass
                try:
                    hid = sb.connect("size-allocate", self._on_scroll_changed, doc_key)
                    if record is not None:
                        record.setdefault("strip_ids", []).append((sb, hid))
                except Exception:
                    pass
            if not placed:
                logger.debug("strip pack unavailable; gutter marks only")
                try:
                    strip.destroy()
                except Exception:
                    pass
                return None
            try:
                strip.show()
            except Exception:
                pass
            return strip
        except Exception as e:
            logger.debug(f"attach strip failed: {e!r}")
            return None

    def _on_strip_draw(self, area, cr, doc_key) -> bool:
        try:
            record = self._tracked.get(doc_key)
            if record is None:
                return False
            lines = record.get("lines", [])
            if not lines:
                return False
            try:
                alloc = area.get_allocation()
                height = alloc.height
                width = alloc.width
            except Exception:
                return False
            if height <= 0 or width <= 0:
                return False
            line_count = record.get("line_count", 1) or 1
            try:
                rgba = Gdk.RGBA()
                ok = rgba.parse(HIGHLIGHT_COLOR)
                if ok:
                    try:
                        Gdk.cairo_set_source_rgba(cr, rgba)
                    except Exception:
                        cr.set_source_rgb(rgba.red, rgba.green, rgba.blue)
                else:
                    cr.set_source_rgb(0.9, 0.86, 0.45)
            except Exception:
                try:
                    cr.set_source_rgb(0.9, 0.86, 0.45)
                except Exception:
                    return False
            for line in lines:
                try:
                    y = int(line / max(1, line_count) * height)
                    if y + 3 > height:
                        y = height - 3
                    cr.rectangle(0, y, width, 3)
                except Exception:
                    continue
            try:
                cr.fill()
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"strip draw failed: {e!r}")
        return False

    def _on_scroll_changed(self, *args) -> None:
        try:
            doc_key = args[-1] if args else None
            record = self._tracked.get(doc_key)
            if record is None:
                return
            strip = record.get("strip")
            if strip is not None:
                try:
                    strip.queue_draw()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"scroll changed failed: {e!r}")

def attach(window) -> OccurrencesManager | None:
    """Wire occurrences highlighting to *window*.

    Returns the manager, or ``None`` when GTK is unavailable.
    Safe to call headless — no-ops when window or Gtk is ``None``.
    """
    if window is None:
        return None
    if Gtk is None:
        logger.debug("attach skipped — Gtk unavailable (headless)")
        return None
    existing = getattr(window, "_thor_occurrences_manager", None)
    if existing is not None:
        return existing  # type: ignore[return-value]
    mgr = OccurrencesManager(window)
    try:
        mgr.attach()
    except Exception as e:
        logger.debug(f"attach failed: {e!r}")
        return None
    try:
        window._thor_occurrences_manager = mgr  # type: ignore[attr-defined]
    except Exception:
        pass
    return mgr

def detach(window) -> None:
    """Detach occurrences highlighting from *window*."""
    if window is None:
        return
    mgr = getattr(window, "_thor_occurrences_manager", None)
    if mgr is None:
        return
    try:
        mgr.detach()
    except Exception as e:
        logger.debug(f"detach failed: {e!r}")
    try:
        delattr(window, "_thor_occurrences_manager")
    except Exception:
        try:
            window._thor_occurrences_manager = None  # type: ignore[attr-defined]
        except Exception:
            pass
