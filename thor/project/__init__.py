# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

try:
    from . import gitstatus as _gitstatus
except Exception:
    try:
        import gitstatus as _gitstatus  # type: ignore[no-redef]
    except Exception:
        _gitstatus = None  # type: ignore[assignment]

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

_PANEL_ICONS = ("system-file-manager", "folder", "view-list-tree")
_TREE_FOLDER_ICON = "folder"

#: TreeView background: the theme's view base color darkened 20%.
#: Both legacy (GtkTreeView) and modern (treeview.view) selectors so it
#: applies across GTK 3.x versions. Resolved live by the theme engine,
#: so light/dark theme switches update automatically.
_TREE_BG_CSS = (
    "GtkTreeView.view, GtkTreeView, treeview.view {"
    " background-color: shade(@theme_base_color, 0.8); }"
)

def tree_bg_css() -> str:
    """CSS for the file-tree background (pure string, headless-safe)."""
    return _TREE_BG_CSS
_TREE_FILE_ICON = "text-x-generic"

def _pick_icon(candidates: tuple[str, ...]) -> str:
    if Gtk is None:
        return candidates[0]
    try:
        theme = Gtk.IconTheme.get_default()
    except Exception:
        theme = None
    if theme is not None:
        for name in candidates:
            try:
                if theme.has_icon(name):
                    return name
            except Exception:
                logger.debug(f"icon probe failed for {name}", exc_info=True)
                continue
    return candidates[0]

#: One-shot handoff written by the `thor-code` launcher: the folder thor-code
#: was pointed at. Read once per window activation, then consumed (deleted),
#: so it acts as launch intent — not as persisted state. Lives under the
#: user cache dir because thor is single-instance: cwd/env of a `thor-code`
#: invocation never reach an already-running thor process.
PENDING_FILENAME = "pending-root"
PENDING_MAX_AGE_S = 60

#: Top-level names that mark a directory as a project worth auto-loading.
_PROJECT_MARKER_NAMES = frozenset(
    {
        ".git",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "CMakeLists.txt",
        "Makefile",
        "meson.build",
        "pyproject.toml",
    }
)

#: Top-level suffixes that mark a directory as a project.
_PROJECT_MARKER_SUFFIXES = (".sln", ".slnx", ".csproj")

def _cache_dir() -> str:
    try:
        from thor import xdg
        return os.path.join(xdg.cache_home(), "thor", "project-mode")
    except Exception:
        if GLib is not None:
            try:
                return os.path.join(GLib.get_user_cache_dir(), "thor", "project-mode")
            except Exception:
                logger.debug("user cache dir lookup failed", exc_info=True)
        return os.path.join(os.path.expanduser("~/.cache"), "thor", "project-mode")

def pending_root_path(base: str | None = None) -> str:
    """Path of the one-shot `thor-code` handoff file."""
    return os.path.join(base or _cache_dir(), PENDING_FILENAME)

def write_pending_root(folder: str, path: str | None = None) -> str | None:
    """Record launch intent for the next window activation. Returns the path."""
    target = path or pending_root_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(os.path.abspath(folder) + "\n")
    except OSError as e:
        logger.debug(f"pending write failed: {e!r}")
        return None
    return target

def take_pending_root(
    path: str | None = None,
    max_age_s: int = PENDING_MAX_AGE_S,
    now: float | None = None,
) -> str | None:
    """Read and consume the `thor-code` handoff (fresh entries only).

    Stale/empty handoffs are consumed (deleted) so one launch never
    affects a later window. A handoff naming a non-directory is invalid
    intent (transient mount, typo) — it is left in place, never
    destroyed, so a later activation can still honor it. Returns the
    abspath, or None when missing/stale/empty/not-a-directory.
    """
    target = path or pending_root_path()
    try:
        mtime = os.path.getmtime(target)
    except OSError:
        logger.debug(f"pending handoff missing: {target}", exc_info=True)
        return None
    try:
        with open(target, encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        logger.debug(f"pending handoff unreadable: {target}", exc_info=True)
        return None
    moment = time.time() if now is None else now
    if moment - mtime > max_age_s or not content:
        try:
            os.unlink(target)
        except OSError:
            logger.debug(f"pending consume failed: {target}", exc_info=True)
        return None
    try:
        validated = os.path.abspath(content)
    except Exception:
        logger.debug(f"pending handoff invalid content: {content!r}", exc_info=True)
        return None
    if not os.path.isdir(validated):
        return None
    try:
        os.unlink(target)
    except OSError:
        logger.debug(f"pending consume failed: {target}", exc_info=True)
    return validated

def has_project_markers(folder: str) -> bool:
    """True when folder's top level looks like a project (no recursion)."""
    try:
        entries = os.scandir(folder)
    except OSError:
        logger.debug(f"project markers scan failed for {folder}", exc_info=True)
        return False
    with entries:
        for entry in entries:
            name = entry.name
            lowered = name.lower()
            if lowered in _PROJECT_MARKER_NAMES:
                return True
            if lowered.endswith(_PROJECT_MARKER_SUFFIXES):
                return True
    return False

def is_unsafe_root(folder: str) -> bool:
    """True for $HOME, anything above it, or / — never auto-load these."""
    try:
        real = os.path.realpath(os.path.abspath(folder))
        home = os.path.realpath(os.path.expanduser("~"))
        return real == home or real == os.path.dirname(home) or real == "/"
    except Exception:
        logger.debug(f"unsafe-root check failed for {folder}", exc_info=True)
        return True

def resolve_startup_root(
    cwd: str | None, pending: str | None
) -> tuple[str, str | None]:
    """Decide the startup folder: ("load"|"prompt"|"none", dir|None).

    Explicit `thor-code` intent (pending) with no markers still prompts;
    an incidental cwd without markers (or any unsafe root reached via cwd)
    is silently ignored so plain launch from $HOME never crawls or nags.
    """
    if pending:
        candidate = os.path.abspath(pending)
        if not os.path.isdir(candidate):
            return ("none", None)
        if is_unsafe_root(candidate) or not has_project_markers(candidate):
            return ("prompt", candidate)
        return ("load", candidate)
    if cwd:
        candidate = os.path.abspath(cwd)
        if not os.path.isdir(candidate) or is_unsafe_root(candidate):
            return ("none", None)
        if has_project_markers(candidate):
            return ("load", candidate)
    return ("none", None)

#: Debounce for file-monitor-triggered git refreshes. The old 500ms value
#: let a `.git/index` touch from our own `git status` re-fire the monitor
#: into a steady ~2Hz refresh loop (subprocess + full tree recolor each
#: cycle). 1200ms coalesces bursts without feeling stale.
GIT_REFRESH_DEBOUNCE_MS = 1200

#: Debounce for working-tree file edits. Our own `git status` only ever
#: touches `.git/index`, never working-tree files, so these events cannot
#: self-trigger — no long gate needed, keep colors snappy while typing.
GIT_DIR_DEBOUNCE_MS = 500

#: Minimum time between two monitor-triggered `git status` runs. Manual
#: refreshes (set_root) bypass this via force=True. Prevents a hot
#: `.git/` (fetch/gc/index rewrite) from keeping the editor busy.
GIT_REFRESH_MIN_INTERVAL_S = 10.0

#: Debounce for filesystem-triggered tree rebuilds (create/delete/move).
#: Off-thread walk + idle populate, so a shorter window than git feels live
#: without hammering the disk during bursts (e.g. `git checkout`).
TREE_REFRESH_DEBOUNCE_MS = 800

#: Cap on recursive directory watches (inotify handles). Sized for mid-size
#: repos (a 2600-dir checkout needs every dir watched); still far below the
#: typical 8k+ `max_user_watches` floor. Beyond the cap the shallowest dirs
#: win (breadth-first, sorted), so overflow degrades to top-level coverage
#: instead of an arbitrary DFS subset.
MAX_WATCH_DIRS = 3000

#: Relative paths inside `.git/` that genuinely change status output.
#: Everything else (objects/, logs/, index.lock, *.tmp, …) is noise —
#: notably `git status` itself may rewrite `index`, which used to
#: self-trigger the next refresh.
_GIT_RELEVANT_FILES = frozenset({"HEAD", "index", "packed-refs", "ORIG_HEAD", "FETCH_HEAD"})

_GIT_NOISE_SUFFIXES = (".lock", ".tmp", ".swp", "~")

from thor.util import is_save_completed, tab_state_name

def _rel_within(path: str, base: str) -> str | None:
    """Relative path of `path` under `base`, or None when outside."""
    try:
        abs_path = os.path.abspath(path)
        abs_base = os.path.abspath(base)
    except Exception:
        logger.debug(f"rel-within failed for {path!r} under {base!r}", exc_info=True)
        return None
    try:
        rel = os.path.relpath(abs_path, abs_base)
    except Exception:
        logger.debug(f"relpath failed for {path!r} under {base!r}", exc_info=True)
        return None
    if rel == ".":
        return ""
    if rel.startswith(".." + os.sep) or rel == "..":
        return None
    return rel

def _enclosing_git_dir(root_dir: str) -> str:
    """Absolute `.git` path for `root_dir` (headless-safe, no subprocess).

    Walks up from `root_dir` so opening a subdirectory still resolves the
    enclosing repo's `.git` — matching `git_monitor_target`. Falls back to
    `root_dir/.git` when no ancestor carries one.
    """
    try:
        walking = os.path.abspath(root_dir)
    except Exception:
        return os.path.join(str(root_dir), ".git")
    fallback = os.path.join(walking, ".git")
    try:
        while True:
            candidate = os.path.join(walking, ".git")
            try:
                if os.path.isdir(candidate) or os.path.isfile(candidate):
                    return candidate
            except Exception:
                logger.debug("enclosing-git-dir probe failed", exc_info=True)
                break
            parent = os.path.dirname(walking)
            if parent == walking:
                break
            walking = parent
    except Exception:
        logger.debug("enclosing-git-dir walk failed", exc_info=True)
    return fallback

def should_refresh_for_git_event(
    root_dir: str,
    file_path: str | None,
    other_path: str | None = None,
    event_type: str = "",
) -> bool:
    """Headless-safe filter: does this monitor event merit a re-query?

    - Events inside `.git/` only count for status-relevant files
      (HEAD/index/refs/...). objects/logs/locks are ignored so our own
      `git status` touching `index` cannot self-trigger a loop.
    - Events elsewhere under the root (top-level dir monitor) count —
      they may signal added/removed files.
    - Events outside the root never count, except inside the real `.git/`
      — which lives above the root when a subdirectory is open (the
      monitor watches the enclosing repo's `.git`, see
      `git_monitor_target`).
    """
    try:
        candidates = [p for p in (file_path, other_path) if p]
        if not candidates:
            # Conservative: unknown file, but only if some monitor fired —
            # treat as relevant so we never miss a real change.
            return True
        git_dir = _enclosing_git_dir(root_dir)
        for candidate in candidates:
            rel_git = _rel_within(candidate, git_dir)
            if rel_git is not None:
                if rel_git == "":
                    # The `.git` dir itself changed (e.g. created) — refresh.
                    return True
                base = os.path.basename(rel_git)
                if base.endswith(_GIT_NOISE_SUFFIXES):
                    continue
                first = rel_git.split(os.sep, 1)[0]
                if rel_git in _GIT_RELEVANT_FILES or first == "refs":
                    return True
                # Noise inside .git (objects/logs/info/hooks/…) — ignore.
                continue
            if _rel_within(candidate, root_dir) is None:
                continue
            # A real path under the project root changed.
            return True
        return False
    except Exception:
        logger.debug("git event filter failed", exc_info=True)
        return True

def should_rebuild_tree_for_event(
    root_dir: str,
    file_path: str | None,
    other_path: str | None = None,
) -> bool:
    """Headless-safe filter: does this event change the file tree?

    True for creates/deletes/moves anywhere under the root except inside
    `.git/` (git colors handle that side). Outside the root never counts.
    Unknown paths count so a delete is never missed.
    """
    try:
        candidates = [p for p in (file_path, other_path) if p]
        if not candidates:
            return True
        for candidate in candidates:
            rel = _rel_within(candidate, root_dir)
            if rel is None:
                continue
            if rel == ".git" or rel.startswith(".git" + os.sep):
                continue
            return True
        return False
    except Exception:
        logger.debug("tree event filter failed", exc_info=True)
        return True

def collect_watch_dirs(
    root_dir: str, max_depth: int = 10, max_dirs: int = MAX_WATCH_DIRS
) -> list[str]:
    """Directories to monitor recursively (headless-safe, no GTK/Gio).

    Mirrors build_file_tree pruning (hidden, _PRUNE_DIRS, symlinks) so we
    never watch build output or symlink farms. Always includes root_dir.
    Breadth-first and sorted: when `max_dirs` still overflows, the
    shallowest levels stay covered and the subset is deterministic.
    """
    out: list[str] = []
    try:
        if not os.path.isdir(root_dir):
            return out
        out.append(os.path.abspath(root_dir))
        queue: list[tuple[str, int]] = [(os.path.abspath(root_dir), 0)]
        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth or len(out) >= max_dirs:
                continue
            try:
                with os.scandir(current) as entries:
                    children = sorted(
                        (e for e in entries if e.is_dir(follow_symlinks=False)),
                        key=lambda e: e.name,
                    )
                    for entry in children:
                        try:
                            name = entry.name
                            if name.startswith("."):
                                continue
                            if name in _PRUNE_DIRS:
                                continue
                            path = os.path.abspath(entry.path)
                            if os.path.islink(path):
                                continue
                            if not os.path.isdir(path):
                                continue
                            out.append(path)
                            if len(out) >= max_dirs:
                                break
                            queue.append((path, depth + 1))
                        except OSError:
                            logger.debug("watch-dir entry skipped", exc_info=True)
                            continue
            except OSError:
                logger.debug(f"watch-dir scandir failed for {current}", exc_info=True)
                continue
    except Exception:
        logger.debug(f"collect-watch-dirs failed for {root_dir}", exc_info=True)
    return out

def git_monitor_target(folder: str) -> str | None:
    """`.git` path to monitor for folder (headless-safe, no GTK/Gio).

    Resolves the enclosing repo root so opening a subdirectory still
    watches the real `.git`; falls back to `folder/.git` when the root
    cannot be determined. Returns None for an empty folder.
    """
    try:
        if not folder:
            return None
        git_root = None
        try:
            if _gitstatus is not None:
                git_root = _gitstatus.find_git_root(folder)
        except Exception:
            logger.debug("git root probe failed", exc_info=True)
            git_root = None
        base = git_root or folder
        return os.path.join(base, ".git")
    except Exception:
        logger.debug(f"git monitor target failed for {folder!r}", exc_info=True)
        return None

def git_event_paths(file_obj, other_obj=None) -> tuple[str | None, str | None]:
    """Best-effort Gio.File -> filesystem path (headless-safe)."""
    def _one(obj) -> str | None:
        if obj is None:
            return None
        try:
            get_path = getattr(obj, "get_path", None)
            if callable(get_path):
                value = get_path()
                return value if isinstance(value, str) else None
        except Exception:
            logger.debug("event path probe failed", exc_info=True)
        return None

    try:
        return (_one(file_obj), _one(other_obj))
    except Exception:
        logger.debug("event paths failed", exc_info=True)
        return (None, None)

@dataclass
class FileNode:
    name: str
    path: str
    is_dir: bool
    children: list["FileNode"] = field(default_factory=list)

def build_file_tree(root_dir: str, max_depth: int = 10) -> list[FileNode]:
    nodes: list[FileNode] = []
    try:
        entries = sorted(
            os.scandir(root_dir),
            key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
        )
    except OSError:
        logger.debug(f"file tree scandir failed for {root_dir}", exc_info=True)
        return []
    for entry in entries:
        name = entry.name
        path = entry.path
        try:
            if name.startswith("."):
                continue
            if os.path.islink(path):
                continue
            if entry.is_dir(follow_symlinks=False):
                if (
                    name in _PRUNE_DIRS
                    or max_depth <= 0
                ):
                    continue
                children = build_file_tree(path, max_depth - 1)
                nodes.append(FileNode(name=name, path=path, is_dir=True, children=children))
            else:
                nodes.append(FileNode(name=name, path=path, is_dir=False, children=[]))
        except OSError:
            logger.debug(f"file tree entry skipped: {path}", exc_info=True)
            continue
    return nodes

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    try:
        gi.require_version("GtkSource", "4")
    except Exception:
        try:
            gi.require_version("GtkSource", "3.0")
        except Exception:
            logger.debug("GtkSource require failed", exc_info=True)
    from gi.repository import Gtk, Gdk, Gio, GLib, GObject, GtkSource  # type: ignore
except Exception:  # headless
    Gtk = Gdk = Gio = GLib = GObject = GtkSource = None  # type: ignore

if Gtk is not None:
    class ProjectBrowser(Gtk.Box):
        __gsignals__ = {
            "open-file": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_STRING,)),
            "choose-root": (GObject.SignalFlags.RUN_LAST, None, ()),
        }

        def __init__(self) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self._root_dir = ""
            self._col_icon = 0
            self._col_label = 1
            self._col_path = 2
            self._col_kind = 3
            self._col_fg = 4
            self._col_fg_set = 5
            self._git_statuses: dict = {}
            self._git_generation = 0
            self._monitors: list = []
            self._dir_monitors: dict = {}
            self._git_monitor = None
            self._git_procs: list = []
            self._git_procs_lock = threading.Lock()
            self._refresh_timer = None
            self._refresh_interval_s = GIT_REFRESH_MIN_INTERVAL_S
            self._tree_timer = None
            self._watched_dirs: set = set()
            self._git_root_cached: str | None = None
            self._last_git_refresh = 0.0
            self._tree_generation = 0

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            self._root_label = Gtk.Label(label="No folder selected")
            self._root_label.set_xalign(0.0)
            header.pack_start(self._root_label, True, True, 0)
            self.pack_start(header, False, False, 0)

            self.store = Gtk.TreeStore(str, str, str, str, str, bool)
            self.tree = Gtk.TreeView.new_with_model(self.store)
            self.tree.set_headers_visible(False)
            col = Gtk.TreeViewColumn("Files")
            icon = Gtk.CellRendererPixbuf()
            cell = Gtk.CellRendererText()
            col.pack_start(icon, False)
            col.pack_start(cell, True)
            col.add_attribute(icon, "icon-name", self._col_icon)
            col.add_attribute(cell, "text", self._col_label)
            col.add_attribute(cell, "foreground", self._col_fg)
            col.add_attribute(cell, "foreground-set", self._col_fg_set)
            self.tree.append_column(col)
            self.tree.connect("row-activated", self._on_row_activated)
            try:
                provider = Gtk.CssProvider()
                provider.load_from_data(tree_bg_css().encode("utf-8"))
                self.tree.get_style_context().add_provider(
                    provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
            except Exception as e:
                logger.debug(f"tree bg css failed: {e!r}")

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(self.tree)
            self.pack_start(scrolled, True, True, 0)
            self.show_all()

        def set_root(self, folder: str) -> None:
            self._git_generation += 1
            self._tree_generation += 1
            generation = self._tree_generation
            try:
                self._kill_git_procs()
            except Exception:
                logger.debug("set_root git kill failed", exc_info=True)
            self._cancel_monitors()
            self._root_dir = folder
            self._git_statuses = {}
            self._git_root_cached = None
            try:
                self.store.clear()
            except Exception:
                logger.debug("tree store clear failed", exc_info=True)
            if not folder or not os.path.isdir(folder):
                self._root_label.set_text("No folder selected")
                return
            base = os.path.basename(folder.rstrip(os.sep)) or folder
            self._root_label.set_text(base)
            try:
                root_iter = self.store.append(
                    None,
                    [_TREE_FOLDER_ICON, base + "/", folder, "folder", None, False],
                )
                self.store.append(
                    root_iter, [_TREE_FILE_ICON, "Loading…", folder, "loading", None, False]
                )
            except Exception:
                root_iter = None
            try:
                self.tree.expand_row(Gtk.TreePath.new_from_indices([0]), False)
            except Exception:
                logger.debug("tree expand failed", exc_info=True)
            self._setup_git_monitors(folder)
            # Build the file list off the UI thread; even mid-size repos
            # made the old synchronous walk hitch the editor on open.
            try:
                thread = threading.Thread(
                    target=self._build_tree_thread,
                    args=(folder, generation),
                    daemon=True,
                )
                thread.start()
            except Exception as e:
                logger.debug(f"tree build spawn failed: {e!r}")
                try:
                    self._populate_tree(build_file_tree(folder), generation)
                except Exception:
                    logger.debug("tree populate fallback failed", exc_info=True)
            self.refresh_git_statuses(force=True)

        def _build_tree_thread(self, folder: str, generation: int) -> None:
            try:
                nodes = build_file_tree(folder)
            except Exception:
                logger.debug(f"tree build failed for {folder}", exc_info=True)
                nodes = []
            try:
                if generation != self._tree_generation:
                    return
            except Exception:
                logger.debug("tree generation check failed", exc_info=True)
                return
            try:
                if GLib is not None:
                    GLib.idle_add(self._populate_tree, nodes, generation)
                else:
                    self._populate_tree(nodes, generation)
            except Exception:
                logger.debug("tree populate schedule failed", exc_info=True)

        def _expanded_dir_paths(self) -> set:
            """Filesystem paths of expanded folder rows (survives rebuild)."""
            out: set = set()
            try:
                store, tree = self.store, self.tree
                def walk(tree_iter) -> None:
                    current = tree_iter
                    while current is not None:
                        try:
                            kind = store.get_value(current, self._col_kind)
                            path = store.get_value(current, self._col_path)
                            tpath = store.get_path(current)
                        except Exception:
                            kind = path = tpath = None
                        if kind == "folder" and isinstance(path, str) and tpath is not None:
                            try:
                                if tree.row_expanded(tpath):
                                    out.add(os.path.abspath(path))
                            except Exception:
                                logger.debug("expanded probe failed", exc_info=True)
                        try:
                            child = store.iter_children(current)
                        except Exception:
                            child = None
                        if child is not None:
                            walk(child)
                        try:
                            current = store.iter_next(current)
                        except Exception:
                            logger.debug("expanded iter failed", exc_info=True)
                            break
                first = store.get_iter_first()
                if first is not None:
                    walk(first)
            except Exception:
                logger.debug("expanded walk failed", exc_info=True)
            return out

        def _restore_expanded(self, paths: set) -> None:
            if not paths:
                return
            try:
                store, tree = self.store, self.tree
                def walk(tree_iter) -> None:
                    current = tree_iter
                    while current is not None:
                        try:
                            kind = store.get_value(current, self._col_kind)
                            path = store.get_value(current, self._col_path)
                            tpath = store.get_path(current)
                        except Exception:
                            kind = path = tpath = None
                        if kind == "folder" and isinstance(path, str) and tpath is not None:
                            try:
                                if os.path.abspath(path) in paths:
                                    tree.expand_row(tpath, False)
                            except Exception:
                                logger.debug("restore probe failed", exc_info=True)
                        try:
                            child = store.iter_children(current)
                        except Exception:
                            child = None
                        if child is not None:
                            walk(child)
                        try:
                            current = store.iter_next(current)
                        except Exception:
                            logger.debug("restore iter failed", exc_info=True)
                            break
                first = store.get_iter_first()
                if first is not None:
                    walk(first)
            except Exception:
                logger.debug("restore walk failed", exc_info=True)

        def refresh_tree(self) -> None:
            """Re-walk the root off-thread (create/delete/move updates)."""
            root = self._root_dir
            if not root or not os.path.isdir(root):
                return
            self._tree_generation += 1
            generation = self._tree_generation
            try:
                thread = threading.Thread(
                    target=self._build_tree_thread,
                    args=(root, generation),
                    daemon=True,
                )
                thread.start()
            except Exception as e:
                logger.debug(f"tree refresh spawn failed: {e!r}")

        def _populate_tree(self, nodes, generation: int) -> bool:
            if generation != self._tree_generation:
                return False
            keep_expanded = self._expanded_dir_paths()
            try:
                self.store.clear()
            except Exception:
                return False
            folder = self._root_dir
            if not folder or not os.path.isdir(folder):
                return False
            base = os.path.basename(folder.rstrip(os.sep)) or folder
            try:
                root_iter = self.store.append(
                    None,
                    [_TREE_FOLDER_ICON, base + "/", folder, "folder", None, False],
                )
            except Exception:
                return False
            try:
                self._freeze_tree(True)
                # Color rows with the last known statuses right away: the
                # post-build _apply_git_statuses below is skipped when the
                # statuses are unchanged, which is exactly when a rebuild
                # (content edit, same git status) would otherwise leave
                # every row uncolored forever.
                known = dict(self._git_statuses or {})
                child_colors: list = []
                for node in nodes:
                    child_colors.append(self._append_node(root_iter, node, known))
                gs = _gitstatus if _gitstatus is not None else None
                try:
                    root_color = gs.aggregate_dir_color(child_colors) if gs is not None else None
                except Exception:
                    root_color = None
                try:
                    self.store.set_value(root_iter, self._col_fg, root_color)
                    self.store.set_value(root_iter, self._col_fg_set, root_color is not None)
                except Exception:
                    logger.debug("tree populate failed", exc_info=True)
            except Exception as e:
                logger.debug(f"tree populate failed: {e!r}")
            finally:
                try:
                    self._freeze_tree(False)
                except Exception:
                    logger.debug("tree unfreeze failed", exc_info=True)
            try:
                self.tree.expand_row(Gtk.TreePath.new_from_indices([0]), False)
            except Exception:
                    logger.debug("tree expand-root failed", exc_info=True)
            try:
                self._restore_expanded(keep_expanded)
            except Exception:
                    logger.debug("expanded restore failed", exc_info=True)
            try:
                self._refresh_dir_monitors()
            except Exception:
                    logger.debug("dir monitors refresh failed", exc_info=True)
            # Statuses may have arrived while the tree was building.
            if self._git_statuses:
                try:
                    self._apply_git_statuses(self._git_statuses, self._git_generation)
                except Exception:
                    logger.debug("git statuses apply failed", exc_info=True)
            return False

        def _freeze_tree(self, freeze: bool) -> None:
            try:
                if freeze:
                    self.tree.freeze_child_notify()
                else:
                    self.tree.thaw_child_notify()
            except Exception:
                    logger.debug("tree freeze failed", exc_info=True)

        def _append_node(self, parent, node: FileNode, statuses: dict | None = None) -> str | None:
            gs = _gitstatus if _gitstatus is not None else None
            if statuses is None:
                statuses = self._git_statuses
            if node.is_dir:
                child_colors: list = []
                # Append first so children have a parent, then set the
                # folder color once the subtree aggregate is known.
                folder_iter = self.store.append(
                    parent,
                    [_TREE_FOLDER_ICON, node.name + "/", node.path, "folder", None, False],
                )
                for child in node.children:
                    child_colors.append(self._append_node(folder_iter, child, statuses))
                color = gs.aggregate_dir_color(child_colors) if gs is not None else None
                try:
                    self.store.set_value(folder_iter, self._col_fg, color)
                    self.store.set_value(folder_iter, self._col_fg_set, color is not None)
                except Exception:
                    logger.debug("folder color set failed", exc_info=True)
                return color
            color = gs.color_for_path(statuses, node.path) if gs is not None else None
            self.store.append(
                parent, [_TREE_FILE_ICON, node.name, node.path, "file", color, color is not None]
            )
            return color

        # -- git -------------------------------------------------------
        def refresh_git_statuses(self, force: bool = False, min_interval_s=None) -> None:
            """Re-query `git status` off the UI thread, then repaint colors.

            Monitor-triggered callers pass force=False and are rate-limited
            so a hot `.git/` cannot keep the editor busy; set_root passes
            force=True. Working-tree edits (via _on_dir_changed) pass a
            zero interval — our own `git status` never touches those files,
            so they cannot self-trigger and stay snappy.
            """
            root = self._root_dir
            if not root or not os.path.isdir(root):
                return
            if _gitstatus is None:
                return
            try:
                interval = float(GIT_REFRESH_MIN_INTERVAL_S if min_interval_s is None else min_interval_s)
            except Exception:
                interval = GIT_REFRESH_MIN_INTERVAL_S
            if not force:
                try:
                    now = time.monotonic()
                except Exception:
                    now = 0.0
                try:
                    last = float(getattr(self, "_last_git_refresh", 0.0) or 0.0)
                except Exception:
                    last = 0.0
                if interval > 0 and now and last and (now - last) < interval:
                    try:
                        remaining_ms = int((interval - (now - last)) * 1000)
                    except Exception:
                        remaining_ms = 0
                    if remaining_ms > 0:
                        self._arm_git_timer(
                            max(remaining_ms, GIT_REFRESH_DEBOUNCE_MS),
                            self._git_generation,
                            interval,
                        )
                    return
            self._git_generation += 1
            generation = self._git_generation
            try:
                kill = getattr(self, "_kill_git_procs", None)
                if callable(kill):
                    kill()
            except Exception:
                logger.debug("git proc kill on refresh failed", exc_info=True)
            try:
                self._last_git_refresh = time.monotonic()
            except Exception:
                logger.debug("git refresh clock failed", exc_info=True)
            try:
                thread = threading.Thread(
                    target=self._query_git_thread,
                    args=(root, generation),
                    daemon=True,
                )
                thread.start()
            except Exception as e:
                logger.debug(f"git refresh spawn failed: {e!r}")

        def _kill_git_procs(self) -> None:
            """Kill outstanding `git status` procs (generation bump/cleanup)."""
            procs = []
            lock = getattr(self, "_git_procs_lock", None)
            try:
                if lock is not None:
                    lock.acquire()
                procs = list(getattr(self, "_git_procs", []) or [])
                try:
                    self._git_procs = []
                except Exception:
                    logger.debug("git procs reset failed", exc_info=True)
            except Exception:
                logger.debug("git procs snapshot failed", exc_info=True)
                procs = []
            finally:
                try:
                    if lock is not None:
                        lock.release()
                except Exception:
                    logger.debug("git procs unlock failed", exc_info=True)
            for proc in procs:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    logger.debug("git proc kill failed", exc_info=True)

        def _run_git_statuses(self, git_root: str, generation: int, timeout_s: float = 3.0) -> dict:
            """Run `git status` with a short timeout; abort on generation bump."""
            gs = _gitstatus
            if gs is None:
                return {}
            try:
                proc = subprocess.Popen(
                    ["git", "-c", "core.quotepath=false", "status",
                     "--porcelain=v1", "-uall", "-z"],
                    cwd=git_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                logger.debug(f"git status spawn failed for {git_root}", exc_info=True)
                return {}
            lock = getattr(self, "_git_procs_lock", None)
            try:
                if lock is not None:
                    lock.acquire()
                procs = getattr(self, "_git_procs", None)
                if isinstance(procs, list):
                    procs.append(proc)
            except Exception:
                logger.debug("git procs register failed", exc_info=True)
            finally:
                try:
                    if lock is not None:
                        lock.release()
                except Exception:
                    logger.debug("git procs unlock failed", exc_info=True)
            try:
                try:
                    deadline = time.monotonic() + float(timeout_s)
                except Exception:
                    logger.debug("git status clock failed", exc_info=True)
                    deadline = 0.0
                while True:
                    try:
                        if generation != self._git_generation:
                            try:
                                proc.kill()
                            except Exception:
                                logger.debug("git proc kill on bump failed", exc_info=True)
                            return {}
                    except Exception:
                        logger.debug("git generation check failed", exc_info=True)
                        return {}
                    try:
                        rc = proc.poll()
                    except Exception:
                        logger.debug("git proc poll failed", exc_info=True)
                        return {}
                    if rc is not None:
                        break
                    try:
                        now = time.monotonic()
                    except Exception:
                        logger.debug("git status clock failed", exc_info=True)
                        now = 0.0
                    if deadline and now >= deadline:
                        try:
                            proc.kill()
                        except Exception:
                            logger.debug("git proc kill on timeout failed", exc_info=True)
                        logger.debug(f"git status timeout for {git_root}")
                        return {}
                    time.sleep(0.05)
                try:
                    out, _ = proc.communicate()
                except Exception:
                    logger.debug("git status communicate failed", exc_info=True)
                    return {}
                try:
                    if generation != self._git_generation:
                        return {}
                except Exception:
                    logger.debug("git generation check failed", exc_info=True)
                    return {}
                if proc.returncode != 0:
                    logger.debug(f"git status rc={proc.returncode} for {git_root}")
                    return {}
                try:
                    return gs.parse_porcelain_z(out or b"", os.path.abspath(git_root))
                except Exception:
                    logger.debug(f"git status parse failed for {git_root}", exc_info=True)
                    return {}
            finally:
                try:
                    if lock is not None:
                        lock.acquire()
                    procs = getattr(self, "_git_procs", None)
                    if isinstance(procs, list):
                        try:
                            procs.remove(proc)
                        except ValueError:
                            logger.debug("git procs unregister skipped", exc_info=True)
                except Exception:
                    logger.debug("git procs unregister failed", exc_info=True)
                finally:
                    try:
                        if lock is not None:
                            lock.release()
                    except Exception:
                        logger.debug("git procs unlock failed", exc_info=True)

        def _query_git_thread(self, root: str, generation: int) -> None:
            statuses: dict = {}
            try:
                git_root = getattr(self, "_git_root_cached", None)
                if not git_root or not os.path.isdir(git_root):
                    git_root = _gitstatus.find_git_root(root)
                    try:
                        self._git_root_cached = git_root
                    except Exception:
                        logger.debug("git root cache failed", exc_info=True)
                if git_root:
                    try:
                        runner = getattr(self, "_run_git_statuses", None)
                        if callable(runner):
                            statuses = runner(git_root, generation)
                        else:
                            statuses = _gitstatus.get_git_statuses(git_root)
                    except Exception:
                        logger.debug(f"git status failed for {root}", exc_info=True)
                        statuses = {}
            except Exception:
                logger.debug(f"git status failed for {root}", exc_info=True)
                statuses = {}
            try:
                if generation != self._git_generation:
                    return
            except Exception:
                logger.debug("git generation check failed", exc_info=True)
                return
            try:
                if GLib is not None:
                    GLib.idle_add(self._apply_git_statuses, statuses, generation)
                else:
                    self._apply_git_statuses(statuses, generation)
            except Exception:
                logger.debug("git apply schedule failed", exc_info=True)

        def _status_color_map(self, statuses: dict) -> dict:
            """Precompute {abspath: color} once per refresh (no per-row abspath)."""
            color_map: dict = {}
            gs = _gitstatus
            if gs is None or not statuses:
                return color_map
            try:
                for path, code in statuses.items():
                    try:
                        color = gs.status_to_color(code[0], code[1])
                    except Exception:
                        logger.debug("git color convert failed", exc_info=True)
                        continue
                    if color is None:
                        continue
                    try:
                        color_map[os.path.abspath(path)] = color
                    except Exception:
                        logger.debug("git color map store failed", exc_info=True)
                        continue
            except Exception:
                logger.debug("git color map failed", exc_info=True)
            return color_map

        def _apply_git_statuses(self, statuses: dict, generation: int) -> bool:
            if generation != self._git_generation:
                return False
            statuses = statuses or {}
            if statuses == self._git_statuses:
                # Steady state (e.g. our own index touch re-firing the
                # monitor): skip the full tree walk entirely.
                return False
            self._git_statuses = statuses
            try:
                root_iter = self.store.get_iter_first()
            except Exception:
                root_iter = None
            if root_iter is None:
                return False
            color_map = self._status_color_map(statuses)
            try:
                self._freeze_tree(True)
                child = self.store.iter_children(root_iter)
                colors: list = []
                while child is not None:
                    colors.append(self._recolor_subtree(child, color_map))
                    try:
                        child = self.store.iter_next(child)
                    except Exception:
                        logger.debug("git recolor iter failed", exc_info=True)
                        break
                gs = _gitstatus
                root_color = gs.aggregate_dir_color(colors) if gs is not None else None
                try:
                    current = self.store.get_value(root_iter, self._col_fg)
                except Exception:
                    current = object()
                try:
                    current_set = self.store.get_value(root_iter, self._col_fg_set)
                except Exception:
                    current_set = object()
                want_set = root_color is not None
                if current != root_color or current_set != want_set:
                    try:
                        self.store.set_value(root_iter, self._col_fg, root_color)
                        self.store.set_value(root_iter, self._col_fg_set, want_set)
                    except Exception:
                        logger.debug("git recolor row failed", exc_info=True)
            except Exception as e:
                logger.debug(f"git recolor failed: {e!r}")
            finally:
                try:
                    self._freeze_tree(False)
                except Exception:
                    logger.debug("git recolor children failed", exc_info=True)
            return False

        def _recolor_subtree(self, tree_iter, color_map: dict | None = None) -> str | None:
            gs = _gitstatus
            if color_map is None:
                color_map = self._status_color_map(self._git_statuses)
            try:
                kind = self.store.get_value(tree_iter, self._col_kind)
                path = self.store.get_value(tree_iter, self._col_path)
            except Exception:
                return None
            if kind == "file":
                color = None
                try:
                    if isinstance(path, str):
                        color = color_map.get(os.path.abspath(path))
                except Exception:
                    color = None
                try:
                    current = self.store.get_value(tree_iter, self._col_fg)
                except Exception:
                    current = object()
                try:
                    current_set = self.store.get_value(tree_iter, self._col_fg_set)
                except Exception:
                    current_set = object()
                want_set = color is not None
                if current != color or current_set != want_set:
                    try:
                        self.store.set_value(tree_iter, self._col_fg, color)
                        self.store.set_value(tree_iter, self._col_fg_set, want_set)
                    except Exception:
                        logger.debug("git recolor row failed", exc_info=True)
                return color
            # Folder: recurse into children, then aggregate.
            colors: list = []
            try:
                child = self.store.iter_children(tree_iter)
            except Exception:
                child = None
            while child is not None:
                colors.append(self._recolor_subtree(child, color_map))
                try:
                    child = self.store.iter_next(child)
                except Exception:
                    logger.debug("git recolor iter failed", exc_info=True)
                    break
            color = gs.aggregate_dir_color(colors) if gs is not None else None
            try:
                current = self.store.get_value(tree_iter, self._col_fg)
            except Exception:
                current = object()
            try:
                current_set = self.store.get_value(tree_iter, self._col_fg_set)
            except Exception:
                current_set = object()
            want_set = color is not None
            if current != color or current_set != want_set:
                try:
                    self.store.set_value(tree_iter, self._col_fg, color)
                    self.store.set_value(tree_iter, self._col_fg_set, want_set)
                except Exception:
                    logger.debug("git recolor children failed", exc_info=True)
            return color

        def _setup_git_monitors(self, folder: str) -> None:
            if Gio is None:
                return
            try:
                watched: list = []
                dir_map: dict = {}
                watch_dirs = collect_watch_dirs(folder)
                self._watched_dirs = set(watch_dirs)
                for watch_dir in watch_dirs:
                    try:
                        monitor = Gio.File.new_for_path(watch_dir).monitor_directory(
                            Gio.FileMonitorFlags.NONE, None
                        )
                    except Exception:
                        logger.debug(f"dir monitor failed for {watch_dir}", exc_info=True)
                        continue
                    try:
                        monitor.connect("changed", self._on_dir_changed)
                    except Exception:
                        logger.debug(f"dir monitor connect failed for {watch_dir}", exc_info=True)
                        try:
                            monitor.cancel()
                        except Exception:
                            logger.debug(f"dir monitor cancel failed for {watch_dir}", exc_info=True)
                        continue
                    watched.append(monitor)
                    dir_map[watch_dir] = monitor
                git_path = git_monitor_target(folder)
                git_monitor = None
                try:
                    if git_path is not None and os.path.isdir(git_path):
                        git_monitor = Gio.File.new_for_path(git_path).monitor_directory(
                            Gio.FileMonitorFlags.NONE, None
                        )
                    elif git_path is not None and os.path.isfile(git_path):
                        git_monitor = Gio.File.new_for_path(git_path).monitor_file(
                            Gio.FileMonitorFlags.NONE, None
                        )
                    if git_monitor is not None:
                        try:
                            git_monitor.connect("changed", self._on_git_changed)
                        except Exception:
                            logger.debug(f"git monitor connect failed for {git_path}", exc_info=True)
                        watched.append(git_monitor)
                except Exception:
                    logger.debug(f"git monitor failed for {git_path}", exc_info=True)
                    git_monitor = None
                self._monitors = watched
                self._dir_monitors = dir_map
                self._git_monitor = git_monitor
            except Exception:
                logger.debug("git monitors setup failed", exc_info=True)
                self._monitors = []
                self._dir_monitors = {}
                self._git_monitor = None

        def _refresh_dir_monitors(self) -> None:
            """Delta-update dir watches after a rebuild (new folders appear).

            Only watches for added/removed dirs change; the `.git` monitor
            is never touched so no git event is dropped mid-refresh.
            """
            if Gio is None or not self._root_dir:
                return
            try:
                wanted = set(collect_watch_dirs(self._root_dir))
            except Exception:
                logger.debug("dir monitors collect failed", exc_info=True)
                return
            try:
                current = set(getattr(self, "_watched_dirs", set()) or set())
            except Exception:
                logger.debug("dir monitors current failed", exc_info=True)
                current = set()
            if wanted == current:
                return
            dir_map = getattr(self, "_dir_monitors", None)
            if not isinstance(dir_map, dict):
                try:
                    self._setup_git_monitors(self._root_dir)
                except Exception:
                    logger.debug("dir monitors rebuild failed", exc_info=True)
                return
            try:
                for dead in current - wanted:
                    mon = dir_map.pop(dead, None)
                    if mon is None:
                        continue
                    try:
                        mon.cancel()
                    except Exception:
                        logger.debug(f"dir monitor cancel failed for {dead}", exc_info=True)
                    try:
                        self._monitors.remove(mon)
                    except (ValueError, AttributeError):
                        logger.debug("dir monitor list remove skipped", exc_info=True)
                    except Exception:
                        logger.debug("dir monitors list remove failed", exc_info=True)
                for fresh in sorted(wanted - current):
                    try:
                        monitor = Gio.File.new_for_path(fresh).monitor_directory(
                            Gio.FileMonitorFlags.NONE, None
                        )
                    except Exception:
                        logger.debug(f"dir monitor failed for {fresh}", exc_info=True)
                        continue
                    try:
                        monitor.connect("changed", self._on_dir_changed)
                    except Exception:
                        logger.debug(f"dir monitor connect failed for {fresh}", exc_info=True)
                        try:
                            monitor.cancel()
                        except Exception:
                            logger.debug(f"dir monitor cancel failed for {fresh}", exc_info=True)
                        continue
                    dir_map[fresh] = monitor
                    try:
                        self._monitors.append(monitor)
                    except Exception:
                        logger.debug("dir monitors list append failed", exc_info=True)
                        dir_map.pop(fresh, None)
                        try:
                            monitor.cancel()
                        except Exception:
                            logger.debug(f"dir monitor cancel failed for {fresh}", exc_info=True)
                self._watched_dirs = wanted
            except Exception:
                logger.debug("dir monitors refresh failed", exc_info=True)

        def _arm_git_timer(self, delay_ms: int, generation: int, min_interval_s=None) -> None:
            if GLib is None:
                return
            try:
                try:
                    self._refresh_interval_s = float(
                        GIT_REFRESH_MIN_INTERVAL_S if min_interval_s is None else min_interval_s
                    )
                except Exception:
                    self._refresh_interval_s = GIT_REFRESH_MIN_INTERVAL_S
                if self._refresh_timer is not None:
                    try:
                        GLib.source_remove(self._refresh_timer)
                    except Exception:
                        logger.debug("git timer remove failed", exc_info=True)
                    self._refresh_timer = None
                self._refresh_timer = GLib.timeout_add(
                    max(50, int(delay_ms)), self._on_git_changed_fire, generation
                )
            except Exception as e:
                logger.debug(f"git change debounce failed: {e!r}")

        def _on_git_changed(self, _monitor, _file, _other=None, _event=None) -> None:
            if GLib is None:
                return
            try:
                file_path, other_path = git_event_paths(_file, _other)
                try:
                    event_type = str(getattr(_event, "value_nick", None) or _event or "")
                except Exception:
                    event_type = ""
                if not should_refresh_for_git_event(
                    self._root_dir, file_path, other_path, event_type
                ):
                    return
                self._arm_git_timer(GIT_REFRESH_DEBOUNCE_MS, self._git_generation)
            except Exception as e:
                logger.debug(f"git change debounce failed: {e!r}")

        def _on_git_changed_fire(self, generation: int) -> bool:
            self._refresh_timer = None
            if generation != self._git_generation:
                return False
            try:
                self.refresh_git_statuses(
                    min_interval_s=getattr(
                        self, "_refresh_interval_s", GIT_REFRESH_MIN_INTERVAL_S
                    )
                )
            except Exception as e:
                logger.debug(f"git auto-refresh failed: {e!r}")
            return False

        def _arm_tree_timer(self, delay_ms: int, generation: int) -> None:
            if GLib is None:
                try:
                    self.refresh_tree()
                except Exception:
                    logger.debug("tree refresh fallback failed", exc_info=True)
                return
            try:
                if self._tree_timer is not None:
                    try:
                        GLib.source_remove(self._tree_timer)
                    except Exception:
                        logger.debug("tree timer remove failed", exc_info=True)
                    self._tree_timer = None
                self._tree_timer = GLib.timeout_add(
                    max(50, int(delay_ms)), self._on_tree_changed_fire, generation
                )
            except Exception as e:
                logger.debug(f"tree change debounce failed: {e!r}")

        def _on_dir_changed(self, _monitor, _file, _other=None, _event=None) -> None:
            try:
                file_path, other_path = git_event_paths(_file, _other)
                if not should_rebuild_tree_for_event(
                    self._root_dir, file_path, other_path
                ):
                    return
                if GLib is None:
                    return
                self._arm_tree_timer(TREE_REFRESH_DEBOUNCE_MS, self._tree_generation)
                try:
                    if should_refresh_for_git_event(
                        self._root_dir, file_path, other_path
                    ):
                        # Working-tree edit: bypass the `.git/` storm gate
                        # (our own status runs never touch these files).
                        self._arm_git_timer(
                            GIT_DIR_DEBOUNCE_MS, self._git_generation, 0.0
                        )
                except Exception:
                    logger.debug("git timer arm failed", exc_info=True)
            except Exception as e:
                logger.debug(f"tree change debounce failed: {e!r}")

        def _on_tree_changed_fire(self, generation: int) -> bool:
            self._tree_timer = None
            if generation != self._tree_generation:
                return False
            try:
                self.refresh_tree()
            except Exception as e:
                logger.debug(f"tree auto-refresh failed: {e!r}")
            return False

        def _cancel_monitors(self) -> None:
            if GLib is not None:
                for attr in ("_refresh_timer", "_tree_timer"):
                    timer = getattr(self, attr, None)
                    if timer is not None:
                        try:
                            GLib.source_remove(timer)
                        except Exception:
                            logger.debug(f"timer remove failed: {attr}", exc_info=True)
                    setattr(self, attr, None)
            else:
                self._refresh_timer = None
                self._tree_timer = None
            self._refresh_interval_s = GIT_REFRESH_MIN_INTERVAL_S
            for monitor in list(getattr(self, "_monitors", []) or []):
                try:
                    monitor.cancel()
                except Exception:
                    logger.debug("monitor cancel failed", exc_info=True)
            self._monitors = []
            self._dir_monitors = {}
            self._git_monitor = None
            self._watched_dirs = set()

        def cleanup(self) -> None:
            try:
                self._git_generation += 1
            except Exception:
                logger.debug("cleanup git generation failed", exc_info=True)
            try:
                self._tree_generation += 1
            except Exception:
                logger.debug("cleanup tree generation failed", exc_info=True)
            try:
                kill = getattr(self, "_kill_git_procs", None)
                if callable(kill):
                    kill()
            except Exception:
                logger.debug("cleanup git kill failed", exc_info=True)
            self._cancel_monitors()

        def _on_row_activated(self, _tree, path, _col) -> None:
            tree_iter = self.store.get_iter(path)
            kind = self.store.get_value(tree_iter, self._col_kind)
            fpath = self.store.get_value(tree_iter, self._col_path)
            if kind == "file":
                if os.path.isfile(fpath):
                    self.emit("open-file", fpath)
                    return
                try:
                    self.refresh_tree()
                except Exception:
                    logger.debug("row refresh failed", exc_info=True)
                return
            if self.tree.row_expanded(path):
                self.tree.collapse_row(path)
            else:
                self.tree.expand_row(path, False)
else:
    ProjectBrowser = None  # type: ignore  # headless

# ---------------------------------------------------------------------------
# Thor-native attach helpers
# ---------------------------------------------------------------------------

def _open_in_thor(window, path: str) -> None:
    """Open file in Thor window from browser signal."""
    if not path or Gio is None:
        return
    try:
        loc = Gio.File.new_for_path(path)
    except Exception:
        return
    try:
        existing = None
        try:
            existing = window.get_tab_from_location(loc)
        except Exception:
            existing = None
        if existing is not None:
            try:
                window.set_active_tab(existing)
            except Exception:
                logger.debug("open tab activate failed", exc_info=True)
        else:
            try:
                window.create_tab_from_location(loc, None, 0, True, True)
            except TypeError:
                try:
                    window.create_tab_from_location(loc, create=True, jump_to=True)
                except Exception:
                    logger.debug("open tab fallback create failed", exc_info=True)
            except Exception:
                logger.debug("open tab create failed", exc_info=True)
    except Exception as e:
        logger.debug(f"open file failed for {path}: {e!r}")

def _choose_root(window, browser) -> None:
    if Gtk is None:
        return
    try:
        dialog = Gtk.FileChooserDialog(
            title="Open Folder",
            transient_for=window,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        try:
            dialog.set_transient_for(window)
            dialog.set_modal(True)
        except Exception:
            logger.debug("folder chooser transient failed", exc_info=True)
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Open", Gtk.ResponseType.ACCEPT,
        )
        try:
            cur = getattr(browser, "_root_dir", None)
            if cur and os.path.isdir(cur):
                dialog.set_current_folder(cur)
        except Exception:
            logger.debug("folder chooser folder set failed", exc_info=True)
    except Exception as e:
        logger.debug(f"folder chooser create failed: {e!r}")
        return
    folder = None
    try:
        resp = dialog.run()
        if resp == Gtk.ResponseType.ACCEPT:
            try:
                folder = dialog.get_filename()
            except Exception:
                try:
                    folder = dialog.get_current_folder()
                except Exception:
                    folder = None
    except Exception as e:
        logger.debug(f"folder chooser failed: {e!r}")
        folder = None
    finally:
        try:
            dialog.destroy()
        except Exception:
            logger.debug("folder chooser destroy failed", exc_info=True)
    if folder and os.path.isdir(folder):
        try:
            if is_unsafe_root(folder):
                logger.debug(f"refusing unsafe root: {folder}")
                return
        except Exception:
            return
        try:
            browser.set_root(os.path.abspath(folder))
        except Exception as e:
            logger.debug(f"set_root failed for {folder}: {e!r}")

def _project_key(window, event, browser) -> bool:
    if Gtk is None or Gdk is None:
        return False
    try:
        mods = event.state & Gtk.accelerator_get_default_mod_mask()
        keyname = Gdk.keyval_name(event.keyval) or ""
        ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(mods & Gdk.ModifierType.MOD1_MASK)
        if ctrl and shift and not alt and keyname.lower() == "o":
            _choose_root(window, browser)
            return True
    except Exception:
        logger.debug("project key check failed", exc_info=True)
    return False

def _consume_pending() -> str | None:
    """Consume pending-root handoff, checking Thor cache locations."""
    candidates: list[str] = []
    try:
        base_env = os.environ.get("XDG_CACHE_HOME")
        if base_env:
            candidates.append(os.path.join(base_env, "thor", "project-mode", PENDING_FILENAME))
        else:
            home_cache = os.path.expanduser("~/.cache")
            candidates.append(os.path.join(home_cache, "thor", "project-mode", PENDING_FILENAME))
        if GLib is not None:
            try:
                glib_cache = GLib.get_user_cache_dir()
                candidates.append(os.path.join(glib_cache, "thor", "project-mode", PENDING_FILENAME))
            except Exception:
                logger.debug("glib cache dir lookup failed", exc_info=True)
    except Exception:
        logger.debug("pending candidates failed", exc_info=True)
    for cand in candidates:
        try:
            val = take_pending_root(cand)
            if val:
                return val
        except Exception:
            logger.debug("pending candidate failed", exc_info=True)
            continue
    try:
        return take_pending_root()
    except Exception:
        return None

def attach(window, initial_folder: str | None = None) -> object | None:
    """Attach ProjectBrowser to a ThorWindow.

    Creates a ProjectBrowser, wires open-file to window.create_tab_from_location,
    adds it to window.get_side_panel(), handles pending-root handoff,
    and binds Ctrl+Shift+O to choose root. Returns the browser or None headless.
    """
    if Gtk is None or window is None:
        return None
    BrowserCls = ProjectBrowser
    if BrowserCls is None:
        return None
    try:
        browser = BrowserCls()
    except Exception as e:
        logger.debug(f"browser create failed: {e!r}")
        return None
    handlers: list = []
    try:
        handlers = list(getattr(window, "_thor_project_handlers", None) or [])
    except Exception:
        logger.debug("project handlers read failed", exc_info=True)
        handlers = []
    try:
        hid = browser.connect("open-file", lambda _w, p: _open_in_thor(window, p))
        handlers.append((browser, hid))
    except Exception:
        logger.debug("browser open-file connect failed", exc_info=True)
    try:
        hid = browser.connect("choose-root", lambda _w: _choose_root(window, browser))
        handlers.append((browser, hid))
    except Exception:
        logger.debug("browser choose-root connect failed", exc_info=True)
    try:
        side = window.get_side_panel()
    except Exception:
        logger.debug("side panel lookup failed", exc_info=True)
        side = None
    if side is not None:
        try:
            try:
                side.add_item(browser, "Project", _pick_icon(_PANEL_ICONS))
            except Exception:
                try:
                    side.add_item(browser, "Project Mode", _pick_icon(_PANEL_ICONS))
                except Exception:
                    side.add_item(browser, "Project", "folder")
        except Exception:
            logger.debug("side panel add failed", exc_info=True)
            try:
                side.add(browser)
            except Exception:
                logger.debug("side panel fallback add failed", exc_info=True)
    try:
        window._thor_project_browser = browser  # type: ignore[attr-defined]
    except Exception:
        logger.debug("project browser store failed", exc_info=True)
    try:
        setattr(window, "_project_browser", browser)
    except Exception:
        logger.debug("project browser alias store failed", exc_info=True)
    try:
        hid = window.connect("key-press-event", lambda w, e: _project_key(w, e, browser))
        handlers.append((window, hid))
    except Exception:
        logger.debug("window keys connect failed", exc_info=True)
    try:
        window._thor_project_handlers = handlers  # type: ignore[attr-defined]
    except Exception:
        logger.debug("project handlers store failed", exc_info=True)
    folder_to_load: str | None = initial_folder
    if folder_to_load is None:
        try:
            pending = _consume_pending()
            if pending and os.path.isdir(pending):
                folder_to_load = pending
        except Exception:
            logger.debug("pending handoff consume failed", exc_info=True)
    if folder_to_load and os.path.isdir(folder_to_load):
        try:
            if not is_unsafe_root(folder_to_load):
                browser.set_root(folder_to_load)
            else:
                logger.debug(f"refusing unsafe root at attach: {folder_to_load}")
        except Exception as e:
            logger.debug(f"browser.set_root({folder_to_load!r}) failed: {e!r}")
    return browser

def get_browser(window) -> object | None:
    """Return the ProjectBrowser attached to window, or None."""
    if window is None:
        return None
    for attr in ("_thor_project_browser", "_project_browser", "browser", "_browser"):
        try:
            val = getattr(window, attr, None)
            if val is not None:
                return val
        except Exception:
            logger.debug("browser attr probe failed", exc_info=True)
            continue
    try:
        side = window.get_side_panel()
        if side is not None:
            try:
                n = side.get_n_items() if hasattr(side, "get_n_items") else side.get_n_pages() if hasattr(side, "get_n_pages") else 0
                for i in range(n):
                    try:
                        nb = getattr(side, "_notebook", None)
                        if nb is not None:
                            w = nb.get_nth_page(i)
                            if hasattr(w, "set_root") and hasattr(w, "store"):
                                return w
                    except Exception:
                        logger.debug("browser page probe failed", exc_info=True)
                        continue
            except Exception:
                logger.debug("browser side-panel probe failed", exc_info=True)
    except Exception:
        logger.debug("browser side-panel scan failed", exc_info=True)
    return None

def detach(window) -> None:
    """Remove ProjectBrowser from window and clean up monitors."""
    if window is None or Gtk is None:
        return
    browser = get_browser(window)
    if browser is None:
        return
    try:
        cleanup = getattr(browser, "cleanup", None)
        if callable(cleanup):
            cleanup()
    except Exception:
        logger.debug("browser cleanup failed", exc_info=True)
    try:
        handlers = list(getattr(window, "_thor_project_handlers", None) or [])
    except Exception:
        logger.debug("project handlers read failed", exc_info=True)
        handlers = []
    for obj, hid in handlers:
        if hid is None:
            continue
        try:
            obj.disconnect(hid)
        except Exception:
            logger.debug("project handler disconnect failed", exc_info=True)
    try:
        window._thor_project_handlers = []  # type: ignore[attr-defined]
    except Exception:
        logger.debug("project handlers clear failed", exc_info=True)
    try:
        side = window.get_side_panel()
        if side is not None:
            try:
                side.remove_item(browser)
            except Exception:
                try:
                    side.remove(browser)
                except Exception:
                    logger.debug("side panel remove failed", exc_info=True)
    except Exception:
        logger.debug("side panel lookup failed", exc_info=True)
    try:
        browser.destroy()
    except Exception:
        logger.debug("browser destroy failed", exc_info=True)
    for attr in ("_thor_project_browser", "_project_browser"):
        try:
            if hasattr(window, attr):
                delattr(window, attr)
        except Exception:
            try:
                setattr(window, attr, None)
            except Exception:
                logger.debug(f"project attr clear failed: {attr}", exc_info=True)

# soft alias for tests that import gitstatus helpers from project
try:
    from . import gitstatus  # noqa: F401
    # Compat: some callers import get_git_status (singular)
    try:
        if not hasattr(gitstatus, "get_git_status") and hasattr(gitstatus, "get_git_statuses"):
            gitstatus.get_git_status = gitstatus.get_git_statuses  # type: ignore[attr-defined]
    except Exception:
        logger.debug("git status alias failed", exc_info=True)
except Exception:
    gitstatus = None  # type: ignore
