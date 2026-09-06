"""Side-panel Solution Explorer. GTK-only; imported lazily by __init__."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import GObject, Gtk, Gdk  # type: ignore

    _GTK_AVAILABLE = True
except Exception:  # headless
    GObject = Gtk = Gdk = None  # type: ignore
    _GTK_AVAILABLE = False

from .solution import ProjectInfo, SolutionModel, project_tree
(COL_LABEL, COL_PATH, COL_KIND, COL_HINT) = range(4)


if not _GTK_AVAILABLE:
    class SolutionExplorer:  # type: ignore
        def __init__(self, *a, **kw): pass
        def set_model(self, *a, **kw): pass
        def connect(self, *a, **kw): return 0
        def show_all(self, *a, **kw): pass
        def destroy(self, *a, **kw): pass
        __gsignals__ = {}
else:

    class SolutionExplorer(Gtk.Box):
        __gsignals__ = {
            "open-file": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "build-solution": (GObject.SignalFlags.RUN_LAST, None, ()),
            "build-project": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "run-project": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "test-project": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "restore": (GObject.SignalFlags.RUN_LAST, None, ()),
            "refresh": (GObject.SignalFlags.RUN_LAST, None, ()),
        }

        def __init__(self) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self._model: SolutionModel | None = None
            self._loaded = False

            toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            for label, signal in (
                ("Build", "build-solution"),
                ("Restore", "restore"),
                ("Refresh", "refresh"),
            ):
                btn = Gtk.Button.new_with_label(label)
                btn.connect("clicked", lambda _b, s=signal: self.emit(s))
                toolbar.pack_start(btn, True, True, 0)
            self.pack_start(toolbar, False, False, 0)

            self.store = Gtk.TreeStore(str, str, str, str)
            self.tree = Gtk.TreeView.new_with_model(self.store)
            self.tree.set_headers_visible(False)
            col = Gtk.TreeViewColumn("Solution")
            cell = Gtk.CellRendererText()
            col.pack_start(cell, True)
            col.add_attribute(cell, "text", COL_LABEL)
            self.tree.append_column(col)
            self.tree.connect("row-activated", self._on_row_activated)
            self.tree.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.tree.connect("button-press-event", self._on_button_press)

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(self.tree)
            self.pack_start(scrolled, True, True, 0)
            self.show_all()

        # -- model -------------------------------------------------------
        def _expanded_paths(self) -> set:
            """Index-tuples of currently expanded rows (for rebuild restore)."""
            out: set = set()

            def walk(tree_iter, prefix: tuple) -> None:
                index = 0
                current = tree_iter
                while current is not None:
                    path = prefix + (index,)
                    try:
                        from gi.repository import Gtk as _Gtk

                        if self.tree.row_expanded(_Gtk.TreePath.new_from_indices(list(path))):
                            out.add(path)
                    except Exception:
                        pass
                    try:
                        child = self.store.iter_children(current)
                    except Exception:
                        child = None
                    if child is not None:
                        walk(child, path)
                    try:
                        current = self.store.iter_next(current)
                    except Exception:
                        current = None
                    index += 1

            try:
                first = self.store.get_iter_first()
            except Exception as e:
                logger.debug(f"explorer expanded scan failed: {e!r}")
                return out
            if first is not None:
                walk(first, ())
            return out

        def set_model(self, model: SolutionModel) -> None:
            try:
                old_path = self._model.path if self._model else None
                new_path = model.path if model else None
                if old_path != new_path:
                    self._loaded = False
            except Exception:
                pass
            self._model = model
            keep_expanded = self._expanded_paths() if self._loaded else set()
            self.store.clear()
            sln_label = os.path.basename(model.path) if model.path else f"{os.path.basename(model.root_dir)}/ (no .sln/.slnx)"
            sln_iter = self.store.append(None, [sln_label, model.path or model.root_dir, "solution", ""])
            for project in model.projects:
                hint = ", ".join(project.target_frameworks) or project.output_type
                if project.is_test_project:
                    hint = (hint + " [tests]").strip()
                proj_iter = self.store.append(sln_iter, [project.name, project.path, "project", hint])
                try:
                    self._append_tree(proj_iter, project_tree(os.path.dirname(project.path)))
                except Exception as e:
                    logger.debug(f"explorer tree failed: {e!r}")
            from gi.repository import Gtk as _Gtk

            if not self._loaded:
                self.tree.collapse_all()
                self._loaded = True
                try:
                    if self.store.get_iter_first() is not None:
                        self.tree.expand_row(_Gtk.TreePath.new_from_indices([0]), False)
                except Exception:
                    pass
            else:
                for path in sorted(keep_expanded):
                    try:
                        self.tree.expand_row(_Gtk.TreePath.new_from_indices(list(path)), False)
                    except Exception:
                        continue

        def _append_tree(self, parent, nodes) -> None:
            for node in nodes:
                if node.is_dir:
                    folder_iter = self.store.append(parent, [node.name + "/", node.path, "folder", ""])
                    self._append_tree(folder_iter, node.children)
                else:
                    self.store.append(parent, [node.name, node.path, "file", ""])

        # -- interaction -------------------------------------------------
        def _selected(self):
            selection = self.tree.get_selection()
            model, tree_iter = selection.get_selected()
            if tree_iter is None:
                return None
            return (
                model.get_value(tree_iter, COL_LABEL),
                model.get_value(tree_iter, COL_PATH),
                model.get_value(tree_iter, COL_KIND),
            )

        def _on_row_activated(self, _tree, path, _col) -> None:
            tree_iter = self.store.get_iter(path)
            kind = self.store.get_value(tree_iter, COL_KIND)
            fpath = self.store.get_value(tree_iter, COL_PATH)
            if kind == "file":
                if os.path.isfile(fpath):
                    self.emit("open-file", fpath)
                    return
                try:
                    self.emit("refresh")
                except Exception:
                    pass
                return
            elif kind == "folder":
                if self.tree.row_expanded(path):
                    self.tree.collapse_row(path)
                else:
                    self.tree.expand_row(path, False)

        def _on_button_press(self, _tree, event) -> bool:
            if event.button != 3:
                return False
            hit = self.tree.get_path_at_pos(int(event.x), int(event.y))
            if not hit:
                return False
            path, _col, _x, _y = hit
            self.tree.get_selection().select_path(path)
            selected = self._selected()
            if not selected:
                return False
            _label, fpath, kind = selected
            if kind == "folder":
                if self.tree.row_expanded(path):
                    self.tree.collapse_row(path)
                else:
                    self.tree.expand_row(path, False)
                return True
            menu = Gtk.Menu()
            if kind == "file":
                item = Gtk.MenuItem.new_with_label("Open")
                item.connect("activate", lambda _i: self.emit("open-file", fpath))
                menu.append(item)
            elif kind == "project":
                for label, signal in (
                    ("Build project", "build-project"),
                    ("Run project", "run-project"),
                    ("Test project", "test-project"),
                ):
                    item = Gtk.MenuItem.new_with_label(label)
                    item.connect("activate", lambda _i, s=signal, p=fpath: self.emit(s, p))
                    menu.append(item)
            menu.show_all()
            menu.popup_at_pointer(event)
            return True


    def describe_project(project: ProjectInfo) -> str:
        tfm = ",".join(project.target_frameworks) or "?"
        return f"{project.name} ({tfm}, {project.output_type})"
