"""Window routing policy: folders open new windows, files reuse (headless)."""

from __future__ import annotations

from thor.app import _argv_has_path_arg, _decide_window_action


def _decide(**kw):
    base = {"has_window": True, "new_window": False,
            "folder_explicit": False, "has_files": False}
    base.update(kw)
    return _decide_window_action(**base)


def test_argv_has_path_arg():
    assert _argv_has_path_arg(["thor"]) is False
    assert _argv_has_path_arg(["thor", "--new-window"]) is False
    assert _argv_has_path_arg(["thor", "-n"]) is False
    assert _argv_has_path_arg(["thor", "+10"]) is False
    assert _argv_has_path_arg(["thor", "-x"]) is False
    assert _argv_has_path_arg(["thor", "."]) is True
    assert _argv_has_path_arg(["thor", "file.cs"]) is True
    assert _argv_has_path_arg(["thor", "+10", "file.cs"]) is True
    assert _argv_has_path_arg(["thor", "--new-window", "proj"]) is True


def test_no_window_always_new():
    assert _decide(has_window=False) == "new"
    assert _decide(has_window=False, folder_explicit=True, has_files=True) == "new"


def test_new_window_flag_wins():
    assert _decide(new_window=True) == "new"
    assert _decide(new_window=True, has_files=True) == "new"


def test_explicit_folder_opens_new_window():
    assert _decide(folder_explicit=True) == "new"
    assert _decide(folder_explicit=True, has_files=True) == "new"


def test_files_only_reuses_window():
    assert _decide(has_files=True) == "reuse"


def test_bare_invocation_presents():
    assert _decide() == "present"
