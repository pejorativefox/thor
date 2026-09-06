"""Side-panel Test Explorer. GTK-only; imported lazily by __init__."""

from __future__ import annotations

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GObject, Gtk  # type: ignore

    _GTK_AVAILABLE = True
except Exception:  # headless
    GObject = Gtk = None  # type: ignore
    _GTK_AVAILABLE = False

(COL_LABEL, COL_PROJECT, COL_FQN, COL_OUTCOME) = range(4)

OUTCOME_GLYPH = {"Passed": "✓", "Failed": "✗", "Skipped": "○", "NotRun": "·", "Running": "…"}
OUTCOME_ORDER = {"Failed": 0, "Passed": 1, "Skipped": 2, "NotRun": 3, "Running": 4}


if not _GTK_AVAILABLE:
    class TestPanel:  # type: ignore
        def __init__(self, *a, **kw): pass
        def set_projects(self, *a, **kw): pass
        def set_tests(self, *a, **kw): pass
        def set_status(self, *a, **kw): pass
        def mark_running(self, *a, **kw): pass
        def apply_results(self, *a, **kw): pass
        def connect(self, *a, **kw): return 0
        def show_all(self, *a, **kw): pass
        def destroy(self, *a, **kw): pass
        __gsignals__ = {}
else:

    class TestPanel(Gtk.Box):
        __gsignals__ = {
            "run-test": (
                GObject.SignalFlags.RUN_LAST,
                None,
                (GObject.TYPE_STRING, GObject.TYPE_STRING),
            ),
            "run-all-tests": (GObject.SignalFlags.RUN_LAST, None, ()),
            "refresh-tests": (GObject.SignalFlags.RUN_LAST, None, ()),
        }

        def __init__(self) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            for label, handler in (
                ("Run All", lambda _b: self.emit("run-all-tests")),
                ("Refresh", lambda _b: self.emit("refresh-tests")),
            ):
                btn = Gtk.Button.new_with_label(label)
                btn.connect("clicked", handler)
                toolbar.pack_start(btn, True, True, 0)
            self.pack_start(toolbar, False, False, 0)

            self.status = Gtk.Label(label="No tests discovered yet.")
            self.status.set_xalign(0.0)
            self.pack_start(self.status, False, False, 0)

            self.store = Gtk.TreeStore(str, str, str, str)
            self.tree = Gtk.TreeView.new_with_model(self.store)
            self.tree.set_headers_visible(False)
            for index, expand in ((0, False), (1, True)):
                col = Gtk.TreeViewColumn("Tests")
                cell = Gtk.CellRendererText()
                col.pack_start(cell, expand)
                col.add_attribute(cell, "text", index)
                self.tree.append_column(col)
            self.tree.connect("row-activated", self._on_row_activated)
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(self.tree)
            self.pack_start(scrolled, True, True, 0)
            self.show_all()

        # -- model -------------------------------------------------------
        def set_projects(self, projects: list[tuple[str, str]]) -> None:
            """Show test projects while discovery runs. Items: (name, csproj path)."""
            self.store.clear()
            for name, csproj in projects:
                self.store.append(None, [name, csproj, "", "Running"])
            self.tree.expand_all()
            self.set_status(f"Discovering tests in {len(projects)} project(s)…")

        def set_tests(self, tests_by_project: dict[str, list[str]]) -> None:
            self.store.clear()
            total = 0
            for project, names in sorted(tests_by_project.items()):
                short = project.split("/")[-1]
                proj_iter = self.store.append(None, [f"{short} ({len(names)})", project, "", "NotRun"])
                for name in sorted(names):
                    self.store.append(proj_iter, [f"· {name}", project, name, "NotRun"])
                    total += 1
            self.tree.expand_all()
            self.set_status(f"{total} test(s) in {len(tests_by_project)} project(s).")

        def set_status(self, text: str) -> None:
            try:
                self.status.set_text(text)
            except Exception:
                pass

        def mark_running(self, project: str, fqn: str | None = None) -> None:
            def _walk(tree_iter):
                while tree_iter:
                    prow = self.store.get_value(tree_iter, 1)
                    frow = self.store.get_value(tree_iter, 2)
                    if prow == project and (fqn is None or frow == fqn or not frow):
                        glyph = OUTCOME_GLYPH["Running"]
                        label = self.store.get_value(tree_iter, 0)
                        base = label[2:] if label[:1] in "✓✗○·…" and label[1:2] == " " else label
                        self.store.set(tree_iter, 0, f"{glyph} {base}", 3, "Running")
                    child = self.store.iter_children(tree_iter)
                    if child:
                        _walk(child)
                    tree_iter = self.store.iter_next(tree_iter)

            _walk(self.store.get_iter_first())

        def apply_results(self, project: str, outcomes: dict[str, str]) -> None:
            """Update rows from {fullyQualifiedName: outcome}."""

            def _walk(tree_iter):
                while tree_iter:
                    prow = self.store.get_value(tree_iter, 1)
                    frow = self.store.get_value(tree_iter, 2)
                    if prow == project and frow and frow in outcomes:
                        outcome = outcomes[frow]
                        glyph = OUTCOME_GLYPH.get(outcome, "?")
                        label = self.store.get_value(tree_iter, 0)
                        base = label[2:] if label[:1] in "✓✗○·…" and label[1:2] == " " else label
                        self.store.set(tree_iter, 0, f"{glyph} {base}", 3, outcome)
                    child = self.store.iter_children(tree_iter)
                    if child:
                        _walk(child)
                    tree_iter = self.store.iter_next(tree_iter)

            _walk(self.store.get_iter_first())
            counts: dict[str, int] = {}
            for outcome in outcomes.values():
                counts[outcome] = counts.get(outcome, 0) + 1
            summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items()))
            self.set_status(f"{project.split('/')[-1]}: {summary or 'no results'}")

        # -- interaction -------------------------------------------------
        def _selected_test(self):
            selection = self.tree.get_selection()
            _model, tree_iter = selection.get_selected()
            if tree_iter is None:
                return None
            return (
                self.store.get_value(tree_iter, COL_PROJECT),
                self.store.get_value(tree_iter, COL_FQN),
            )

        def _on_row_activated(self, _tree, path, _col) -> None:
            tree_iter = self.store.get_iter(path)
            project = self.store.get_value(tree_iter, COL_PROJECT)
            fqn = self.store.get_value(tree_iter, COL_FQN)
            if project:
                self.emit("run-test", project, fqn or "")
