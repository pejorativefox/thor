"""Roslyn language server lifecycle.

Handles the Roslyn-specific startup quirks:
- Launched with --stdio (+ optional --autoLoadProjects / --extensionLogDirectory).
- Standard LSP `initialize` -> `initialized`, then a Roslyn `solution/open`
  notification carrying the discovered .sln (mirrors csharp-language-server /
  roslyn.nvim behavior for non-VSCode clients).
- Tracks open documents (didOpen/didChange/didClose) and forwards
  publishDiagnostics to the UI layer.

GTK integration is deliberately narrow: an optional `ui_dispatch` callable
(default: call directly) lets the plugin marshal callbacks onto the GTK main
loop with GLib.idle_add while unit tests inject a synchronous dispatcher.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Dict, List, Optional

from . import dotnet_cli
from .lsp_transport import LspTransport, PendingRequests

logger = logging.getLogger(__name__)

ROSSLYN_SOLUTION_OPEN = "solution/open"

DiagnosticsCallback = Callable[[str, list], None]
ErrorCallback = Callable[[str], None]


def resolve_server_command(configured: str) -> Optional[List[str]]:
    """Turn the configured roslyn path into an argv list, or None if missing."""
    if not configured:
        return None
    candidate = os.path.expanduser(configured)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return [candidate]
    if os.path.sep not in configured:
        found = dotnet_cli.resolve_dotnet("dotnet")
        import shutil

        which = shutil.which(configured)
        if which:
            return [which]
        # `dotnet tool install -g roslyn-language-server` drops it in ~/.dotnet/tools.
        fallback = os.path.expanduser(f"~/.dotnet/tools/{configured}")
        if os.path.isfile(fallback):
            return [fallback]
        logger.debug(f"roslyn server not found: {configured!r}")
        return None
    logger.debug(f"roslyn server not executable: {candidate!r}")
    return None


def file_uri(path: str) -> str:
    from pathlib import Path

    return Path(os.path.abspath(path)).as_uri()


def full_document_range(text: str) -> dict:
    """LSP range covering all of `text` (for full-document sync changes)."""
    lines = text.split("\n")
    return {
        "start": {"line": 0, "character": 0},
        "end": {"line": len(lines) - 1, "character": len(lines[-1])},
    }


class RoslynManager:
    def __init__(
        self,
        on_diagnostics: Optional[DiagnosticsCallback] = None,
        ui_dispatch: Optional[Callable[[Callable[[], None]], None]] = None,
        on_error: Optional[ErrorCallback] = None,
        on_ready: Optional[Callable[[], None]] = None,
    ) -> None:
        self.on_diagnostics = on_diagnostics
        self.ui_dispatch = ui_dispatch or (lambda fn: fn())
        self.on_error = on_error
        self.on_ready = on_ready
        self.transport: Optional[LspTransport] = None
        self.pending = PendingRequests()
        self._lock = threading.Lock()
        self.open_docs: Dict[str, int] = {}
        self._doc_text: Dict[str, str] = {}
        self.solution_path: Optional[str] = None
        self.workspace_root: Optional[str] = None
        self.server_argv: Optional[List[str]] = None
        self.stderr_log_path: Optional[str] = None
        self._log_dir: Optional[str] = None
        self._log_level: str = "Information"
        self.state = "stopped"
        self.last_error = ""
        self.capabilities: dict = {}

    # -- lifecycle -----------------------------------------------------
    def start(
        self,
        solution_path: Optional[str],
        workspace_root: str,
        server_argv: List[str],
        log_dir: Optional[str] = None,
        log_level: str = "Information",
        stderr_log_path: Optional[str] = None,
    ) -> bool:
        with self._lock:
            if self.transport is not None:
                logger.debug("RoslynManager.start: already running")
                return True
            self.solution_path = solution_path
            self.workspace_root = workspace_root
            self.stderr_log_path = stderr_log_path
            self._log_dir = log_dir
            self._log_level = log_level
            self.last_error = ""
            argv = list(server_argv) + ["--stdio", "--logLevel", log_level]
            if log_dir:
                argv += ["--extensionLogDirectory", log_dir]
            # Always auto-load too: solution/open with a .slnx (or an
            # older server) may not establish the workspace on its own.
            argv += ["--autoLoadProjects"]
            self.server_argv = argv
            try:
                self.transport = LspTransport(
                    argv,
                    self._on_message,
                    on_exit=self._on_transport_exit,
                    stderr_log_path=stderr_log_path,
                )
            except FileNotFoundError:
                logger.warning(f"RoslynManager: server binary missing: {argv[0]!r}")
                self.transport = None
                self.state = "error"
                self.last_error = f"Roslyn language server not found: {argv[0]!r}"
                return False
            self.state = "starting"
            self._send_initialize()
            return True

    def stop(self) -> None:
        with self._lock:
            transport, self.transport = self.transport, None
            self.pending.clear()
            self.open_docs.clear()
            self._doc_text.clear()
            self.state = "stopped"
        if transport is not None:
            transport.stop()

    def _on_transport_exit(self, returncode: Optional[int]) -> None:
        """The server died on its own: release it so Refresh can restart."""
        with self._lock:
            transport = self.transport
            if transport is None:
                return  # intentional stop() already handled it
            tail = transport.stderr_tail()
            head = transport.stderr_head()
            self.transport = None
            self.pending.clear()
            self.open_docs.clear()
            self._doc_text.clear()
            self.state = "error"
            log_hint = f" Server log: {self.stderr_log_path}" if self.stderr_log_path else ""
            lines = [f"Roslyn language server exited (code {returncode}).{log_hint}"]
            blob = "\n".join(head + tail)
            if returncode in (134, 139) and "/proc/" in blob:
                lines.append("This crash means the server crawled a symlink into /proc "
                             "(e.g. a Wine dosdevices/z: -> / under the workspace root) "
                             "and hit a dead PID. Open the solution folder directly so "
                             "the workspace root stays narrow.")
            # Lead with the exception (head of stderr), then the tail.
            lines.extend(f"  {line}" for line in head)
            lines.extend(f"  {line}" for line in tail[-8:] if line.strip() and line not in head)
            lines.append("Use C# Solution -> Refresh to restart it.")
            self.last_error = "\n".join(lines)
            message = self.last_error
        logger.warning(message.splitlines()[0] if message else "Roslyn server exited")
        if self.on_error is not None:
            cb = self.on_error
            self.ui_dispatch(lambda: cb(message))

    def restart(self) -> bool:
        with self._lock:
            argv = list(self.server_argv or [])
            sln, root = self.solution_path, self.workspace_root or ""
            log_dir, log_level = self._log_dir, self._log_level
            stderr_log_path = self.stderr_log_path
        self.stop()
        if not argv:
            return False
        # server_argv[0] is the binary; start() rebuilds the flags it owns
        # (--stdio/--logLevel/--extensionLogDirectory/--autoLoadProjects)
        # from the persisted settings above.
        return self.start(
            sln, root, argv[:1],
            log_dir=log_dir, log_level=log_level, stderr_log_path=stderr_log_path,
        )

    # -- LSP messages --------------------------------------------------
    def _send_initialize(self) -> None:
        assert self.transport is not None
        logger.debug(f"roslyn: sending initialize root={self.workspace_root} sln={self.solution_path}")
        root_uri = file_uri(self.workspace_root or os.path.expanduser("~"))
        params = {
            "processId": os.getpid(),
            "clientInfo": {"name": "thor-csharp", "version": "0.1.0"},
            "rootUri": root_uri,
            "rootPath": self.workspace_root,
            "capabilities": {
                "textDocument": {
                    "synchronization": {"didSave": True},
                    "publishDiagnostics": {"relatedInformation": True},
                    "completion": {
                        "completionItem": {
                            "snippetSupport": True,
                            "commitCharactersSupport": True,
                            "documentationFormat": ["markdown", "plaintext"],
                            "resolveSupport": {
                                "properties": [
                                    "documentation",
                                    "detail",
                                    "additionalTextEdits",
                                ]
                            },
                        },
                        "completionItemKind": {"valueSet": list(range(1, 26))},
                        "contextSupport": True,
                    },
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {},
                    "references": {},
                    "codeAction": {},
                    "formatting": {},
                },
                "workspace": {"workspaceFolders": True},
            },
            "workspaceFolders": [{"uri": root_uri, "name": "workspace"}],
        }
        request_id = self.transport.next_id()
        self.pending.add(request_id, self._on_initialize_result)
        self.transport.send(
            {"jsonrpc": "2.0", "id": request_id, "method": "initialize", "params": params}
        )

    def _on_initialize_result(self, message: dict) -> None:
        error = message.get("error")
        result = message.get("result") or {}
        capabilities = result.get("capabilities")
        if error is not None or not isinstance(capabilities, dict):
            detail = error if error is not None else "missing capabilities"
            text = f"Roslyn language server failed to initialize: {detail}"
            with self._lock:
                self.state = "error"
                self.last_error = text
            logger.warning(text)
            if self.on_error is not None:
                cb = self.on_error
                self.ui_dispatch(lambda: cb(text))
            return
        with self._lock:
            self.capabilities = capabilities
            transport = self.transport
            sln = self.solution_path
        logger.debug("Roslyn initialize OK; sending initialized + solution/open")
        if transport is None:
            return
        transport.send_notification("initialized", {})
        if sln:
            transport.send_notification(
                ROSSLYN_SOLUTION_OPEN, {"solution": file_uri(sln)}
            )
        with self._lock:
            self.state = "ready"
        if self.on_ready is not None:
            cb = self.on_ready
            self.ui_dispatch(cb)

    def _on_message(self, message: dict) -> None:
        if "id" in message and ("result" in message or "error" in message):
            try:
                request_id = int(message["id"])
            except (TypeError, ValueError):
                logger.debug(f"roslyn: dropping response with bad id: {message.get('id')!r}")
                return
            callback = self.pending.pop(request_id)
            if callback is not None:
                self.ui_dispatch(lambda: callback(message))
            return
        method = message.get("method", "")
        params = message.get("params", {}) or {}
        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            diagnostics = params.get("diagnostics", [])
            if self.on_diagnostics is not None:
                cb = self.on_diagnostics
                self.ui_dispatch(lambda: cb(uri, diagnostics))
        elif method == "window/showMessage":
            logger.debug(f"roslyn: {params.get('message', '')}")

    # -- document sync -------------------------------------------------
    # Roslyn only tracks C# documents. A didOpen for any other extension
    # is ignored server-side (never tracked), so a later didClose for it
    # is fatal here (Contract.Fail -> SIGABRT, exit -6). Gate everything
    # centrally: no caller can kill the server with a non-C# path.
    def did_open(self, path: str, language_id: str, version: int, text: str) -> None:
        if not path.endswith(".cs"):
            return
        uri = file_uri(path)
        with self._lock:
            transport = self.transport
            if transport is None:
                return
            self.open_docs[uri] = version
            self._doc_text[uri] = text
        transport.send_notification(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": language_id, "version": version, "text": text}},
        )

    def did_change(self, path: str, version: int, text: str) -> None:
        if not path.endswith(".cs"):
            return
        uri = file_uri(path)
        with self._lock:
            transport = self.transport
            if transport is None:
                return
            # Full-document sync MUST carry a range: some Roslyn versions
            # NullReference-crash on rangeless changes (RangeToTextSpan).
            # The range spans the previously sent text (what is being replaced).
            previous = self._doc_text.get(uri, text)
            self.open_docs[uri] = version
            self._doc_text[uri] = text
        change = {
            "range": full_document_range(previous),
            "rangeLength": len(previous),
            "text": text,
        }
        transport.send_notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [change],
            },
        )

    def did_close(self, path: str) -> None:
        if not path.endswith(".cs"):
            return
        uri = file_uri(path)
        with self._lock:
            transport = self.transport
            if transport is None:
                return
            # Never-opened URIs MUST NOT send didClose: this Roslyn version
            # treats it as fatal (Contract.Fail -> SIGABRT, exit -6) instead
            # of a no-op, killing the server for every open tab.
            if uri not in self.open_docs and uri not in self._doc_text:
                return
            self.open_docs.pop(uri, None)
            self._doc_text.pop(uri, None)
        transport.send_notification("textDocument/didClose", {"textDocument": {"uri": uri}})

    def did_save(self, path: str, text: str) -> None:
        if not path.endswith(".cs"):
            return
        uri = file_uri(path)
        with self._lock:
            transport = self.transport
            if transport is None:
                return
        transport.send_notification(
            "textDocument/didSave", {"textDocument": {"uri": uri}, "text": text}
        )


    # -- requests (completion/hover/definition/references/actions/format)
    def request(self, method: str, params: dict, callback) -> Optional[int]:
        with self._lock:
            transport = self.transport
            if transport is None:
                logger.debug(f"RoslynManager.request {method}: server not running")
                return None
        request_id = transport.next_id()
        self.pending.add(request_id, callback)
        transport.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return request_id
