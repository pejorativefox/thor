# -*- coding: utf-8 -*-
"""Font selection for editor, terminal and side panel.

Native ``Gtk.FontChooserDialog`` opened from the command palette; choices
persist in the existing window/panel state (``state.toml``) and are
re-applied at startup. Headless-safe: pure get/set works without a
display, all GTK/Pango work is guarded.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

KINDS = ("editor", "terminal", "panel")
KEYS = {
    "editor": "editor_font",
    "terminal": "terminal_font",
    "panel": "panel_font",
}

ROW_LABELS = {
    "editor": "Editor",
    "terminal": "Terminal",
    "panel": "Side panel",
}
#: FontDescription string; "" means system default (no override).
DEFAULTS = {"editor": "", "terminal": "", "panel": ""}

#: Creation-time cache (document/terminal have no window ref); seeded from
#: state.toml on first use, updated by set_font.
_CACHE: dict[str, str] = {}


def _seed_cache() -> dict[str, str]:
    if _CACHE:
        return _CACHE
    try:
        from .state import load_state

        saved = load_state()
    except Exception:
        saved = {}
    for kind in KINDS:
        try:
            val = saved.get(KEYS[kind], "")
        except Exception:
            val = ""
        _CACHE[kind] = val if isinstance(val, str) else ""
    return _CACHE


def current(kind: str, window=None) -> str:
    """Active font desc for *kind*; "" means system default."""
    if window is not None:
        try:
            val = getattr(window, "_panel_state", {}).get(KEYS[kind], "")
            if isinstance(val, str) and val:
                return val
        except Exception:
            pass
    _seed_cache()
    return _CACHE.get(kind, "")


def _font_desc(desc: str):
    try:
        from gi.repository import Pango  # type: ignore
    except Exception:
        return None
    try:
        return Pango.FontDescription(desc)
    except Exception:
        logger.debug("bad font desc %r", desc, exc_info=True)
        return None


def apply_to_view(view) -> bool:
    """Apply the editor font to one editor view. False when default/unavailable."""
    desc = _seed_cache().get("editor", "")
    if not desc or view is None:
        return False
    font = _font_desc(desc)
    if font is None:
        return False
    try:
        view.override_font(font)
        return True
    except Exception:
        logger.debug("editor font apply failed", exc_info=True)
        return False


def apply_to_term(term) -> bool:
    """Apply the terminal font to one Vte terminal. False when default/unavailable."""
    desc = _seed_cache().get("terminal", "")
    if not desc or term is None:
        return False
    font = _font_desc(desc)
    if font is None:
        return False
    try:
        term.set_font(font)
        return True
    except Exception:
        logger.debug("terminal font apply failed", exc_info=True)
        return False


def apply_to_panel_widget(widget, desc: str | None = None) -> None:
    """Recursively override fonts under the side panel (labels, tree, entries)."""
    if widget is None:
        return
    if desc is None:
        desc = _seed_cache().get("panel", "")
    if not desc:
        return
    font = _font_desc(desc)
    if font is None:
        return
    # ponytail: iterative walk capped at 512 widgets; deeper trees keep defaults
    seen = 0
    stack = [widget]
    while stack and seen < 512:
        node = stack.pop()
        seen += 1
        try:
            node.override_font(font)
        except Exception:
            pass
        try:
            children = node.get_children()
        except Exception:
            children = None
        if children:
            try:
                stack.extend(children)
            except Exception:
                pass


def apply_all(window) -> None:
    """Apply all three fonts to a live window. Never raises."""
    if window is None:
        return
    try:
        for view in window.get_views():
            try:
                apply_to_view(view)
            except Exception:
                continue
    except Exception:
        logger.debug("font apply: views failed", exc_info=True)
    try:
        panel = getattr(window, "_thor_terminal_panel", None)
        notebook = getattr(panel, "notebook", None)
        if notebook is not None:
            for i in range(notebook.get_n_pages()):
                try:
                    apply_to_term(notebook.get_nth_page(i))
                except Exception:
                    continue
    except Exception:
        logger.debug("font apply: terminal failed", exc_info=True)
    try:
        side = window.get_side_panel()
        if side is not None:
            apply_to_panel_widget(side)
    except Exception:
        logger.debug("font apply: panel failed", exc_info=True)


def set_font(window, kind: str, desc: str) -> bool:
    """Persist *desc* for *kind* and apply immediately. Never raises."""
    if kind not in KEYS or not isinstance(desc, str):
        return False
    _seed_cache()[kind] = desc
    if window is not None:
        try:
            state = getattr(window, "_panel_state", None)
            if isinstance(state, dict):
                state[KEYS[kind]] = desc
        except Exception:
            logger.debug("font persist failed", exc_info=True)
        try:
            from .state import save_state

            save_state(getattr(window, "_panel_state", {}))
        except Exception:
            logger.debug("font save failed", exc_info=True)
        try:
            apply_all(window)
        except Exception:
            logger.debug("font apply failed", exc_info=True)
    return True


def choose_all(window) -> None:
    """One window with a native font picker per surface. Headless-safe no-op."""
    try:
        from gi.repository import Gtk  # type: ignore
    except Exception:
        return
    if Gtk is None or window is None:
        return
    try:
        dlg = Gtk.Dialog(
            "Select Fonts",
            window,
            Gtk.DialogFlags.MODAL,
            ("Close", Gtk.ResponseType.CLOSE),
        )
    except Exception as e:
        logger.warning("font window create failed: %r", e, exc_info=True)
        return
    try:
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        try:
            grid.set_margin_top(12)
            grid.set_margin_bottom(12)
            grid.set_margin_start(12)
            grid.set_margin_end(12)
        except Exception:
            pass
        for row, kind in enumerate(KINDS):
            try:
                label = Gtk.Label(label=ROW_LABELS[kind])
                label.set_xalign(0.0)
                grid.attach(label, 0, row, 1, 1)
            except Exception:
                logger.debug("font row label failed", exc_info=True)
                continue
            try:
                button = Gtk.FontButton()
                existing = current(kind, window)
                if existing:
                    try:
                        button.set_font_name(existing)
                    except Exception:
                        pass

                def _on_set(btn, _kind=kind):
                    try:
                        picked = btn.get_font_name()
                    except Exception:
                        picked = None
                    if picked:
                        set_font(window, _kind, picked)

                button.connect("font-set", _on_set)
                grid.attach(button, 1, row, 1, 1)
            except Exception:
                logger.debug("font row picker failed", exc_info=True)
        dlg.get_content_area().add(grid)
        dlg.show_all()
        dlg.run()
    except Exception as e:
        logger.warning("font window failed: %r", e, exc_info=True)
    finally:
        try:
            dlg.destroy()
        except Exception:
            pass
