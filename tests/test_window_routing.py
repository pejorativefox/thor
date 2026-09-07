"""Launch routing: one process per window, no in-process reuse (headless).

Explicit folders are gated by the per-root lock in `thor.app.main`
(second process for a live root is refused); file-only launches always
spawn isolated processes. `_decide_window_action` remains as a
deprecated pure helper (no longer consulted by the application).
"""

from __future__ import annotations

from thor.app import _argv_has_path_arg


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


def test_explicit_folder_gate_uses_lock():
    # The same-root gate lives in thor.lock (covered by test_root_lock.py):
    # an explicit folder arg is what triggers it. Bare invocations and
    # file-only launches never take the gate.
    assert _argv_has_path_arg(["thor", "."]) is True
    assert _argv_has_path_arg(["thor"]) is False
