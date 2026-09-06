"""Bottom-panel output + problems views. GTK-only; imported lazily by __init__."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Problems ListStore column indices: (severity, file, line1, message, path).
# The path column is hidden (no TreeViewColumn) but used for jump-to.
(PROB_SEV, PROB_FILE, PROB_LINE, PROB_MSG, PROB_PATH) = range(5)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GObject, Gtk, GLib  # type: ignore

    _GTK_AVAILABLE = True
except Exception:  # headless
    GObject = Gtk = GLib = None  # type: ignore
    _GTK_AVAILABLE = False



if not _GTK_AVAILABLE:
    class OutputView:  # type: ignore
        def __init__(self, *a, **kw): pass
        def append(self, *a, **kw): pass
        def set_status(self, *a, **kw): pass
        def set_problems(self, *a, **kw): pass
        def show_problems(self, *a, **kw): pass
        def connect(self, *a, **kw): return 0
        def show_all(self, *a, **kw): pass
        def destroy(self, *a, **kw): pass
        __gsignals__ = {}
else:

    class OutputView(Gtk.Box):
        __gsignals__ = {
            "jump-to": (
                GObject.SignalFlags.RUN_LAST,
                None,
                (GObject.TYPE_STRING, GObject.TYPE_INT, GObject.TYPE_INT),
            ),
        }

        def __init__(self) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.status_label = Gtk.Label(label="Ready")
            self.status_label.set_xalign(0.0)
            self.status_label.set_hexpand(True)
            clear_btn = Gtk.Button.new_with_label("Clear")
            clear_btn.connect("clicked", lambda _b: self.clear())
            toolbar.pack_start(self.status_label, True, True, 0)
            toolbar.pack_start(clear_btn, False, False, 0)
            self.pack_start(toolbar, False, False, 0)

            self.notebook = Gtk.Notebook()
            self.notebook.set_scrollable(True)
            self.pack_start(self.notebook, True, True, 0)

            # -- Output page (existing behavior) --
            self.textview = Gtk.TextView()
            self.textview.set_editable(False)
            self.textview.set_monospace(True)
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            out_scrolled = Gtk.ScrolledWindow()
            out_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            out_scrolled.add(self.textview)
            self.notebook.append_page(out_scrolled, Gtk.Label(label="Output"))

            # -- Problems page (clickable diagnostics / references) --
            self.problem_store = Gtk.ListStore(str, str, int, str, str)
            self.problem_tree = Gtk.TreeView.new_with_model(self.problem_store)
            self.problem_tree.set_headers_visible(True)
            for index, title in ((PROB_SEV, "Severity"), (PROB_FILE, "File"), (PROB_LINE, "Line"), (PROB_MSG, "Message")):
                col = Gtk.TreeViewColumn(title)
                cell = Gtk.CellRendererText()
                col.pack_start(cell, True)
                col.add_attribute(cell, "text", index)
                self.problem_tree.append_column(col)
            self.problem_tree.connect("row-activated", self._on_problem_activated)
            prob_scrolled = Gtk.ScrolledWindow()
            prob_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            prob_scrolled.add(self.problem_tree)
            self.notebook.append_page(prob_scrolled, Gtk.Label(label="Problems"))
            self.show_all()

        def _append(self, text: str) -> None:
            buf = self.textview.get_buffer()
            end = buf.get_end_iter()
            buf.insert(end, text)
            mark = buf.create_mark(None, buf.get_end_iter(), False)
            try:
                self.textview.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
            except Exception:
                logger.debug("OutputView scroll failed", exc_info=True)

        def append(self, text: str) -> None:
            GLib.idle_add(self._append, text)

        def clear(self) -> None:
            self.textview.get_buffer().set_text("")

        def set_status(self, text: str) -> None:
            logger.debug(f"status: {text}")
            GLib.idle_add(self.status_label.set_text, text)

        # -- problems ----------------------------------------------------
        def set_problems(self, rows: list[tuple[str, str, int, str, str]]) -> None:
            """Replace the Problems list. Rows: (severity, file, line1, message, path)."""

            def _apply() -> None:
                self.problem_store.clear()
                for row in rows:
                    self.problem_store.append(list(row))

            GLib.idle_add(_apply)

        def show_problems(self) -> None:
            GLib.idle_add(self.notebook.set_current_page, 1)

        def _on_problem_activated(self, _tree, path, _col) -> None:
            try:
                tree_iter = self.problem_store.get_iter(path)
                fpath = self.problem_store.get_value(tree_iter, PROB_PATH)
                line1 = int(self.problem_store.get_value(tree_iter, PROB_LINE))
            except Exception:
                logger.debug("problem activation failed", exc_info=True)
                return
            if fpath and os.path.isfile(fpath):
                self.emit("jump-to", fpath, max(0, line1 - 1), 0)
