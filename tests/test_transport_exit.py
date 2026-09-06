"""Unit tests for server-death handling (headless, no GTK)."""

import os
import sys
import tempfile
import threading
import time


from thor.csharp import lsp_transport, roslyn


def _wait_for(predicate, timeout_s=10.0):
    end = time.time() + timeout_s
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_transport_reports_clean_exit():
    seen: list = []
    done = threading.Event()

    def _on_exit(rc) -> None:
        seen.append(rc)
        done.set()

    t = lsp_transport.LspTransport(["true"], lambda _m: None, on_exit=_on_exit)
    t.start()
    assert done.wait(10), "on_exit never fired"
    assert seen == [0], seen
    assert t.running is False


def test_transport_captures_stderr_and_exit_code():
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "stderr.log")
        seen: list = []
        done = threading.Event()

        def _on_exit(rc) -> None:
            seen.append(rc)
            done.set()

        t = lsp_transport.LspTransport(
            ["sh", "-c", "echo boom >&2; echo details-here >&2; exit 3"],
            lambda _m: None,
            on_exit=_on_exit,
            stderr_log_path=log,
        )
        t.start()
        assert done.wait(10), "on_exit never fired"
        assert seen == [3], seen
        tail = " ".join(t.stderr_tail())
        assert "boom" in tail and "details-here" in tail, tail
        with open(log, encoding="utf-8") as f:
            logged = f.read()
        assert "boom" in logged


def test_transport_send_after_death_is_quiet():
    import io
    from contextlib import redirect_stderr

    t = lsp_transport.LspTransport(["true"], lambda _m: None)
    t.start()
    assert _wait_for(lambda: not t.running), "transport never noticed exit"
    buf = io.StringIO()
    with redirect_stderr(buf):
        for _ in range(5):
            t.send({"jsonrpc": "2.0", "method": "ping", "params": {}})
    # First failure logs once; the rest are suppressed (no BrokenPipe spam).
    assert buf.getvalue().count("send failed") <= 1, buf.getvalue()


def test_roslyn_manager_death_resets_state_and_reports():
    import types

    errors = []
    mgr = roslyn.RoslynManager(on_error=errors.append)
    mgr.state = "starting"
    mgr.transport = types.SimpleNamespace(  # type: ignore[assignment]
        stderr_tail=lambda: ["kaboom tail"], stderr_head=lambda: ["kaboom line"]
    )
    mgr.pending.add(1, lambda _m: None)
    mgr._on_transport_exit(137)
    assert mgr.state == "error", mgr.state
    assert mgr.transport is None
    assert len(errors) == 1
    assert "137" in errors[0] and "kaboom line" in errors[0], errors[0]
    assert "Refresh" in errors[0]


def test_roslyn_manager_intentional_stop_is_not_an_error():
    errors = []
    mgr = roslyn.RoslynManager(on_error=errors.append)
    t = lsp_transport.LspTransport(["sleep", "30"], lambda _m: None,
                                   on_exit=mgr._on_transport_exit)
    mgr.transport = t
    mgr.state = "ready"
    t.start()
    mgr.stop()  # intentional: must not report an error
    time.sleep(0.5)
    assert mgr.state == "stopped"
    assert errors == [], errors
