# -*- coding: utf-8 -*-
"""Thor terminal — tabbed VTE bottom panel.

Migrated from plugins/tabbed-terminal. Clean Thor imports, no plugin
interface indirection, no dynamic typelib hacks.
"""

from __future__ import annotations

import logging
import signal

logger = logging.getLogger(__name__)

import os

PANEL_TITLE = "Terminal"
PANEL_ICONS = ("utilities-terminal", "terminal", "dialog-information")


def _resolve_shell_argv() -> list[str]:
    """Shell argv for new terminals: ``$SHELL`` with ``/bin/bash`` fallback.

    Honors the user's login shell; falls back to ``/bin/bash`` when
    ``$SHELL`` is unset, empty, or not an absolute executable path.
    """
    shell = (os.environ.get("SHELL") or "").strip()
    if shell and os.path.isabs(shell) and os.access(shell, os.X_OK):
        return [shell]
    if shell:
        logger.debug("ignoring unusable $SHELL=%r, falling back to /bin/bash", shell)
    return ["/bin/bash"]


BASE_LABEL = "Terminal"

SCHEME_SETTINGS_SCHEMA = "org.x.editor.preferences.editor"
SCHEME_SETTINGS_KEY = "scheme"
ATOM_SCHEME_ID = "atom-one-dark"

#: Hardcoded Atom One Dark terminal theme. Mirrors styles/atom-one-dark.xml
#: (bg/fg/cursor/selection + red/green/yellow/blue/purple/cyan/comment/white).
#: Used when GtkSource is unavailable and as a test oracle for the XML file.
ATOM_ONE_DARK = {
    "fg": "#ABB2BF",
    "bg": "#282C34",
    "cursor": "#528BFF",
    "highlight": "#3E4451",
    # 16 ANSI entries: 0-7 normal, 8-15 bright.
    "palette": [
        "#282C34",  # 0 black (bg)
        "#E06C75",  # 1 red
        "#98C379",  # 2 green
        "#E5C07B",  # 3 yellow
        "#61AFEF",  # 4 blue
        "#C678DD",  # 5 magenta (purple)
        "#56B6C2",  # 6 cyan
        "#ABB2BF",  # 7 white (fg)
        "#5C6370",  # 8 bright black (comment)
        "#E06C75",  # 9 bright red
        "#98C379",  # 10 bright green
        "#E5C07B",  # 11 bright yellow
        "#61AFEF",  # 12 bright blue
        "#C678DD",  # 13 bright magenta
        "#56B6C2",  # 14 bright cyan
        "#DCDFE4",  # 15 bright white
    ],
}

#: Scheme style names mapped to terminal roles. Tuples are tried in order
#: (foreground unless noted). "orange" (def:number) has no ANSI slot and is
#: intentionally unused. Pure data, headless-safe.
SCHEME_STYLE_MAP = {
    "fg": (("text", "foreground"),),
    "bg": (("text", "background"),),
    "cursor": (("cursor", "foreground"),),
    "selection": (("selection", "background"),),
    "gray": (("def:comment", "foreground"),),
    "red": (
        ("def:preprocessor", "foreground"),
        ("def:identifier", "foreground"),
        ("def:error", "background"),
    ),
    "green": (("def:string", "foreground"),),
    "yellow": (("def:type", "foreground"),),
    "blue": (("def:function", "foreground"),),
    "magenta": (("def:keyword", "foreground"),),
    "cyan": (("def:operator", "foreground"),),
    "white": (("def:error", "foreground"),),
}

def atom_one_dark_theme() -> dict:
    """Atom One Dark VTE theme (fresh dict each call, headless-safe)."""
    return {
        "fg": ATOM_ONE_DARK["fg"],
        "bg": ATOM_ONE_DARK["bg"],
        "cursor": ATOM_ONE_DARK["cursor"],
        "highlight": ATOM_ONE_DARK["highlight"],
        "palette": list(ATOM_ONE_DARK["palette"]),
    }

def palette_from_scheme_colors(colors: dict) -> list[str]:
    """Build a complete 16-entry ANSI palette from semantic scheme colors.

    Missing entries fall back to fg/bg so the result is always complete.
    Keys: bg, fg, red, green, yellow, blue, magenta, cyan, gray, white.
    Pure logic, headless-safe.
    """
    fg = colors.get("fg") or "#FFFFFF"
    bg = colors.get("bg") or "#000000"
    red = colors.get("red") or fg
    green = colors.get("green") or fg
    yellow = colors.get("yellow") or fg
    blue = colors.get("blue") or fg
    magenta = colors.get("magenta") or fg
    cyan = colors.get("cyan") or fg
    gray = colors.get("gray") or fg
    white = colors.get("white") or fg
    return [
        bg, red, green, yellow, blue, magenta, cyan, fg,
        gray, red, green, yellow, blue, magenta, cyan, white,
    ]

def build_vte_theme(colors: dict | None) -> dict | None:
    """Full VTE theme from extracted scheme colors, or None to keep defaults.

    Returns None when the scheme defines no text fg/bg (e.g. tango
    inherit the GTK theme, which VTE already follows). Pure, headless-safe.
    """
    if not colors:
        return None
    fg = colors.get("fg")
    bg = colors.get("bg")
    if not fg and not bg:
        return None
    fg = fg or "#FFFFFF"
    bg = bg or "#000000"
    gray = colors.get("gray") or fg
    merged = dict(colors)
    merged["fg"] = fg
    merged["bg"] = bg
    return {
        "fg": fg,
        "bg": bg,
        "cursor": colors.get("cursor") or fg,
        "highlight": colors.get("selection") or gray,
        "palette": palette_from_scheme_colors(merged),
    }

_BACKTICK_NAMES = frozenset({"grave", "quoteleft", "`", "asciigrave"})

def unique_label(base: str, existing: list[str] | set[str] | tuple) -> str:
    """First free tab label: base, base 2, base 3, ..."""
    taken = set(existing)
    if base not in taken:
        return base
    index = 2
    while f"{base} {index}" in taken:
        index += 1
    return f"{base} {index}"

def apply_fonts(window) -> None:
    """Apply the saved terminal font to every tab of this window's terminal."""
    try:
        panel = getattr(window, "_thor_terminal_panel", None)
        notebook = getattr(panel, "notebook", None)
        if notebook is None:
            return
        from .fonts import apply_to_term

        for i in range(notebook.get_n_pages()):
            try:
                apply_to_term(notebook.get_nth_page(i))
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"terminal font apply failed: {e!r}")


def focus_in_panel(window) -> bool:
    """True when keyboard focus sits inside the embedded terminal.

    Ctrl+R is readline reverse-search there and must not be stolen by
    the window-level word-wrap toggle.
    """
    try:
        panel = getattr(window, "_thor_terminal_panel", None)
        if panel is None:
            return False
        focus = window.get_focus() if hasattr(window, "get_focus") else None
        ancestor = focus
        for _ in range(8):
            if ancestor is None:
                break
            if ancestor is panel:
                return True
            ancestor = ancestor.get_parent() if hasattr(ancestor, "get_parent") else None
    except Exception as e:
        logger.debug(f"terminal focus probe failed: {e!r}")
    return False


def handle_global_key(keyname: str, ctrl: bool, shift: bool, alt: bool) -> str | None:
    """Map a window keypress to a terminal action.

    Returns "new" | "close" | "focus" | None. Pure logic, headless-testable.
    """
    name = (keyname or "").lower()
    if ctrl and shift and not alt and name == "t":
        return "new"
    if ctrl and shift and not alt and name == "w":
        return "close"
    if ctrl and not shift and not alt and (keyname in _BACKTICK_NAMES or name in _BACKTICK_NAMES):
        return "focus"
    return None

# -- gi imports (soft-fail, headless-safe) ---------------------------------
try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import GObject, Gtk, Gdk, Gio, GLib, Pango  # type: ignore

    try:
        gi.require_version("GtkSource", "4")
        from gi.repository import GtkSource  # type: ignore
    except Exception:
        try:
            gi.require_version("GtkSource", "3.0")
            from gi.repository import GtkSource  # type: ignore
        except Exception:
            GtkSource = None  # type: ignore

    try:
        gi.require_version("Vte", "2.91")
        from gi.repository import Vte  # type: ignore
    except Exception as _vte_err:
        Vte = None  # type: ignore
        try:
            logger.warning(f"VTE unavailable ({_vte_err}); install gir1.2-vte-2.91 for terminal tabs.")
        except Exception:
            pass
except Exception:
    GObject = Gtk = Gdk = Gio = GLib = Pango = Vte = GtkSource = None  # type: ignore[no-redef]

from ..keys import decode_key_event

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
                continue
    return candidates[0]

def _rgba_to_hex(rgba) -> str | None:
    """Gdk.RGBA (or "#rrggbb" string) -> "#RRGGBB". None when unusable."""
    try:
        if isinstance(rgba, str):
            text = rgba.strip()
            if len(text) == 7 and text.startswith("#"):
                int(text[1:], 16)
                return text.upper()
            return None
        red = int(round(float(rgba.red) * 255))
        green = int(round(float(rgba.green) * 255))
        blue = int(round(float(rgba.blue) * 255))
        clamp = lambda v: max(0, min(255, v))
        return f"#{clamp(red):02X}{clamp(green):02X}{clamp(blue):02X}"
    except Exception:
        return None

def current_scheme_id() -> str | None:
    """Current editor color-scheme id (GSettings), or None if unknown."""
    if Gio is None:
        return None
    try:
        settings = Gio.Settings.new(SCHEME_SETTINGS_SCHEMA)
        scheme_id = settings.get_string(SCHEME_SETTINGS_KEY)
    except Exception as e:
        logger.debug(f"editor scheme lookup failed: {e!r}")
        return None
    scheme_id = (scheme_id or "").strip()
    return scheme_id or None

def extract_scheme_colors(scheme_id: str):
    """Semantic hex colors for a GtkSource style-scheme id.

    Returns a dict of role -> "#RRGGBB" (missing roles omitted) or None
    when GtkSource is unavailable or the scheme cannot be found.
    """
    if GtkSource is None:
        return None
    wanted = (scheme_id or "").strip()
    if not wanted:
        return None
    try:
        manager = GtkSource.StyleSchemeManager.get_default()
    except Exception as e:
        logger.debug(f"scheme manager unavailable: {e!r}")
        return None
    # ensure thor styles dir is visible
    try:
        seen = set(manager.get_search_path() or [])
        try:
            from thor import xdg
            s_dirs = xdg.styles_dirs()
        except Exception:
            styledir = os.environ.get("THOR_STYLE_DIR") or os.path.expanduser("~/.local/share/thor/styles")
            s_dirs = [styledir]
        for s_dir in s_dirs:
            if s_dir and s_dir not in seen and os.path.isdir(s_dir):
                try:
                    manager.append_search_path(s_dir)
                    seen.add(s_dir)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        scheme = manager.get_scheme(wanted)
    except Exception as e:
        logger.debug(f"scheme lookup failed for {wanted!r}: {e!r}")
        return None
    if scheme is None:
        logger.debug(f"scheme not found: {wanted!r}")
        return None

    def _lookup(style_name: str, kind: str) -> str | None:
        try:
            style = scheme.get_style(style_name)
        except Exception:
            return None
        if style is None:
            return None
        try:
            is_set = style.get_property(f"{kind}-set")
        except Exception:
            is_set = True
        if not is_set:
            return None
        try:
            rgba = style.get_property(kind)
        except Exception:
            return None
        return _rgba_to_hex(rgba)

    colors: dict[str, str] = {}
    for role, candidates in SCHEME_STYLE_MAP.items():
        for style_name, kind in candidates:
            found = _lookup(style_name, kind)
            if found:
                colors[role] = found
                break
    return colors

def current_editor_theme() -> dict | None:
    """VTE theme dict for current scheme, or None to keep VTE defaults.

    Falls back to the hardcoded Atom One Dark theme when the active scheme
    is atom-one-dark but its XML cannot be read (e.g. no GtkSource).
    """
    try:
        scheme_id = current_scheme_id()
    except Exception:
        return None
    if not scheme_id:
        return None
    if scheme_id == ATOM_SCHEME_ID and GtkSource is None:
        return atom_one_dark_theme()
    try:
        colors = extract_scheme_colors(scheme_id)
    except Exception as e:
        logger.debug(f"scheme color extraction failed: {e!r}")
        colors = None
    if not colors:
        if scheme_id == ATOM_SCHEME_ID:
            return atom_one_dark_theme()
        return None
    return build_vte_theme(colors)

def apply_theme_to_terminal(term, theme: dict | None) -> bool:
    """Apply a VTE theme dict to a Vte.Terminal. Soft-only, never raises."""
    if Gdk is None or term is None or not theme:
        return False
    try:
        palette_hex = theme.get("palette") or []
        if len(palette_hex) != 16:
            return False

        def _parse(value):
            rgba = Gdk.RGBA()
            if not rgba.parse(str(value)):
                raise ValueError(f"unparsable color: {value!r}")
            return rgba

        term.set_colors(_parse(theme["fg"]), _parse(theme["bg"]),
                        [_parse(entry) for entry in palette_hex])
    except Exception as e:
        logger.debug(f"terminal palette apply failed: {e!r}")
        return False
    for key, method in (("cursor", "set_color_cursor"),
                        ("highlight", "set_color_highlight")):
        try:
            rgba = Gdk.RGBA()
            if rgba.parse(str(theme[key])):
                getattr(term, method)(rgba)
        except Exception as e:
            logger.debug(f"terminal {key} apply failed: {e!r}")
    return True

# -- TerminalPanel ---------------------------------------------------------
if Gtk is not None:
    class TerminalPanel(Gtk.Box):
        """Bottom-panel widget: toolbar + notebook of VTE terminals."""

        def __init__(self) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self._labels: list[str] = []
            self._pids: dict = {}
            self.notebook = None
            self._fallback = None

            if Vte is None:
                # Soft-only fallback: visible hint, no raise.
                self._fallback = Gtk.Label(
                    label="Terminal unavailable: install gir1.2-vte-2.91, "
                          "then restart.")
                self._fallback.set_xalign(0.0)
                self.pack_start(self._fallback, True, True, 0)
                self.show_all()
                return

            self.notebook = Gtk.Notebook()
            self.notebook.set_scrollable(True)
            self.pack_start(self.notebook, True, True, 0)
            self.show_all()

        # -- tab management -------------------------------------------
        def current_labels(self) -> list[str]:
            return list(self._labels)

        def _tab_label_widget(self, label: str):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            text = Gtk.Label(label=label)
            close = Gtk.Button.new_with_label("✕")
            close.set_relief(Gtk.ReliefStyle.NONE)
            close.set_focus_on_click(False)
            close.connect("clicked", lambda _b, w=box: self._close_by_tab_widget(w))
            box.pack_start(text, True, True, 0)
            box.pack_start(close, False, False, 0)
            box.show_all()
            return box, text

        def _set_tab_text(self, page_index: int, text: str) -> None:
            try:
                tab_widget = self.notebook.get_tab_label(
                    self.notebook.get_nth_page(page_index))
                for child in tab_widget.get_children():
                    if isinstance(child, Gtk.Label):
                        child.set_text(text)
                        break
            except Exception as e:
                logger.debug(f"tab label update failed: {e!r}")

        def new_terminal(self, grab_focus: bool = True) -> None:
            if Vte is None or self.notebook is None:
                return
            label = unique_label(BASE_LABEL, self._labels)
            try:
                term = Vte.Terminal()
            except Exception as e:
                logger.debug(f"Vte.Terminal create failed: {e!r}")
                return
            try:
                term.set_scrollback_lines(10000)
            except Exception:
                pass
            try:
                term.set_mouse_autohide(True)
            except Exception:
                pass
            try:
                term.set_audible_bell(False)
            except Exception:
                pass
            try:
                term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            except Exception:
                pass
            try:
                from thor.fonts import apply_to_term

                if not apply_to_term(term):
                    term.set_font(Pango.FontDescription("Monospace 10"))
            except Exception:
                try:
                    term.set_font(Pango.FontDescription("Monospace 10"))
                except Exception:
                    pass
            try:
                self.apply_theme_to_term(term)
            except Exception as e:
                logger.debug(f"terminal theme failed: {e!r}")
            try:
                term.connect("child-exited", self._on_child_exited)
            except Exception:
                pass
            try:
                term.connect("button-press-event", self._on_button_press)
            except Exception:
                pass
            tab_widget, _text = self._tab_label_widget(label)
            try:
                page = self.notebook.append_page(term, tab_widget)
            except Exception as e:
                logger.debug(f"notebook append failed: {e!r}")
                return
            self._labels.append(label)
            self.show_all()
            try:
                self.notebook.set_current_page(page)
            except Exception:
                pass
            self._spawn(term, page)
            if grab_focus:
                try:
                    GLib.idle_add(term.grab_focus)
                except Exception:
                    pass
        def _spawn(self, term, page: int) -> None:
            argv = list(_resolve_shell_argv())
            # Prefer spawn_async (non-blocking) when the VTE build offers it;
            # fall back to spawn_sync otherwise. Documented here because the
            # async signature varies across VTE versions, so any failure
            # drops through to the stable sync path.
            spawn_async = getattr(term, "spawn_async", None)
            if callable(spawn_async):
                try:
                    spawn_async(
                        Vte.PtyFlags.DEFAULT,
                        None,
                        argv,
                        None,
                        GLib.SpawnFlags.DEFAULT,
                        None,
                        None,
                        -1,
                        None,
                        lambda _t, _p, _e: None,
                        None,
                    )
                    # Async launch reports pid via callback; None here means
                    # unknown pid — close destroys the widget only, never kill().
                    self._pids[id(term)] = None
                    return
                except Exception as e:
                    logger.debug("spawn_async failed, trying spawn_sync: %r", e, exc_info=True)
            try:
                # working_directory=None inherits cwd.
                ok, pid = term.spawn_sync(
                    Vte.PtyFlags.DEFAULT,
                    None,
                    argv,
                    None,
                    GLib.SpawnFlags.DEFAULT,
                    None,
                    None,
                    None,
                )
                if not ok:
                    raise RuntimeError("spawn_sync returned False")
            except Exception as e:
                logger.debug(f"terminal spawn failed: {e!r}")
                try:
                    self._set_tab_text(page, f"{self._labels[page]} (failed)")
                except Exception as e2:
                    logger.debug("tab failed label failed: %r", e2, exc_info=True)
                logger.error(f"failed to launch {' '.join(argv)}: {e!r}")
                return
            try:
                self._pids[id(term)] = int(pid) if pid is not None else None
            except Exception as e:
                logger.debug("pid track failed: %r", e, exc_info=True)

        def _forget_pid(self, term) -> int | None:
            try:
                return self._pids.pop(id(term), None)
            except Exception as e:
                logger.debug("pid forget failed: %r", e, exc_info=True)
                return None

        def _kill_pid(self, pid: int | None) -> None:
            if pid is None or pid <= 0:
                return
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception as e:
                logger.debug("kill child %s failed: %r", pid, e, exc_info=True)

        def _on_child_exited(self, term, _status) -> None:
            # No auto-respawn: an exited shell stays visibly "(exited)" so a
            # fast-crashing child cannot spin the UI into an infinite
            # respawn loop. The user reaps it with Close / + New.
            self._forget_pid(term)
            try:
                page = self.notebook.page_num(term)
            except Exception as e:
                logger.debug("child-exited page lookup failed: %r", e, exc_info=True)
                return
            if page < 0:
                return
            try:
                old = self._labels[page]
                if "(exited)" not in old:
                    self._labels[page] = f"{old} (exited)"
                    self._set_tab_text(page, self._labels[page])
            except Exception as e:
                logger.debug("exited label failed: %r", e, exc_info=True)

        def _on_button_press(self, term, event) -> bool:
            try:
                is_right = event.button == 3
            except Exception:
                return False
            if not is_right:
                return False
            try:
                menu = Gtk.Menu()
                copy_item = Gtk.MenuItem.new_with_label("Copy")
                copy_item.connect("activate", lambda _m: self._copy(term))
                paste_item = Gtk.MenuItem.new_with_label("Paste")
                paste_item.connect("activate", lambda _m: self._paste(term))
                new_item = Gtk.MenuItem.new_with_label("New Tab (Ctrl+Shift+T)")
                new_item.connect("activate", lambda _m: self.new_terminal())
                menu.append(copy_item)
                menu.append(paste_item)
                menu.append(Gtk.SeparatorMenuItem())
                menu.append(new_item)
                menu.show_all()
                menu.popup(None, None, None, None, event.button, event.time)
            except Exception as e:
                logger.debug(f"context menu failed: {e!r}")
            return True

        @staticmethod
        def _copy(term) -> None:
            try:
                term.copy_clipboard_format(Vte.Format.TEXT)
            except Exception as e:
                logger.debug(f"copy failed: {e!r}")

        @staticmethod
        def _paste(term) -> None:
            try:
                term.paste_clipboard()
            except Exception as e:
                logger.debug(f"paste failed: {e!r}")

        def _close_by_tab_widget(self, tab_widget) -> None:
            if self.notebook is None:
                return
            try:
                pages = self.notebook.get_n_pages()
                for i in range(pages):
                    if self.notebook.get_tab_label(self.notebook.get_nth_page(i)) is tab_widget:
                        self._close_page(i)
                        return
            except Exception as e:
                logger.debug(f"close by widget failed: {e!r}")

        def _close_page(self, page: int) -> None:
            if self.notebook is None:
                return
            # Tab-close race: a double close-click or a child-exited event
            # racing a manual close can hand us a stale index or a dead
            # widget. Bail out instead of killing an unrelated pid.
            try:
                pages = self.notebook.get_n_pages()
            except Exception:
                return
            if page < 0 or page >= pages:
                return
            try:
                term = self.notebook.get_nth_page(page)
            except Exception as e:
                logger.debug("close page lookup failed: %r", e, exc_info=True)
                return
            if term is None:
                return
            # Ensure the child dies with the tab: SIGTERM the tracked pid,
            # then ask the pty to exit, then drop the widget.
            try:
                self._kill_pid(self._forget_pid(term))
            except Exception as e:
                logger.debug("close kill failed: %r", e, exc_info=True)
            try:
                term.feed_child(b"exit\n", -1)
            except Exception as e:
                logger.debug("close feed exit failed: %r", e, exc_info=True)
            try:
                self.notebook.remove_page(page)
            except Exception as e:
                logger.debug(f"remove page failed: {e!r}")
                return
            try:
                del self._labels[page]
            except Exception as e:
                logger.debug("close label drop failed: %r", e, exc_info=True)
            try:
                term.destroy()
            except Exception as e:
                logger.debug("close destroy failed: %r", e, exc_info=True)
            if self.notebook.get_n_pages() == 0:
                self.new_terminal()

        def close_current_terminal(self) -> None:
            if self.notebook is None:
                return
            try:
                page = self.notebook.get_current_page()
            except Exception:
                return
            if page < 0:
                return
            self._close_page(page)

        def focus_current(self) -> None:
            if self.notebook is None:
                return
            try:
                page = self.notebook.get_current_page()
                term = self.notebook.get_nth_page(page if page >= 0 else 0)
            except Exception:
                return
            if term is None:
                return
            try:
                term.grab_focus()
            except Exception as e:
                logger.debug(f"grab focus failed: {e!r}")

        # -- editor-following theme -----------------------------------
        def apply_theme_to_term(self, term) -> bool:
            """Tint one terminal with current scheme. Never raises."""
            try:
                theme = current_editor_theme()
            except Exception as e:
                logger.debug(f"editor theme lookup failed: {e!r}")
                return False
            if theme is None:
                return False
            return apply_theme_to_terminal(term, theme)

        def apply_editor_theme(self) -> None:
            """Re-tint every open terminal (called on scheme switches)."""
            if self.notebook is None:
                return
            try:
                theme = current_editor_theme()
            except Exception as e:
                logger.debug(f"editor theme lookup failed: {e!r}")
                return
            if theme is None:
                return
            try:
                pages = self.notebook.get_n_pages()
            except Exception:
                return
            for page in range(pages):
                try:
                    term = self.notebook.get_nth_page(page)
                except Exception:
                    continue
                apply_theme_to_terminal(term, theme)

# -- attach / detach -------------------------------------------------------

def _safe(fn):
    try:
        return fn()
    except Exception:
        return None

def attach(window) -> object | None:
    """Wire a TerminalPanel to a ThorWindow's bottom panel.

    Soft-fails (stderr + None) when Gtk/Vte unavailable. Idempotent per
    window — second call returns the existing panel.
    """
    if Gtk is None or window is None:
        return None
    # idempotent
    existing = getattr(window, "_thor_terminal_panel", None)
    if existing is not None:
        return existing
    logger.debug("attach: creating terminal panel")
    try:
        panel = TerminalPanel()  # type: ignore[call-arg]
    except Exception as e:
        logger.debug(f"panel create failed: {e!r}")
        try:
            logger.error(f"panel failed: {e!r}")
        except Exception:
            pass
        return None

    bottom = _safe(lambda: window.get_bottom_panel())
    if bottom is not None and panel is not None:
        added = False
        icon = _pick_icon(PANEL_ICONS)
        for attempt in (
            lambda: bottom.add_item(panel, PANEL_TITLE, icon),
            lambda: bottom.add(panel),  # type: ignore[attr-defined]
        ):
            try:
                attempt()
                added = True
                break
            except Exception as e:
                logger.debug(f"bottom panel add attempt failed: {e!r}")
                continue
        if not added:
            logger.debug("bottom panel add failed")

    # initial terminal
    try:
        if getattr(panel, "notebook", None) is not None:
            panel.new_terminal(grab_focus=False)
    except Exception as e:
        logger.debug(f"initial terminal failed: {e!r}")

    # -- window key handling (Ctrl+Shift+T/W, Ctrl+`) -------------------
    def _new_terminal():
        if panel is not None:
            try:
                panel.new_terminal()
            except Exception as e:
                logger.debug(f"new terminal failed: {e!r}")
        _reveal(focus=True)

    def _close_current():
        if panel is not None:
            try:
                panel.close_current_terminal()
            except Exception as e:
                logger.debug(f"close terminal failed: {e!r}")

    def _reveal(focus: bool = False):
        btm = _safe(lambda: window.get_bottom_panel())
        if btm is not None and panel is not None:
            try:
                visible = btm.get_visible() if hasattr(btm, "get_visible") else True
            except Exception:
                visible = True
            if not visible:
                try:
                    btm.set_visible(True)
                except Exception as e:
                    logger.debug(f"bottom show failed: {e!r}")
            try:
                if hasattr(btm, "activate_item"):
                    btm.activate_item(panel)
            except Exception as e:
                logger.debug(f"panel activate failed: {e!r}")
        if focus and panel is not None:
            try:
                GLib.idle_add(panel.focus_current)
            except Exception:
                try:
                    panel.focus_current()
                except Exception as e:
                    logger.debug(f"focus failed: {e!r}")

    def _terminal_has_focus() -> bool:
        try:
            focus = window.get_focus() if hasattr(window, "get_focus") else None
        except Exception as e:
            logger.debug("focus query failed: %r", e, exc_info=True)
            return False
        if focus is None or panel is None:
            return False
        try:
            # focus inside the panel (terminal or its toolbar)?
            ancestor = focus
            for _ in range(6):
                if ancestor is panel:
                    return True
                ancestor = ancestor.get_parent() if hasattr(ancestor, "get_parent") else None
                if ancestor is None:
                    break
        except Exception as e:
            logger.debug("focus walk failed: %r", e, exc_info=True)
        return False

    def _focus_editor() -> None:
        try:
            view = window.get_active_view() if hasattr(window, "get_active_view") else None
        except Exception as e:
            logger.debug("active view lookup failed: %r", e, exc_info=True)
            view = None
        if view is not None:
            try:
                view.grab_focus()
                return
            except Exception as e:
                logger.debug("editor focus failed: %r", e, exc_info=True)
        try:
            window.grab_focus()
        except Exception as e:
            logger.debug("window focus failed: %r", e, exc_info=True)

    def _toggle_focus() -> None:
        # Two-way Ctrl+`: terminal -> editor, editor -> terminal.
        if _terminal_has_focus():
            logger.debug("key: Ctrl+` focus-editor (toggle back)")
            _focus_editor()
        else:
            logger.debug("key: Ctrl+` focus-terminal")
            _reveal(focus=True)

    def _handle_global(keyname: str, ctrl: bool, shift: bool, alt: bool) -> bool:
        action = handle_global_key(keyname, ctrl, shift, alt)
        if action == "new":
            logger.debug("key: Ctrl+Shift+T new-terminal")
            _new_terminal()
            return True
        if action == "close":
            # Owned here, NOT window-close: the window must only close when
            # this handler declines (it never declines Ctrl+Shift+W).
            logger.debug("key: Ctrl+Shift+W close-terminal")
            _close_current()
            return True
        if action == "focus":
            _toggle_focus()
            return True
        return False

    def _handle_key(_win, keyname, ctrl, shift, alt) -> bool:
        return _handle_global(keyname, ctrl, shift, alt)

    try:
        window.register_key_handler(_handle_key)
    except Exception as e:
        logger.debug(f"window keys register failed: {e!r}")

    # live scheme tracking: re-tint on GSettings change
    scheme_settings = None
    scheme_changed_id = None
    try:
        if Gio is not None:
            settings = Gio.Settings.new(SCHEME_SETTINGS_SCHEMA)
            scheme_settings = settings

            def _on_scheme_changed(*_a):
                try:
                    panel.apply_editor_theme()
                except Exception as e:
                    logger.debug(f"scheme re-tint failed: {e!r}")

            scheme_changed_id = settings.connect(
                f"changed::{SCHEME_SETTINGS_KEY}", _on_scheme_changed
            )
    except Exception as e:
        logger.debug(f"scheme watch failed: {e!r}")
        scheme_settings = None
        scheme_changed_id = None

    # stash for detach
    try:
        window._thor_terminal_panel = panel  # type: ignore[attr-defined]
        window._thor_terminal_key_handler = _handle_key  # type: ignore[attr-defined]
        window._thor_terminal_scheme_settings = scheme_settings  # type: ignore[attr-defined]
        window._thor_terminal_scheme_changed_id = scheme_changed_id  # type: ignore[attr-defined]
    except Exception:
        pass

    return panel

def detach(window) -> None:
    """Undo attach() — disconnect signals and remove panel from bottom."""
    if window is None:
        return
    panel = getattr(window, "_thor_terminal_panel", None)
    key_handler = getattr(window, "_thor_terminal_key_handler", None)
    scheme_settings = getattr(window, "_thor_terminal_scheme_settings", None)
    scheme_changed_id = getattr(window, "_thor_terminal_scheme_changed_id", None)

    if key_handler is not None:
        try:
            window.unregister_key_handler(key_handler)
        except Exception:
            pass
    if scheme_changed_id is not None:
        try:
            scheme_settings.disconnect(scheme_changed_id)
        except Exception:
            pass
    if panel is not None:
        bottom = _safe(lambda: window.get_bottom_panel())
        if bottom is not None:
            for attempt in (lambda: bottom.remove_item(panel), lambda: bottom.remove(panel)):  # type: ignore[attr-defined]
                try:
                    attempt()
                    break
                except Exception:
                    continue
        try:
            panel.destroy()
        except Exception:
            pass
    try:
        window._thor_terminal_panel = None  # type: ignore[attr-defined]
        window._thor_terminal_key_handler = None  # type: ignore[attr-defined]
        window._thor_terminal_scheme_settings = None  # type: ignore[attr-defined]
        window._thor_terminal_scheme_changed_id = None  # type: ignore[attr-defined]
    except Exception:
        pass

__all__ = [
    "PANEL_TITLE",
    "PANEL_ICONS",
    "_resolve_shell_argv",
    "BASE_LABEL",
    "ATOM_ONE_DARK",
    "SCHEME_STYLE_MAP",
    "SCHEME_SETTINGS_SCHEMA",
    "SCHEME_SETTINGS_KEY",
    "ATOM_SCHEME_ID",
    "atom_one_dark_theme",
    "palette_from_scheme_colors",
    "build_vte_theme",
    "unique_label",
    "handle_global_key",
    "current_scheme_id",
    "extract_scheme_colors",
    "current_editor_theme",
    "apply_theme_to_terminal",
    "TerminalPanel",
    "attach",
    "detach",
]
