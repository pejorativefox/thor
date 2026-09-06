"""Unit tests for thor-open script and out-of-tree target resolution."""

import importlib.machinery
import os
import subprocess
import tempfile
import pytest

from thor.app import _resolve_initial_target_with_lines, _split_location_arg


def _load_thor_open():
    path = os.path.join(os.path.dirname(__file__), "..", "thor-open")
    loader = importlib.machinery.SourceFileLoader("thor_open", path)
    return loader.load_module()


def test_thor_open_parse_location():
    thor_open = _load_thor_open()
    assert thor_open.parse_location("foo.cs") == ("foo.cs", None, None)
    assert thor_open.parse_location("foo.cs:10") == ("foo.cs", 10, None)
    assert thor_open.parse_location("foo.cs:10:5") == ("foo.cs", 10, 5)
    assert thor_open.parse_location("foo.cs(10)") == ("foo.cs", 10, None)
    assert thor_open.parse_location("foo.cs(10,5)") == ("foo.cs", 10, 5)
    assert thor_open.parse_location("foo.cs(10:5)") == ("foo.cs", 10, 5)


def test_thor_open_passes_absolute_path_and_line():
    thor_open = _load_thor_open()
    calls = []
    saved_popen = thor_open.subprocess.Popen
    thor_open.subprocess.Popen = lambda argv, **kw: calls.append((argv, kw))
    try:
        res = thor_open.open_location("relative/file.cs", 42, 10)
        assert res == 0
        assert len(calls) == 1
        argv, kw = calls[0]
        assert "+42:10" in argv
        expected_abs = os.path.abspath("relative/file.cs")
        assert expected_abs in argv
        assert kw.get("start_new_session") is True
    finally:
        thor_open.subprocess.Popen = saved_popen


def test_resolve_initial_target_out_of_tree_with_client_cwd():
    with tempfile.TemporaryDirectory() as client_dir:
        out_file = os.path.join(client_dir, "out_of_tree.cs")
        with open(out_file, "w") as f:
            f.write("// out of tree")

        # Passing relative file path with client cwd
        folder, files, lines = _resolve_initial_target_with_lines(
            ["thor", "+15:3", "out_of_tree.cs"],
            cwd=client_dir,
        )
        assert folder is None
        assert files == [out_file]
        assert lines == {out_file: (15, 3)}

        # Passing file:line:col path
        folder, files, lines = _resolve_initial_target_with_lines(
            ["thor", f"{out_file}:35:5"],
            cwd="/some/other/cwd",
        )
        assert folder is None
        assert files == [out_file]
        assert lines == {out_file: (35, 5)}

        # Passing absolute file path without line
        folder, files, lines = _resolve_initial_target_with_lines(
            ["thor", out_file],
            cwd="/some/other/cwd",
        )
        assert folder is None
        assert files == [out_file]


def test_resolve_initial_target_non_existent_file_extension():
    folder, files, lines = _resolve_initial_target_with_lines(
        ["thor", "/tmp/non_existent_file.cs"]
    )
    assert folder is None
    assert files == [os.path.abspath("/tmp/non_existent_file.cs")]
