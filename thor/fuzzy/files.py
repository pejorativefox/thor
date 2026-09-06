# -*- coding: utf-8 -*-
"""Project-root file enumeration for the fuzzy finder.

Headless-safe (no GTK imports) so unit tests run without a display.

Strategy: every file under the project-mode root, recursively, minus
hidden files/dirs, symlinks and build/vcs junk — and minus anything
git ignores (via ``git ls-files --exclude-standard`` when available).
"""

from __future__ import annotations

import os
import subprocess

#: Directories never descended into. Mirrors project-mode's prune set so
#: both plugins agree on what "the project" contains.
_PRUNE_DIRS = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        ".cache",
        ".dotnet",
        ".nuget",
        "bin",
        "obj",
        "node_modules",
        ".vs",
        ".idea",
        "dosdevices",
        "drive_c",
    }
)


def _git_files(root: str) -> list[str] | None:
    """List files via git (respects .gitignore), or None on any failure.

    Uses ``--cached --others --exclude-standard`` so tracked files plus
    untracked-but-not-ignored files are returned. Returns absolute paths.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others",
             "--exclude-standard", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        raw = proc.stdout.split(b"\0")
    except Exception:
        return None
    out: list[str] = []
    for chunk in raw:
        if not chunk:
            continue
        try:
            rel = chunk.decode("utf-8", errors="surrogateescape")
        except Exception:
            continue
        path = os.path.join(root, rel)
        try:
            if os.path.isfile(path) and not os.path.islink(path):
                out.append(path)
        except OSError:
            continue
    return sorted(out)


def _walk_files(root: str, max_depth: int = 20) -> list[str]:
    """Fallback recursive walk: all non-hidden, non-symlink files."""
    out: list[str] = []
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as entries:
                ordered = sorted(entries, key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in ordered:
            try:
                name = entry.name
                path = entry.path
                if name.startswith("."):
                    continue
                if os.path.islink(path):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if name in _PRUNE_DIRS:
                        continue
                    if depth < max_depth:
                        stack.append((path, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    out.append(path)
            except OSError:
                continue
    return sorted(out)


def list_project_files(root: str, max_depth: int = 20) -> list[str]:
    """All files under root, gitignore-aware when git is usable."""
    if not root or not os.path.isdir(root):
        return []
    root = os.path.abspath(root)
    try:
        git_hit = _git_files(root)
    except Exception:
        git_hit = None
    if git_hit is not None:
        return git_hit
    return _walk_files(root, max_depth)
