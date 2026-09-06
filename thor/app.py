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


def _split_location_arg(arg: str) -> tuple[str, int | None]:
    """Return (path_without_line, line_or_None) for file:line / file(line) forms."""
    m = re.match(r"^(.*?)\((\d+)(?:[,:](\d+))?\)\s*$", arg)
    if m:
        try:
            return m.group(1).strip(), max(1, int(m.group(2)))
        except Exception:
            return arg, None
    m = re.match(r"^(.*?):(\d+)(?::(\d+))?\s*$", arg)
    if m:
        cand = m.group(1).strip()
        if cand:
            # Treat as line suffix when it looks like a path; avoids splitting
            # option-like strings while still handling `file.cs:10`, `a/b:10`.
            looks_like_path = os.path.exists(cand) or os.path.isfile(cand) or "." in os.path.basename(cand) or "/" in cand
            # Accept either obvious path shape or any non-option cand — covers
            # new files that don't exist yet.
            if looks_like_path or not cand.startswith("-"):
                try:
                    return cand, max(1, int(m.group(2)))
                except Exception:
                    pass
    return arg, None


def _resolve_initial_target(args: list[str]) -> tuple[str | None, list[str]]:
    """First non-option arg is file/folder target; rest are extra files.

    Returns (folder, files). For callers needing line numbers use
    _resolve_initial_target_with_lines().
    """
    folder, files, _lines = _resolve_initial_target_with_lines(args)
    return folder, files


def _resolve_initial_target_with_lines(args: list[str]) -> tuple[str | None, list[str], dict[str, int]]:
    """Like _resolve_initial_target but also returns {abspath: line} for +N / file:line."""
    folder: str | None = None
    files: list[str] = []
    file_lines: dict[str, int] = {}
    pending_line: int | None = None
    for a in args[1:]:
        if a in ("--help", "-h", "--version", "-v", "--new-window"):
            continue
        if a.startswith("+") and a[1:].isdigit():
            try:
                pending_line = max(1, int(a[1:]))
            except Exception:
                pending_line = None
            continue
        if a.startswith("-"):
            continue
        path_part, line_from_suffix = _split_location_arg(a)
        line = line_from_suffix if line_from_suffix is not None else pending_line
        pending_line = None
        p = os.path.abspath(path_part)
        if os.path.isdir(p) and folder is None:
            folder = p
            if line is not None:
                # folder with line doesn't make sense — ignore
                pass
        elif os.path.isfile(p):
            files.append(p)
            if line is not None:
                file_lines[p] = line
        elif os.path.exists(p):
            files.append(p)
            if line is not None:
                file_lines[p] = line
        elif folder is None:
            # non-existent folder arg -> treat as folder to create/open
            folder = p
        else:
            # extra non-existent file arg (e.g. new file) — still add as file
            files.append(p)
            if line is not None:
                file_lines[p] = line
    if folder is None and not files:
        # default to cwd (like thor-code .)
        folder = os.getcwd()
    return folder, files, file_lines


if Gtk is not None:
    class ThorApplication(Gtk.Application):  # type: ignore[misc]
        __gtype_name__ = "ThorApplication"

        def __init__(self) -> None:
            super().__init__(
                application_id=APP_ID,
                flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.HANDLES_COMMAND_LINE,  # type: ignore[union-attr]
            )
            self._pending_folder: str | None = None
            self._pending_files: list[str] = []
            self._pending_file_lines: dict[str, int] = {}
            self._pending_new_window: bool = False
            # CLI option passthrough
            try:
                self.add_main_option("new-window", ord("n"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open in new window", None)  # type: ignore[attr-defined]
            except Exception:
                pass

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
                    pass
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
                    pass
            # Global accelerators (fallback for window-level handling)
            try:
                self.set_accels_for_action("app.new_window", ["<Primary>n"])
                self.set_accels_for_action("app.open_folder", ["<Primary><Shift>o"])
                self.set_accels_for_action("app.quit", ["<Primary>q"])
            except Exception:
                pass
        def do_activate(self) -> None:  # type: ignore[override]
            win = self.get_active_window()
            new_window = bool(getattr(self, "_pending_new_window", False))
            # Clear flag immediately
            try:
                self._pending_new_window = False
            except Exception:
                pass
            pending_lines = getattr(self, "_pending_file_lines", {}) or {}
            try:
                self._pending_file_lines = {}
            except Exception:
                pass
            if win is None or new_window:
                win = self._create_window(self._pending_folder, self._pending_files, file_lines=pending_lines)
            else:
                # Existing window: open pending files there (e.g. thor file:line while running)
                for fp in self._pending_files:
                    try:
                        loc = Gio.File.new_for_path(fp)  # type: ignore[union-attr]
                        line = pending_lines.get(fp, -1)
                        win.create_tab_from_location(loc, line_pos=line - 1 if line and line > 0 else -1, create=True, jump_to=True)
                    except Exception:
                        pass
                try:
                    if win._notebook.get_n_pages() == 0:  # type: ignore[attr-defined]
                        win.create_tab(jump_to=True)
                except Exception:
                    pass
            try:
                win.present()
            except Exception:
                pass

        def do_open(self, files, hint, data=None):  # type: ignore[override]
            # Gio.File[] from DBus open (e.g. thor file.cs)
            folder = None
            file_paths: list[str] = []
            for f in files:
                try:
                    p = f.get_path() or f.get_uri()
                    if p and os.path.isdir(p):
                        folder = p
                    elif p and os.path.isfile(p):
                        file_paths.append(p)
                except Exception:
                    continue
            win = self._create_window(folder, file_paths)
            win.present()

        def do_command_line(self, cmd):  # type: ignore[override]
            # Gio.ApplicationCommandLine wraps argv; unwrap if needed
            try:
                # cmd is Gio.ApplicationCommandLine
                argv = cmd.get_arguments()  # type: ignore[union-attr]
                # argv[0] is program name
                folder, files, file_lines = _resolve_initial_target_with_lines(argv)
                try:
                    opts = cmd.get_options_dict()  # type: ignore[attr-defined]
                    if opts is not None and opts.contains("new-window"):
                        self._pending_new_window = True
                    else:
                        self._pending_new_window = "--new-window" in (argv or [])
                except Exception:
                    self._pending_new_window = "--new-window" in (argv or [])
            except Exception:
                folder, files, file_lines = _resolve_initial_target_with_lines(list(sys.argv))
                self._pending_new_window = "--new-window" in sys.argv
            self._pending_folder = folder
            self._pending_files = files
            self._pending_file_lines = file_lines
            self.activate()
            try:
                return 0
            finally:
                try:
                    cmd.set_exit_status(0)  # type: ignore[attr-defined]
                except Exception:
                    pass

        # -- helpers ---------------------------------------------------
        def _create_window(self, folder: str | None, files: list[str], file_lines: dict[str, int] | None = None) -> ThorWindow:
            win = ThorWindow(self, initial_folder=folder)
            file_lines = file_lines or {}
            # Open requested files
            for fp in files:
                try:
                    loc = Gio.File.new_for_path(fp)
                    line = file_lines.get(fp)
                    line_pos = (line - 1) if line and line > 0 else -1
                    win.create_tab_from_location(loc, line_pos=line_pos, create=True, jump_to=True)
                except Exception as e:
                    logger.warning("open %s failed: %r", fp, e)
            try:
                if win._notebook.get_n_pages() == 0:  # type: ignore[attr-defined]
                    win.create_tab(jump_to=True)
            except Exception:
                pass
            # Bake built-in panels/plugins
            try:
                from .host import attach_builtin_plugins

                attach_builtin_plugins(win, initial_folder=folder)
            except Exception as e:
                logger.exception("plugin attach failed: %r", e)
            return win

        def _new_window(self) -> None:
            w = self._create_window(None, [])
            w.present()

        def _prompt_open_folder(self) -> None:
            win = self.get_active_window()
            try:
                dlg = Gtk.FileChooserDialog(  # type: ignore[attr-defined]
                    title="Open Folder",
                    transient_for=win,
                    action=Gtk.FileChooserAction.SELECT_FOLDER,
                )
                dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
                if dlg.run() == Gtk.ResponseType.OK:
                    folder = dlg.get_filename()
                    dlg.destroy()
                    if folder and os.path.isdir(folder):
                        w = self._create_window(folder, [])
                        w.present()
                        return
                dlg.destroy()
            except Exception as e:
                logger.warning("open folder dialog failed: %r", e)
        # Backward compat alias for typo
        _promp_open_folder = _prompt_open_folder

        def _prompt_open_file(self) -> None:
            win = self.get_active_window()
            if not isinstance(win, ThorWindow):
                return
            try:
                dlg = Gtk.FileChooserDialog(  # type: ignore[attr-defined]
                    title="Open File",
                    transient_for=win,
                    action=Gtk.FileChooserAction.OPEN,
                )
                dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
                dlg.set_select_multiple(True)
                if dlg.run() == Gtk.ResponseType.OK:
                    files = dlg.get_filenames()
                    dlg.destroy()
                    for fp in files:
                        try:
                            loc = Gio.File.new_for_path(fp)
                            win.create_tab_from_location(loc, create=True, jump_to=True)
                        except Exception:
                            pass
                    return
                dlg.destroy()
            except Exception as e:
                logger.warning("open file dialog failed: %r", e)

        _promp_open_file = _prompt_open_file

        def _show_about(self) -> None:
            win = self.get_active_window()
            try:
                from . import __version__

                dlg = Gtk.AboutDialog(transient_for=win, modal=True)  # type: ignore[attr-defined]
                dlg.set_program_name("Thor")
                dlg.set_version(__version__)
                dlg.set_comments("GtkSourceView editor — plugins baked in.\nNo Peas. Just Thor.")
                dlg.set_website("https://github.com/thor-editor/thor")
                dlg.set_license_type(Gtk.License.MIT_X11)
                dlg.run()
                dlg.destroy()
            except Exception:
                pass

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
        pass
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
