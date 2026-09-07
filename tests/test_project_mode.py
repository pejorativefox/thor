"""Project browser tree building (headless)."""

import os
import tempfile

import thor.project as projectmode


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
    with tempfile.TemporaryDirectory() as tmp:
        with tempfile.TemporaryDirectory() as elsewhere:
            _touch(os.path.join(elsewhere, "from-link.txt"))
            os.symlink(elsewhere, os.path.join(tmp, "linked"))
        _touch(os.path.join(tmp, "real", "ok.txt"))

        nodes = projectmode.build_file_tree(tmp)
        assert [(n.name, n.is_dir) for n in nodes] == [("real", True)]
