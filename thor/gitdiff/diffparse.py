# -*- coding: utf-8 -*-
"""Git diff parsing for git-inline-diff. Headless-safe: no GTK imports.

Line numbers are 0-based new-file lines, matching Gtk TextBuffer lines.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)
GIT_TIMEOUT_S = 10

# Same palette as the project-mode tree (VS Code gitDecoration defaults).
COLOR_ADDED = "#73C991"
COLOR_MODIFIED = "#E2C08D"
COLOR_DELETED = "#C74E39"

CATEGORY_ADDED = "gitinline-added"
CATEGORY_MODIFIED = "gitinline-modified"
CATEGORY_DELETED = "gitinline-deleted"

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


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

_GIT_ROOT_CACHE: dict[str, tuple[float, str | None]] = {}


def _cached_git_root(folder: str) -> str | None:
    """Cached find_git_root (5 s TTL, soft-only)."""
    try:
        key = os.path.abspath(folder)
    except Exception:
        return None
    try:
        now = time.monotonic()
    except Exception:
        return find_git_root(folder)
    try:
        hit = _GIT_ROOT_CACHE.get(key)
        if hit is not None and (now - hit[0]) < 5.0:
            return hit[1]
    except Exception:
        pass
    result = find_git_root(folder)
    try:
        if len(_GIT_ROOT_CACHE) > 128:
            for old_key, (stamp, _val) in list(_GIT_ROOT_CACHE.items()):
                if (now - stamp) > 60.0:
                    _GIT_ROOT_CACHE.pop(old_key, None)
            while len(_GIT_ROOT_CACHE) > 128:
                _GIT_ROOT_CACHE.pop(next(iter(_GIT_ROOT_CACHE)), None)
        _GIT_ROOT_CACHE[key] = (now, result)
    except Exception:
        pass
    return result


def file_status_short(repo_root: str, path: str, timeout: int = GIT_TIMEOUT_S) -> str:
    """Two-char porcelain status for one file ('' when clean/unknown).

    '??' = untracked, '!!' would need --ignored (never returned here).
    Parses the first NUL-separated record only; renames (``R  old\\0new\\0``)
    and directory queries yielding multiple records no longer leak the
    second record's bytes into the status.
    """
    try:
        if not repo_root or not os.path.isdir(repo_root) or not path:
            return ""
    except Exception:
        return ""
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
                "--",
                path,
            ],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0 or not proc.stdout:
        return ""
    try:
        # -z records: b"XY path\x00" (rename: b"R  old\x00new\x00"). Only the
        # first record belongs to this query; anything after the first NUL
        # is a second file or a rename target.
        first = proc.stdout.split(b"\0", 1)[0]
        # entries: b"XY path"; path may itself contain spaces, so only
        # the first two bytes + blank are the status.
        if len(first) >= 3 and first[2:3] == b" ":
            return first[:2].decode("ascii", "replace")
    except Exception:
        pass
    return ""


def get_diff_text(repo_root: str, path: str, timeout: int = GIT_TIMEOUT_S) -> str:
    """Unified diff (zero context) of path vs HEAD. '' on any failure."""
    try:
        if not repo_root or not os.path.isdir(repo_root) or not path:
            return ""
    except Exception:
        return ""
    try:
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "diff",
                "HEAD",
                "--no-color",
                "--no-ext-diff",
                "-U0",
                "--",
                path,
            ],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    try:
        return (proc.stdout or b"").decode("utf-8", "surrogateescape")
    except Exception:
        return ""


def get_buffer_diff_text(
    repo_root: str, relpath: str, new_text: str, timeout: int = GIT_TIMEOUT_S
) -> str:
    """Unified diff (-U0) of in-memory buffer text vs the HEAD blob.

    Lets the gutter track unsaved edits: hunk `+` lines refer to buffer
    lines. '' on any failure (e.g. path not in HEAD).

    Trivial cases stay in-process (no fork/tempdir): byte-identical or
    trailing-newline-only differences return '' directly. Anything real
    still forks ``git diff --no-index`` via a tempdir (logged at debug).
    """
    try:
        if not repo_root or not os.path.isdir(repo_root) or not relpath:
            return ""
        if new_text is None:
            return ""
    except Exception:
        return ""
    try:
        old = subprocess.run(
            ["git", "show", f"HEAD:{relpath}"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except Exception as e:
        logger.debug("git show %s failed: %r", relpath, e, exc_info=True)
        return ""
    if old.returncode != 0:
        # Staged-new / renamed-not-in-HEAD: no HEAD blob to diff against.
        # Fall back to a full-file addition diff (/dev/null vs buffer).
        try:
            new_bytes = new_text.encode("utf-8", "replace")
        except Exception:
            return ""
        tmpdir = None
        proc = None
        try:
            tmpdir = tempfile.mkdtemp(prefix="thor-gutter-")
            new_path = os.path.join(tmpdir, "new")
            with open(new_path, "wb") as f:
                f.write(new_bytes)
            proc = subprocess.run(
                [
                    "git",
                    "diff",
                    "--no-index",
                    "--no-color",
                    "--no-ext-diff",
                    "-U0",
                    "--",
                    "/dev/null",
                    new_path,
                ],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except Exception:
            return ""
        finally:
            try:
                if tmpdir is not None:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
        if proc is None or proc.returncode not in (0, 1):
            return ""
        try:
            return (proc.stdout or b"").decode("utf-8", "surrogateescape")
        except Exception:
            return ""
    try:
        new_bytes = new_text.encode("utf-8", "replace")
    except Exception:
        return ""
    # In-process trivial check: identical or trailing-newline-only edits
    # (GtkSource keeps the final newline implicit) mean "no gutter diff".
    # Avoids a mkdtemp + fork per keystroke for the common clean case.
    try:
        old_bytes = old.stdout or b""
        if new_bytes == old_bytes or new_bytes + b"\n" == old_bytes or new_bytes == old_bytes + b"\n":
            return ""
    except Exception as e:
        logger.debug("trivial diff check failed: %r", e, exc_info=True)
    logger.debug("buffer diff forks git diff --no-index for %s", relpath)
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="thor-gutter-")
        old_path = os.path.join(tmpdir, "old")
        new_path = os.path.join(tmpdir, "new")
        with open(old_path, "wb") as f:
            f.write(old.stdout or b"")
        with open(new_path, "wb") as f:
            f.write(new_bytes)
        proc = subprocess.run(
            [
                "git",
                "diff",
                "--no-index",
                "--no-color",
                "--no-ext-diff",
                "-U0",
                "--",
                old_path,
                new_path,
            ],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    finally:
        try:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
    # --no-index exits 1 when files differ; anything else is failure.
    if proc.returncode not in (0, 1):
        return ""
    try:
        return (proc.stdout or b"").decode("utf-8", "surrogateescape")
    except Exception:
        return ""


def parse_unified_diff(text: str) -> dict[str, list[int]]:
    """Parse `git diff -U0` output -> 0-based new-file lines per kind.

    Pure addition (old count 0) -> 'added'; pure deletion (new count 0)
    -> 'deleted' marker at the line after which text was removed
    (0-based ``new_start - 1``, floored at 0, clamped to the buffer by
    the caller); anything else -> 'modified' over the new range.
    Binary diffs ('GIT binary patch') yield no hunks -> all empty.
    """
    added: list[int] = []
    modified: list[int] = []
    deleted: list[int] = []
    if not text:
        return {"added": added, "modified": modified, "deleted": deleted}
    try:
        lines = text.splitlines()
    except Exception:
        return {"added": added, "modified": modified, "deleted": deleted}
    for line in lines:
        # Binary/rename headers, "\ No newline" markers, and anything
        # non-str yield no hunks; per-line guard so one odd line cannot
        # abort the whole parse.
        try:
            if not line.startswith("@@"):
                continue
            match = _HUNK_RE.match(line)
        except Exception:
            continue
        if not match:
            continue
        try:
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            old_count = int(match.group(2)) if match.group(2) is not None else 1
        except (TypeError, ValueError):
            continue
        if old_count == 0:
            # Pure addition: new range is 1-based new_start..+count.
            for n in range(new_start, new_start + new_count):
                added.append(n - 1)
                if len(added) + len(modified) + len(deleted) >= 20000:
                    break
        elif new_count == 0:
            # Pure deletion: marker on the line above the gap (0-based).
            deleted.append(max(0, new_start - 1))
        else:
            for n in range(new_start, new_start + new_count):
                modified.append(n - 1)
                if len(added) + len(modified) + len(deleted) >= 20000:
                    break
        if len(added) + len(modified) + len(deleted) >= 20000:
            break
    return {"added": added, "modified": modified, "deleted": deleted}


def clamp_lines(lines: list[int], line_count: int) -> list[int]:
    """Clamp 0-based lines into [0, line_count-1]; dedupe, sorted."""
    try:
        if line_count <= 0:
            return []
    except Exception:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for line in lines:
        try:
            n = int(line)
        except Exception:
            continue
        n = max(0, min(line_count - 1, n))
        if n not in seen:
            seen.add(n)
            out.append(n)
    out.sort()
    return out

def untracked_marks(line_count: int, cap: int = 2000) -> dict[str, list[int]]:
    """Marks for a file not yet in git: every line counts as added."""
    try:
        count = int(line_count)
    except Exception:
        count = 0
    try:
        limit = int(cap)
    except Exception:
        limit = 2000
    count = max(0, count)
    limit = max(0, limit)
    return {
        "added": list(range(min(count, limit))),
        "modified": [],
        "deleted": [],
    }


def buffer_matches_head(
    repo_root: str, relpath: str, text: str, timeout: int = GIT_TIMEOUT_S
) -> bool:
    """True when buffer text agrees with the HEAD blob, per git itself.

    Compares `git rev-parse HEAD:<relpath>` against `git hash-object
    --stdin --path=<relpath>` of the buffer bytes, so attributes, CRLF
    filters, and git's own normalization apply — the gutter defers to
    git's verdict instead of second-guessing it with raw bytes. Also
    accepts text one trailing newline off: GtkSource keeps the final
    newline implicit, so freshly loaded (or transient mid-load) buffer
    text is routinely one `\\n` short of the blob without anything having
    changed. False on any failure (callers fall back to the buffer diff).
    """
    try:
        if not repo_root or not os.path.isdir(repo_root) or not relpath:
            return False
        if text is None:
            return False
        content = text.encode("utf-8", "replace")
    except Exception:
        return False
    try:
        blob = subprocess.run(
            ["git", "rev-parse", f"HEAD:{relpath}"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return False
    if blob.returncode != 0 or not blob.stdout:
        return False
    try:
        want = blob.stdout.strip().decode("ascii", "replace")
    except Exception:
        return False
    if not want:
        return False
    candidates = [content]
    if content.endswith(b"\n"):
        candidates.append(content[:-1])
    else:
        candidates.append(content + b"\n")
    for variant in candidates:
        try:
            proc = subprocess.run(
                ["git", "hash-object", "--stdin", f"--path={relpath}"],
                input=variant,
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except Exception:
            return False
        if proc.returncode != 0 or not proc.stdout:
            return False
        try:
            got = proc.stdout.strip().decode("ascii", "replace")
        except Exception:
            return False
        if got and got == want:
            return True
    return False
