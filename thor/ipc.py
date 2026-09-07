# -*- coding: utf-8 -*-
"""Per-root IPC — focus existing window / forward files to live process.

One editor per process, one window per folder. The per-root ``flock``
in :mod:`thor.lock` decides ownership; this module is the messenger:

* owner (lock holder) listens on
  ``$XDG_RUNTIME_DIR/thor/sockets/<sha32>.sock``
  (fallback ``$XDG_CACHE_HOME/thor/sockets`` — same base selection as
  :func:`thor.lock.locks_dir` so both peers agree).
* second process for the same root connects, sends newline-delimited
  JSON (``{"cmd": "present"}`` or ``{"cmd": "open", "files": [...]}``),
  then exits 0. No DBus, no shared memory.

Headless-safe: stdlib only. GTK work happens via callbacks the owner
wires to ``GLib.idle_add``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading

logger = logging.getLogger(__name__)


def sockets_dir(base: str | None = None) -> str:
    """Directory holding per-root socket files (runtime dir preferred)."""
    if base:
        directory = os.path.abspath(base)
    else:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if runtime and os.path.isabs(runtime):
            directory = os.path.join(runtime, "thor", "sockets")
        else:
            try:
                from . import xdg

                directory = os.path.join(xdg.cache_home(), "thor", "sockets")
            except Exception:
                directory = os.path.join(os.path.expanduser("~/.cache"), "thor", "sockets")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        logger.debug("ipc dir create failed for %s", directory, exc_info=True)
    return directory


def socket_path_for_root(root: str, base: str | None = None) -> str:
    """Filesystem path of the IPC socket for a project root."""
    import hashlib as _hashlib

    try:
        from . import lock as _lock_mod

        _key = _lock_mod.lock_key(root)
    except Exception:
        _key = _hashlib.sha256(os.path.realpath(os.path.abspath(root)).encode()).hexdigest()[:32]

    directory = os.path.abspath(base) if base else sockets_dir()
    return os.path.join(directory, _key + ".sock")


def is_root_live(root: str) -> bool:
    """True when another process currently holds the root lock."""
    try:
        from .lock import canonical_root, try_acquire_root_lock
    except Exception:
        return False
    canon = canonical_root(root)
    if not canon or not os.path.isdir(canon):
        return False
    lock, _owner = try_acquire_root_lock(canon)
    if lock is None:
        return True
    try:
        lock.release()
    except Exception:
        pass
    return False


def _send_payload(sock_path: str, payload: dict, timeout: float = 2.0) -> bool:
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(timeout)
            client.connect(sock_path)
            data = (json.dumps(payload) + "\n").encode("utf-8")
            client.sendall(data)
            return True
        finally:
            try:
                client.close()
            except OSError:
                pass
    except OSError:
        logger.debug("ipc send failed for %s", sock_path, exc_info=True)
        return False


def notify_existing(
    root: str,
    files: list[str] | None = None,
    file_lines: dict | None = None,
    timeout: float = 2.0,
) -> bool:
    """Ask the live owner of root to present / open files. True if delivered."""
    try:
        from .lock import canonical_root
    except Exception:
        canonical_root = lambda r: os.path.realpath(os.path.abspath(r))  # type: ignore
    canon = canonical_root(root)
    if not canon:
        return False
    sock_path = socket_path_for_root(canon)
    if not os.path.exists(sock_path):
        return False
    entries: list[dict] = []
    for fp in files or []:
        try:
            loc = (file_lines or {}).get(fp)
            if isinstance(loc, (list, tuple)) and len(loc) >= 1:
                line, col = loc[0], (loc[1] if len(loc) > 1 else None)
            else:
                line, col = None, None
            entries.append({"path": os.path.abspath(fp), "line": line, "col": col})
        except Exception:
            logger.debug("ipc entry build failed for %r", fp, exc_info=True)
    if entries:
        payload: dict = {"cmd": "open", "files": entries}
    else:
        payload = {"cmd": "present"}
    return _send_payload(sock_path, payload, timeout=timeout)


def find_live_root_for_path(path: str, max_depth: int = 25) -> str | None:
    """Deepest ancestor dir of path that is currently live (lock held)."""
    try:
        ap = os.path.abspath(os.path.expanduser(path))
        if os.path.isfile(ap) or not os.path.exists(ap):
            ap = os.path.dirname(ap)
        cur = os.path.realpath(ap)
        for _ in range(max_depth):
            if os.path.isdir(cur) and is_root_live(cur):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    except Exception:
        logger.debug("ipc find_live_root failed for %r", path, exc_info=True)
    return None


class IpcServer:
    """Unix-socket owner server. Calls handlers in caller thread via idle."""

    def __init__(self, root: str, on_present=None, on_open=None) -> None:
        from .lock import canonical_root

        self.root = canonical_root(root) or root
        self.sock_path = socket_path_for_root(self.root)
        self.on_present = on_present
        self.on_open = on_open
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> bool:
        # Stale socket without a live lock holder: unlink it.
        if os.path.exists(self.sock_path) and not is_root_live(self.root):
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass
        # If another owner is already listening, don't steal it.
        if os.path.exists(self.sock_path) and is_root_live(self.root):
            # Probe: if connect succeeds, someone owns it.
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.5)
                try:
                    probe.connect(self.sock_path)
                    probe.close()
                    logger.debug("ipc server: owner already listening for %r", self.root)
                    return False
                except OSError:
                    pass
                finally:
                    try:
                        probe.close()
                    except OSError:
                        pass
            except OSError:
                pass
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass
        try:
            parent = os.path.dirname(self.sock_path)
            os.makedirs(parent, exist_ok=True)
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(self.sock_path)
            srv.listen(8)
            srv.settimeout(0.5)
            self._sock = srv
        except OSError:
            logger.debug("ipc server bind failed for %s", self.sock_path, exc_info=True)
            return False
        self._stop.clear()
        thread = threading.Thread(target=self._serve, name="thor-ipc", daemon=True)
        self._thread = thread
        thread.start()
        return True

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(2.0)
                chunks: list[bytes] = []
                try:
                    while True:
                        data = conn.recv(65536)
                        if not data:
                            break
                        chunks.append(data)
                        if b"\n" in data or sum(len(c) for c in chunks) > 1_000_000:
                            break
                except socket.timeout:
                    pass
                raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
                for line in raw.splitlines():
                    self._dispatch(line.strip())
            except Exception:
                logger.debug("ipc serve failed", exc_info=True)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _dispatch(self, line: str) -> None:
        if not line:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        cmd = msg.get("cmd")
        try:
            if cmd == "present" and callable(self.on_present):
                self.on_present()
            elif cmd == "open" and callable(self.on_open):
                files = msg.get("files") or []
                if isinstance(files, list):
                    self.on_open(files)
        except Exception:
            logger.debug("ipc dispatch failed", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        try:
            # Only unlink our own socket (don't nuke a successor's).
            if os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except OSError:
            pass


__all__ = [
    "sockets_dir",
    "socket_path_for_root",
    "is_root_live",
    "notify_existing",
    "find_live_root_for_path",
    "IpcServer",
]
