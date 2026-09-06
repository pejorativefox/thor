"""Unit tests for LSP Content-Length framing (headless, no GTK)."""

import os
import sys


from thor.csharp import lsp_transport


def test_encode_decode_roundtrip():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    data = lsp_transport.encode_message(payload)
    assert data.startswith(b"Content-Length:")
    buf = bytearray(data)
    messages = lsp_transport.decode_messages(buf)
    assert messages == [payload]
    assert len(buf) == 0


def test_decode_partial_and_multiple():
    first = {"jsonrpc": "2.0", "id": 1, "result": {}}
    second = {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {}}
    blob = lsp_transport.encode_message(first) + lsp_transport.encode_message(second)
    # Feed first half: no complete message yet.
    buf = bytearray(blob[:10])
    assert lsp_transport.decode_messages(buf) == []
    buf.extend(blob[10:])
    assert lsp_transport.decode_messages(buf) == [first, second]


def test_decode_skips_bad_body():
    good = {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}
    bad_body = b"{not json"
    blob = (
        f"Content-Length: {len(bad_body)}\r\n\r\n".encode("ascii")
        + bad_body
        + lsp_transport.encode_message(good)
    )
    buf = bytearray(blob)
    assert lsp_transport.decode_messages(buf) == [good]


def test_pending_requests():
    pending = lsp_transport.PendingRequests()
    seen = []
    pending.add(3, lambda m: seen.append(m))
    cb = pending.pop(3)
    assert cb is not None
    cb({"ok": True})
    assert seen == [{"ok": True}]
    assert pending.pop(3) is None
