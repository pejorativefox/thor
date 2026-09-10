# -*- coding: utf-8 -*-
"""C# DevKit feature for Thor.

Solution explorer, test explorer, build output, Roslyn LSP
(completion, hover, go-to-definition, references, format, code actions,
diagnostics) and test runner — now for ThorWindow.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import GObject, Gtk, Gdk, Gio, GLib, Pango  # type: ignore

    from .settings import SettingsStore
    from . import deps as deps_mod
    from . import dotnet_cli
    from . import solution as solution_mod
    from . import roslyn as roslyn_mod
    from . import intelligence as intel
    from . import testing as testing_mod
    from .explorer import SolutionExplorer
    from .output import OutputView
    from .testpanel import TestPanel
    from .completion import COMMIT_CHARS, CompletionPopup, NAV_KEYS
    from . import gscompletion as gs_mod
    from .views import (
        ViewTracker,
        buffer_text,
        cursor_line0,
        cursor_offset,
        doc_path,
        is_csharp_doc,
    )

    try:
        gi.require_version("GtkSource", "4")
        from gi.repository import GtkSource  # type: ignore

        _GTKSOURCE_AVAILABLE = True
    except Exception as _e:
        GtkSource = None  # type: ignore
        _GTKSOURCE_AVAILABLE = False
        try:
            logger.error(f"missing optional dependency: GtkSource-4 typelib ({_e}). "
                  "Falling back to the custom completion popup.")
        except Exception:
            pass

    _GTK_AVAILABLE = True
    logger.debug("module-imported gtk_ok=True")
except Exception:  # headless / missing typelib
    logger.exception("GUI imports failed; using headless fallback")
    try:
        logger.error("IMPORT-FALLBACK: GUI imports failed, dummy feature active. "
                     "Likely missing python3-gi or typelib.")
    except Exception:
        pass

    class _DummyObject:
        def __init__(self, *args, **kwargs) -> None:
            pass

        @classmethod
        def Property(cls, *args, **kwargs):  # noqa: N802
            return None

    class _DummyGObject:
        Object = _DummyObject

        @classmethod
        def Property(cls, *args, **kwargs):  # noqa: N802
            return None

    class _DummyGLib:
        @staticmethod
        def idle_add(fn, *args):
            try:
                return fn(*args)
            except Exception:
                return None

        @staticmethod
        def timeout_add(_ms, _cb):
            return None

        @staticmethod
        def source_remove(_sid):
            return None

        @staticmethod
        def get_user_cache_dir():
            import tempfile

            return tempfile.gettempdir()

    GObject = _DummyGObject  # type: ignore[no-redef]
    GLib = _DummyGLib  # type: ignore[no-redef]
    Gtk = Gio = Gdk = Pango = None  # type: ignore[no-redef]
    GtkSource = None  # type: ignore[no-redef]
    SolutionExplorer = OutputView = TestPanel = None  # type: ignore[no-redef]
    CompletionPopup = ViewTracker = None  # type: ignore[no-redef]
    NAV_KEYS = frozenset()  # type: ignore[no-redef]
    COMMIT_CHARS = frozenset({".", "(", "[", "<", ";", ","})  # type: ignore[no-redef]
    SettingsStore = None  # type: ignore[no-redef]
    buffer_text = cursor_line0 = cursor_offset = doc_path = None  # type: ignore[no-redef]
    is_csharp_doc = None  # type: ignore[no-redef]

    from . import dotnet_cli  # noqa: E402
    from . import solution as solution_mod  # noqa: E402
    from . import roslyn as roslyn_mod  # noqa: E402
    from . import intelligence as intel  # noqa: E402
    from . import testing as testing_mod  # noqa: E402
    from . import gscompletion as gs_mod  # noqa: E402
    from . import deps as deps_mod  # noqa: E402

    _GTK_AVAILABLE = False
    _GTKSOURCE_AVAILABLE = False

def _note(*args, **kwargs) -> None:
    """Env-gated activation marker; no-op when GUI imports are unavailable."""
    try:
        logger.debug(*args, **kwargs)
    except Exception:
        pass


DIAG_TAG_NAMES = {1: "thor-csharp-diag-error", 2: "thor-csharp-diag-warning", 3: "thor-csharp-diag-info"}
DIAG_MARK_CATEGORY = "thor-csharp-diagnostic"

#: Debounce (ms) before a solution refresh runs after tab switches/adds.
#: Tabs often change in bursts (startup, session restore); one sweep wins.
REFRESH_DEBOUNCE_MS = 250

#: Diagnostics arrive in bursts (one notification per file per keystroke);
#: coalesce them for this long before touching the UI. Reuses a single
#: timeout source while notifications keep arriving.
DIAG_DEBOUNCE_MS = 250

#: Minimum gap (s) between Problems-panel rebuilds. Diagnostics storms
#: (save-all, branch switch) would otherwise rebuild the list per file.
PROBLEMS_THROTTLE_S = 0.5

#: Cap on Problems rows. Tawdry but effective: a broken generated file
#: can carry thousands of diagnostics; the panel only needs the head.
PROBLEMS_MAX_ROWS = 500


def _gio_file_path(location) -> str | None:
    try:
        if location is None:
            return None
        if location.has_uri_scheme("file"):
            return location.get_path()
        return None
    except Exception:
        return None


PANEL_ICONS = {
    "solution": ("application-x-executable", "folder", "package-x-generic"),
    "tests": ("applications-science", "system-run", "dialog-information"),
    "output": ("utilities-terminal", "text-x-generic", "dialog-information"),
}


def _pick_panel_icon(candidates) -> str:
    """First installed icon name (names verified against Adwaita/Legacy)."""
    try:
        from gi.repository import Gtk as _Gtk

        theme = _Gtk.IconTheme.get_default()
        if theme is not None:
            for name in candidates:
                try:
                    if theme.has_icon(name):
                        return name
                except Exception:
                    continue
    except Exception:
        pass
    return candidates[0]


def _add_to_panel(panel, widget, title: str, icon_key: str) -> bool:
    # ThorPanel.add_item(item, name, icon_name): name and icon are what the
    # tab shows. Passing anything else as icon_name renders the broken-image
    # placeholder (circle with a cross).
    icon = _pick_panel_icon(PANEL_ICONS.get(icon_key, ("dialog-information",)))
    for attempt in (
        lambda: panel.add_item(widget, title, icon),
        lambda: panel.add(widget),
    ):
        try:
            attempt()
            return True
        except Exception as e:
            logger.debug(f"panel add attempt failed: {e!r}")
            continue
    return False


def _remove_from_panel(panel, widget) -> None:
    for attempt in (lambda: panel.remove_item(widget), lambda: panel.remove(widget)):
        try:
            attempt()
            return
        except Exception as e:
            logger.debug(f"panel remove attempt failed: {e!r}")
            continue


_BaseManager = GObject.Object if _GTK_AVAILABLE and hasattr(GObject, 'Object') else object  # type: ignore


class CSharpManager(_BaseManager):  # type: ignore[misc]
    __gtype_name__ = "ThorCSharpManager"

    def __init__(self, window=None, initial_folder=None) -> None:
        try:
            super().__init__()
        except Exception as e:
            logger.debug(f"manager super().__init__ failed: {e!r}")
        self.window = window
        self._initial_folder = initial_folder
        try:
            self.settings = SettingsStore() if SettingsStore is not None else None
        except Exception as e:
            logger.warning(f"settings store unavailable, using defaults: {e!r}")
            self.settings = None
        self.explorer: SolutionExplorer | None = None
        self.testpanel: TestPanel | None = None
        self.output: OutputView | None = None
        self.tracker: ViewTracker | None = None
        self.completion_popup: CompletionPopup | None = None
        self._gs_provider = None
        self._gs_attached: set = set()
        self.roslyn = roslyn_mod.RoslynManager(
            on_diagnostics=self._on_diagnostics,
            ui_dispatch=lambda fn: GLib.idle_add(fn),
            on_error=self._on_roslyn_error,
            on_ready=self._on_roslyn_ready,
        )
        self.diagnostics: dict[str, list[intel.Diagnostic]] = {}
        self._model: solution_mod.SolutionModel | None = None
        self._signal_ids: list[tuple[object, int]] = []
        self._refresh_source: int | None = None
        self._doc_versions: dict[str, int] = {}
        self._pending_completion: dict | None = None
        self._completion_forward: tuple | None = None
        # Coalesce refilter idles: one per main-loop turn, not one per keypress.
        self._refilter_scheduled = False
        self._mark_views_configured: set = set()
        self._discovering_tests = False
        self._completion_warned = ""
        # Solution refresh runs off-thread; the generation counter drops
        # stale results when refreshes overlap (startup bursts).
        self._refresh_gen = 0
        # Diagnostics coalescing: one pending source + last Problems build.
        self._diag_source: int | None = None
        self._diag_pending = False
        self._problems_last = 0.0
        # Live dotnet children (build/test/run); cancelled on deactivate
        # so no orphan keeps writing to a dead panel.
        self._stream_handles: list = []
        # Coalesced output appends: per-line appends each create a Gtk
        # TextMark + scroll; batch them per idle tick instead.
        self._output_pending: list[str] = []
        self._output_scheduled = False
        self._output_lock = threading.Lock()

    # -- activation --------------------------------------------------
    def do_activate(self) -> None:
        _note("do_activate called")
        logger.debug("activate: C# DevKit for Thor starting up")
        self.explorer = SolutionExplorer()
        self.testpanel = TestPanel()
        self.output = OutputView()
        self.tracker = ViewTracker()
        self.completion_popup = CompletionPopup()

        side = self._safe(lambda: self.window.get_side_panel())
        bottom = self._safe(lambda: self.window.get_bottom_panel())
        if side is not None:
            if self.explorer is not None and not _add_to_panel(side, self.explorer, "C# Solution", "solution"):
                logger.debug("side panel (explorer) add failed")
            if self.testpanel is not None and not _add_to_panel(side, self.testpanel, "C# Tests", "tests"):
                logger.debug("side panel (tests) add failed")
        if bottom is not None:
            if self.output is not None and not _add_to_panel(bottom, self.output, "C# Output", "output"):
                logger.debug("bottom panel (output) add failed")
        self._report_startup_deps()

        if self.explorer is not None:
            self._connect(self.explorer, "open-file", lambda _w, p: self._jump_to(p, 0, 0))
            self._connect(self.explorer, "build-solution", lambda _w: self._build_solution())
            self._connect(self.explorer, "build-project", lambda _w, p: self._build_project(p))
            self._connect(self.explorer, "run-project", lambda _w, p: self._run_project(p))
            self._connect(self.explorer, "test-project", lambda _w, p: self._run_test_project(p))
            self._connect(self.explorer, "restore", lambda _w: self._restore())
            self._connect(self.explorer, "refresh", lambda _w: self._schedule_refresh())
        if self.testpanel is not None:
            self._connect(self.testpanel, "run-test", lambda _w, p, f: self._run_test_project(p, f or None))
            self._connect(self.testpanel, "run-all-tests", lambda _w: self._run_all_tests())
            self._connect(self.testpanel, "refresh-tests", lambda _w: self._refresh_tests())
        if self.output is not None:
            self._connect(self.output, "jump-to", lambda _w, p, l, c: self._jump_to(p, l, c))
        if self.tracker is not None:
            self._connect(self.tracker, "doc-changed", lambda _w, p: self._sync_doc(p))
            self._connect(self.tracker, "doc-saved", lambda _w, p: self._on_doc_saved(p))
            self._connect(self.tracker, "doc-closed", lambda _w, p: self._on_doc_closed(p))
            self._connect(self.tracker, "completion-request", self._on_completion_request)
            self._connect(self.tracker, "goto-definition", self._on_goto_definition)
            self._connect(self.tracker, "find-references", self._on_find_references)
            self._connect(self.tracker, "hover-request", self._on_hover_request)
            self._connect(self.tracker, "format-request", lambda _w, p: self._format_doc_path(p))
            self._connect(self.tracker, "code-action-request", self._on_code_action_request)
            try:
                self.tracker.attach(self.window)
            except Exception as e:
                logger.debug(f"tracker attach failed: {e!r}")
            self._setup_framework_completion()
        if self.completion_popup is not None:
            self._connect(self.completion_popup, "item-activated", lambda _w, i: self._apply_completion(i))
            self._connect(self.completion_popup, "dismissed", lambda _w: self._clear_pending_completion())

        self._connect(self.window, "active-tab-changed", lambda *_a: self._schedule_refresh())
        self._connect(self.window, "tab-added", lambda *_a: self._schedule_refresh())
        self._schedule_refresh()

    def do_deactivate(self) -> None:
        logger.debug("deactivate")
        if self._refresh_source is not None:
            try:
                GLib.source_remove(self._refresh_source)
            except Exception as e:
                logger.debug(f"deactivate refresh remove failed: {e!r}")
            self._refresh_source = None
        if self._diag_source is not None:
            try:
                GLib.source_remove(self._diag_source)
            except Exception as e:
                logger.debug(f"deactivate diag remove failed: {e!r}")
            self._diag_source = None
        self._refresh_gen += 1  # drop any in-flight worker publish
        # Reap live dotnet children so no orphan writes to dead panels.
        for handle in list(getattr(self, "_stream_handles", [])):
            try:
                handle.cancel()
            except Exception as e:
                logger.debug(f"deactivate stream cancel failed: {e!r}")
        try:
            self._stream_handles.clear()
        except Exception as e:
            logger.debug(f"deactivate stream clear failed: {e!r}")
        if self.tracker is not None:
            try:
                self.tracker.detach()
            except Exception as e:
                logger.debug(f"deactivate tracker detach failed: {e!r}")
            self.tracker = None
        try:
            if self._gs_provider is not None and _GTKSOURCE_AVAILABLE:
                gs_mod.detach_from_views(self.window, self._gs_provider, self._gs_attached)
        except Exception as e:
            logger.debug(f"deactivate gs detach failed: {e!r}")
        self._gs_provider = None
        self._disconnect_completion_forward()
        for obj, handler_id in self._signal_ids:
            try:
                obj.disconnect(handler_id)  # type: ignore[attr-defined]
            except Exception as e:
                logger.debug(f"deactivate disconnect failed: {e!r}")
        self._signal_ids.clear()
        try:
            self.roslyn.stop()
        except Exception as e:
            logger.debug(f"deactivate roslyn stop failed: {e!r}")
        for widget_name, accessor in (
            ("explorer", lambda: self.window.get_side_panel()),
            ("testpanel", lambda: self.window.get_side_panel()),
            ("output", lambda: self.window.get_bottom_panel()),
        ):
            widget = getattr(self, widget_name, None)
            if widget is None:
                continue
            panel = self._safe(accessor)
            if panel is not None:
                _remove_from_panel(panel, widget)
            try:
                widget.destroy()
            except Exception as e:
                logger.debug(f"deactivate widget destroy failed: {e!r}")
            setattr(self, widget_name, None)
        if self.completion_popup is not None:
            try:
                self.completion_popup.destroy()
            except Exception as e:
                logger.debug(f"deactivate popup destroy failed: {e!r}")
            self.completion_popup = None

    def do_update_state(self) -> None:
        return

    def _report_startup_deps(self) -> None:
        try:
            settings = getattr(self, "settings", None)
            try:
                dotnet = str(settings.get("dotnet_executable")) if settings else "dotnet"
            except Exception:
                dotnet = "dotnet"
            try:
                roslyn_server = str(settings.get("roslyn_server")) if settings else "~/.dotnet/tools/roslyn-language-server"
            except Exception:
                roslyn_server = "~/.dotnet/tools/roslyn-language-server"
            issues = deps_mod.check_all(
                dotnet=dotnet or "dotnet",
                roslyn_server=roslyn_server,
            )
        except Exception as e:
            try:
                logger.error(f"startup check failed: {e!r}")
            except Exception:
                pass
            return
        for issue in issues:
            try:
                logger.error(f"startup check: {issue.log_line()}")
            except Exception:
                pass
        if issues and self.output is not None:
            try:
                hard = [i for i in issues if not i.warn_only]
                if hard:
                    self.output.append("C# startup check: missing " + ", ".join(i.name for i in hard) + "\n")
            except Exception:
                pass

    # -- helpers -----------------------------------------------------
    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception as e:
            logger.debug(f"window accessor failed: {e!r}")
            return None

    def _connect(self, obj, signal: str, callback) -> None:
        try:
            handler_id = obj.connect(signal, callback)
            self._signal_ids.append((obj, handler_id))
        except Exception as e:
            logger.debug(f"connect {signal} failed: {e!r}")

    def _active_path(self) -> str | None:
        doc = self._safe(lambda: self.window.get_active_document())
        if doc is None:
            return None
        return doc_path(doc)

    def _open_doc_dir(self) -> str | None:
        """Directory of any open document with a path (even if not active)."""
        try:
            docs = self._safe(lambda: list(self.window.get_documents())) or []
        except Exception:
            return None
        for doc in docs:
            try:
                path = doc_path(doc)
            except Exception:
                continue
            if path:
                try:
                    return os.path.dirname(path) or None
                except Exception:
                    return None
        return None

    @staticmethod
    def _startup_dir() -> str | None:
        """Thor's working directory (where it was launched).

        With no document open there is no active path, so discovery would
        otherwise start at $HOME and miss a nearby .sln/.slnx. The process
        cwd reflects the launch directory for terminal launches; the home
        and crawl guards downstream keep menu launches safe.
        """
        try:
            cwd = os.getcwd()
        except Exception:
            return None
        try:
            return cwd if os.path.isdir(cwd) else None
        except Exception:
            return None

    def _find_doc(self, path: str):
        try:
            for doc in self.window.get_documents():
                if doc_path(doc) == path:
                    return doc
        except Exception as e:
            logger.debug(f"_find_doc failed: {e!r}")
        return None

    def _iter_csharp_docs(self):
        try:
            docs = self.window.get_documents()
        except Exception:
            return
        for doc in docs:
            try:
                if is_csharp_doc(doc):
                    yield doc_path(doc), doc
            except Exception:
                continue

    def _schedule_refresh(self) -> None:
        if self._refresh_source is not None:
            try:
                GLib.source_remove(self._refresh_source)
            except Exception as e:
                logger.debug(f"refresh unschedule failed: {e!r}")
        try:
            self._refresh_source = GLib.timeout_add(REFRESH_DEBOUNCE_MS, self._refresh_cb)
        except Exception as e:
            logger.debug(f"refresh schedule failed: {e!r}")
            self._refresh_cb()

    def _setting(self, key: str, default=None):
        """Read a C# setting with a default; never raises.

        The settings store may be unavailable (None) or raise (missing
        state.toml section, corrupt file); every caller below runs on
        hot paths (refresh, Roslyn startup, save) that must not crash.
        """
        try:
            settings = getattr(self, "settings", None)
            if settings is None:
                return default
            value = settings.get(key)
            return default if value is None else value
        except Exception as e:
            logger.debug(f"csharp setting {key!r} failed: {e!r}")
            return default

    def _dotnet(self) -> str:
        configured = str(self._setting("dotnet_executable", "dotnet") or "dotnet")
        try:
            resolved = dotnet_cli.resolve_dotnet(configured)
        except Exception as e:
            logger.debug(f"resolve_dotnet failed: {e!r}")
            resolved = None
        return resolved or configured

    def _refresh_cb(self) -> bool:
        """Debounce entry: discovery runs on a worker, publish on idle.

        ``dotnet sln list`` + the project glob can take seconds on big
        trees; blocking the main loop here froze the editor on every tab
        switch. The generation counter drops late results when a newer
        refresh overtook them.
        """
        self._refresh_source = None
        self._refresh_gen += 1
        gen = self._refresh_gen
        if self.completion_popup is not None:
            try:
                self.completion_popup.dismiss()
            except Exception as e:
                logger.debug(f"refresh dismiss failed: {e!r}")
        active = self._active_path()
        start = (
            active
            or self._open_doc_dir()
            or self._startup_dir()
            or os.path.expanduser("~")
        )
        try:
            cwd = os.getcwd()
        except Exception as e:
            logger.debug(f"refresh cwd failed: {e!r}")
            cwd = "?"
        logger.debug(f"refresh solution from {start} (active={active} cwd={cwd}) gen={gen}")
        if self.output is not None:
            try:
                self.output.set_status("Discovering C# solution…")
            except Exception as e:
                logger.debug(f"refresh status failed: {e!r}")
        dotnet = self._dotnet()

        def _worker() -> None:
            try:
                model = solution_mod.load_solution(start, dotnet)
            except OSError as e:
                # Discovery must never crash the refresh path.
                logger.debug(f"refresh discovery failed: {e!r}")
                model = None
            except Exception as e:
                logger.debug(f"refresh discovery failed: {e!r}")
                model = None
            try:
                GLib.idle_add(self._publish_solution, model, gen)
            except Exception as e:
                logger.debug(f"refresh publish schedule failed: {e!r}")

        try:
            threading.Thread(target=_worker, name="thor-csharp-refresh", daemon=True).start()
        except Exception as e:
            logger.debug(f"refresh thread failed, running inline: {e!r}")
            _worker()
        return False

    def _publish_solution(self, model, gen: int) -> bool:
        """Main-loop publish of a worker-loaded solution (drops stale gens)."""
        if gen != self._refresh_gen:
            logger.debug(f"refresh gen={gen} superseded by gen={self._refresh_gen}, dropping")
            return False
        try:
            self._refresh_solution(model)
        except OSError as e:
            logger.debug(f"refresh publish failed: {e!r}")
            if self.output is not None:
                self.output.set_status("Solution refresh failed — see log.")
        except Exception as e:
            logger.debug(f"refresh publish failed: {e!r}")
            if self.output is not None:
                self.output.set_status("Solution refresh failed — see log.")
        return False

    def _refresh_solution(self, model=None) -> None:
        if model is None and self._model is not None:
            # Worker found nothing usable (or crashed); keep the last good
            # model instead of blanking the explorer mid-session.
            logger.debug("refresh: worker returned no model, keeping previous")
            if self.output is not None:
                self.output.set_status("Solution refresh failed — keeping previous.")
            return
        if model is not None:
            self._model = model
        elif self._model is None:
            # Synchronous fallback (tests/headless): load inline.
            active = self._active_path()
            start = (
                active
                or self._open_doc_dir()
                or self._startup_dir()
                or os.path.expanduser("~")
            )
            dotnet = self._dotnet()
            try:
                self._model = solution_mod.load_solution(start, dotnet)
            except OSError as e:
                logger.debug(f"refresh discovery failed: {e!r}")
                if self.output is not None:
                    self.output.set_status("Solution refresh failed — see log.")
                return
        if self.explorer is not None:
            self.explorer.set_model(self._model)
        if self.output is not None:
            if self._model.path:
                self.output.set_status(f"{os.path.basename(self._model.path)} — {len(self._model.projects)} projects")
            else:
                self.output.set_status("No .sln/.slnx found — showing nearby .csproj files")
        for path, _doc in self._iter_csharp_docs():
            self._sync_doc(path)
        if self._model.path or self._model.projects:
            self._ensure_roslyn()
        else:
            self._warn_completion_once(
                "no-solution",
                "no .sln/.slnx or .csproj found; Roslyn not started so completion is unavailable.",
                "No solution found — completion unavailable.",
            )

    # -- roslyn ------------------------------------------------------
    def _ensure_roslyn(self) -> None:
        if self._model is None:
            return
        if getattr(self.roslyn, "state", "") in ("starting", "ready"):
            return
        if solution_mod.is_home_root(self._model.root_dir):
            message = (f"Roslyn not started: workspace root is {self._model.root_dir}. "
                       "Open the solution folder directly (starting it on your home "
                       "directory makes the server crawl symlinks like Wine "
                       "dosdevices/z: into /proc, where it crashes).")
            try:
                logger.error(message)
            except Exception:
                pass
            if self.output is not None:
                self.output.append(message + "\n")
                self.output.set_status("Roslyn not started — open the solution folder.")
            return
        configured = str(self._setting("roslyn_server", "~/.dotnet/tools/roslyn-language-server") or "~/.dotnet/tools/roslyn-language-server")
        argv = roslyn_mod.resolve_server_command(configured)
        if argv is None:
            try:
                logger.error(f"Roslyn server not found: {configured}. "
                      "Install: dotnet tool install --global roslyn-language-server")
            except Exception:
                pass
            if self.output is not None:
                self.output.append(f"Roslyn server not found: {configured}\n")
                self.output.append("Install: dotnet tool install --global roslyn-language-server\n")
            return
        try:
            from thor import xdg
            log_dir = xdg.roslyn_log_dir()
        except Exception:
            try:
                log_dir = os.path.join(GLib.get_user_cache_dir(), "thor", "thor-csharp", "roslyn-logs")
            except Exception:
                log_dir = ""
        try:
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
        except OSError:
            log_dir = ""
        logger.debug(f"roslyn ensure: root={self._model.root_dir} sln={self._model.path} "
              f"state={getattr(self.roslyn, 'state', '?')} argv0={argv[0] if argv else None}")
        ok = self.roslyn.start(
            self._model.path,
            self._model.root_dir,
            argv,
            log_dir=log_dir or None,
            log_level=str(self._setting("roslyn_log_level", "Information") or "Information"),
            stderr_log_path=os.path.join(log_dir, "roslyn-stderr.log") if log_dir else None,
        )
        logger.debug(f"roslyn ensure: start returned {ok}")
        if not ok:
            try:
                logger.error("Roslyn failed to start (binary missing or spawn failed).")
            except Exception:
                pass
            if self.output is not None:
                self.output.append("Roslyn failed to start.\n")
            return
        if self.output is not None:
            self.output.append(f"Roslyn starting: {' '.join(argv)}\n")

    def _on_roslyn_error(self, message: str) -> None:
        try:
            logger.error(message.splitlines()[0] if message else "Roslyn server error")
        except Exception:
            pass
        if self.output is not None:
            self.output.append(f"\n{message}\n")
            self.output.set_status("Roslyn server died — see Output. Refresh to restart.")

    def _on_roslyn_ready(self) -> None:
        try:
            self._completion_warned = ""
        except Exception:
            pass
        synced = 0
        logger.debug(f"roslyn ready: syncing open docs (open_docs={len(getattr(self.roslyn, 'open_docs', {}))})")
        try:
            for path, _doc in self._iter_csharp_docs():
                try:
                    self._sync_doc(path)
                    synced += 1
                except Exception as e:
                    logger.debug(f"ready sync failed for {path}: {e!r}")
        except Exception as e:
            logger.debug(f"ready sync sweep failed: {e!r}")
        logger.debug(f"roslyn ready: synced {synced} doc(s)")
        if self.output is not None:
            try:
                self.output.set_status(f"Roslyn ready — {synced} C# file(s) synced.")
            except Exception:
                pass

    def _sync_doc(self, path: str) -> None:
        state = getattr(self.roslyn, "state", "")
        if state != "ready":
            logger.debug(f"sync skip {path}: roslyn {state!r}")
            return
        if not path.endswith(".cs"):
            return
        doc = self._find_doc(path)
        if doc is None:
            logger.debug(f"sync skip {path}: no open buffer")
            return
        text = buffer_text(doc)
        version = self._doc_versions.get(path, 0) + 1
        self._doc_versions[path] = version
        try:
            known = roslyn_mod.file_uri(path) in self.roslyn.open_docs
        except Exception:
            known = False
        if known:
            logger.debug(f"sync did_change {path} v{version} ({len(text)} chars)")
            self.roslyn.did_change(path, version, text)
        else:
            logger.debug(f"sync did_open {path} v{version} ({len(text)} chars)")
            self.roslyn.did_open(path, "csharp", version, text)

    def _on_doc_saved(self, path: str) -> None:
        if not path.endswith(".cs"):
            return
        doc = self._find_doc(path)
        text = buffer_text(doc) if doc is not None else ""
        try:
            self.roslyn.did_save(path, text)
        except Exception as e:
            logger.debug(f"did_save failed: {e!r}")
        if bool(self._setting("format_on_save", False)):
            self._format_doc_path(path)

    def _on_doc_closed(self, path: str) -> None:
        # doc-closed fires for every tab; only C# docs are ever sent to
        # Roslyn (which SIGABRTs on didClose for anything else).
        if path.endswith(".cs"):
            try:
                self.roslyn.did_close(path)
            except Exception:
                pass
        try:
            uri = roslyn_mod.file_uri(path)
            self.diagnostics.pop(uri, None)
            self._refresh_problems()
        except Exception:
            pass
        self._doc_versions.pop(path, None)

    # -- diagnostics -------------------------------------------------
    def _on_diagnostics(self, uri: str, raw: list) -> None:
        try:
            items = intel.normalize_diagnostics(uri, raw)
        except Exception as e:
            logger.debug(f"diagnostics normalize failed: {e!r}")
            return
        self.diagnostics[uri] = items
        # Coalesce bursts (one notification per file per keystroke): reuse
        # the pending source instead of rebuilding the UI per file.
        self._diag_pending = True
        if self._diag_source is not None:
            return
        try:
            self._diag_source = GLib.timeout_add(DIAG_DEBOUNCE_MS, self._diag_cb)
        except Exception as e:
            logger.debug(f"diagnostics schedule failed: {e!r}")
            self._diag_cb()

    def _diag_cb(self) -> bool:
        """Flush coalesced diagnostics to status/problems/gutter."""
        self._diag_source = None
        if not self._diag_pending:
            return False
        self._diag_pending = False
        try:
            total_errors = sum(1 for v in self.diagnostics.values() for d in v if d.severity == 1)
            total_warns = sum(1 for v in self.diagnostics.values() for d in v if d.severity == 2)
            if self.output is not None:
                self.output.set_status(f"C#: {total_errors} errors, {total_warns} warnings")
            self._refresh_problems()
            for path, doc in self._iter_csharp_docs():
                try:
                    # One file_uri per open doc instead of one per
                    # (doc, uri) pair; the old nested scan was O(open docs x
                    # diagnostic uris) as_uri() calls per flush. `is not None`
                    # (not truthiness): an empty list is a real "all clear"
                    # that must still be rendered to drop stale markers.
                    items = self.diagnostics.get(roslyn_mod.file_uri(path))
                    if items is not None:
                        self._render_diagnostics(doc, items)
                except Exception as e:
                    logger.debug(f"diagnostics render failed: {e!r}")
        except Exception as e:
            logger.debug(f"diagnostics flush failed: {e!r}")
        return False

    def _refresh_problems(self, force: bool = False) -> None:
        if self.output is None:
            return
        # Throttle rebuilds during diagnostics storms; re-arm once so the
        # final state still lands.
        try:
            now = time.monotonic()
        except Exception:
            now = 0.0
        if not force and 0.0 < now - self._problems_last < PROBLEMS_THROTTLE_S:
            if self._diag_source is None:
                try:
                    delay_ms = max(1, int(PROBLEMS_THROTTLE_S * 1000))
                    self._diag_source = GLib.timeout_add(delay_ms, self._diag_cb)
                    self._diag_pending = True
                except Exception as e:
                    logger.debug(f"problems re-arm failed: {e!r}")
            return
        self._problems_last = now
        rows: list[tuple[str, str, int, str, str]] = []
        for uri, items in self.diagnostics.items():
            for diag in items:
                rows.append(
                    (
                        intel.SEVERITY_LABEL.get(diag.severity, "?"),
                        os.path.basename(diag.path),
                        diag.line + 1,
                        diag.message,
                        diag.path,
                    )
                )
                if len(rows) >= PROBLEMS_MAX_ROWS:
                    break
            if len(rows) >= PROBLEMS_MAX_ROWS:
                break
        rows.sort(key=lambda r: (r[1], r[2]))
        self.output.set_problems(rows)

    def _ensure_diag_tags(self, doc) -> dict[int, object]:
        tags: dict[int, object] = {}
        try:
            table = doc.get_tag_table()
        except Exception:
            return tags
        specs = {
            1: ("underline", getattr(Pango.Underline, "ERROR", Pango.Underline.SINGLE)),
            2: ("underline", Pango.Underline.SINGLE),
            3: ("underline", Pango.Underline.SINGLE),
        }
        for severity, tag_name in DIAG_TAG_NAMES.items():
            tag = None
            try:
                tag = table.lookup(tag_name)
            except Exception:
                tag = None
            if tag is None:
                try:
                    prop, value = specs[severity]
                    tag = doc.create_tag(tag_name, **{prop: value})
                    if severity == 1:
                        try:
                            rgba = Gdk.RGBA()
                            if rgba.parse("#e01b24"):
                                tag.set_property("underline-rgba", rgba)
                        except Exception:
                            pass
                    elif severity == 2:
                        try:
                            rgba = Gdk.RGBA()
                            if rgba.parse("#e5a50a"):
                                tag.set_property("underline-rgba", rgba)
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug(f"diag tag create failed: {e!r}")
                    continue
            tags[severity] = tag
        return tags

    def _render_diagnostics(self, doc, items: list) -> None:
        try:
            start, end = doc.get_bounds()
        except Exception:
            return
        tags = self._ensure_diag_tags(doc)
        for tag in tags.values():
            try:
                doc.remove_tag(tag, start, end)
            except Exception:
                pass
        try:
            doc.remove_source_marks(start, end, DIAG_MARK_CATEGORY)
        except Exception:
            pass
        for diag in items:
            tag = tags.get(diag.severity)
            try:
                it = doc.get_iter_at_line(diag.line)
                it.forward_chars(diag.character)
                it_end = doc.get_iter_at_line(diag.line)
                it_end.forward_to_line_end()
                if it_end.get_offset() <= it.get_offset():
                    continue
                if tag is not None:
                    doc.apply_tag(tag, it, it_end)
                try:
                    doc.create_source_mark(None, DIAG_MARK_CATEGORY, it)
                except Exception:
                    pass
            except Exception:
                continue
        self._configure_marks(doc)

    @staticmethod
    def _marks_key(view):
        """Stable identity key for a view (never hash(view): collisions)."""
        try:
            hash(view)
        except Exception:
            return ("id", id(view))
        return view

    def _configure_marks(self, doc) -> None:
        """Enable gutter marks on views showing this doc (once per view)."""
        if not _GTKSOURCE_AVAILABLE:
            return
        try:
            views = self.window.get_views()
        except Exception:
            return
        for view in views:
            try:
                if view.get_buffer() is not doc:
                    continue
                if self._marks_key(view) in self._mark_views_configured:
                    continue
                view.set_show_line_marks(True)
                for category, color in (
                    (DIAG_MARK_CATEGORY, "#e01b24"),
                ):
                    try:
                        attrs = GtkSource.MarkAttributes()
                        rgba = Gdk.RGBA()
                        if rgba.parse(color):
                            attrs.set_background(rgba)
                        view.set_mark_attributes(category, attrs, 10)
                    except Exception as e:
                        logger.debug(f"mark attributes {category} failed: {e!r}")
                self._mark_views_configured.add(self._marks_key(view))
            except Exception as e:
                logger.debug(f"configure marks failed: {e!r}")
                continue

    # -- completion --------------------------------------------------
    def _roslyn_ready(self) -> bool:
        return getattr(self.roslyn, "state", "") == "ready"

    # -- GtkSource framework completion (preferred, wordcompletion-style)
    def _use_framework(self) -> bool:
        try:
            return bool(_GTKSOURCE_AVAILABLE and self._gs_provider is not None)
        except Exception:
            return False

    def _setup_framework_completion(self) -> None:
        if not _GTKSOURCE_AVAILABLE:
            return
        try:
            if self._gs_provider is None:
                self._gs_provider = gs_mod.RoslynCompletionProvider(
                    is_ready=self._roslyn_ready,
                    resolve_path=self._gs_resolve_path,
                    send_request=self._gs_send_request,
                    flush_doc=self._flush_completion_doc,
                )
            if self.tracker is not None:
                self.tracker.framework_completion = True
            self._ensure_gs_providers()
            self._connect(self.window, "tab-added", lambda *_a: self._ensure_gs_providers())
            self._connect(
                self.window, "active-tab-changed", lambda *_a: self._ensure_gs_providers()
            )
        except Exception as e:
            logger.debug(f"framework completion setup failed: {e!r}")
            self._gs_provider = None

    def _ensure_gs_providers(self) -> None:
        if not self._use_framework():
            return
        try:
            gs_mod.attach_to_views(self.window, self._gs_provider, self._gs_attached)
        except Exception as e:
            logger.debug(f"completion provider attach failed: {e!r}")

    def _warn_completion_once(self, key: str, message: str, status: str = "") -> None:
        if self._completion_warned == key:
            return
        self._completion_warned = key
        try:
            logger.error(f"completion unavailable: {message}")
        except Exception:
            pass  # logging itself failed; nothing left to report with
        if status and self.output is not None:
            try:
                self.output.set_status(status)
            except Exception as e:
                logger.debug(f"completion warn status failed: {e!r}")

    def _gs_resolve_path(self, buf) -> str | None:
        """Map a buffer to its .cs path (None for anything else)."""
        try:
            direct = doc_path(buf)
            if direct and direct.endswith(".cs"):
                return direct
        except Exception as e:
            logger.debug(f"gs resolve direct failed: {e!r}")
        try:
            for doc in self.window.get_documents():
                try:
                    # Identity first, then path equality. Never hash(doc):
                    # hashes collide across buffers and are not identity.
                    same = doc is buf
                    if not same:
                        try:
                            same = doc_path(doc) == doc_path(buf) and doc_path(doc) is not None
                        except Exception:
                            same = False
                    if same:
                        path = doc_path(doc)
                        return path if (path and path.endswith(".cs")) else None
                except Exception as e:
                    logger.debug(f"gs resolve doc failed: {e!r}")
                    continue
        except Exception as e:
            logger.debug(f"gs resolve scan failed: {e!r}")
        return None

    def _gs_send_request(self, method: str, params: dict, callback):
        try:
            request_id = self.roslyn.request(method, params, callback)
        except Exception as e:
            logger.debug(f"framework completion request failed: {e!r}")
            return None
        if request_id is None:
            self._warn_completion_once(
                f"send-{getattr(self.roslyn, 'state', '?')}",
                f"{method} not sent: Roslyn server is {getattr(self.roslyn, 'state', '?')!r}.",
                "Roslyn not running — completion unavailable. Refresh to restart.",
            )
        return request_id

    def _flush_completion_doc(self, path: str) -> None:
        """Synchronously push the buffer to Roslyn before a completion query.

        didChange is normally debounced 400ms; VSCode sends the latest
        text before completing. Best-effort: missing doc/server is fine.
        """
        try:
            if getattr(self.roslyn, "state", "") != "ready":
                return
            if not path.endswith(".cs"):
                return
            doc = self._find_doc(path)
            if doc is None:
                return
            text = buffer_text(doc)
            version = self._doc_versions.get(path, 0) + 1
            self._doc_versions[path] = version
            try:
                known = roslyn_mod.file_uri(path) in self.roslyn.open_docs
            except Exception:
                known = False
            if known:
                self.roslyn.did_change(path, version, text)
            else:
                self.roslyn.did_open(path, "csharp", version, text)
        except Exception as e:
            logger.debug(f"completion flush failed: {e!r}")

    def _show_framework_completion(self) -> bool:
        """Force the GtkSource popup open (explicit invoke, VSCode Ctrl+Space)."""
        try:
            if not self._use_framework():
                self._warn_completion_once(
                    "no-framework",
                    "GtkSource completion unavailable; install gir1.2-gtksource-4.",
                    "Completion unavailable (no GtkSource).",
                )
                return False
            if not self._roslyn_ready():
                self._warn_completion_once(
                    f"not-ready-{getattr(self.roslyn, 'state', '?')}",
                    f"Roslyn server is {getattr(self.roslyn, 'state', '?')!r}; invoke ignored.",
                    "Roslyn not ready — completion unavailable yet.",
                )
                return False
            view = self._safe(lambda: self.window.get_active_view())
            if view is None:
                return False
            opened = bool(gs_mod.show_completion(view, self._gs_provider))
            logger.debug(f"completion show: opened={opened}")
            if not opened:
                self._warn_completion_once(
                    "show-failed",
                    "GtkSource completion.start() refused the request.",
                    "Completion popup would not open.",
                )
            return opened
        except Exception as e:
            logger.debug(f"completion show failed: {e!r}")
            return False

    def _completion_visible(self) -> bool:
        try:
            return bool(self.completion_popup is not None and self.completion_popup.get_visible())
        except Exception:
            return False

    def _on_completion_request(self, _tracker, path: str, line: int, char: int, trigger: str) -> None:
        logger.debug(f"completion-request: path={path} line={line} char={char} trigger={trigger} "
              f"ready={self._roslyn_ready()} framework={self._use_framework()}")
        if not self._roslyn_ready():
            self._warn_completion_once(
                f"not-ready-{getattr(self.roslyn, 'state', '?')}",
                f"Roslyn server is {getattr(self.roslyn, 'state', '?')!r}; completion skipped for {path}.",
                "Roslyn not ready — completion unavailable yet.",
            )
            return
        try:
            self._completion_warned = ""
        except Exception:
            pass
        if self._use_framework():
            # GtkSource interactive activation already populated on every
            # keystroke; the tracker no longer emits auto triggers in this
            # mode. Only explicit invokes arrive here (fallback key
            # bindings / menus): force the popup open at the cursor.
            if trigger == "invoke":
                self._show_framework_completion()
            return
        doc = self._find_doc(path)
        if doc is None:
            return
        if self.completion_popup is None:
            return
        # Live filtering: the list is already up and the user typed another
        # word char -> just narrow locally, no new LSP round-trip.
        if self._completion_visible() and trigger.startswith("auto:"):
            try:
                self._refilter_completion()
            except Exception as e:
                logger.debug(f"completion refilter failed: {e!r}")
            return
        # VSCode pops the list on the first identifier char; a visible
        # list is already handled (refilter) above, so always request.
        if trigger.startswith("auto:"):
            trigger_kind, trigger_char = 1, None
        else:
            trigger_kind = 1 if trigger == "invoke" else 2
            trigger_char = trigger if len(trigger) == 1 else None
        params = intel.position_params(path, line, char)
        params["context"] = {
            "triggerKind": trigger_kind,
            "triggerCharacter": trigger_char,
        }
        pending = {"path": path, "line": line, "char": char}
        self._pending_completion = pending

        def _on_response(message: dict, _pending=pending) -> None:
            # Two requests can be in flight while the user types: an older
            # answer must not consume (or render into) a newer request's slot.
            if self._pending_completion is not _pending:
                logger.debug("fallback completion: dropped stale response")
                return
            self._on_completion_response(message)

        if self.roslyn.request("textDocument/completion", params, _on_response) is None:
            if self._pending_completion is pending:
                self._pending_completion = None

    def _on_completion_response(self, message: dict) -> None:
        pending, self._pending_completion = self._pending_completion, None
        if pending is None or self.completion_popup is None:
            logger.debug(f"fallback completion: dropped (pending={pending is not None}, "
                  f"popup={self.completion_popup is not None})")
            return
        if message.get("error"):
            logger.debug(f"fallback completion error: {message.get('error')}")
            return
        path = pending["path"]
        doc = self._find_doc(path)
        if doc is None:
            logger.debug(f"fallback completion: no buffer for {path}")
            return
        text = buffer_text(doc)
        # The buffer may have moved while Roslyn answered; parse against the
        # request offset but filter against what is typed NOW.
        offset = intel.position_to_offset(text, pending["line"], pending["char"])
        items = intel.parse_completion(message, text, offset)
        logger.debug(f"fallback completion: {len(items)} items for {path}")
        if not items:
            return
        try:
            cur_offset = cursor_offset(doc)
            prefix, _start = intel.prefix_at(text, cur_offset)
            cur_line, cur_char = intel.offset_to_position(text, cur_offset)
        except Exception:
            prefix, cur_line, cur_char = "", pending["line"], pending["char"]
        view = self._safe(lambda: self.window.get_active_view())
        if view is None:
            return
        self.completion_popup.set_items(items, prefix)
        if not self.completion_popup.has_items():
            return  # typed past every match while waiting: stay hidden
        self.completion_popup.show_at_view(view, cur_line, cur_char)
        self._hook_completion_forward(view)
        self._pending_completion = {"path": path}

    def _refilter_completion(self) -> None:
        """Narrow the visible list to the identifier at the cursor."""
        self._refilter_scheduled = False
        popup = self.completion_popup
        if popup is None or not self._completion_visible():
            return
        pending = self._pending_completion or {}
        path = pending.get("path") or self._active_path()
        if not path:
            return
        doc = self._find_doc(path)
        if doc is None:
            self._dismiss_completion()
            return
        try:
            text = buffer_text(doc)
            cur_off = cursor_offset(doc)
            prefix, _start = intel.prefix_at(text, cur_off)
        except Exception:
            return
        # Cursor left the word (space, ')', cursor move, ...) -> dismiss,
        # like VSCode. Commit chars are handled pre-insert in
        # _forward_completion_key so they never reach this branch.
        # Exception: right after a member trigger (``Console.|``) the
        # prefix is empty but VSCode shows the full member list.
        if not prefix:
            try:
                prev = text[cur_off - 1] if cur_off > 0 else ""
            except Exception:
                prev = ""
            if prev in (".", "<", "("):
                popup.update_filter("")
                return
            self._dismiss_completion()
            return
        popup.update_filter(prefix)
        if not popup.has_items():
            # Keep the popup up but uncommittable while nothing matches;
            # further typing may match again, Escape/space dismisses.
            pass

    @staticmethod
    def _completion_range(item, text: str, cur: int) -> tuple:
        """(start, end) offsets to replace when applying a completion item.

        Prefers the LSP ``replace`` range, then the ``insert`` range, when
        it still intersects the cursor; otherwise the identifier at the
        cursor (the user typed on while the popup was up). Bounds are
        clamped to the buffer so stale ranges cannot corrupt text.
        """
        fallback_start, fallback_cur = intel.prefix_at(text, cur)[1], cur
        candidates = []
        try:
            candidates.append((int(item.replace_start), int(item.replace_end)))
        except (TypeError, ValueError, AttributeError):
            pass
        try:
            candidates.append((int(item.insert_start), int(item.insert_end)))
        except (TypeError, ValueError, AttributeError):
            pass
        for s, e in candidates:
            if 0 <= s <= cur <= e <= len(text) and e > s:
                return s, e
            if 0 <= s <= cur <= len(text) and e >= cur:
                return s, cur
        logger.debug("completion apply: stored range stale, word fallback")
        return max(0, fallback_start), max(0, fallback_cur)

    def _apply_completion(self, item) -> None:
        pending = self._pending_completion or {}
        path = pending.get("path") or self._active_path()
        if not path:
            return
        doc = self._find_doc(path)
        if doc is None:
            return
        # Prefer the LSP ranges carried by the item (replace, else insert);
        # fall back to the CURRENT word prefix only when the stored range
        # no longer intersects the cursor (stale: the user typed on while
        # filtering, or the buffer changed under the request).
        try:
            text = buffer_text(doc)
            cur = cursor_offset(doc)
        except Exception as e:
            logger.debug(f"completion apply: buffer read failed: {e!r}")
            return
        start, end = self._completion_range(item, text, cur)
        try:
            doc.begin_user_action()
            start_iter = doc.get_iter_at_offset(max(0, start))
            end_iter = doc.get_iter_at_offset(max(0, end))
            doc.delete(start_iter, end_iter)
            at = doc.get_iter_at_offset(max(0, start))
            doc.insert(at, item.insert_text)
            suffix = intel.completion_suffix(getattr(item, "kind", 0), item.insert_text)
            if suffix:
                try:
                    ahead = at.copy()
                    ahead.forward_char()
                    if ahead.get_offset() > at.get_offset():
                        existing = doc.get_text(at, ahead, True)
                    else:
                        existing = ""
                except Exception:
                    existing = ""
                if existing != suffix:
                    doc.insert(at, suffix)
                    if suffix == "(":
                        try:
                            doc.insert(at, ")")
                            doc.place_cursor(
                                doc.get_iter_at_offset(at.get_offset() - 1)
                            )
                        except Exception as e:
                            logger.debug(f"completion pair insert failed: {e!r}")
            doc.end_user_action()
        except Exception as e:
            logger.debug(f"completion apply failed: {e!r}")
            try:
                doc.end_user_action()
            except Exception as e:
                logger.debug(f"completion apply cleanup failed: {e!r}")
        self._dismiss_completion()

    def _dismiss_completion(self) -> None:
        popup, self._pending_completion = self.completion_popup, None
        self._disconnect_completion_forward()
        if popup is not None:
            try:
                # disconnect first so the hide() emission cannot recurse.
                popup.hide()
            except Exception:
                pass

    def _clear_pending_completion(self) -> None:
        self._pending_completion = None
        self._disconnect_completion_forward()

    def _hook_completion_forward(self, view) -> None:
        """Forward navigation keys from the editor view to a visible popup.

        Guarantees keyboard navigation even when the window manager refuses
        focus to the popup (common on Wayland, occasional on X11).
        """
        self._disconnect_completion_forward()
        try:
            handler_id = view.connect("key-press-event", self._forward_completion_key)
            self._completion_forward = (view, handler_id)
        except Exception as e:
            logger.debug(f"completion forward hook failed: {e!r}")

    def _disconnect_completion_forward(self) -> None:
        hook, self._completion_forward = self._completion_forward, None
        if hook is None:
            return
        view, handler_id = hook
        try:
            view.disconnect(handler_id)
        except Exception:
            pass

    def _schedule_refilter(self) -> None:
        """Refilter after the pending keystroke is inserted/deleted.

        Coalesced: a typing burst would otherwise queue one idle per
        keystroke, each re-snapshotting the whole buffer for a prefix it
        only needs the cursor's line for.
        """
        if getattr(self, "_refilter_scheduled", False):
            return
        try:
            self._refilter_scheduled = True
            GLib.idle_add(self._refilter_completion)
        except Exception:
            self._refilter_scheduled = False
            try:
                self._refilter_completion()
            except Exception:
                pass

    def _forward_completion_key(self, _view, event) -> bool:
        """Editor-view key handling while the popup is visible.

        The editor keeps focus; this runs pre-insert on key-press:
        navigation is consumed, commit chars accept-then-insert (return
        False), plain text falls through and refilters afterwards, and
        cursor-moving / boundary keys dismiss.
        """
        popup = self.completion_popup
        if popup is None:
            return False
        try:
            visible = popup.get_visible()
        except Exception:
            visible = False
        if not visible:
            return False
        try:
            name = Gdk.keyval_name(event.keyval) or ""
        except Exception:
            return False
        if name in NAV_KEYS:
            try:
                consumed = bool(popup.handle_nav_key(name))
                if name == "Escape":
                    self._clear_pending_completion()
                elif name in ("Return", "KP_Enter", "Tab"):
                    # activate_selected() emits item-activated ->
                    # _apply_completion -> _dismiss_completion.
                    pass
                return consumed
            except Exception:
                return False
        # Commit characters: accept the current item first, then let the
        # keystroke insert normally (so '.' chains into member access).
        # VSCode uses the selected item's LSP commitCharacters when it
        # sends any, else the C# defaults.
        try:
            typed = Gdk.keyval_to_unicode(event.keyval)
            char = chr(typed) if typed else ""
        except Exception:
            char = ""
        try:
            commit_chars = popup.selected_commit_chars()
        except Exception:
            commit_chars = COMMIT_CHARS
        if char and char in commit_chars:
            try:
                if popup.has_items():
                    popup.activate_selected()
                else:
                    self._dismiss_completion()
            except Exception:
                pass
            return False
        if name in ("BackSpace", "Delete", "KP_Delete"):
            self._schedule_refilter()
            return False
        if name in ("Left", "KP_Left", "Right", "KP_Right"):
            # Cursor moves -> completion no longer applies (VSCode hides
            # the widget). Home/End are handled above as navigation.
            self._dismiss_completion()
            return False
        if char and intel.is_identifier_char(char):
            # Let it insert; views.py key-release will refilter, but also
            # cover the case where key-release is missed.
            self._schedule_refilter()
            return False
        if char:
            # Punctuation/space/etc ends the session (commit already
            # handled above). Let it insert, then hide.
            try:
                GLib.idle_add(self._dismiss_completion)
            except Exception:
                pass
            return False
        return False

    # -- navigation / hover ------------------------------------------
    def _jump_to(self, path: str, line0: int, char0: int = 0) -> None:
        try:
            location = Gio.File.new_for_path(path)
        except Exception as e:
            logger.debug(f"jump: bad path {path}: {e!r}")
            return
        existing = None
        try:
            existing = self.window.get_tab_from_location(location)
        except Exception:
            existing = None
        try:
            if existing is not None:
                self.window.set_active_tab(existing)
            else:
                # ThorWindow signature: (location, encoding=None,
                # line_pos=-1, col_pos=-1, create=True, jump_to=True).
                self.window.create_tab_from_location(
                    location,
                    encoding=None,
                    line_pos=max(0, line0),
                    col_pos=max(0, char0),
                    create=True,
                    jump_to=True,
                )
        except Exception as e:
            logger.debug(f"jump open failed: {e!r}")
            if self.output is not None:
                self.output.append(f"Cannot open {path}: {e}\n")
            return

        def _go() -> bool:
            doc = self._find_doc(path)
            if doc is None:
                return False
            try:
                text = buffer_text(doc)
                off = intel.position_to_offset(text, max(0, line0), max(0, char0))
            except Exception:
                off = -1
            try:
                if off >= 0:
                    doc.place_cursor(doc.get_iter_at_offset(off))
                else:
                    doc.goto_line(max(0, line0))
            except Exception:
                try:
                    doc.place_cursor(doc.get_iter_at_line(max(0, line0)))
                except Exception:
                    return False
            try:
                view = self.window.get_active_view()
                if view is not None:
                    view.scroll_to_cursor()
                    view.grab_focus()
            except Exception:
                pass
            return False

        try:
            GLib.idle_add(_go)
        except Exception:
            _go()

    def _open_file(self, path: str) -> None:
        self._jump_to(path, 0, 0)

    def _on_goto_definition(self, _tracker, path: str, line: int, char: int) -> None:
        if not self._roslyn_ready():
            self._warn_completion_once(
                f"nav-not-ready-{getattr(self.roslyn, 'state', '?')}",
                f"Roslyn server is {getattr(self.roslyn, 'state', '?')!r}; go-to-definition skipped for {path}.",
                "Roslyn not ready — definition unavailable yet.",
            )
            return
        self._flush_completion_doc(path)
        self.roslyn.request(
            "textDocument/definition",
            intel.position_params(path, line, char),
            self._on_definition_response,
        )

    def _problems_message(self, path: str) -> str:
        """Short distinguishing message for a definition/reference row.

        The File column already shows the basename, so the message shows
        the workspace-relative path (disambiguates same-named files in
        different folders); outside the workspace it falls back to
        parent-dir + basename instead of the full absolute path.
        """
        try:
            root = getattr(self._model, "root_dir", "") or ""
            if root:
                rel = os.path.relpath(path, root)
                if rel and not rel.startswith(".."):
                    return rel
        except Exception as e:
            logger.debug(f"problems message relpath failed: {e!r}")
        try:
            parent = os.path.basename(os.path.dirname(path))
            base = os.path.basename(path)
            return f"{parent}/{base}" if parent else base
        except Exception:
            return os.path.basename(path)

    def _on_definition_response(self, message: dict) -> None:
        targets = intel.parse_locations(message)
        if not targets:
            if self.output is not None:
                self.output.set_status("No definition found.")
            return
        first = targets[0]
        self._jump_to(first.path, first.line, first.character)
        if len(targets) > 1 and self.output is not None:
            self.output.set_problems(
                [("definition", os.path.basename(t.path), t.line + 1, self._problems_message(t.path), t.path) for t in targets]
            )
            self.output.show_problems()

    def _on_find_references(self, _tracker, path: str, line: int, char: int) -> None:
        if not self._roslyn_ready():
            self._warn_completion_once(
                f"nav-not-ready-{getattr(self.roslyn, 'state', '?')}",
                f"Roslyn server is {getattr(self.roslyn, 'state', '?')!r}; find-references skipped for {path}.",
                "Roslyn not ready — references unavailable yet.",
            )
            return
        self._flush_completion_doc(path)
        params = intel.position_params(path, line, char)
        params["context"] = {"includeDeclaration": True}
        self.roslyn.request("textDocument/references", params, self._on_references_response)

    def _on_references_response(self, message: dict) -> None:
        targets = intel.parse_locations(message)
        if self.output is None:
            return
        if not targets:
            self.output.set_status("No references found.")
            return
        self.output.set_problems(
            [("reference", os.path.basename(t.path), t.line + 1, self._problems_message(t.path), t.path) for t in targets]
        )
        self.output.show_problems()
        self.output.set_status(f"{len(targets)} reference(s).")

    def _on_hover_request(self, _tracker, path: str, line: int, char: int) -> None:
        if not self._roslyn_ready() or self.tracker is None:
            return
        self._flush_completion_doc(path)
        try:
            seq = self.tracker.last_hover_seq()
        except Exception:
            seq = None
        self.roslyn.request(
            "textDocument/hover",
            intel.position_params(path, line, char),
            lambda message: self._on_hover_response(message, seq),
        )

    def _on_hover_response(self, message: dict, seq) -> None:
        tracker = self.tracker
        if tracker is None:
            return
        try:
            text = intel.parse_hover(message)
        except Exception as e:
            logger.debug(f"hover parse failed: {e!r}")
            return
        try:
            tracker.show_hover_text(text, seq)
        except TypeError:
            # Older/test doubles with show_hover_text(text): retry unpaired.
            try:
                tracker.show_hover_text(text)
            except Exception as e:
                logger.debug(f"hover deliver failed: {e!r}")
        except Exception as e:
            logger.debug(f"hover deliver failed: {e!r}")

    # -- formatting / code actions -----------------------------------
    def _format_doc_path(self, path: str) -> None:
        if not self._roslyn_ready():
            self._warn_completion_once(
                f"nav-not-ready-{getattr(self.roslyn, 'state', '?')}",
                f"Roslyn server is {getattr(self.roslyn, 'state', '?')!r}; format skipped for {path}.",
                "Roslyn not ready — format unavailable yet.",
            )
            return
        self._flush_completion_doc(path)
        doc = self._find_doc(path)
        if doc is None:
            return
        params = {
            "textDocument": {"uri": roslyn_mod.file_uri(path)},
            "options": {"tabSize": 4, "insertSpaces": True},
        }
        self.roslyn.request("textDocument/formatting", params, lambda m: self._on_format_response(path, m))

    @staticmethod
    def _verified_ops(ops: list, length: int, what: str) -> list:
        """Drop edit ops whose ranges no longer fit ``length`` chars.

        LSP edits are computed against older buffer text; the user may
        have typed since. Out-of-bounds or reversed ranges are dropped
        with a debug log instead of corrupting the buffer. Callers apply
        the survivors end-first (``text_edits_to_ops`` order) so earlier
        offsets stay valid.
        """
        good = []
        for op in ops or []:
            try:
                s, e = int(op.start), int(op.end)
            except (TypeError, ValueError, AttributeError) as ex:
                logger.debug(f"{what}: dropping op with bad offsets: {ex!r}")
                continue
            if not (0 <= s <= e <= length):
                logger.debug(f"{what}: dropping op {s}-{e} outside 0-{length}")
                continue
            good.append(op)
        return good

    def _on_format_response(self, path: str, message: dict) -> None:
        doc = self._find_doc(path)
        if doc is None:
            return
        edits = (message or {}).get("result") or []
        if not edits:
            if self.output is not None:
                self.output.set_status("Already formatted.")
            return
        ops = intel.text_edits_to_ops(edits, buffer_text(doc))
        if not ops:
            return
        # Re-read: the buffer may have moved since the request. Ranges
        # are re-anchored against current text; stale ones are dropped.
        current = buffer_text(doc)
        ops = self._verified_ops(ops, len(current), "format")
        if not ops:
            logger.debug("format: all ops stale, nothing applied")
            return
        try:
            doc.begin_user_action()
            for op in ops:
                start = doc.get_iter_at_offset(max(0, op.start))
                end = doc.get_iter_at_offset(max(0, op.end))
                doc.delete(start, end)
                doc.insert(doc.get_iter_at_offset(max(0, op.start)), op.new_text)
            doc.end_user_action()
            if self.output is not None:
                self.output.set_status("Document formatted.")
        except Exception as e:
            logger.debug(f"format apply failed: {e!r}")
            try:
                doc.end_user_action()
            except Exception as ce:
                logger.debug(f"format cleanup failed: {ce!r}")

    def _on_code_action_request(self, _tracker, path: str, line: int, char: int) -> None:
        if not self._roslyn_ready():
            return
        uri = roslyn_mod.file_uri(path)
        diags_raw = []
        for diag in self.diagnostics.get(uri, []):
            if diag.line == line:
                diags_raw.append(
                    {
                        "range": {
                            "start": {"line": diag.line, "character": diag.character},
                            "end": {"line": diag.line, "character": diag.character},
                        },
                        "severity": diag.severity,
                        "message": diag.message,
                    }
                )
        params = intel.position_params(path, line, char)
        params["context"] = {"diagnostics": diags_raw}
        self.roslyn.request("textDocument/codeAction", params, lambda m: self._on_code_actions(path, m))

    def _on_code_actions(self, path: str, message: dict) -> None:
        actions = intel.parse_code_actions(message)
        if not actions:
            if self.output is not None:
                self.output.set_status("No quick fixes available.")
            return
        menu = Gtk.Menu()
        for action in actions[:12]:
            item = Gtk.MenuItem.new_with_label(action.title)
            item.connect("activate", lambda _i, a=action: self._apply_code_action(path, a))
            menu.append(item)
        menu.show_all()
        try:
            menu.popup_at_pointer(None)
        except Exception as e:
            logger.debug(f"code action menu failed: {e!r}")

    def _apply_code_action(self, path: str, action) -> None:
        if action.edit:
            self._apply_workspace_edit(action.edit)
        elif action.needs_resolve:
            self.roslyn.request(
                "codeAction/resolve", {"title": action.title, "data": action.data, "kind": action.kind},
                lambda m: self._apply_resolved_action(path, m),
            )
        elif action.command:
            cmd = action.command
            self.roslyn.request(
                "workspace/executeCommand",
                {"command": cmd.get("command", ""), "arguments": cmd.get("arguments", [])},
                lambda m: self._status_from_command(m),
            )
        else:
            if self.output is not None:
                self.output.set_status(f"No edit for: {action.title}")

    def _apply_resolved_action(self, path: str, message: dict) -> None:
        result = (message or {}).get("result") or {}
        edit = result.get("edit")
        if edit:
            self._apply_workspace_edit(edit)
        else:
            command = result.get("command")
            if command:
                self.roslyn.request(
                    "workspace/executeCommand",
                    {"command": command.get("command", ""), "arguments": command.get("arguments", [])},
                    lambda m: self._status_from_command(m),
                )

    def _status_from_command(self, message: dict) -> None:
        if self.output is None:
            return
        if (message or {}).get("error"):
            self.output.set_status(f"Command failed: {(message.get('error') or {}).get('message', '?')}")
        else:
            self.output.set_status("Command applied.")

    def _apply_workspace_edit(self, edit: dict) -> None:
        texts: dict[str, str] = {}
        try:
            for doc in self.window.get_documents():
                p = doc_path(doc)
                if p:
                    texts[roslyn_mod.file_uri(p)] = buffer_text(doc)
        except Exception as e:
            logger.debug(f"workspace edit doc scan failed: {e!r}")
        ops_by_uri = intel.workspace_edit_to_ops(edit, texts)
        applied = 0
        for uri, ops in ops_by_uri.items():
            if not ops:
                continue
            fpath = intel.uri_to_path(uri)
            doc = self._find_doc(fpath)
            try:
                if doc is not None:
                    current = buffer_text(doc)
                    ops = self._verified_ops(ops, len(current), f"workspace-edit {fpath}")
                    if not ops:
                        logger.debug(f"workspace edit: all ops stale for {fpath}")
                        continue
                    doc.begin_user_action()
                    for op in ops:
                        start = doc.get_iter_at_offset(max(0, op.start))
                        end = doc.get_iter_at_offset(max(0, op.end))
                        doc.delete(start, end)
                        doc.insert(doc.get_iter_at_offset(max(0, op.start)), op.new_text)
                    doc.end_user_action()
                    applied += 1
                elif fpath and os.path.isfile(fpath):
                    with open(fpath, "r", encoding="utf-8") as f:
                        current = f.read()
                    ops = self._verified_ops(ops, len(current), f"workspace-edit {fpath}")
                    if not ops:
                        logger.debug(f"workspace edit: all ops stale for {fpath}")
                        continue
                    new_text = intel.apply_ops_to_text(current, ops)
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(new_text)
                    applied += 1
            except OSError as e:
                logger.debug(f"workspace edit file failed for {fpath}: {e!r}")
                try:
                    if doc is not None:
                        doc.end_user_action()
                except Exception as ce:
                    logger.debug(f"workspace edit cleanup failed: {ce!r}")
            except Exception as e:
                logger.debug(f"workspace edit apply failed for {fpath}: {e!r}")
                try:
                    if doc is not None:
                        doc.end_user_action()
                except Exception as ce:
                    logger.debug(f"workspace edit cleanup failed: {ce!r}")
        if self.output is not None:
            self.output.set_status(f"Applied edits to {applied} file(s).")

    # -- build / run / test actions ----------------------------------
    def _append_output(self, text: str) -> None:
        """Batched OutputView append (mark-leak adjacent).

        ``OutputView._append`` creates a Gtk TextMark per call; streaming
        a build line-by-line would create thousands of marks. Coalesce
        everything pending into one append per main-loop tick instead.
        Safe from any thread.
        """
        if self.output is None:
            return
        schedule = False
        try:
            with self._output_lock:
                self._output_pending.append(text)
                if not self._output_scheduled:
                    self._output_scheduled = True
                    schedule = True
        except Exception as e:
            logger.debug(f"output batch failed: {e!r}")
            return
        if schedule:
            try:
                GLib.idle_add(self._flush_output)
            except Exception as e:
                logger.debug(f"output flush schedule failed: {e!r}")

    def _flush_output(self) -> bool:
        try:
            with self._output_lock:
                chunk = "".join(self._output_pending)
                self._output_pending.clear()
                self._output_scheduled = False
        except Exception as e:
            logger.debug(f"output flush failed: {e!r}")
            return False
        if chunk and self.output is not None:
            try:
                self.output.append(chunk)
            except Exception as e:
                logger.debug(f"output append failed: {e!r}")
        return False

    def _track_stream(self, handle) -> None:
        try:
            self._stream_handles.append(handle)
        except Exception as e:
            logger.debug(f"stream track failed: {e!r}")

    def _untrack_stream(self, handle) -> None:
        try:
            if handle in self._stream_handles:
                self._stream_handles.remove(handle)
        except Exception as e:
            logger.debug(f"stream untrack failed: {e!r}")

    def _run_stream(self, argv: list[str], cwd: str, label: str) -> None:
        if self.output is None:
            return
        self._append_output(f"\n$ {' '.join(argv)}\n")
        self.output.set_status(label + "…")

        def on_line(_stream: str, text: str) -> None:
            self._append_output(text)

        def on_done(returncode: int) -> None:
            def _done() -> None:
                self._untrack_stream(handle)
                self._flush_output()
                if self.output is not None:
                    self._append_output(f"\n(exit {returncode})\n")
                    self.output.set_status(f"{label}: exit {returncode}")
                if "Build" in label or "Restore" in label:
                    self._schedule_refresh()
                return False

            try:
                GLib.idle_add(_done)
            except Exception as e:
                logger.debug(f"stream done schedule failed: {e!r}")

        try:
            handle = dotnet_cli.run_streaming(argv, cwd, on_line, on_done)
        except Exception as e:
            logger.debug(f"stream start failed: {e!r}")
            if self.output is not None:
                self.output.set_status(f"{label} failed to start.")
            return
        self._track_stream(handle)

    def _build_solution(self) -> None:
        if not self._model:
            if self.output is not None:
                self.output.set_status("No solution loaded — nothing to build.")
            return
        target = self._model.path or self._model.root_dir
        self._run_stream([self._dotnet(), "build", target], self._model.root_dir, "Build")

    def _build_project(self, project: str) -> None:
        root = self._model.root_dir if self._model else os.path.dirname(project)
        self._run_stream([self._dotnet(), "build", project], root, "Build project")

    def _run_project(self, project: str) -> None:
        root = self._model.root_dir if self._model else os.path.dirname(project)
        self._run_stream([self._dotnet(), "run", "--project", project], root, "Run")

    def _restore(self) -> None:
        if not self._model:
            if self.output is not None:
                self.output.set_status("No solution loaded — nothing to restore.")
            return
        target = self._model.path or self._model.root_dir
        self._run_stream([self._dotnet(), "restore", target], self._model.root_dir, "Restore")

    # -- test explorer -----------------------------------------------
    def _test_projects(self) -> list:
        if not self._model:
            return []
        return [p for p in self._model.projects if p.is_test_project] or list(self._model.projects)

    def _refresh_tests(self) -> None:
        if self.testpanel is None or self._discovering_tests:
            return
        projects = self._test_projects()
        if not projects:
            self.testpanel.set_status("No projects in solution.")
            return
        self._discovering_tests = True
        self.testpanel.set_projects([(p.name, p.path) for p in projects])
        dotnet = self._dotnet()

        def _worker() -> None:
            results: dict[str, list[str]] = {}
            for project in projects:
                try:
                    results[project.path] = testing_mod.list_tests(dotnet, project.path)
                except Exception as e:
                    logger.debug(f"test discovery failed for {project.path}: {e!r}")
                    results[project.path] = []

            def _done() -> bool:
                self._discovering_tests = False
                if self.testpanel is not None:
                    self.testpanel.set_tests(results)
                return False

            GLib.idle_add(_done)

        threading.Thread(target=_worker, name="thor-csharp-test-discover", daemon=True).start()

    def _run_test_project(self, project: str, fqn: str | None = None) -> None:
        if self.output is None or self.testpanel is None:
            return
        root = self._model.root_dir if self._model else os.path.dirname(project)
        argv = [self._dotnet(), "test", project, "--nologo", "-v", "n"]
        if fqn:
            argv += ["--filter", f"FullyQualifiedName={fqn}"]
        label = f"Test {os.path.basename(project)}{f':{fqn}' if fqn else ''}"
        self.testpanel.mark_running(project, fqn)
        self._append_output(f"\n$ {' '.join(argv)}\n")
        self.output.set_status(label + "…")
        lines: list[str] = []

        def on_line(_stream: str, text: str) -> None:
            lines.append(text)
            self._append_output(text)

        def on_done(returncode: int) -> None:
            def _done() -> bool:
                self._untrack_stream(handle)
                self._flush_output()
                run = testing_mod.parse_test_output("".join(lines), project)
                outcomes = {c.name: c.outcome for c in run.cases}
                if self.testpanel is not None:
                    if outcomes:
                        self.testpanel.apply_results(project, outcomes)
                    else:
                        summary = f"exit {returncode}"
                        if run.total:
                            summary = f"{run.passed} passed, {run.failed} failed, {run.skipped} skipped"
                        self.testpanel.set_status(f"{os.path.basename(project)}: {summary}")
                if self.output is not None:
                    self._append_output(f"\n(exit {returncode})\n")
                    if run.total:
                        self.output.set_status(
                            f"{label}: {run.passed} passed, {run.failed} failed, "
                            f"{run.skipped} skipped (exit {returncode})"
                        )
                    else:
                        self.output.set_status(f"{label}: exit {returncode}")
                return False

            try:
                GLib.idle_add(_done)
            except Exception as e:
                logger.debug(f"test done schedule failed: {e!r}")

        try:
            handle = dotnet_cli.run_streaming(argv, root, on_line, on_done)
        except Exception as e:
            logger.debug(f"test stream start failed: {e!r}")
            self.testpanel.set_status(f"{label}: failed to start.")
            return
        self._track_stream(handle)

    def _run_all_tests(self) -> None:
        projects = [p.path for p in self._test_projects()]
        if not projects:
            if self.output is not None:
                self.output.set_status("No test projects found.")
            elif self.testpanel is not None:
                self.testpanel.set_status("No test projects found.")
            return

        def _chain(index: int) -> None:
            if index >= len(projects):
                return
            project = projects[index]
            root = self._model.root_dir if self._model else os.path.dirname(project)
            argv = [self._dotnet(), "test", project, "--nologo", "-v", "n"]
            self._append_output(f"\n$ {' '.join(argv)}\n")
            if self.testpanel is not None:
                self.testpanel.mark_running(project)
            lines: list[str] = []

            def on_line(_stream: str, text: str) -> None:
                lines.append(text)
                self._append_output(text)

            def on_done(_rc: int) -> None:
                def _done() -> bool:
                    self._untrack_stream(handle)
                    self._flush_output()
                    run = testing_mod.parse_test_output("".join(lines), project)
                    if self.testpanel is not None:
                        outcomes = {c.name: c.outcome for c in run.cases}
                        if outcomes:
                            self.testpanel.apply_results(project, outcomes)
                    _chain(index + 1)
                    return False

                try:
                    GLib.idle_add(_done)
                except Exception as e:
                    logger.debug(f"test chain schedule failed: {e!r}")

            try:
                handle = dotnet_cli.run_streaming(argv, root, on_line, on_done)
            except Exception as e:
                logger.debug(f"test chain start failed: {e!r}")
                if self.testpanel is not None:
                    self.testpanel.set_status(f"{os.path.basename(project)}: failed to start.")
                return
            self._track_stream(handle)

        _chain(0)


def detach(window) -> None:
    """Deactivate the C# feature on *window* (drop panels, timers, procs)."""
    if window is None:
        return
    manager = getattr(window, "_csharp_manager", None)
    if manager is None:
        return
    try:
        manager.do_deactivate()
    except Exception as e:
        logger.debug(f"csharp deactivate failed: {e!r}", exc_info=True)
    try:
        delattr(window, "_csharp_manager")
    except Exception:
        pass


def attach(window, initial_folder=None):
    """Entry point for Thor host — wire C# panels to ThorWindow.

    Called by thor/host.py after window creation.  Soft-fails when Gtk
    is unavailable or the window is None so `import thor.csharp` stays
    headless-safe.
    """
    if window is None:
        logger.debug("attach: no window — headless, skipping")
        return None
    if not _GTK_AVAILABLE or Gtk is None:
        logger.debug("attach: Gtk unavailable — headless, skipping C# UI")
        return None
    try:
        manager = CSharpManager(window=window, initial_folder=initial_folder)
        # Expose on window so host/tests can reach it
        try:
            window._csharp_manager = manager  # type: ignore[attr-defined]
        except Exception:
            pass
        # Activate the manager
        try:
            manager.do_activate()
        except Exception as e:
            # fallback to activate
            try:
                manager.activate()
            except Exception:
                logger.debug(f"attach activate failed: {e!r}")
                logger.exception("attach activate failed")
        return manager
    except Exception as e:
        logger.debug(f"attach failed: {e!r}")
        logger.exception("attach failed")
        return None

