# -*- coding: utf-8 -*-
"""Per-root single-window locks — one process per folder.

Each editor process owns at most one window. A second `thor-code <path>`
for an already-live root focuses the owner via the per-root IPC socket
(see :mod:`thor.ipc`); only when delivery fails does the launcher exit 2
(no shared memory, no DBus, no focus stealing by the toolkit itself):
the live process holds an exclusive non-blocking `flock` on
`locks/<sha256(canon-root)>.lock` for its whole lifetime. The kernel
releases the lock on crash, so stale locks are impossible.

Headless-safe: stdlib only (`fcntl` on POSIX). Where `fcntl` is
unavailable the lock fail-opens (returns acquired) so launches never brick.
"""

from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger(__name__)

try:
    import fcntl  # POSIX only
except Exception:  # Windows / odd platforms: fail open
    fcntl = None  # type: ignore[assignment]


def canonical_root(root: str | None) -> str | None:
    """Normalized absolute realpath of a project root, or None when empty."""
    if not root or not isinstance(root, str):
        return None
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(root)))
    except Exception:
        logger.debug("lock canonical root failed for %r", root, exc_info=True)
        return None


def locks_dir() -> str:
    """Directory holding per-root lock files (runtime dir preferred)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime and os.path.isabs(runtime):
        base = os.path.join(runtime, "thor", "locks")
    else:
        try:
            from . import xdg

            base = os.path.join(xdg.cache_home(), "thor", "locks")
        except Exception:
            base = os.path.join(os.path.expanduser("~/.cache"), "thor", "locks")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        logger.debug("lock dir create failed for %s", base, exc_info=True)
    return base


def lock_key(root: str) -> str:
    """Stable 32-char hex key for a project root."""
    canon = canonical_root(root) or str(root)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


def lock_path_for_root(root: str, base: str | None = None) -> str:
    """Filesystem path of the lock file for a project root."""
    directory = os.path.abspath(base) if base else locks_dir()
    return os.path.join(directory, lock_key(root) + ".lock")


def _read_owner_pid(path: str) -> int | None:
    """Best-effort pid recorded in an existing lock file (message only)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read(32).strip()
        pid = int(text)
        return pid if pid > 0 else None
    except Exception:
        return None


class RootLock:
    """Held exclusive lock for one project root. Idempotent release."""

    def __init__(self, root: str, path: str, fd: int | None) -> None:
        self.root = root
        self.path = path
        self._fd = fd
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    logger.debug("lock unlock failed for %s", self.path, exc_info=True)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> RootLock:
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        self.release()


def try_acquire_root_lock(root: str, base: str | None = None) -> tuple[RootLock | None, int | None]:
    """Try to take the exclusive lock for root (non-blocking).

    Returns (lock_or_None, owner_pid_or_None). (None, pid) means a live
    process holds it — the caller should refuse the second window.
    (lock, None) means acquired. Without `fcntl` this fail-opens
    (always acquired) so launches never brick on odd platforms.
    """
    canon = canonical_root(root)
    if not canon or not os.path.isdir(canon):
        return None, None
    path = lock_path_for_root(canon, base=base)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except OSError:
        logger.debug("lock parent create failed for %s", path, exc_info=True)
    if fcntl is None:
        logger.debug("fcntl unavailable; lock fail-open for %r", canon)
        return RootLock(canon, path, None), None
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        logger.debug("lock open failed for %s", path, exc_info=True)
        return None, _read_owner_pid(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None, _read_owner_pid(path)
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
    except OSError:
        logger.debug("lock pid stamp failed for %s", path, exc_info=True)
    return RootLock(canon, path, fd), None


__all__ = [
    "canonical_root",
    "locks_dir",
    "lock_key",
    "lock_path_for_root",
    "try_acquire_root_lock",
    "RootLock",
]
