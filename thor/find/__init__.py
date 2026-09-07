# -*- coding: utf-8 -*-
"""Document find — Ctrl+F bar for the active Thor tab.

Headless logic lives in ``thor.find.search``; this module owns the GTK
bar, highlights, and key handling.  Attach is soft-only: if GTK or
GtkSource are unavailable the window is untouched.

Bar: entry + 1/12 label + Prev / Next + Aa (case) + Close + Esc.
Highlights: TAG_MATCH for all hits, TAG_CURRENT for the current hit.
Search is plain substring (case-insensitive by default).
"""

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
        GtkSource = None  # type: ignore
        _GTKSOURCE_AVAILABLE = False
except Exception:  # headless
    Gtk = Gdk = GLib = GtkSource = None  # type: ignore
    _GTKSOURCE_AVAILABLE = False

from .search import current_index, find_all, next_index, prev_index
from ..keys import decode_key_event

TAG_MATCH = "thor-find-match"
TAG_CURRENT = "thor-find-current"
# Atom One Dark friendly, visible on #282C34 base
MATCH_BG = "#5a4a1a"  # dark amber
CURRENT_BG = "#9a7b0a"  # brighter amber for current
MATCH_FG = None
CURRENT_FG = "#ffffff"


def _get_active_doc_view(window):
    try:
        tab = window.get_active_tab()  # type: ignore[attr-defined]
        if tab is None:
            return None, None, None
        doc = tab.get_document()  # type: ignore[attr-defined]
        view = tab.get_view()  # type: ignore[attr-defined]
        return tab, doc, view
    except Exception:
        return None, None, None


def _buffer_text(doc) -> str:
    try:
        s, e = doc.get_bounds()  # type: ignore[attr-defined]
        return doc.get_text(s, e, True)  # type: ignore[attr-defined]
    except Exception:
        try:
            # headless fake
            return doc.get_text()  # type: ignore
        except Exception:
            return ""


def _cursor_offset(doc) -> int:
    try:
        it = doc.get_iter_at_mark(doc.get_insert())  # type: ignore[attr-defined]
        return it.get_offset()  # type: ignore[attr-defined]
    except Exception:
        return 0


def _selection_text(doc) -> str | None:
    try:
        if not doc.get_has_selection():  # type: ignore[attr-defined]
            return None
        b, e = doc.get_selection_bounds()  # type: ignore[attr-defined]
        return doc.get_text(b, e, True)  # type: ignore[attr-defined]
    except Exception:
        return None


def _word_at_cursor(doc):
    """Best-effort word under cursor for initial query (alnum+underscore)."""
    try:
        text = _buffer_text(doc)
        off = _cursor_offset(doc)
        if not text or off < 0 or off >= len(text):
            # try offset 0 fallback
            pass
        # find word boundaries around off
        import re

        # If cursor is on word char, expand; else return None
        if off < len(text) and (text[off].isalnum() or text[off] == "_"):
            pass
        elif off > 0 and off <= len(text) and (text[off - 1].isalnum() or text[off - 1] == "_"):
            off -= 1
        else:
            return None
        # expand
        s = off
        while s > 0 and (text[s - 1].isalnum() or text[s - 1] == "_"):
            s -= 1
        e = off
        while e < len(text) and (text[e].isalnum() or text[e] == "_"):
            e += 1
        w = text[s:e]
        if len(w) >= 2:
            return w
        return None
    except Exception:
        return None


def _ensure_tags(doc) -> None:
    try:
        table = doc.get_tag_table()  # type: ignore[attr-defined]
        if table.lookup(TAG_MATCH) is None:
            try:
                doc.create_tag(TAG_MATCH, background=MATCH_BG)  # type: ignore[attr-defined]
            except Exception:
                # fallback: create_tag with props may need Gtk.TextTag
                tag = Gtk.TextTag.new(TAG_MATCH)  # type: ignore[union-attr]
                try:
                    tag.set_property("background", MATCH_BG)
                except Exception:
                    pass
                table.add(tag)
        if table.lookup(TAG_CURRENT) is None:
            try:
                props = {"background": CURRENT_BG}
                if CURRENT_FG:
                    props["foreground"] = CURRENT_FG
                doc.create_tag(TAG_CURRENT, **props)  # type: ignore[attr-defined]
            except Exception:
                tag = Gtk.TextTag.new(TAG_CURRENT)  # type: ignore[union-attr]
                try:
                    tag.set_property("background", CURRENT_BG)
                    if CURRENT_FG:
                        tag.set_property("foreground", CURRENT_FG)
                except Exception:
                    pass
                table.add(tag)
    except Exception as e:
        logger.debug(f"ensure_tags failed: {e!r}")


def _clear_tags(doc) -> None:
    try:
        s, e = doc.get_bounds()  # type: ignore[attr-defined]
        doc.remove_tag_by_name(TAG_MATCH, s, e)  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug("find clear match tags failed: %r", e, exc_info=True)
    try:
        s, e = doc.get_bounds()  # type: ignore[attr-defined]
        doc.remove_tag_by_name(TAG_CURRENT, s, e)  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug("find clear current tag failed: %r", e, exc_info=True)


#: Max highlighted hits per buffer. Tag application is O(hits) GTK work;
#: beyond this the bar still counts all hits but only tags the first N.
MAX_HIGHLIGHT_TAGS = 1000


def _apply_highlights(doc, hits: list[tuple[int, int]], current_idx: int | None) -> None:
    _ensure_tags(doc)
    _clear_tags(doc)
    if not hits:
        return
    if len(hits) > MAX_HIGHLIGHT_TAGS:
        logger.debug("find: capping %d hits to %d tags", len(hits), MAX_HIGHLIGHT_TAGS)
    for idx, (s, e) in enumerate(hits[:MAX_HIGHLIGHT_TAGS]):
        try:
            si = doc.get_iter_at_offset(s)  # type: ignore[attr-defined]
            ei = doc.get_iter_at_offset(e)  # type: ignore[attr-defined]
            if idx == current_idx:
                doc.apply_tag_by_name(TAG_CURRENT, si, ei)  # type: ignore[attr-defined]
            else:
                doc.apply_tag_by_name(TAG_MATCH, si, ei)  # type: ignore[attr-defined]
        except Exception as e:
            logger.debug("find highlight #%d failed: %r", idx, e, exc_info=True)
            continue


class FindBar(Gtk.Box if Gtk is not None else object):  # type: ignore[misc]
    """Horizontal find bar shown above the editor."""

    def __init__(self, manager: "FindManager"):
        if Gtk is None:
            return
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.manager = manager
        self.set_border_width(4)
        # subtle background to distinguish from editor
        try:
            self.get_style_context().add_class("thor-find-bar")
        except Exception:
            pass

        # Close button on left (like VS Code) — actually put on right for familiarity
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Find")
        self.entry.set_hexpand(True)
        self.entry.set_width_chars(30)
        try:
            self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "edit-find")
        except Exception:
            pass

        self.count_label = Gtk.Label(label="")
        self.count_label.set_xalign(0.0)
        # fixed width to avoid jump
        self.count_label.set_width_chars(7)

        self.prev_btn = Gtk.Button.new_from_icon_name("go-up", Gtk.IconSize.MENU)
        self.prev_btn.set_tooltip_text("Previous (Shift+Enter, Shift+F3)")
        self.prev_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.prev_btn.set_focus_on_click(False)

        self.next_btn = Gtk.Button.new_from_icon_name("go-down", Gtk.IconSize.MENU)
        self.next_btn.set_tooltip_text("Next (Enter, F3)")
        self.next_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.next_btn.set_focus_on_click(False)

        self.case_btn = Gtk.ToggleButton(label="Aa")
        self.case_btn.set_tooltip_text("Match case")
        self.case_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.case_btn.set_focus_on_click(False)

        self.close_btn = Gtk.Button.new_from_icon_name("window-close", Gtk.IconSize.MENU)
        self.close_btn.set_tooltip_text("Close (Escape)")
        self.close_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.close_btn.set_focus_on_click(False)

        self.pack_start(Gtk.Label(label="Find:"), False, False, 0)
        self.pack_start(self.entry, True, True, 0)
        self.pack_start(self.count_label, False, False, 0)
        self.pack_start(self.prev_btn, False, False, 0)
        self.pack_start(self.next_btn, False, False, 0)
        self.pack_start(self.case_btn, False, False, 0)
        self.pack_start(self.close_btn, False, False, 0)

        # signals
        self.entry.connect("changed", lambda *_: self.manager._on_query_changed())
        self.entry.connect("activate", lambda *_: self.manager._go_next())
        self.entry.connect("key-press-event", self._on_entry_key)
        self.prev_btn.connect("clicked", lambda *_: self.manager._go_prev())
        self.next_btn.connect("clicked", lambda *_: self.manager._go_next())
        self.case_btn.connect("toggled", lambda *_: self.manager._on_query_changed())
        self.close_btn.connect("clicked", lambda *_: self.manager.hide())

        self.show_all()
        self.hide()

    def _on_entry_key(self, _w, event) -> bool:
        parts = decode_key_event(event)
        if parts is None:
            return False
        key, _ctrl, shift, _alt = parts
        if key == "Escape":
            self.manager.hide()
            return True
        if key in ("Return", "KP_Enter"):
            if shift:
                self.manager._go_prev()
            else:
                self.manager._go_next()
            return True
        return False


class FindManager:
    """Per-window find controller."""

    def __init__(self, window):
        self.window = window
        self.bar: FindBar | None = None
        self._hits: list[tuple[int, int]] = []
        self._current: int | None = None
        self._query: str = ""
        self._case_sensitive: bool = False
        self._handler_id: int | None = None
        self._tab_handler: int | None = None
        self._buffer_handler: int | None = None
        self._current_doc = None

        if Gtk is None or window is None:
            return

        # locate vbox to pack bar
        vbox = getattr(window, "_vbox", None)
        if vbox is None:
            try:
                vbox = window.get_child()  # type: ignore[attr-defined]
            except Exception:
                vbox = None
        if vbox is None:
            logger.debug("find: no vbox to pack bar")
            return

        self.bar = FindBar(self)
        try:
            # pack at top of vbox above hpaned
            vbox.pack_start(self.bar, False, False, 0)
            try:
                vbox.reorder_child(self.bar, 0)
            except Exception:
                pass
            # FindBar already did show_all()+hide(); don't show_all on vbox
            # (would re-show hidden side/bottom panels). Just ensure bar stays hidden.
            self.bar.hide()
            try:
                vbox.queue_resize()
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"find pack failed: {e!r}")
            self.bar = None
            return

        # key handling — connect after window's own handler so we can intercept Ctrl+F
        try:
            self._handler_id = window.connect("key-press-event", self._on_window_key)
        except Exception as e:
            logger.debug(f"find key connect failed: {e!r}")

        # tab switch → re-apply for new doc
        try:
            self._tab_handler = window.connect("active-tab-changed", lambda *_: self._on_active_tab_changed())
        except Exception:
            pass

        # expose for tests / window integration
        try:
            window._thor_find_mgr = self  # type: ignore[attr-defined]
        except Exception:
            pass
        logger.debug("find attached")

    # -- public ---------------------------------------------------------

    def show(self, initial_query: str | None = None) -> None:
        if self.bar is None:
            return
        # decide initial query if not given
        if initial_query is None:
            _, doc, _ = _get_active_doc_view(self.window)
            if doc is not None:
                sel = _selection_text(doc)
                if sel and "\n" not in sel and len(sel) < 200:
                    initial_query = sel
                else:
                    w = _word_at_cursor(doc)
                    if w:
                        initial_query = w
        if initial_query is not None:
            try:
                # avoid recursive changed spam — set text will trigger _on_query_changed
                self.bar.entry.set_text(initial_query)
                self.bar.entry.select_region(0, -1)
            except Exception:
                pass
        else:
            try:
                self.bar.entry.select_region(0, -1)
            except Exception:
                pass
        self.bar.show()
        try:
            self.bar.entry.grab_focus()
        except Exception:
            pass
        # ensure we are tracking the active buffer's changes
        self._track_buffer()
        self._on_query_changed()

    def hide(self) -> None:
        if self.bar is None:
            return
        # clear highlights in active doc (or last doc)
        try:
            _, doc, view = _get_active_doc_view(self.window)
            target = doc if doc is not None else self._current_doc
            if target is not None:
                _clear_tags(target)
        except Exception:
            pass
        self._hits = []
        self._current = None
        self._update_label()
        try:
            self.bar.hide()
        except Exception:
            pass
        # return focus to editor
        try:
            _, _, view = _get_active_doc_view(self.window)
            if view is not None:
                view.grab_focus()
        except Exception:
            pass

    def is_visible(self) -> bool:
        try:
            return bool(self.bar and self.bar.get_visible())
        except Exception:
            return False

    # -- internals ------------------------------------------------------

    def _track_buffer(self) -> None:
        old_doc = self._current_doc
        # disconnect old
        if self._buffer_handler and old_doc is not None:
            try:
                old_doc.disconnect(self._buffer_handler)  # type: ignore[attr-defined]
            except Exception as e:
                logger.debug("find buffer disconnect failed: %r", e, exc_info=True)
            self._buffer_handler = None
        _, doc, _ = _get_active_doc_view(self.window)
        # Drop highlights left on the previous buffer so a doc switch never
        # leaves stale amber tags behind in a background document.
        if old_doc is not None and old_doc is not doc:
            try:
                _clear_tags(old_doc)
            except Exception as e:
                logger.debug("find stale clear failed: %r", e, exc_info=True)
        self._current_doc = doc
        if doc is None:
            return
        try:
            self._buffer_handler = doc.connect("changed", lambda *_: self._on_buffer_changed())
        except Exception as e:
            logger.debug("find buffer watch failed: %r", e, exc_info=True)
            self._buffer_handler = None

    def _on_active_tab_changed(self) -> None:
        if not self.is_visible():
            # if hidden, just retrack for next show
            self._track_buffer()
            return
        self._track_buffer()
        self._on_query_changed(select=True)

    def _on_buffer_changed(self) -> None:
        if not self.is_visible():
            return
        # buffer edited → recompute hits but keep current query
        self._on_query_changed(select=False)

    def _on_query_changed(self, select: bool = True) -> None:
        if self.bar is None:
            return
        try:
            q = self.bar.entry.get_text()  # type: ignore[attr-defined]
        except Exception:
            q = ""
        try:
            cs = bool(self.bar.case_btn.get_active())  # type: ignore[attr-defined]
        except Exception:
            cs = False
        self._query = q
        self._case_sensitive = cs

        _, doc, view = _get_active_doc_view(self.window)
        if doc is None:
            self._hits = []
            self._current = None
            self._update_label()
            return
        _ensure_tags(doc)
        if not q:
            _clear_tags(doc)
            self._hits = []
            self._current = None
            self._update_label()
            return
        text = _buffer_text(doc)
        if len(text) > 500_000:
            _clear_tags(doc)
            self._hits = []
            self._current = None
            self._update_label()
            return
        hits = find_all(text, q, case_sensitive=cs)
        self._hits = hits
        if not hits:
            _clear_tags(doc)
            self._current = None
            self._update_label()
            return
        # decide current: if cursor inside a hit, keep it; else next after cursor
        off = _cursor_offset(doc)
        try:
            keep = current_index(hits, off)
        except Exception:
            keep = None
        if keep is not None:
            nxt = keep
        else:
            nxt = next_index(hits, off - 1 if off > 0 else -1, wrap=True)
        cur = nxt if nxt is not None else 0
        self._current = cur
        _apply_highlights(doc, hits, cur)
        self._update_label()
        if select and cur is not None and view is not None:
            self._select_hit(doc, view, hits[cur])

    def _update_label(self) -> None:
        if self.bar is None:
            return
        try:
            if not self._query:
                self.bar.count_label.set_text("")
            elif not self._hits:
                self.bar.count_label.set_text("No results")
            else:
                cur = (self._current + 1) if self._current is not None else 0
                self.bar.count_label.set_text(f"{cur}/{len(self._hits)}")
        except Exception:
            pass

    def _select_hit(self, doc, view, rng: tuple[int, int]) -> None:
        try:
            s, e = rng
            si = doc.get_iter_at_offset(s)  # type: ignore[attr-defined]
            ei = doc.get_iter_at_offset(e)  # type: ignore[attr-defined]
            # use select_range to highlight selection, or place cursor
            try:
                doc.select_range(si, ei)  # type: ignore[attr-defined]
            except Exception:
                try:
                    doc.place_cursor(ei)  # type: ignore[attr-defined]
                    view.scroll_to_iter(si, 0.0, False, 0, 0)  # type: ignore[attr-defined]
                    return
                except Exception:
                    pass
            try:
                view.scroll_to_iter(si, 0.1, False, 0, 0)  # type: ignore[attr-defined]
                view.grab_focus()  # keep entry focus? actually entry should stay focused for typing
                # we want entry to keep focus while navigating via buttons/enter, but after Next we keep entry focus
                # for now keep entry focused
                if self.bar:
                    self.bar.entry.grab_focus()
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"select_hit failed: {e!r}")

    def _go_next(self) -> None:
        if not self._hits:
            return
        _, doc, view = _get_active_doc_view(self.window)
        if doc is None or view is None:
            return
        off = _cursor_offset(doc)
        # cursor may be at start of current selection; we want next after current
        # use next_index with current offset
        nxt = next_index(self._hits, off, wrap=True)
        if nxt is None:
            return
        self._current = nxt
        _apply_highlights(doc, self._hits, nxt)
        self._update_label()
        self._select_hit(doc, view, self._hits[nxt])

    def _go_prev(self) -> None:
        if not self._hits:
            return
        _, doc, view = _get_active_doc_view(self.window)
        if doc is None or view is None:
            return
        off = _cursor_offset(doc)
        prv = prev_index(self._hits, off, wrap=True)
        if prv is None:
            return
        self._current = prv
        _apply_highlights(doc, self._hits, prv)
        self._update_label()
        self._select_hit(doc, view, self._hits[prv])

    def _on_window_key(self, window, event) -> bool:
        parts = decode_key_event(event)
        if parts is None:
            return False
        try:
            keyname, ctrl, shift, alt = parts
            lower = keyname.lower()

            # Escape hides bar when visible (even if entry not focused)
            if lower == "escape" and self.is_visible():
                self.hide()
                return True

            # Ctrl+F — show find (always, even when already visible → refocus)
            if ctrl and not shift and not alt and lower == "f":
                self.show()
                return True

            # F3 / Ctrl+G for next, Shift+F3 / Shift+Ctrl+G for prev — when bar visible
            is_f3 = lower in ("f3", "kp_f3")
            is_g = ctrl and not alt and lower == "g"
            if (is_f3 or is_g) and self.is_visible():
                if shift:
                    self._go_prev()
                else:
                    self._go_next()
                return True
            # also handle bare F3 when bar visible but hidden? if bar hidden but hits exist, still navigate?
            # For hidden bar, F3 should still work if we have query history — show bar and go next
            if (is_f3 or is_g) and not self.is_visible() and self._query:
                # if we have a previous query, show bar with it and go next
                self.show(self._query)
                if shift:
                    self._go_prev()
                else:
                    self._go_next()
                return True
        except Exception as e:
            logger.debug("find key handler failed: %r", e, exc_info=True)
        return False


def attach(window) -> FindManager | None:
    """Attach find bar to *window*. Returns manager or None (headless)."""
    if Gtk is None or window is None:
        logger.debug("Gtk not available, attach no-op")
        return None
    if getattr(window, "_thor_find_mgr", None) is not None:
        logger.debug("find already attached")
        return window._thor_find_mgr  # type: ignore[attr-defined]
    try:
        mgr = FindManager(window)
        # if bar creation failed, mgr.bar is None — treat as no-op
        if mgr.bar is None:
            logger.debug("find bar creation failed, not attached")
            return None
        return mgr
    except Exception as e:
        logger.debug(f"find attach failed: {e!r}")
        return None


def detach(window) -> None:
    """Detach find bar from *window*."""
    if window is None or Gtk is None:
        return
    mgr = getattr(window, "_thor_find_mgr", None)
    if mgr is None:
        return
    try:
        if mgr._handler_id is not None:
            try:
                window.disconnect(mgr._handler_id)
            except Exception:
                pass
        if mgr._tab_handler is not None:
            try:
                window.disconnect(mgr._tab_handler)
            except Exception:
                pass
        if mgr._buffer_handler and mgr._current_doc:
            try:
                mgr._current_doc.disconnect(mgr._buffer_handler)
            except Exception:
                pass
        if mgr.bar is not None:
            try:
                parent = mgr.bar.get_parent()
                if parent is not None:
                    parent.remove(mgr.bar)
                mgr.bar.destroy()
            except Exception:
                pass
    except Exception:
        pass
    try:
        delattr(window, "_thor_find_mgr")
    except Exception:
        try:
            window._thor_find_mgr = None  # type: ignore[attr-defined]
        except Exception:
            pass


def create_manager(window):
    """Test helper: return object with _handle_global_key style if needed."""
    # For compatibility with other managers' test shims — find uses window key handler directly.
    return attach(window)


__all__ = ["FindManager", "FindBar", "attach", "detach", "create_manager", "TAG_MATCH", "TAG_CURRENT"]
