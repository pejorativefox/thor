"""Thin wrappers around the dotnet CLI. Pure-python so unit tests run headless."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    argv: List[str] = field(default_factory=list)


def resolve_dotnet(configured: str = "dotnet") -> Optional[str]:
    """Resolve the dotnet executable honoring explicit paths."""
    try:
        if not configured or configured == "dotnet":
            found = shutil.which("dotnet")
            logger.debug(f"resolve_dotnet default -> {found!r}")
            return found
        expanded = os.path.expanduser(configured)
        if os.path.isfile(expanded):
            if os.access(expanded, os.X_OK):
                return expanded
            logger.debug(f"resolve_dotnet {configured!r}: not executable")
            return None
        found = shutil.which(expanded)
        logger.debug(f"resolve_dotnet {configured!r} -> {found!r}")
        if found is not None:
            return found
        if os.path.exists(expanded):
            # Exists but not runnable (noexec mount, missing loader, ...):
            # report it as unusable rather than crashing the caller.
            logger.debug(f"resolve_dotnet {configured!r}: exists, not executable")
            return None
        return None
    except OSError as e:
        logger.debug(f"resolve_dotnet {configured!r} failed: {e!r}")
        return None


#: Sync-run timeout (s) for solution discovery. Generous: first-run
#: restore checks on cold NuGet caches can take a while; callers that
#: need tighter bounds pass ``timeout`` explicitly.
RUN_SYNC_TIMEOUT_S = 120


def run_sync(argv: List[str], cwd: Optional[str] = None, timeout: int = RUN_SYNC_TIMEOUT_S) -> CommandResult:
    """Blocking run used by solution discovery and tests."""
    logger.debug(f"run_sync: {' '.join(argv)} cwd={cwd}")
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "", argv)
    except FileNotFoundError as e:
        return CommandResult(127, "", str(e), argv)
    except PermissionError as e:
        # 126 = found but not runnable (POSIX convention).
        return CommandResult(126, "", str(e), argv)
    except OSError as e:
        # Noexec mounts, bad loaders, EMFILE, ... : never crash discovery.
        return CommandResult(127, "", str(e), argv)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return CommandResult(124, out, f"timed out after {timeout}s", argv)

@dataclass
class StreamingHandle:
    """Cancellable handle for a :func:`run_streaming` child process.

    Attributes: ``thread`` (worker, daemon), ``cancelled`` (set by
    :meth:`cancel`), ``proc`` (the live Popen once spawned, else None).
    Callbacks (``on_line``/``on_done``) always fire on the worker thread,
    never on the GTK main loop — marshal UI updates via ``GLib.idle_add``.
    :meth:`cancel` is safe to call twice and from any thread; the
    ``on_done`` callback still fires exactly once with the final code.
    """

    thread: threading.Thread
    cancelled: threading.Event = field(default_factory=threading.Event)
    proc: Optional["subprocess.Popen[str]"] = None

    def cancel(self) -> None:
        """Signal stop and kill the child (SIGKILL fallback). No-op if done."""
        self.cancelled.set()
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except OSError as e:
            logger.debug(f"streaming cancel terminate failed: {e!r}")
            return
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                if proc.poll() is None:
                    proc.kill()
            except OSError as e:
                logger.debug(f"streaming cancel kill failed: {e!r}")

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the worker thread (passes ``threading.Thread.join``)."""
        self.thread.join(timeout)


def run_streaming(
    argv: List[str],
    cwd: Optional[str],
    on_line: Callable[[str, str], None],
    on_done: Callable[[int], None],
) -> StreamingHandle:
    """Run a long-lived command (build/test/run) on a worker thread.

    on_line(stream, text) where stream is 'stdout' or 'stderr'.
    on_done(returncode). Both fire on the worker thread; UI callers must
    hop to the main loop (``GLib.idle_add``) before touching widgets.

    stderr is merged into stdout (``STDOUT``): dotnet interleaves MSBuild
    diagnostics across both pipes and two-pipe pumping risks deadlock
    when one pipe's buffer fills. Every line arrives as ``'stdout'``.

    Returns a :class:`StreamingHandle`; call :meth:`cancel` to stop the
    child (used by the plugin deactivate path to reap orphans).
    """
    handle = StreamingHandle(thread=threading.current_thread())  # placeholder, replaced below

    def _worker() -> None:
        logger.debug(f"run_streaming start: {' '.join(argv)}")
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as e:
            on_line("stderr", str(e) + "\n")
            on_done(127)
            return
        except PermissionError as e:
            on_line("stderr", str(e) + "\n")
            on_done(126)
            return
        except OSError as e:
            on_line("stderr", str(e) + "\n")
            on_done(127)
            return
        handle.proc = proc
        if handle.cancelled.is_set():
            handle.cancel()
            on_done(proc.wait())
            return
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if handle.cancelled.is_set():
                    break
                on_line("stdout", line)
        except OSError as e:
            logger.debug(f"run_streaming read failed: {e!r}")
        if handle.cancelled.is_set():
            handle.cancel()
        rc = proc.wait()
        logger.debug(f"run_streaming done rc={rc}: {' '.join(argv)}")
        on_done(rc)

    thread = threading.Thread(target=_worker, name="thor-csharp-dotnet", daemon=True)
    handle.thread = thread
    thread.start()
    return handle


def dotnet_info(dotnet: str = "dotnet") -> CommandResult:
    return run_sync([dotnet, "--info"])
