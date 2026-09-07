# -*- coding: utf-8 -*-
"""Per-project-root session persistence (open tabs + cursor + unsaved content).

Saved per open project root under the XDG cache dir::

    $XDG_CACHE_HOME/thor/sessions/<sha256(root)[:32]>.json
    $XDG_CACHE_HOME/thor/sessions/<sha256(root)[:32]>-unsaved/<backup>.bak

Headless-safe: no GTK import. All window/tab access is duck-typed so unit
tests can use FakeWindow/FakeDoc stand-ins. GTK-specific work (opening tabs,
cursor placement) degrades gracefully when the window lacks those methods.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

SESSION_VERSION = 1
SESSION_SAVE_DEBOUNCE_MS = 500
MAX_TABS = 100
MAX_BACKUP_BYTES = 1_000_000
MAX_TOTAL_BACKUP_BYTES = 10_000_000


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def canonical_root(root: str | None) -> str | None:
    """Normalized absolute realpath of a project root, or None when empty."""
    if not root or not isinstance(root, str):
        return None
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(root)))
    except Exception:
        logger.debug("session canonical root failed for %r", root, exc_info=True)
        return None


def session_key(root: str) -> str:
    """Stable 32-char hex key for a project root."""
    canon = canonical_root(root) or str(root)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


def _sessions_base(base: str | None = None) -> str:
    if base:
        return os.path.join(os.path.abspath(base), "thor", "sessions")
    try:
        from thor import xdg

        return os.path.join(xdg.cache_home(), "thor", "sessions")
    except Exception:
        logger.debug("session cache base fallback", exc_info=True)
        return os.path.join(os.path.expanduser("~/.cache"), "thor", "sessions")


def session_path_for_root(root: str, base: str | None = None) -> str:
    return os.path.join(_sessions_base(base), session_key(root) + ".json")


def unsaved_dir_for_root(root: str, base: str | None = None) -> str:
    return os.path.join(_sessions_base(base), session_key(root) + "-unsaved")


def _is_unsafe_root(root: str) -> bool:
    try:
        from . import is_unsafe_root as _unsafe

        return bool(_unsafe(root))
    except Exception:
        logger.debug("session unsafe check failed", exc_info=True)
        try:
            real = os.path.realpath(os.path.abspath(root))
            home = os.path.realpath(os.path.expanduser("~"))
            return real == home or real == os.path.dirname(home) or real == "/"
        except Exception:
            return True


# ---------------------------------------------------------------------------
# Window introspection (duck-typed)
# ---------------------------------------------------------------------------

def get_window_root(window) -> str | None:
    """Project root currently associated with window, or None.

    Prefers the attached project browser's live root, falls back to the
    window's initial folder. Returns the canonical path.
    """
    if window is None:
        return None
    for attr in ("_thor_project_browser", "_project_browser", "browser", "_browser"):
        try:
            browser = getattr(window, attr, None)
        except Exception:
            continue
        if browser is None:
            continue
        try:
            root = getattr(browser, "_root_dir", None)
        except Exception:
            root = None
        if root and isinstance(root, str) and os.path.isdir(root):
            return canonical_root(root)
    try:
        initial = getattr(window, "_initial_folder", None)
    except Exception:
        initial = None
    if initial and isinstance(initial, str) and os.path.isdir(initial):
        return canonical_root(initial)
    return None


def _doc_location_path(doc) -> str | None:
    try:
        loc = doc.get_location()
    except Exception:
        return None
    if loc is None:
        return None
    try:
        get_path = getattr(loc, "get_path", None)
        if callable(get_path):
            path = get_path()
            if isinstance(path, str) and path:
                return os.path.abspath(path)
            return None
    except Exception:
        logger.debug("session location path failed", exc_info=True)
    return None


def _doc_cursor(doc) -> tuple[int, int]:
    """(line, col) 0-based cursor guess; (0, 0) when unknown."""
    try:
        insert = doc.get_insert_mark() if hasattr(doc, "get_insert_mark") else None
        it = doc.get_iter_at_mark(insert) if insert is not None else None
        if it is not None:
            return max(0, int(it.get_line())), max(0, int(it.get_line_offset()))
    except Exception:
        logger.debug("session cursor probe failed", exc_info=True)
    return 0, 0


def _doc_text(doc) -> str | None:
    try:
        start, end = doc.get_bounds()
        text = doc.get_text(start, end, True)
        return text if isinstance(text, str) else None
    except Exception:
        logger.debug("session text probe failed", exc_info=True)
        return None


def _doc_modified(doc) -> bool:
    try:
        return bool(doc.get_modified())
    except Exception:
        return False


def _active_index(window, tabs: list) -> int:
    try:
        active = window.get_active_tab() if hasattr(window, "get_active_tab") else None
    except Exception:
        active = None
    if active is not None:
        for i, t in enumerate(tabs):
            if t is active:
                return i
    try:
        notebook = getattr(window, "_notebook", None)
        if notebook is not None and hasattr(notebook, "get_current_page"):
            cur = int(notebook.get_current_page())
            if 0 <= cur < len(tabs):
                return cur
    except Exception:
        logger.debug("session active index probe failed", exc_info=True)
    return 0


def _window_tabs(window) -> list:
    try:
        tabs = getattr(window, "_tabs", None)
        if isinstance(tabs, list) and tabs:
            return list(tabs)
    except Exception:
        pass
    try:
        docs = window.get_documents() if hasattr(window, "get_documents") else []
        return list(docs or [])
    except Exception:
        return []


def collect_tabs(window) -> tuple[list[dict], int]:
    """Collect saveable tab entries + active index from window.

    Entries: {path: str|None, line: int, col: int, modified: bool, text: str|None}.
    Untitled unmodified tabs are skipped (no path, nothing to restore).
    """
    tabs = _window_tabs(window)
    # _tabs may hold ThorTab objects or bare docs in tests; normalize.
    entries: list[dict] = []
    for tab in tabs[:MAX_TABS]:
        try:
            doc = tab.get_document() if hasattr(tab, "get_document") else tab
        except Exception:
            continue
        if doc is None:
            continue
        path = _doc_location_path(doc)
        modified = _doc_modified(doc)
        line, col = _doc_cursor(doc)
        text = _doc_text(doc) if modified else None
        if path is None and (not modified or not text):
            continue
        entries.append(
            {"path": path, "line": int(line), "col": int(col),
             "modified": bool(modified), "text": text}
        )
    try:
        active = _active_index(window, tabs[:MAX_TABS])
    except Exception:
        active = 0
    return entries, int(active)


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------

def _write_json_atomic(path: str, obj: dict) -> bool:
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=1)
                f.write("\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    logger.debug("session fsync failed for %s", path, exc_info=True)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            dir_fd = os.open(parent or ".", os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                logger.debug("session dir fsync failed", exc_info=True)
            finally:
                os.close(dir_fd)
        return True
    except Exception as e:
        logger.debug("session write failed for %s: %r", path, e, exc_info=True)
        return False


def _sanitize_backup_name(index: int, path: str | None) -> str:
    base = os.path.basename(path) if path else "untitled"
    safe = "".join(c if (c.isalnum() or c in (".", "-", "_")) else "_" for c in base)[:40] or "file"
    return f"{index:03d}-{safe}.bak"


def save_for_root(root: str, window, base: str | None = None) -> str | None:
    """Collect window tabs and persist them for root. Returns path or None."""
    canon = canonical_root(root)
    if not canon or not os.path.isdir(canon):
        return None
    if _is_unsafe_root(canon):
        logger.debug("session save: refusing unsafe root %r", canon)
        return None
    if window is None:
        return None
    entries, active = collect_tabs(window)
    session_path = session_path_for_root(canon, base=base)
    unsaved_dir = unsaved_dir_for_root(canon, base=base)
    files: list[dict] = []
    valid_backups: set[str] = set()
    total_bytes = 0
    try:
        os.makedirs(unsaved_dir, exist_ok=True)
    except OSError:
        logger.debug("session unsaved dir create failed", exc_info=True)
    for i, entry in enumerate(entries):
        record: dict = {"path": entry["path"], "line": entry["line"], "col": entry["col"],
                         "backup": None}
        text = entry.get("text")
        if entry.get("modified") and isinstance(text, str) and text:
            try:
                encoded = text.encode("utf-8")
            except Exception:
                encoded = b""
            if 0 < len(encoded) <= MAX_BACKUP_BYTES and total_bytes + len(encoded) <= MAX_TOTAL_BACKUP_BYTES:
                name = _sanitize_backup_name(i, entry["path"])
                try:
                    dest = os.path.join(unsaved_dir, name)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(text)
                    valid_backups.add(name)
                    record["backup"] = name
                    total_bytes += len(encoded)
                except OSError:
                    logger.debug("session backup write failed", exc_info=True)
            else:
                logger.debug("session backup skipped (too large): %r", entry["path"])
        files.append(record)
    payload = {"version": SESSION_VERSION, "root": canon,
               "active": max(0, min(active, max(0, len(files) - 1))), "files": files}
    if not _write_json_atomic(session_path, payload):
        return None
    # Prune stale backups best-effort.
    try:
        for name in os.listdir(unsaved_dir):
            if name not in valid_backups and name.endswith(".bak"):
                try:
                    os.unlink(os.path.join(unsaved_dir, name))
                except OSError:
                    logger.debug("session backup prune failed for %s", name, exc_info=True)
    except OSError:
        logger.debug("session backup prune scan failed", exc_info=True)
    return session_path


def load_for_root(root: str, base: str | None = None) -> dict | None:
    """Load and validate the saved session for root, or None."""
    canon = canonical_root(root)
    if not canon:
        return None
    path = session_path_for_root(canon, base=base)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    except Exception:
        logger.debug("session load failed for %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    try:
        files = data.get("files", [])
        if not isinstance(files, list):
            return None
        cleaned: list[dict] = []
        for item in files[:MAX_TABS]:
            if not isinstance(item, dict):
                continue
            p = item.get("path")
            if p is not None and not isinstance(p, str):
                continue
            try:
                line = max(0, int(item.get("line", 0)))
            except (TypeError, ValueError):
                line = 0
            try:
                col = max(0, int(item.get("col", 0)))
            except (TypeError, ValueError):
                col = 0
            backup = item.get("backup")
            if backup is not None and not isinstance(backup, str):
                backup = None
            if backup and (os.sep in backup or "/" in backup or ".." in backup):
                backup = None
            cleaned.append({"path": p, "line": line, "col": col, "backup": backup})
        try:
            active = max(0, int(data.get("active", 0)))
        except (TypeError, ValueError):
            active = 0
        return {"version": 1, "root": canon, "active": active, "files": cleaned}
    except Exception:
        logger.debug("session validate failed", exc_info=True)
        return None


def read_backup(root: str, backup_name: str, base: str | None = None) -> str | None:
    """Read stashed unsaved text for a backup entry, or None."""
    if not backup_name or os.sep in backup_name or "/" in backup_name or ".." in backup_name:
        return None
    canon = canonical_root(root)
    if not canon:
        return None
    try:
        dest = os.path.join(unsaved_dir_for_root(canon, base=base), backup_name)
        if os.path.getsize(dest) > MAX_BACKUP_BYTES + 1024:
            return None
        with open(dest, "r", encoding="utf-8") as f:
            return f.read(MAX_BACKUP_BYTES + 1)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Merge (pure, headless-testable)
# ---------------------------------------------------------------------------

def merge_file_lists(session_files: list[dict] | None,
                     extra_files: list[str] | None) -> tuple[list[dict], int]:
    """Merge session entries with explicitly requested files.

    Extras are appended (deduped by abspath) and win the active index when
    present; otherwise the session's order/active is kept. Returns
    (ordered_entries, active_index) where extras are {path, line: -1, col: -1}.
    """
    ordered: list[dict] = [dict(f) for f in (session_files or []) if isinstance(f, dict)]
    seen: set[str] = set()
    for f in ordered:
        p = f.get("path")
        if isinstance(p, str):
            seen.add(os.path.abspath(p))
    active = -1
    extras = [e for e in (extra_files or []) if isinstance(e, str) and e]
    for e in extras:
        ap = os.path.abspath(e)
        if ap in seen:
            active = next((i for i, f in enumerate(ordered) if f.get("path") and os.path.abspath(f["path"]) == ap), active)
            continue
        seen.add(ap)
        ordered.append({"path": ap, "line": -1, "col": -1, "backup": None})
        active = len(ordered) - 1
    return ordered, active


# ---------------------------------------------------------------------------
# GTK restore / continuous-save wiring (duck-typed, best-effort)
# ---------------------------------------------------------------------------

def _apply_backup_text(window, tab, text: str) -> None:
    try:
        doc = tab.get_document() if hasattr(tab, "get_document") else tab
        if doc is None:
            return
        try:
            doc.begin_not_undoable_action()
            started = True
        except Exception:
            started = False
        try:
            if hasattr(doc, "set_text"):
                doc.set_text(text)
        finally:
            try:
                if started:
                    doc.end_not_undoable_action()
            except Exception:
                logger.debug("session backup undoable end failed", exc_info=True)
        try:
            doc.set_modified(True)
        except Exception:
            logger.debug("session backup set_modified failed", exc_info=True)
    except Exception:
        logger.debug("session backup apply failed", exc_info=True)


def _place_cursor(window, tab, line: int, col: int) -> None:
    try:
        jump = getattr(window, "_jump_to_line", None)
        if callable(jump) and line >= 0:
            jump(tab, int(line), col=int(col) if col >= 0 else -1)
            return
        doc = tab.get_document() if hasattr(tab, "get_document") else None
        if doc is not None and hasattr(doc, "place_cursor") and line >= 0:
            try:
                it = doc.get_iter_at_line_offset(int(line), max(0, int(col)))
            except Exception:
                try:
                    it = doc.get_iter_at_line(int(line))
                except Exception:
                    it = None
            if it is not None:
                doc.place_cursor(it)
    except Exception:
        logger.debug("session cursor place failed", exc_info=True)


def restore_into_window(window, root: str | None, extra_files: list[str] | None = None,
                        extra_lines: dict | None = None, base: str | None = None) -> bool:
    """Restore saved session tabs into window, merging explicit files on top.

    Returns True when at least one tab was opened. Never raises: failures are
    logged at debug and the caller falls back to opening extras directly.
    """
    if window is None:
        return False
    extras = [e for e in (extra_files or []) if isinstance(e, str) and e]
    extra_lines = dict(extra_lines or {})
    session = load_for_root(root, base=base) if root else None
    session_files = list(session["files"]) if session else []
    if session is not None:
        try:
            active_hint = max(0, int(session.get("active", 0)))
        except (TypeError, ValueError):
            active_hint = 0
    else:
        active_hint = -1
    ordered, extra_active = merge_file_lists(session_files, extras)
    if extra_active >= 0:
        active_hint = extra_active
    # Filter: keep entries with a restorable path, an available backup, or an
    # explicit request (extras are created on open). Drop session-only entries
    # whose file vanished and which carry no backup.
    filtered: list[dict] = []
    extra_set = {os.path.abspath(e) for e in extras}
    for entry in ordered:
        path = entry.get("path")
        backup = entry.get("backup")
        if path and os.path.abspath(path) in extra_set:
            filtered.append(entry)
            continue
        if path and os.path.isfile(path):
            filtered.append(entry)
            continue
        if backup and root and read_backup(root, backup, base=base) is not None:
            filtered.append(entry)
            continue
        if path and not session:
            # No session on disk: extras pass through even if new files.
            filtered.append(entry)
            continue
    ordered = filtered
    if not ordered:
        return False
    if active_hint < 0 or active_hint >= len(ordered):
        active_hint = len(ordered) - 1 if extras else 0
    opened: list = []
    for i, entry in enumerate(ordered):
        path = entry.get("path")
        backup = entry.get("backup")
        line = entry.get("line", -1)
        col = entry.get("col", -1)
        if path and path in extra_lines:
            try:
                loc = extra_lines.get(path)
                if isinstance(loc, tuple):
                    l, c = loc
                elif isinstance(loc, int):
                    l, c = loc, None
                else:
                    l, c = None, None
                line = (int(l) - 1) if l and int(l) > 0 else -1
                col = (int(c) - 1) if c and int(c) > 0 else -1
            except (TypeError, ValueError):
                pass
        tab = None
        try:
            if path:
                opener = getattr(window, "open_file", None)
                if callable(opener):
                    tab = opener(path, line_pos=int(line) if isinstance(line, int) else -1,
                                 col_pos=int(col) if isinstance(col, int) else -1, jump_to=False)
            else:
                creator = getattr(window, "create_tab", None)
                if callable(creator):
                    tab = creator(jump_to=False)
        except Exception:
            logger.debug("session restore open failed for %r", path, exc_info=True)
            tab = None
        if tab is None:
            continue
        try:
            if backup and root:
                text = read_backup(root, backup, base=base)
                if text is not None:
                    _apply_backup_text(window, tab, text)
                    if isinstance(line, int) and line >= 0:
                        _place_cursor(window, tab, int(line), int(col) if isinstance(col, int) else -1)
            elif isinstance(line, int) and line >= 0 and not (path and path in extra_lines):
                # open_file already jumped for plain session entries; backups
                # need cursor after set_text (offsets shift), extras handled
                # by the opener itself.
                pass
        except Exception:
            logger.debug("session restore cursor/backup failed", exc_info=True)
        opened.append(tab)
    if not opened:
        return False
    try:
        notebook = getattr(window, "_notebook", None)
        if notebook is not None and hasattr(notebook, "set_current_page"):
            notebook.set_current_page(max(0, min(active_hint, len(opened) - 1)))
        else:
            setter = getattr(window, "set_active_tab", None)
            if callable(setter):
                setter(opened[max(0, min(active_hint, len(opened) - 1))])
    except Exception:
        logger.debug("session restore activate failed", exc_info=True)
    try:
        focus = getattr(window, "focus_active_editor", None)
        if callable(focus):
            focus()
    except Exception:
        logger.debug("session restore focus failed", exc_info=True)
    return True


def attach_window_session(window) -> None:
    """Connect tab signals to the window's debounced session saver (best-effort)."""
    if window is None:
        return
    try:
        sched = getattr(window, "_schedule_session_save", None)
        if not callable(sched):
            return
        existing = list(getattr(window, "_thor_session_handlers", None) or [])
        if existing:
            return  # already wired

        def _on_tabs_changed(*_args) -> None:
            try:
                window._schedule_session_save()
            except Exception:
                logger.debug("session schedule failed", exc_info=True)

        handlers: list = []
        connect = getattr(window, "connect", None)
        if not callable(connect):
            return
        for signal in ("tab-added", "tab-removed", "tabs-reordered", "active-tab-changed"):
            try:
                hid = connect(signal, _on_tabs_changed)
                handlers.append((window, hid))
            except Exception:
                logger.debug("session connect failed for %s", signal, exc_info=True)
        try:
            window._thor_session_handlers = handlers
        except Exception:
            logger.debug("session handlers store failed", exc_info=True)
    except Exception:
        logger.debug("session attach failed", exc_info=True)
