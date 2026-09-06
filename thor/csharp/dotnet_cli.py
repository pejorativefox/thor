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
    if not configured or configured == "dotnet":
        found = shutil.which("dotnet")
        logger.debug(f"resolve_dotnet default -> {found!r}")
        return found
    expanded = os.path.expanduser(configured)
    if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
        return expanded
    found = shutil.which(expanded)
    logger.debug(f"resolve_dotnet {configured!r} -> {found!r}")
    return found or (expanded if os.path.exists(expanded) else None)


def run_sync(argv: List[str], cwd: Optional[str] = None, timeout: int = 120) -> CommandResult:
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
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return CommandResult(124, out, f"timed out after {timeout}s", argv)


def run_streaming(
    argv: List[str],
    cwd: Optional[str],
    on_line: Callable[[str, str], None],
    on_done: Callable[[int], None],
) -> threading.Thread:
    """Run a long-lived command (build/test/run) on a worker thread.

    on_line(stream, text) where stream is 'stdout' or 'stderr'.
    on_done(returncode).
    """

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
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line("stdout", line)
        rc = proc.wait()
        logger.debug(f"run_streaming done rc={rc}: {' '.join(argv)}")
        on_done(rc)

    thread = threading.Thread(target=_worker, name="thor-csharp-dotnet", daemon=True)
    thread.start()
    return thread


def dotnet_info(dotnet: str = "dotnet") -> CommandResult:
    return run_sync([dotnet, "--info"])
