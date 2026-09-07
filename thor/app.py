# -*- coding: utf-8 -*-
"""ThorApplication — Gtk.Application entry point."""

from __future__ import annotations

import logging
import os
import re
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
    """Route an open request: "new" | "reuse" | "present".

    Explicit folders always open a new window (that root's session is
    restored there); files-only requests reuse the active window (the
    thor-open single-file case included); bare invocations just present
    the running window.
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
            super().__init__(
                application_id=APP_ID,
                flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.HANDLES_COMMAND_LINE,  # type: ignore[union-attr]
            )
            self._pending_folder: str | None = None
            self._pending_folder_explicit: bool = False
            self._pending_files: list[str] = []
            self._pending_file_lines: dict[str, int] = {}
            self._pending_new_window: bool = False
            # Windows whose panel state was already saved (window_removed vs
            # shutdown both save; saving twice is redundant I/O).
            self._saved_panel_windows: set[int] = set()
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
                ("quit", lambda *_: self.quit()),
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

        def _save_panels_once(self, window) -> None:
            try:
                key = id(window)
                saved = getattr(self, "_saved_panel_windows", None)
                if saved is not None:
                    if key in saved:
                        return
                    saved.add(key)
                if hasattr(window, "_save_panel_state"):
                    window._save_panel_state()
                if hasattr(window, "_save_session_now"):
                    window._save_session_now()
            except Exception:
                pass

        def do_window_removed(self, window: Gtk.Window) -> None:  # type: ignore[override]
            self._save_panels_once(window)
            try:
                Gtk.Application.do_window_removed(self, window)
            except Exception:
                pass

        def do_shutdown(self) -> None:  # type: ignore[override]
            try:
                for win in self.get_windows():
                    self._save_panels_once(win)
            except Exception:
                pass
            try:
                Gtk.Application.do_shutdown(self)
            except Exception:
                pass
        def do_activate(self) -> None:  # type: ignore[override]
            # Snapshot-then-clear: consume pending state up front so a
            # re-entrant activate (e.g. open while opening) can't double-open.
            new_window = bool(getattr(self, "_pending_new_window", False))
            pending_folder = getattr(self, "_pending_folder", None)
            pending_folder_explicit = bool(getattr(self, "_pending_folder_explicit", False))
            pending_files = list(getattr(self, "_pending_files", None) or [])
            pending_lines = dict(getattr(self, "_pending_file_lines", None) or {})
            self._pending_new_window = False
            self._pending_folder = None
            self._pending_folder_explicit = False
            self._pending_files = []
            self._pending_file_lines = {}
            win = self.get_active_window()
            action = _decide_window_action(
                has_window=win is not None,
                new_window=new_window,
                folder_explicit=pending_folder_explicit and pending_folder is not None,
                has_files=bool(pending_files),
            )
            if action == "new":
                try:
                    win = self._create_window(pending_folder, pending_files, file_lines=pending_lines)
                except Exception:
                    logger.exception("do_activate: create window failed")
                    return
            elif action == "reuse":
                # Existing window: open pending files there (e.g. thor-open file:line)
                for fp in pending_files:
                    try:
                        loc = pending_lines.get(fp)
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
                        logger.debug("do_activate: open %s failed", fp, exc_info=True)
                try:
                    if win._notebook.get_n_pages() == 0:  # type: ignore[attr-defined]
                        win.create_tab(jump_to=True)
                except Exception:
                    logger.debug("do_activate: empty notebook guard failed", exc_info=True)
            if win is None:
                return
            try:
                win.present()
                if GLib is not None and hasattr(win, "focus_active_editor"):
                    GLib.idle_add(win.focus_active_editor)
            except Exception:
                logger.debug("do_activate: present failed", exc_info=True)

        def do_open(self, files, hint, data=None):  # type: ignore[override]
            # Gio.File[] from DBus open (e.g. thor file.cs). Non-existent
            # paths route to create=True; remote URIs (get_path() None)
            # open via the Gio.File directly.
            # Consume all command-line pending state: with HANDLES_OPEN the
            # files arrive here, so stale pendings must not leak into the
            # next do_activate.
            new_window = bool(getattr(self, "_pending_new_window", False))
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
            win = self.get_active_window()
            # A directory among the opened files is always an explicit folder
            # action: it gets a new window (with that root's session). Live
            # windows are never retargeted — files open as tabs instead.
            action = _decide_window_action(
                has_window=win is not None,
                new_window=new_window,
                folder_explicit=folder is not None,
                has_files=bool(file_paths or remote_files),
            )
            if action == "new":
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
            else:
                for fp in file_paths:
                    try:
                        loc = Gio.File.new_for_path(fp)  # type: ignore[union-attr]
                        win.create_tab_from_location(loc, create=True, jump_to=True)
                    except Exception:
                        logger.debug("do_open: open %s failed", fp, exc_info=True)
                for f in remote_files:
                    try:
                        win.create_tab_from_location(f, create=True, jump_to=True)
                    except Exception:
                        logger.debug("do_open: remote open failed", exc_info=True)
            if win is None:
                return
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
            # Bake built-in panels/plugins first so the project root (including
            # the thor-code pending-root handoff) resolves before restore.
            try:
                from .host import attach_builtin_plugins

                attach_builtin_plugins(win, initial_folder=folder)
            except Exception as e:
                logger.exception("plugin attach failed: %r", e)
            # Restore per-project session, merging explicit files on top.
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
            return win

        def _new_window(self) -> None:
            try:
                w = self._create_window(None, [])
            except Exception:
                logger.exception("_new_window: create window failed")
                return
            try:
                w.present()
            except Exception:
                logger.debug("_new_window: present failed", exc_info=True)

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
                        try:
                            w = self._create_window(folder, [])
                        except Exception:
                            logger.exception("open folder: create window failed")
                            w = None
                        if w is not None:
                            try:
                                w.present()
                            except Exception:
                                logger.debug("open folder: present failed", exc_info=True)
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
                dlg.set_comments("GtkSourceView editor — plugins baked in.\nNo Peas. Just Thor.")
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
    app = ThorApplication()
    # Gtk.Application.run expects argv
    try:
        return app.run(argv)  # type: ignore[attr-defined]
    except Exception as e:
        logger.exception("run failed: %r", e)
        return 1
