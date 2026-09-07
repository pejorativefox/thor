"""Headless regression tests for recent thor fixes (no display needed).

Covers: output Problems PROB_PATH/PROB_LINE mapping + row activation
jump, and roslyn initialize-error path (stays in error, no on_ready).
"""

import os
import tempfile
import types

import pytest


def test_problem_store_path_line_mapping_and_activation_jump():
    """PROB_PATH/PROB_LINE index the hidden path / 1-based line columns.

    Regression: a swapped index (or a stray NameError in the handler)
    broke problem -> editor jumps. Exercises the real handler with a
    fake store/model so it runs without a display.
    """
    pytest.importorskip("gi")
    from thor.csharp import output as out_mod

    assert (
        out_mod.PROB_SEV,
        out_mod.PROB_FILE,
        out_mod.PROB_LINE,
        out_mod.PROB_MSG,
        out_mod.PROB_PATH,
    ) == (0, 1, 2, 3, 4)
    handler = getattr(out_mod.OutputView, "_on_problem_activated", None)
    if handler is None:
        pytest.skip("OutputView needs GTK typelibs")

    with tempfile.NamedTemporaryFile(suffix=".cs", delete=False) as f:
        f.write(b"// x\n")
        target = f.name
    try:
        line1 = 7

        class FakeStore:
            def get_iter(self, path):
                return path

            def get_value(self, _it, col):
                if col == out_mod.PROB_PATH:
                    return target
                if col == out_mod.PROB_LINE:
                    return line1
                return None

        emitted = []
        view = types.SimpleNamespace(
            problem_store=FakeStore(), emit=lambda *a: emitted.append(a)
        )
        handler(view, None, FakeStore().get_iter(0), None)  # must not NameError
        assert emitted == [("jump-to", target, line1 - 1, 0)], emitted
    finally:
        os.unlink(target)


def test_roslyn_initialize_error_stays_error_without_ready():
    """An initialize error (or empty capabilities) pins error state."""
    from thor.csharp import roslyn

    ready = []
    errors = []
    mgr = roslyn.RoslynManager(on_error=errors.append, on_ready=ready.append)
    mgr._on_initialize_result(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "boom"}}
    )
    assert mgr.state == "error", mgr.state
    assert ready == [], ready
    assert len(errors) == 1 and "boom" in errors[0], errors

    ready.clear()
    errors.clear()
    mgr2 = roslyn.RoslynManager(on_error=errors.append, on_ready=ready.append)
    mgr2._on_initialize_result({"jsonrpc": "2.0", "id": 2, "result": {}})
    assert mgr2.state == "error", mgr2.state
    assert ready == [], ready
    assert len(errors) == 1, errors
