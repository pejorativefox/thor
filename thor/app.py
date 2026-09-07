# -*- coding: utf-8 -*-
"""ThorApplication — Gtk.Application entry point."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkSource", "4")
    from gi.repository import Gio, Gtk, GLib  # type: ignore
except Exception:  # headless import for tests
    Gio = Gtk = GLib = None  # type: ignore

try:
    from .window import ThorWindow
except Exception:  # headless
    ThorWindow = None  # type: ignore


APP_ID = "dev.thor.Editor"


def _split_location_arg(arg: str) -> tuple[str, int | None, int | None]:
    """Return (path_without_line, line_or_None, col_or_None) for file:line[:col] / file(line[,col]) forms."""
    m = re.match(r"^(.*?)\((\d+)(?:[,:](\d+))?\)\s*$", arg)
    if m:
        try:
            line = max(1, int(m.group(2)))
            col = max(1, int(m.group(3))) if m.group(3) else None
            return m.group(1).strip(), line, col
        except (TypeError, ValueError):
            return arg, None, None
    m = re.match(r"^(.*?):(\d+)(?::(\d+))?\s*$", arg)
    if m:
        cand = m.group(1).strip()
        if cand:
            # Treat as line suffix when it looks like a path; avoids splitting
            # option-like strings while still handling `file.cs:10`, `a/b:10:5`.
            looks_like_path = os.path.exists(cand) or os.path.isfile(cand) or "." in os.path.basename(cand) or "/" in cand
            # Accept either obvious path shape or any non-option cand — covers
            # new files that don't exist yet.
            if looks_like_path or not cand.startswith("-"):
                try:
                    line = max(1, int(m.group(2)))
                    col = max(1, int(m.group(3))) if m.group(3) else None
                    return cand, line, col
                except (TypeError, ValueError):
                    pass
    return arg, None, None


def _resolve_initial_target(args: list[str], cwd: str | None = None) -> tuple[str | None, list[str]]:
    """First non-option arg is file/folder target; rest are extra files.

    Returns (folder, files). For callers needing line numbers use
    _resolve_initial_target_with_lines().
    """
    folder, files, _lines = _resolve_initial_target_with_lines(args, cwd=cwd)
    return folder, files


def _resolve_initial_target_with_lines(
    args: list[str], cwd: str | None = None
) -> tuple[str | None, list[str], dict[str, tuple[int, int | None]]]:
    """Like _resolve_initial_target but also returns {abspath: (line, col)} for +N[:M] / file:line[:col]."""
    folder: str | None = None
    files: list[str] = []
    file_locs: dict[str, tuple[int, int | None]] = {}
    pending_loc: tuple[int, int | None] | None = None
    for a in args[1:]:
        if a in ("--help", "-h", "--version", "-v", "--new-window", "-n"):
            continue
        if a.startswith("+"):
            parts = a[1:].split(":")
            if parts[0].isdigit():
                try:
                    line = max(1, int(parts[0]))
                    col = max(1, int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else None
                    pending_loc = (line, col)
                except (TypeError, ValueError):
                    pending_loc = None
                continue
        if a.startswith("-"):
            continue
        path_part, line_from_suffix, col_from_suffix = _split_location_arg(a)
        if line_from_suffix is not None:
            loc = (line_from_suffix, col_from_suffix)
        else:
            loc = pending_loc
        pending_loc = None

        if cwd and not os.path.isabs(os.path.expanduser(path_part)):
            p = os.path.abspath(os.path.join(cwd, os.path.expanduser(path_part)))
        else:
            p = os.path.abspath(os.path.expanduser(path_part))

        if loc is not None:
            files.append(p)
            file_locs[p] = loc
        elif os.path.isdir(p) and folder is None:
            folder = p
        elif os.path.isfile(p) or os.path.exists(p):
            files.append(p)
        elif p.endswith(("/", "\\")):
            if folder is None:
                folder = p
        elif os.path.splitext(p)[1]:
            files.append(p)
        elif folder is None:
            folder = p
        else:
            files.append(p)
    if folder is None and not files:
        # default to cwd (like thor-code .)
        folder = cwd or os.getcwd()
    return folder, files, file_locs


def _argv_has_path_arg(args: list[str]) -> bool:
    """True when argv carries at least one file/folder target arg.

    Skips help/version/new-window flags, other `-x` options, and lone
    `+line` markers. A defaulted cwd folder (bare `thor`) yields False;
    an explicit `thor .` yields True.
    """
    for a in args[1:]:
        if a in ("--help", "-h", "--version", "-v", "--new-window", "-n"):
            continue
        if a.startswith("-"):
            continue
        if a.startswith("+"):
            parts = a[1:].split(":")
            if parts and parts[0] and parts[0].isdigit():
                continue
        return True
    return False


def _decide_window_action(
    *,
    has_window: bool,
    new_window: bool,
    folder_explicit: bool,
    has_files: bool,
) -> str:
    """Deprecated routing helper (kept for backward compatibility).

    No longer consulted: each window is its own process (NON_UNIQUE), so
    there is no in-process reuse/present. Explicit folders are gated by
    the per-root lock in main(); file-only launches always spawn
    isolated processes.
    """
    if not has_window or new_window:
        return "new"
    if folder_explicit:
        return "new"
    if has_files:
        return "reuse"
    return "present"


if Gtk is not None:
    class ThorApplication(Gtk.Application):  # type: ignore[misc]
        __gtype_name__ = "ThorApplication"

        def __init__(self) -> None:
            # One process per window: NON_UNIQUE so every CLI spawns its
            # own process instead of merging into a running primary via
            # DBus. Each process owns exactly one ThorWindow.
            super().__init__(
                application_id=APP_ID,
                flags=(  # type: ignore[union-attr]
                    Gio.ApplicationFlags.HANDLES_OPEN
                    | Gio.ApplicationFlags.HANDLES_COMMAND_LINE
                    | Gio.ApplicationFlags.NON_UNIQUE
                ),
            )
            self._pending_folder: str | None = None
            self._pending_folder_explicit: bool = False
            self._pending_files: list[str] = []
            self._pending_file_lines: dict[str, int] = {}
            self._pending_new_window: bool = False
            # Per-root lock held for the process lifetime (set by main()).
            self._root_lock = None
            # Per-root IPC owner server (focus + file forward). Started once
            # the owned root is known; stopped on shutdown.
            self._ipc_server = None
            # CLI option passthrough (best-effort; argv parsing doesn't depend on it)
            try:
                self.add_main_option("new-window", ord("n"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open in new window", None)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("add_main_option failed", exc_info=True)

        def do_startup(self) -> None:  # type: ignore[override]
            Gtk.Application.do_startup(self)
            # XFCE: don't use Gio app-menu/headerbar — it creates an extra MenuBar
            # child that fights the traditional window menubar. Window menubar
            # (File/View/Help) already exposes these actions. Keep actions for
            # accelerators, but skip set_app_menu on XFCE/X11.
            is_xfce = "XFCE" in (os.environ.get("XDG_CURRENT_DESKTOP", "") + os.environ.get("DESKTOP_SESSION","")).upper()
            if not is_xfce:
                try:
                    menu = Gio.Menu()
                    menu.append("New Window", "app.new_window")
                    menu.append("Open Folder…", "app.open_folder")
                    menu.append("Open File…", "app.open_file")
                    menu.append("About Thor", "app.about")
                    menu.append("Quit", "app.quit")
                    self.set_app_menu(menu)  # GTK3 (GNOME)
                except Exception:
                    logger.debug("set_app_menu failed", exc_info=True)
            for name, cb in (
                ("new_window", lambda *_: self._new_window()),
                ("open_folder", lambda *_: self._prompt_open_folder()),
                ("open_file", lambda *_: self._prompt_open_file()),
                ("about", lambda *_: self._show_about()),
                # Unified exit path: closing the active window runs the
                # same delete-event prompt + saves as the WM close button.
                # One process owns one window, so this quits the process.
                ("quit", lambda *_: self.request_close_active_window()),
            ):
                try:
                    act = Gio.SimpleAction.new(name, None)
                    act.connect("activate", cb)
                    self.add_action(act)
                except Exception:
                    logger.debug("add_action %s failed", name, exc_info=True)
            # Global accelerators (fallback for window-level handling)
            try:
                self.set_accels_for_action("app.new_window", ["<Primary>n"])
                self.set_accels_for_action("app.open_folder", ["<Primary><Shift>o"])
                self.set_accels_for_action("app.quit", ["<Primary>q"])
            except Exception:
                logger.debug("set_accels failed", exc_info=True)

        def request_close_active_window(self) -> None:
            """Close the active window via the WM-close path (with prompt).

            Runs the same delete-event handler as the window-manager `X`
            button, so Ctrl+Q and WM close can never diverge. One process
            owns one window, so closing it quits the process.
            """
            try:
                win = self.get_active_window()
            except Exception:
                win = None
            if win is None:
                return
            try:
                win.close()
            except Exception:
                logger.debug("request_close_active_window failed", exc_info=True)

        def _release_root_lock(self) -> None:
            lock = getattr(self, "_root_lock", None)
            self._root_lock = None
            if lock is None:
                return
            try:
                lock.release()
            except Exception:
                logger.debug("root lock release failed", exc_info=True)
            self._stop_ipc_server()

        def do_window_removed(self, window: Gtk.Window) -> None:  # type: ignore[override]
            # Single-window process: save this window, then let GTK tear
            # down. No fan-out over get_windows() — nothing is shared.
            try:
                if hasattr(window, "_save_panel_state"):
                    window._save_panel_state()
                if hasattr(window, "_save_session_now"):
                    window._save_session_now()
            except Exception:
                pass
            try:
                Gtk.Application.do_window_removed(self, window)
            except Exception:
                pass

        def do_shutdown(self) -> None:  # type: ignore[override]
            # Safety net for the single owned window (destroy already saves;
            # this covers quit paths that skip destroy).
            try:
                for win in list(self.get_windows()):
                    try:
                        if hasattr(win, "_save_panel_state"):
                            win._save_panel_state()
                        if hasattr(win, "_save_session_now"):
                            win._save_session_now()
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                self._release_root_lock()
            except Exception:
                pass
            try:
                self._stop_ipc_server()
            except Exception:
                pass
            try:
                Gtk.Application.do_shutdown(self)
            except Exception:
                pass

        @staticmethod
        def _spawn_argv(extra_args: list[str]) -> list[str]:
            """Argv for a detached child editor process."""
            try:
                pkg_dir = os.path.dirname(os.path.abspath(__file__))
                repo_cli = os.path.join(os.path.dirname(pkg_dir), "thor-cli")
                if os.path.isfile(repo_cli):
                    return [sys.executable, repo_cli, *extra_args]
            except Exception:
                logger.debug("spawn argv repo probe failed", exc_info=True)
            return ["thor", *extra_args]

        def _spawn_new_process(self, extra_args: list[str]) -> None:
            """Launch a fully isolated child editor process (no sharing)."""
            argv = self._spawn_argv(list(extra_args or []))
            try:
                subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception as e:
                logger.warning("spawn new process failed: %r", e)

        # -- Per-root IPC owner side (focus + file forward) -----------------
        def _owned_root(self) -> str | None:
            try:
                lock = getattr(self, "_root_lock", None)
                if lock is not None and getattr(lock, "root", None):
                    return os.path.abspath(lock.root)
            except Exception:
                pass
            try:
                win = self.get_active_window()
            except Exception:
                win = None
            if win is not None:
                try:
                    from .project import session as _session

                    return _session.get_window_root(win)
                except Exception:
                    pass
                try:
                    initial = getattr(win, "_initial_folder", None)
                    if initial and os.path.isdir(initial):
                        return os.path.abspath(initial)
                except Exception:
                    pass
            return None

        def _start_ipc_server(self, root: str | None) -> None:
            if not root or not os.path.isdir(root):
                return
            if getattr(self, "_ipc_server", None) is not None:
                return
            try:
                from .ipc import IpcServer

                server = IpcServer(
                    root,
                    on_present=self._handle_ipc_present,
                    on_open=self._handle_ipc_open,
                )
                if server.start():
                    self._ipc_server = server
            except Exception:
                logger.debug("ipc server start failed", exc_info=True)

        def _stop_ipc_server(self) -> None:
            server = getattr(self, "_ipc_server", None)
            self._ipc_server = None
            if server is None:
                return
            try:
                server.stop()
            except Exception:
                logger.debug("ipc server stop failed", exc_info=True)

        def _handle_ipc_present(self) -> None:
            try:
                if GLib is not None:
                    GLib.idle_add(self._do_present)
                else:
                    self._do_present()
            except Exception:
                logger.debug("ipc present handle failed", exc_info=True)

        def _do_present(self) -> bool:
            try:
                win = self.get_active_window()
                if win is not None:
                    win.present()
                    if hasattr(win, "focus_active_editor"):
                        try:
                            win.focus_active_editor()
                        except Exception:
                            pass
            except Exception:
                logger.debug("ipc present failed", exc_info=True)
            return False

        def _handle_ipc_open(self, files: list[dict]) -> None:
            try:
                if GLib is not None:
                    GLib.idle_add(self._do_ipc_open, list(files or []))
                else:
                    self._do_ipc_open(list(files or []))
            except Exception:
                logger.debug("ipc open handle failed", exc_info=True)

        def _do_ipc_open(self, files: list[dict]) -> bool:
            try:
                win = self.get_active_window()
                if win is None:
                    return False
                for entry in files or []:
                    try:
                        if not isinstance(entry, dict):
                            continue
                        path = entry.get("path")
                        if not path or not isinstance(path, str):
                            continue
                        line = entry.get("line")
                        col = entry.get("col")
                        line_pos = (int(line) - 1) if line and int(line) > 0 else -1
                        col_pos = (int(col) - 1) if col and int(col) > 0 else -1
                        win.open_file(path, line_pos=line_pos, col_pos=col_pos, jump_to=True)
                    except Exception:
                        logger.debug("ipc open entry failed", exc_info=True)
                try:
                    win.present()
                    if hasattr(win, "focus_active_editor"):
                        win.focus_active_editor()
                except Exception:
                    pass
            except Exception:
                logger.debug("ipc open failed", exc_info=True)
            return False

        def _open_files_into_window(self, win, files: list[str], file_lines: dict | None = None) -> None:
            """Open a file list into the single owned window."""
            file_lines = file_lines or {}
            for fp in files or []:
                try:
                    loc = file_lines.get(fp)
                    if isinstance(loc, tuple):
                        line, col = loc
                    elif isinstance(loc, int):
                        line, col = loc, None
                    else:
                        line, col = None, None
                    line_pos = (line - 1) if line and line > 0 else -1
                    col_pos = (col - 1) if col and col > 0 else -1
                    win.open_file(fp, line_pos=line_pos, col_pos=col_pos, jump_to=True)
                except Exception:
                    logger.debug("_open_files_into_window: open %s failed", fp, exc_info=True)
            try:
                if win._notebook.get_n_pages() == 0:  # type: ignore[attr-defined]
                    win.create_tab(jump_to=True)
            except Exception:
                logger.debug("_open_files_into_window: empty notebook guard failed", exc_info=True)

        def do_activate(self) -> None:  # type: ignore[override]
            # One process owns one window: snapshot pending state, create
            # the single window on first activation, present otherwise.
            pending_folder = getattr(self, "_pending_folder", None)
            pending_files = list(getattr(self, "_pending_files", None) or [])
            pending_lines = dict(getattr(self, "_pending_file_lines", None) or {})
            self._pending_new_window = False
            self._pending_folder = None
            self._pending_folder_explicit = False
            self._pending_files = []
            self._pending_file_lines = {}
            try:
                existing = self.get_active_window()
            except Exception:
                existing = None
            if existing is not None:
                try:
                    self._open_files_into_window(existing, pending_files, pending_lines)
                    existing.present()
                    if GLib is not None and hasattr(existing, "focus_active_editor"):
                        GLib.idle_add(existing.focus_active_editor)
                except Exception:
                    logger.debug("do_activate: present failed", exc_info=True)
                return
            try:
                win = self._create_window(pending_folder, pending_files, file_lines=pending_lines)
            except Exception:
                logger.exception("do_activate: create window failed")
                return
            try:
                win.present()
                if GLib is not None and hasattr(win, "focus_active_editor"):
                    GLib.idle_add(win.focus_active_editor)
            except Exception:
                logger.debug("do_activate: present failed", exc_info=True)

        def do_open(self, files, hint, data=None):  # type: ignore[override]
            # Single-window process: Gio.Files from this process's own
            # command line (NON_UNIQUE => no DBus forwarding from others).
            # Remote URIs (get_path() None) open via the Gio.File directly.
            self._pending_new_window = False
            self._pending_folder = None
            self._pending_folder_explicit = False
            self._pending_files = []
            self._pending_file_lines = {}
            folder = None
            file_paths: list[str] = []
            remote_files: list = []
            for f in files:
                try:
                    p = f.get_path()
                except Exception:
                    logger.debug("do_open: get_path failed", exc_info=True)
                    p = None
                if p:
                    try:
                        if os.path.isdir(p):
                            folder = p
                        else:
                            # Includes non-existent paths -> created on open
                            file_paths.append(p)
                    except Exception:
                        logger.debug("do_open: path check failed for %s", p, exc_info=True)
                        file_paths.append(p)
                else:
                    remote_files.append(f)
            try:
                existing = self.get_active_window()
            except Exception:
                existing = None
            if existing is not None:
                for fp in file_paths:
                    try:
                        loc = Gio.File.new_for_path(fp)  # type: ignore[union-attr]
                        existing.create_tab_from_location(loc, create=True, jump_to=True)
                    except Exception:
                        logger.debug("do_open: open %s failed", fp, exc_info=True)
                for f in remote_files:
                    try:
                        existing.create_tab_from_location(f, create=True, jump_to=True)
                    except Exception:
                        logger.debug("do_open: remote open failed", exc_info=True)
                try:
                    existing.present()
                    if GLib is not None and hasattr(existing, "focus_active_editor"):
                        GLib.idle_add(existing.focus_active_editor)
                except Exception:
                    logger.debug("do_open: present failed", exc_info=True)
                return
            try:
                win = self._create_window(folder, file_paths)
            except Exception:
                logger.exception("do_open: create window failed")
                return
            for f in remote_files:
                try:
                    win.create_tab_from_location(f, create=True, jump_to=True)
                except Exception:
                    logger.debug("do_open: remote open failed", exc_info=True)
            try:
                if win._notebook.get_n_pages() == 0:  # type: ignore[attr-defined]
                    win.create_tab(jump_to=True)
            except Exception:
                logger.debug("do_open: empty notebook guard failed", exc_info=True)
            try:
                win.present()
                if GLib is not None and hasattr(win, "focus_active_editor"):
                    GLib.idle_add(win.focus_active_editor)
            except Exception:
                logger.debug("do_open: present failed", exc_info=True)

        def do_command_line(self, cmd):  # type: ignore[override]
            # Gio.ApplicationCommandLine wraps argv; unwrap if needed.
            # argv is authoritative for --new-window (add_main_option may
            # have been swallowed at __init__); the options dict is best-effort.
            try:
                # cmd is Gio.ApplicationCommandLine
                argv = list(cmd.get_arguments() or [])
                client_cwd = None
                try:
                    if hasattr(cmd, "get_cwd"):
                        client_cwd = cmd.get_cwd()
                except Exception:
                    client_cwd = None
                # argv[0] is program name
                folder, files, file_lines = _resolve_initial_target_with_lines(argv, cwd=client_cwd)
                folder_explicit = folder is not None and _argv_has_path_arg(argv)
                new_window = "--new-window" in (argv or []) or "-n" in (argv or [])
                try:
                    opts = cmd.get_options_dict()  # type: ignore[attr-defined]
                    if opts is not None and opts.contains("new-window"):
                        new_window = True
                except Exception:
                    logger.debug("do_command_line: options dict unavailable", exc_info=True)
                self._pending_new_window = new_window
            except Exception:
                logger.debug("do_command_line: argv parse failed", exc_info=True)
                folder, files, file_lines = _resolve_initial_target_with_lines(list(sys.argv))
                folder_explicit = folder is not None and _argv_has_path_arg(list(sys.argv))
                self._pending_new_window = "--new-window" in sys.argv or "-n" in sys.argv
            self._pending_folder = folder
            self._pending_folder_explicit = folder_explicit
            self._pending_files = files
            self._pending_file_lines = file_lines
            try:
                self.activate()
            except Exception:
                logger.exception("do_command_line: activate failed")
            try:
                return 0
            finally:
                try:
                    cmd.set_exit_status(0)  # type: ignore[attr-defined]
                except Exception:
                    logger.debug("do_command_line: set_exit_status failed", exc_info=True)

        def _create_window(self, folder: str | None, files: list[str], file_lines: dict | None = None) -> ThorWindow:
            if ThorWindow is None:
                raise RuntimeError("ThorWindow unavailable (headless import)")
            try:
                win = ThorWindow(self, initial_folder=folder)
            except Exception:
                logger.exception("_create_window: ThorWindow construction failed")
                raise
            file_lines = file_lines or {}
            # Bake built-in panels/features first so the project root
            # resolves before restore.
            try:
                from .host import attach_builtin_features

                attach_builtin_features(win, initial_folder=folder)
            except Exception as e:
                logger.exception("feature attach failed: %r", e)
            # Restore per-project session, merging explicit files on top.
            # Single-window process: no flush of other windows, nothing is
            # shared in memory.
            try:
                from .project import session as _session

                root = _session.get_window_root(win)
                if root is None and folder and os.path.isdir(folder):
                    root = os.path.abspath(folder)
                restored = _session.restore_into_window(
                    win, root, extra_files=files, extra_lines=file_lines
                ) if root else False
            except Exception:
                logger.debug("_create_window: session restore failed", exc_info=True)
                restored = False
            if not restored:
                # No session (or restore skipped): open requested files.
                for fp in files:
                    try:
                        loc = file_lines.get(fp)
                        if isinstance(loc, tuple):
                            line, col = loc
                        elif isinstance(loc, int):
                            line, col = loc, None
                        else:
                            line, col = None, None
                        line_pos = (line - 1) if line and line > 0 else -1
                        col_pos = (col - 1) if col and col > 0 else -1
                        win.open_file(fp, line_pos=line_pos, col_pos=col_pos, jump_to=True)
                    except Exception as e:
                        logger.warning("open %s failed: %r", fp, e)
                try:
                    if win._notebook.get_n_pages() == 0:  # type: ignore[attr-defined]
                        win.create_tab(jump_to=True)
                except Exception:
                    logger.debug("_create_window: empty notebook guard failed", exc_info=True)
            # Own this root for focus + file forwarding from second launches.
            try:
                from .project import session as _session2

                owned = _session2.get_window_root(win)
                if owned is None and folder and os.path.isdir(folder):
                    owned = os.path.abspath(folder)
                lock = getattr(self, "_root_lock", None)
                if lock is not None and getattr(lock, "root", None):
                    owned = os.path.abspath(lock.root)
                self._start_ipc_server(owned)
            except Exception:
                logger.debug("_create_window: ipc server start failed", exc_info=True)
            return win

        def _new_window(self) -> None:
            # Isolated process: a second window is a second process, never
            # a second ThorWindow in this one. --new-window bypasses the
            # same-root lock gate in main(); forward the current root so
            # the child starts in the same folder instead of its cwd.
            try:
                root = self._owned_root()
                if root and os.path.isdir(root):
                    self._spawn_new_process(["--new-window", root])
                else:
                    self._spawn_new_process(["--new-window"])
            except Exception:
                logger.exception("_new_window: spawn failed")
                return

        def _prompt_open_folder(self) -> None:
            win = self.get_active_window()
            dlg = None
            try:
                dlg = Gtk.FileChooserDialog(  # type: ignore[attr-defined]
                    title="Open Folder",
                    transient_for=win,
                    action=Gtk.FileChooserAction.SELECT_FOLDER,
                )
                try:
                    dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
                except Exception:
                    # STOCK_* gone in newer GTK: dialog is still usable.
                    logger.debug("open folder dialog buttons failed", exc_info=True)
                if dlg.run() == Gtk.ResponseType.OK:
                    try:
                        folder = dlg.get_filename()
                    except Exception:
                        logger.debug("open folder dialog filename failed", exc_info=True)
                        folder = None
                    if folder and os.path.isdir(folder):
                        # Already open elsewhere: focus it instead of a
                        # silent detached spawn that would exit 2 to DEVNULL.
                        try:
                            from . import ipc as _ipc

                            if _ipc.is_root_live(folder):
                                if _ipc.notify_existing(folder):
                                    self._show_already_open_notice(folder, win)
                                    return
                        except Exception:
                            logger.debug("open folder live check failed", exc_info=True)
                        # Isolated process: hand the folder to a fresh
                        # process. That child enforces the same-root lock
                        # gate in main() and focuses the owner on duplicates.
                        try:
                            self._spawn_new_process([folder])
                        except Exception:
                            logger.exception("open folder: spawn failed")
            except Exception as e:
                logger.warning("open folder dialog failed: %r", e)
            finally:
                if dlg is not None:
                    try:
                        dlg.destroy()
                    except Exception:
                        logger.debug("open folder dialog destroy failed", exc_info=True)
        # Backward compat alias for typo
        _promp_open_folder = _prompt_open_folder

        def _show_already_open_notice(self, folder: str, win=None) -> None:
            """Visible feedback that a folder is already open (focused)."""
            try:
                parent = win if win is not None else self.get_active_window()
                dlg = Gtk.MessageDialog(  # type: ignore[attr-defined]
                    transient_for=parent,
                    flags=0,
                    message_type=Gtk.MessageType.INFO,  # type: ignore[attr-defined]
                    buttons=Gtk.ButtonsType.OK,  # type: ignore[attr-defined]
                    text=f"Already open — focused existing window:\n{folder}",
                )
                try:
                    dlg.run()
                finally:
                    try:
                        dlg.destroy()
                    except Exception:
                        pass
            except Exception:
                logger.info("folder already open (focused): %s", folder)

        def _prompt_open_file(self) -> None:
            win = self.get_active_window()
            if ThorWindow is None or not isinstance(win, ThorWindow):
                return
            dlg = None
            try:
                dlg = Gtk.FileChooserDialog(  # type: ignore[attr-defined]
                    title="Open File",
                    transient_for=win,
                    action=Gtk.FileChooserAction.OPEN,
                )
                try:
                    dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
                except Exception:
                    logger.debug("open file dialog buttons failed", exc_info=True)
                try:
                    dlg.set_select_multiple(True)
                except Exception:
                    logger.debug("open file dialog multi-select failed", exc_info=True)
                if dlg.run() == Gtk.ResponseType.OK:
                    try:
                        files = dlg.get_filenames() or []
                    except Exception:
                        logger.debug("open file dialog filenames failed", exc_info=True)
                        files = []
                    for fp in files:
                        try:
                            loc = Gio.File.new_for_path(fp)
                            win.create_tab_from_location(loc, create=True, jump_to=True)
                        except Exception:
                            logger.debug("open file %s failed", fp, exc_info=True)
            except Exception as e:
                logger.warning("open file dialog failed: %r", e)
            finally:
                if dlg is not None:
                    try:
                        dlg.destroy()
                    except Exception:
                        logger.debug("open file dialog destroy failed", exc_info=True)

        _promp_open_file = _prompt_open_file

        def _show_about(self) -> None:
            win = self.get_active_window()
            dlg = None
            try:
                from . import __version__

                dlg = Gtk.AboutDialog(transient_for=win, modal=True)  # type: ignore[attr-defined]
                dlg.set_program_name("Thor")
                dlg.set_version(__version__)
                dlg.set_comments("GtkSourceView editor — features baked in.\nNo Peas. Just Thor.")
                dlg.set_website("https://github.com/thor-editor/thor")
                dlg.set_license_type(Gtk.License.MIT_X11)
                dlg.run()
            except Exception:
                logger.debug("about dialog failed", exc_info=True)
            finally:
                if dlg is not None:
                    try:
                        dlg.destroy()
                    except Exception:
                        logger.debug("about dialog destroy failed", exc_info=True)

else:

    class ThorApplication:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass

        def run(self, argv=None):
            logger.error("Thor requires GTK3 + GtkSourceView4 (DISPLAY).")
            return 1


def main(argv: list[str] | None = None) -> int:
    # Re-evaluate THOR_DEBUG at startup (env may be set after import).
    try:
        from .logging_config import setup_logging

        setup_logging()
    except Exception:
        logger.debug("setup_logging failed", exc_info=True)
    if argv is None:
        argv = sys.argv
    if any(a in ("--help", "-h") for a in argv[1:]):
        print("Usage: thor [folder|file ...]  — GtkSourceView editor with built-in project browser, terminal, git gutter, C# support.")
        print("       thor-code [folder]      — open folder (code . equivalent)")
        print("       thor-open file:line     — open at location")
        return 0
    if any(a in ("--version", "-v") for a in argv[1:]):
        from . import __version__

        print(f"Thor {__version__}")
        return 0
    # Require display for real launch
    if Gtk is None or Gio is None:
        logger.error("Thor: GTK not available (headless).")
        return 1
    # One process per window, one window per folder: a second process for
    # an already-live root focuses the owner via per-root IPC socket and
    # exits 0. --new-window bypasses the gate (intentional duplicate).
    # Bare `thor` (defaulted cwd) and file-only launches also gate via
    # the resolved root / enclosing live root so `thor-open file` inside
    # a live project forwards instead of duplicating it.
    root_lock = None
    try:
        folder, _files, _file_lines = _resolve_initial_target_with_lines(argv, cwd=os.getcwd())
        forced = "--new-window" in argv or "-n" in argv
        if not forced:
            from .lock import try_acquire_root_lock

            if folder and os.path.isdir(folder):
                lock, owner_pid = try_acquire_root_lock(folder)
                if lock is None:
                    try:
                        from . import ipc as _gate_ipc

                        if _gate_ipc.notify_existing(folder, _files, _file_lines):
                            print(f"thor: {folder} is already open; focused existing window", file=sys.stderr)
                            return 0
                    except Exception:
                        logger.debug("gate ipc notify failed", exc_info=True)
                    if owner_pid is not None:
                        print(
                            f"thor: {folder} is already open (pid {owner_pid}); focused existing window if available",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"thor: {folder} is already open; focused existing window if available",
                            file=sys.stderr,
                        )
                    return 2
                root_lock = lock
            elif _files:
                try:
                    from . import ipc as _file_ipc

                    remaining: list[str] = []
                    for fp in _files:
                        live = _file_ipc.find_live_root_for_path(fp)
                        if live and _file_ipc.notify_existing(live, [fp], _file_lines):
                            continue
                        remaining.append(fp)
                    if remaining != _files and not remaining:
                        print("thor: forwarded to existing window", file=sys.stderr)
                        return 0
                except Exception:
                    logger.debug("file forward gate failed (fail-open)", exc_info=True)
    except SystemExit:
        raise
    except Exception:
        logger.debug("root lock gate failed (fail-open)", exc_info=True)
        root_lock = None
    app = ThorApplication()
    try:
        app._root_lock = root_lock
    except Exception:
        pass
    # Gtk.Application.run expects argv
    try:
        return app.run(argv)  # type: ignore[attr-defined]
    except Exception as e:
        logger.exception("run failed: %r", e)
        return 1
    finally:
        try:
            if root_lock is not None:
                root_lock.release()
        except Exception:
            pass
