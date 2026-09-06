# -*- coding: utf-8 -*-
"""Git status for project-mode. Headless-safe: no GTK imports.

Colors are VS Code's default dark `gitDecoration.*ResourceForeground` values
(see vscode `vscode-stylenames` / theme-color docs):
  modified  #E2C08D (tan)      added     #81B88B
  untracked #73C991 (green)    deleted   #C74E39
  renamed   #73C991            copied    #81B88B
  conflicting #6C6CC4         ignored   #8C8C8C
  submodule #8DB9E2
"""

from __future__ import annotations

import os
import subprocess

GIT_TIMEOUT_S = 10

# VS Code default dark gitDecoration colors.
COLOR_MODIFIED = "#E2C08D"
COLOR_ADDED = "#81B88B"
COLOR_UNTRACKED = "#73C991"
COLOR_DELETED = "#C74E39"
COLOR_RENAMED = "#73C991"
COLOR_COPIED = "#81B88B"
COLOR_CONFLICTING = "#6C6CC4"
COLOR_IGNORED = "#8C8C8C"
COLOR_SUBMODULE = "#8DB9E2"

_UNMERGED = frozenset({"DD", "AA", "UU", "AU", "UA", "UD", "DU"})


def find_git_root(folder: str, timeout: int = 5) -> str | None:
    """Top-level of the repo containing folder, or None (soft-only)."""
    try:
        if not folder or not os.path.isdir(folder):
            return None
    except Exception:
        return None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=folder,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except Exception:
        proc = None
    if proc is not None and proc.returncode == 0:
        try:
            top = proc.stdout.decode("utf-8", "surrogateescape").strip()
        except Exception:
            top = ""
        if top and os.path.isdir(top):
            return os.path.abspath(top)
    # Fallback: walk up for a `.git` dir/file (worktrees use a file).
    try:
        current = os.path.abspath(folder)
        while True:
            if os.path.isdir(os.path.join(current, ".git")) or os.path.isfile(
                os.path.join(current, ".git")
            ):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                return None
            current = parent
    except Exception:
        return None


def _decode_repo_path(raw: bytes) -> str:
    """Decode one path from `git status -z` output.

    With `-c core.quotepath=false` paths arrive verbatim UTF-8. If git
    still C-quotes (leading `"`), strip quotes and unescape octal/C
    sequences on a best-effort basis.
    """
    try:
        text = raw.decode("utf-8", "surrogateescape")
    except Exception:
        return ""
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        body = text[1:-1]
        try:
            return (
                body.encode("utf-8", "surrogateescape")
                .decode("unicode_escape")
                .encode("latin-1")
                .decode("utf-8", "surrogateescape")
            )
        except Exception:
            return body
    return text


def parse_porcelain_z(raw: bytes, root: str) -> dict[str, tuple[str, str]]:
    """Parse `git status --porcelain=v1 -z` bytes -> {abspath: (X, Y)}.

    Rename/copied entries consume two NUL-separated fields
    (`R  new\\0old\\0` — git emits the *new* path in the status field).
    """
    statuses: dict[str, tuple[str, str]] = {}
    if not raw:
        return statuses
    try:
        fields = raw.split(b"\x00")
    except Exception:
        return statuses
    i = 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if not field:
            continue
        if len(field) < 4:  # "XY path"
            continue
        try:
            x = chr(field[0])
            y = chr(field[1])
        except Exception:
            continue
        rel = _decode_repo_path(field[3:])
        if not rel:
            continue
        if x == "R" or x == "C" or y == "R" or y == "C":
            # Next field is the rename/copy source; status path is the target.
            i += 1
        statuses[os.path.abspath(os.path.join(root, rel))] = (x, y)
    return statuses


def get_git_statuses(root_dir: str, timeout: int = GIT_TIMEOUT_S) -> dict[str, tuple[str, str]]:
    """Run git status under root_dir -> {abspath: (X, Y)}. `{}` on any failure."""
    try:
        if not root_dir or not os.path.isdir(root_dir):
            return {}
    except Exception:
        return {}
    try:
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "status",
                "--porcelain=v1",
                "-uall",
                "-z",
            ],
            cwd=root_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        return parse_porcelain_z(proc.stdout or b"", os.path.abspath(root_dir))
    except Exception:
        return {}


def status_to_color(x: str, y: str) -> str | None:
    """Map porcelain XY codes to a VS Code foreground hex (None = default)."""
    try:
        code = f"{x}{y}"
    except Exception:
        return None
    if code in _UNMERGED or (x == "U" or y == "U"):
        return COLOR_CONFLICTING
    if x == "?" and y == "?":
        return COLOR_UNTRACKED
    if x == "!" and y == "!":
        return COLOR_IGNORED
    if x == "D" or y == "D":
        return COLOR_DELETED
    if x == "R" or y == "R":
        return COLOR_RENAMED
    if x == "C" or y == "C":
        return COLOR_COPIED
    if x == "A" or y == "A":
        return COLOR_ADDED
    if x == "M" or x == "T" or y == "M" or y == "T":
        return COLOR_MODIFIED
    return None


def color_for_path(
    statuses: dict[str, tuple[str, str]], path: str
) -> str | None:
    """Foreground color for one absolute path (None when clean/unknown)."""
    try:
        code = statuses.get(os.path.abspath(path))
    except Exception:
        return None
    if not code:
        return None
    return status_to_color(code[0], code[1])


# Lower rank wins when a folder aggregates mixed child states.
_RANK = {
    COLOR_CONFLICTING: 0,
    COLOR_DELETED: 1,
    COLOR_MODIFIED: 2,
    COLOR_UNTRACKED: 3,
    COLOR_ADDED: 4,
    COLOR_RENAMED: 5,
    COLOR_COPIED: 5,
    COLOR_SUBMODULE: 6,
    COLOR_IGNORED: 7,
}


def aggregate_dir_color(colors) -> str | None:
    """Strongest (lowest-rank) child color, or None when all clean."""
    best: str | None = None
    best_rank = 999
    try:
        items = list(colors)
    except Exception:
        return None
    for color in items:
        if not color:
            continue
        rank = _RANK.get(color, 50)
        if rank < best_rank:
            best_rank = rank
            best = color
    return best
