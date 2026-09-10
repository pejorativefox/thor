"""Solution explorer expansion behavior (needs a display, like test_gscompletion)."""

import os
import sys
import tempfile

import pytest



def _gui():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gtk

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        if not ok:
            return None
        return Gtk
    except Exception:
        return None


_GUI = _gui()


def _model(tmp):
    from thor.csharp.solution import ProjectInfo, SolutionModel

    proj_dir = os.path.join(tmp, "src", "App")
    os.makedirs(os.path.join(proj_dir, "Sub"))
    for name in ("Top.cs", os.path.join("Sub", "Deep.cs")):
        with open(os.path.join(proj_dir, name), "w") as f:
            f.write("// x")
    csproj = os.path.join(proj_dir, "App.csproj")
    with open(csproj, "w") as f:
        f.write("<Project/>")
    sln = os.path.join(tmp, "App.sln")
    with open(sln, "w") as f:
        f.write("x")
    model = SolutionModel(path=sln, root_dir=tmp)
    model.projects.append(ProjectInfo(path=csproj, name="App"))
    return model


def _expanded(explorer, Gtk):
    return explorer.tree.row_expanded(Gtk.TreePath.new_from_indices([0, 0]))


def test_collapsed_on_first_load_only():
    if _GUI is None:
        pytest.skip("no display")
    Gtk = _GUI
    from thor.csharp.explorer import SolutionExplorer

    with tempfile.TemporaryDirectory() as tmp:
        model = _model(tmp)
        explorer = SolutionExplorer()
        explorer.set_model(model)
        assert explorer.tree.row_expanded(Gtk.TreePath.new_from_indices([0]))
        assert not _expanded(explorer, Gtk)
        explorer.tree.expand_row(Gtk.TreePath.new_from_indices([0, 0]), False)
        assert _expanded(explorer, Gtk)
        explorer.set_model(model)
        assert _expanded(explorer, Gtk)


def _model_at(tmp, name):
    from thor.csharp.solution import ProjectInfo, SolutionModel

    proj_dir = os.path.join(tmp, name)
    os.makedirs(proj_dir)
    with open(os.path.join(proj_dir, "Top.cs"), "w") as f:
        f.write("// x")
    csproj = os.path.join(proj_dir, f"{name}.csproj")
    with open(csproj, "w") as f:
        f.write("<Project/>")
    sln = os.path.join(tmp, f"{name}.sln")
    with open(sln, "w") as f:
        f.write("x")
    model = SolutionModel(path=sln, root_dir=tmp)
    model.projects.append(ProjectInfo(path=csproj, name=name))
    return model


def test_new_solution_resets_to_top_level():
    if _GUI is None:
        pytest.skip("no display")
    Gtk = _GUI
    from thor.csharp.explorer import SolutionExplorer

    with tempfile.TemporaryDirectory() as tmp:
        first = _model_at(tmp, "One")
        second = _model_at(tmp, "Two")
        explorer = SolutionExplorer()
        explorer.set_model(first)
        explorer.tree.expand_row(Gtk.TreePath.new_from_indices([0, 0]), False)
        assert _expanded(explorer, Gtk)
        explorer.set_model(second)
        assert explorer.tree.row_expanded(Gtk.TreePath.new_from_indices([0]))
        assert not _expanded(explorer, Gtk)


def test_double_click_keeps_expansion():
    if _GUI is None:
        pytest.skip("no display")
    Gtk = _GUI
    from thor.csharp.explorer import SolutionExplorer

    with tempfile.TemporaryDirectory() as tmp:
        model = _model(tmp)
        explorer = SolutionExplorer()
        opened: list = []
        explorer.connect("open-file", lambda _w, p: opened.append(p))
        explorer.set_model(model)
        explorer.tree.expand_row(Gtk.TreePath.new_from_indices([0]), False)
        explorer.tree.expand_row(Gtk.TreePath.new_from_indices([0, 0]), False)
        # Top.cs sits directly under the project node: path [0, 0, 0]
        # (Sub/ sorts before Top.cs).
        explorer._on_row_activated(explorer.tree, Gtk.TreePath.new_from_indices([0, 0, 1]), None)
        assert opened and opened[0].endswith("Top.cs"), opened
        explorer.set_model(model)
        assert _expanded(explorer, Gtk)


def test_set_model_uses_precomputed_trees(monkeypatch):
    if _GUI is None:
        pytest.skip("no display")
    Gtk = _GUI
    from thor.csharp import explorer as explorer_mod
    from thor.csharp.explorer import SolutionExplorer
    from thor.csharp.solution import FileNode

    with tempfile.TemporaryDirectory() as tmp:
        model = _model(tmp)
        pre = os.path.join(tmp, "Pre.cs")
        with open(pre, "w") as f:
            f.write("// precomputed")
        model.trees = {
            model.projects[0].path: [FileNode("Pre.cs", pre, False, [])]
        }

        def _boom(_dir):
            raise AssertionError("must not walk the disk with precomputed trees")

        monkeypatch.setattr(explorer_mod, "project_tree", _boom)
        explorer = SolutionExplorer()
        explorer.set_model(model)
        sln = explorer.store.get_iter_first()
        proj = explorer.store.iter_children(sln)
        first_file = explorer.store.iter_children(proj)
        assert explorer.store.get_value(first_file, 0) == "Pre.cs"
