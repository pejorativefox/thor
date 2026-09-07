"""Per-root single-window locks: one process per folder (headless)."""

from __future__ import annotations

import os
import tempfile

import pytest

from thor import lock as root_lock


def test_canonical_root_symlink_same_key():
    with tempfile.TemporaryDirectory() as tmp:
        real = os.path.join(tmp, "proj")
        os.makedirs(real)
        link = os.path.join(tmp, "link")
        try:
            os.symlink(real, link)
        except OSError:
            pytest.skip("symlinks unavailable")
        assert root_lock.canonical_root(link) == root_lock.canonical_root(real)
        assert root_lock.lock_key(link) == root_lock.lock_key(real)


def test_acquire_blocks_second_and_releases():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as locks:
        target = os.path.join(tmp, "proj")
        os.makedirs(target)
        first, owner = root_lock.try_acquire_root_lock(target, base=locks)
        assert first is not None and owner is None
        try:
            second, owner2 = root_lock.try_acquire_root_lock(target, base=locks)
            assert second is None
            assert owner2 == os.getpid()
        finally:
            first.release()
        third, _ = root_lock.try_acquire_root_lock(target, base=locks)
        assert third is not None
        third.release()


def test_release_idempotent_and_context_manager():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as locks:
        target = os.path.join(tmp, "proj")
        os.makedirs(target)
        held, _ = root_lock.try_acquire_root_lock(target, base=locks)
        assert held is not None
        with held:
            other, _ = root_lock.try_acquire_root_lock(target, base=locks)
            assert other is None
        # Exited the with-block: lock released, re-acquirable.
        again, _ = root_lock.try_acquire_root_lock(target, base=locks)
        assert again is not None
        again.release()
        again.release()


def test_missing_dir_never_locks():
    with tempfile.TemporaryDirectory() as locks:
        lock, owner = root_lock.try_acquire_root_lock(
            os.path.join(locks, "no-such-root"), base=locks
        )
        assert lock is None and owner is None


def test_same_key_for_trailing_slash():
    with tempfile.TemporaryDirectory() as tmp:
        real = os.path.join(tmp, "proj")
        os.makedirs(real)
        assert root_lock.lock_key(real + os.sep) == root_lock.lock_key(real)
