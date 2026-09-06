"""Minimal LSP-over-stdio transport.

Speaks Content-Length framed JSON-RPC (LSP base protocol) on a reader thread
and exposes a thread-safe send path. No GTK dependency so it is unit-testable.

Only stdlib is used here; the GTK-facing Roslyn manager lives in roslyn.py.
"""
from __future__ import annotations

import collections
import json
import logging
import subprocess
import threading
import time
from typing import Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict], None]
ExitHandler = Callable[[Optional[int]], None]

_STDERR_TAIL_LINES = 30
#: Cap for one newline-less stderr chunk: a server spewing binary/progress
#: without newlines must not grow the partial-line buffer without bound.
_STDERR_PARTIAL_CAP = 1 << 16

#: A request unanswered this long is dead: drop it on the next response so
#: a wedged server cannot leak callbacks or answer into a rewritten buffer.
_REQUEST_TTL_S = 120.0


def encode_message(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


_MAX_BUFFER_BYTES = 16 * 1024 * 1024

def decode_messages(buffer: bytearray) -> List[dict]:
    """Pop complete LSP messages off a byte buffer. Mutates buffer in place."""
    messages: List[dict] = []
    if len(buffer) > _MAX_BUFFER_BYTES:
        logger.debug("decode_messages: buffer cap exceeded, dropping %d bytes", len(buffer))
        del buffer[:]
        return messages
    while True:
        sep = buffer.find(b"\r\n\r\n")
        if sep == -1:
            return messages
        header = buffer[:sep].decode("ascii", "replace")
        length: Optional[int] = None
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    length = None
        if length is None or length < 0 or length > _MAX_BUFFER_BYTES:
            # Corrupt framing or absurd size; drop the header and continue.
            del buffer[: sep + 4]
            continue
        start = sep + 4
        if len(buffer) < start + length:
            return messages
        body = bytes(buffer[start: start + length])
        del buffer[: start + length]
        try:
            messages.append(json.loads(body.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug(f"decode_messages: dropping bad body: {e!r}")
            continue


class LspTransport:
    """Owns a child LSP server process and pumps its stdout on a thread.

    Server stderr is drained on a second thread into an optional log file
    (plus an in-memory tail) so startup crashes are diagnosable instead of
    vanishing into an unread pipe. When the process exits, `on_exit` fires
    with its return code.
    """

    def __init__(
        self,
        argv: List[str],
        on_message: MessageHandler,
        on_exit: Optional[ExitHandler] = None,
        stderr_log_path: Optional[str] = None,
    ) -> None:
        self.argv = argv
        self.on_message = on_message
        self._on_exit = on_exit
        self._stderr_log_path = stderr_log_path
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._stderr_tail: Deque[str] = collections.deque(maxlen=_STDERR_TAIL_LINES)
        self._stderr_head: List[str] = []
        self._tail_lock = threading.Lock()
        self._finish_lock = threading.Lock()
        self._finished = False
        self._send_broken = False
        self.returncode: Optional[int] = None
        self.running = False

    def next_id(self) -> int:
        with self._id_lock:
            current = self._next_id
            self._next_id += 1
            return current

    def start(self) -> None:
        logger.debug(f"LspTransport start: {' '.join(self.argv)}")
        try:
            self._proc = subprocess.Popen(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError:
            logger.debug("LspTransport start: spawn failed", exc_info=True)
            self._proc = None
            self.running = False
            raise
        self.running = True
        self._reader = threading.Thread(
            target=self._read_loop, name="thor-csharp-lsp-reader", daemon=True
        )
        self._reader.start()
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, name="thor-csharp-lsp-stderr", daemon=True
        )
        self._stderr_thread.start()

    def stderr_tail(self) -> List[str]:
        with self._tail_lock:
            return list(self._stderr_tail)

    def stderr_head(self) -> List[str]:
        with self._tail_lock:
            return list(self._stderr_head)

    def stop(self) -> None:
        self.running = False
        # Intentional stop is not a crash: suppress the exit callback.
        self._on_exit = None
        proc = self._proc
        if proc is not None:
            # Graceful LSP shutdown first (shutdown request + exit
            # notification), then a short grace for a clean exit.
            try:
                self.send_request("shutdown", {})
            except Exception:
                logger.debug("LspTransport.stop: shutdown request failed", exc_info=True)
            try:
                self.send_notification("exit", {})
            except Exception:
                logger.debug("LspTransport.stop: exit notification failed", exc_info=True)
            try:
                proc.wait(timeout=0.5)
            except Exception:
                logger.debug("LspTransport.stop: no fast clean exit, terminating")
            # Reap before closing pipes: closing stdout/stderr first races
            # the reader threads with read-on-closed-stream + hides the exit.
            try:
                proc.terminate()
            except Exception:
                logger.debug("LspTransport.stop: terminate failed", exc_info=True)
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    logger.debug("LspTransport.stop: kill failed", exc_info=True)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    logger.debug("LspTransport.stop: stream close failed", exc_info=True)
            self._proc = None
        self._join_threads()

    def _join_threads(self) -> None:
        """Join pump threads with a timeout; never join the current thread."""
        current = threading.current_thread()
        for thread in (self._reader, self._stderr_thread):
            try:
                if thread is not None and thread.is_alive() and thread is not current:
                    thread.join(timeout=2)
            except Exception:
                logger.debug("LspTransport: thread join failed", exc_info=True)

    def send(self, payload: dict) -> bool:
        """Frame + write one message. False when there is no live server."""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.closed:
            logger.debug("LspTransport.send: no process, dropping message")
            return False
        data = encode_message(payload)
        with self._write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as e:
                # ValueError: stop() closed the pipe mid-write (same as broken).
                if not self._send_broken:
                    self._send_broken = True
                    logger.debug(f"LspTransport.send failed (further send errors suppressed): {e!r}")
                self.running = False
                return False
        return True

    def send_request(self, method: str, params: dict) -> int:
        request_id = self.next_id()
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return request_id

    def send_notification(self, method: str, params: dict) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        buf = bytearray()
        while self.running:
            try:
                chunk = self._proc.stdout.read(4096)
            except Exception as e:
                logger.debug(f"LspTransport read error: {e!r}", exc_info=True)
                break
            if not chunk:
                logger.debug("LspTransport: server closed stdout")
                break
            buf.extend(chunk)
            for message in decode_messages(buf):
                try:
                    self.on_message(message)
                except Exception as e:
                    logger.debug(f"on_message handler failed: {e!r}", exc_info=True)
        self._finish()

    def _stderr_loop(self) -> None:
        """Drain stderr so the child never blocks on a full pipe; keep a tail."""
        proc = self._proc
        stream = proc.stderr if proc is not None else None
        if stream is None:
            return
        log_file = None
        if self._stderr_log_path:
            try:
                log_file = open(self._stderr_log_path, "a", encoding="utf-8", errors="replace")
            except OSError as e:
                logger.debug(f"LspTransport: cannot open stderr log: {e!r}", exc_info=True)
        pending = ""
        try:
            while True:
                try:
                    chunk = stream.read(4096)
                except Exception:
                    logger.debug("LspTransport: stderr read failed", exc_info=True)
                    break
                if not chunk:
                    break
                if isinstance(chunk, bytes):
                    text = chunk.decode("utf-8", "replace")
                else:
                    text = chunk
                if log_file is not None:
                    try:
                        log_file.write(text)
                        log_file.flush()
                    except Exception:
                        logger.debug("LspTransport: stderr log write failed", exc_info=True)
                pending += text
                if len(pending) > _STDERR_PARTIAL_CAP:
                    pending = pending[-_STDERR_PARTIAL_CAP:]
                *lines, pending = pending.split("\n")
                if lines:
                    with self._tail_lock:
                        self._stderr_tail.extend(line[:500] for line in lines)
                        for line in lines:
                            if len(self._stderr_head) >= 5:
                                break
                            if line.strip():
                                self._stderr_head.append(line.strip()[:500])
        finally:
            if pending.strip():
                with self._tail_lock:
                    self._stderr_tail.append(pending.strip()[:500])
                    if len(self._stderr_head) < 5:
                        self._stderr_head.append(pending.strip()[:500])
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    logger.debug("LspTransport: stderr log close failed", exc_info=True)

    def _finish(self) -> None:
        """Reap the child exactly once and report its exit code."""
        with self._finish_lock:
            if self._finished:
                return
            self._finished = True
        self.running = False
        proc = self._proc
        returncode: Optional[int] = None
        if proc is not None:
            try:
                returncode = proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    returncode = proc.wait(timeout=5)
                except Exception:
                    logger.debug("LspTransport: reap failed", exc_info=True)
                    returncode = proc.poll()
        self.returncode = returncode
        callback, self._on_exit = self._on_exit, None
        if callback is None:
            logger.debug(f"LspTransport: server exited with code {returncode} (intentional stop)")
        elif returncode not in (0, None):
            logger.warning(f"LspTransport: server exited unexpectedly with code {returncode}")
        else:
            logger.debug(f"LspTransport: server exited with code {returncode}")
        if callback is not None:
            try:
                callback(returncode)
            except Exception as e:
                logger.debug(f"on_exit handler failed: {e!r}", exc_info=True)
        self._join_threads()


class PendingRequests:
    """Maps request id -> callback for responses arriving on the reader thread.

    Entries stamp their send time; responses sweep anything older than
    ``_REQUEST_TTL_S`` so a server that never answers cannot leak callbacks
    (or deliver them minutes later to a rewritten buffer).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._callbacks: Dict[int, Tuple[MessageHandler, float]] = {}

    def add(self, request_id: int, callback: MessageHandler) -> None:
        with self._lock:
            self._callbacks[request_id] = (callback, time.monotonic())

    def pop(self, request_id: int) -> Optional[MessageHandler]:
        with self._lock:
            self._sweep_locked()
            entry = self._callbacks.pop(request_id, None)
            return entry[0] if entry is not None else None

    def clear(self) -> None:
        with self._lock:
            self._callbacks.clear()

    def _sweep_locked(self) -> None:
        now = time.monotonic()
        stale = [
            key for key, (_, sent) in self._callbacks.items()
            if now - sent > _REQUEST_TTL_S
        ]
        for key in stale:
            self._callbacks.pop(key, None)
        if stale:
            logger.debug(f"PendingRequests: expired {len(stale)} unanswered request(s)")
