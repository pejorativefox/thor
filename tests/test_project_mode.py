"""project-mode plugin behavior (headless)."""

import os
import sys
import tempfile
import types


import thor.project as projectmode
IS_THOR = True  # thor standalone, skip plugin UI tests


def _flatten(nodes):
    out = []
    for node in nodes:
        out.append((node.name, node.is_dir))
        out.extend(_flatten(node.children))
    return out


from conftest import _touch  # shared helper (see tests/conftest.py)


def test_build_file_tree_sorted_and_pruned():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "b-dir", "b.txt"))
        _touch(os.path.join(tmp, "A-dir", "a.txt"))
        _touch(os.path.join(tmp, "root.txt"))
        _touch(os.path.join(tmp, ".hidden", "secret.txt"))
        _touch(os.path.join(tmp, "bin", "skip.dll"))

        nodes = projectmode.build_file_tree(tmp)
        assert [(n.name, n.is_dir) for n in nodes] == [
            ("A-dir", True),
            ("b-dir", True),
            ("root.txt", False),
        ]
        flat = _flatten(nodes)
        assert ("secret.txt", False) not in flat
        assert ("skip.dll", False) not in flat


def test_build_file_tree_skips_symlink_dirs():
    if not hasattr(projectmode, "ProjectModePlugin"):
        import pytest; pytest.skip("ProjectModePlugin not available in thor")
    with tempfile.TemporaryDirectory() as tmp:
        with tempfile.TemporaryDirectory() as elsewhere:
            _touch(os.path.join(elsewhere, "from-link.txt"))
            os.symlink(elsewhere, os.path.join(tmp, "linked"))
        _touch(os.path.join(tmp, "real", "ok.txt"))

        nodes = projectmode.build_file_tree(tmp)
        assert [(n.name, n.is_dir) for n in nodes] == [("real", True)]


def test_keybinding_dispatches_choose_root():
    if not hasattr(projectmode, "ProjectModePlugin"):
        import pytest; pytest.skip("ProjectModePlugin not available in thor")
    cls = projectmode.ProjectModePlugin
    ns = types.SimpleNamespace()
    called = []
    ns._choose_root = lambda: called.append("choose")
    ns._handle_global_key = types.MethodType(cls._handle_global_key, ns)

    assert ns._handle_global_key("o", True, True, False) is True
    assert called == ["choose"]
    assert ns._handle_global_key("o", True, False, False) is False
    assert ns._handle_global_key("o", True, True, True) is False


def test_set_root_updates_browser_when_present():
    if not hasattr(projectmode, "ProjectModePlugin"):
        import pytest; pytest.skip("ProjectModePlugin not available in thor")
    cls = projectmode.ProjectModePlugin
    ns = types.SimpleNamespace(_root_dir=None)
    captured = []
    ns.browser = types.SimpleNamespace(set_root=lambda path: captured.append(path))
    ns._set_root = types.MethodType(cls._set_root, ns)

    with tempfile.TemporaryDirectory() as tmp:
        ns._set_root(tmp)
        assert ns._root_dir == os.path.abspath(tmp)
        assert captured == [os.path.abspath(tmp)]