"""Completion readiness: on_ready sync + visible errors on silent paths."""

import os
import sys


from thor.csharp import gscompletion as gs_mod
from thor.csharp import roslyn


class _FakeTransport:
    def __init__(self) -> None:
        self.notifications: list = []
        self.requests: list = []
        self._next_id = 0

    def next_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def send(self, message: dict) -> None:
        self.requests.append(message)

    def send_notification(self, method: str, params: dict) -> None:
        self.notifications.append((method, params))


def test_on_ready_fires_after_initialize():
    ready: list = []
    mgr = roslyn.RoslynManager(on_ready=lambda: ready.append(True))
    fake = _FakeTransport()
    mgr.transport = fake  # type: ignore[assignment]
    mgr.solution_path = None
    mgr.workspace_root = "/tmp"
    mgr._send_initialize()
    (request,) = fake.requests
    request_id = request["id"]
    callback = mgr.pending.pop(request_id)
    assert callback is not None
    mgr.pending.add(request_id, callback)
    callback({"result": {"capabilities": {}}})
    assert mgr.state == "ready"
    assert ready == [True]


def test_on_ready_none_by_default():
    mgr = roslyn.RoslynManager()
    fake = _FakeTransport()
    mgr.transport = fake  # type: ignore[assignment]
    mgr.solution_path = None
    mgr.workspace_root = "/tmp"
    mgr._send_initialize()
    (request,) = fake.requests
    callback = mgr.pending.pop(request["id"])
    mgr.pending.add(request["id"], callback)
    callback({"result": {"capabilities": {}}})
    assert mgr.state == "ready"


class _FakeIter:
    def __init__(self, buf, offset: int) -> None:
        self._buf = buf
        self._offset = offset

    def get_buffer(self):
        return self._buf

    def get_offset(self) -> int:
        return self._offset


class _FakeBuffer:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_bounds(self):
        return (0, len(self._text))

    def get_text(self, _start, _end, _include):
        return self._text


class _FakeContext:
    def __init__(self, buf, user: bool = False) -> None:
        self._buf = buf
        self._user = user
        self.calls: list = []

    def get_iter(self):
        return (True, _FakeIter(self._buf, len(self._buf._text)))

    def get_activation(self):
        raise ValueError("no framework here")

    def add_proposals(self, _provider, proposals, finished):
        self.calls.append((list(proposals), finished))


def test_populate_send_failure_finishes_and_logs(caplog):
    provider = gs_mod.RoslynCompletionProvider(
        is_ready=lambda: True,
        resolve_path=lambda _buf: "/tmp/A.cs",
        send_request=lambda _m, _p, _c: None,
    )
    ctx = _FakeContext(_FakeBuffer("Console.Wri"), user=True)
    with caplog.at_level("ERROR"):
        provider.do_populate(ctx)
    assert ctx.calls and ctx.calls[-1] == ([], True)
    assert any("completion request not sent" in rec.getMessage() for rec in caplog.records)
