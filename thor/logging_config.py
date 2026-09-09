# -*- coding: utf-8 -*-
"""Central stdlib logging configuration for Thor.

All ``thor.*`` modules should use ``logging.getLogger(__name__)``.
This module configures the ``thor`` logger hierarchy with two sinks:

* stderr — gated by ``THOR_DEBUG`` (WARNING unless debug env is set).
* file   — ``$XDG_STATE_HOME/thor/logs/thor.log`` at DEBUG, ALWAYS on
  (rotating, 5 MiB x 3).  A hung/frozen session leaves a full trace
  behind; the last flushed record shows how far the app got.

The ``thor`` logger is always left at DEBUG so the file handler sees
everything; the stderr handler carries its own level gate so terminal
behavior is unchanged.

Importing this module has the side-effect of configuring logging
(headless-safe, idempotent, never raises).  ``setup_logging`` can be
called again to re-evaluate ``THOR_DEBUG`` (e.g. after env changes in
tests).
"""

from __future__ import annotations

import logging
import os
import sys

try:
    from logging.handlers import RotatingFileHandler
except Exception:  # pragma: no cover - stdlib always present
    RotatingFileHandler = None  # type: ignore[assignment]

_THOR_DEBUG_VALUES = ("", "0", "false", "no", "off")

_FILE_NAME = "thor.log"
_FILE_MAX_BYTES = 5 * 1024 * 1024
_FILE_BACKUPS = 3
_FILE_LEVEL = logging.DEBUG
_FILE_FORMAT = "%(asctime)s pid=%(process)d [%(name)s] %(levelname)s: %(message)s"

# Log paths already installed (idempotency across repeated setup_logging).
_installed_files: set[str] = set()


def _is_debug_env() -> bool:
    return os.environ.get("THOR_DEBUG", "").strip().lower() not in _THOR_DEBUG_VALUES


def _file_log_path() -> str | None:
    try:
        from . import xdg

        return os.path.join(xdg.log_dir(), _FILE_NAME)
    except Exception:
        return None


def _ensure_file_handler(thor_logger: logging.Logger) -> None:
    """Attach the always-on DEBUG file handler (best effort, idempotent)."""
    if RotatingFileHandler is None:
        return
    path = _file_log_path()
    if not path or path in _installed_files:
        return
    try:
        handler = RotatingFileHandler(
            path,
            maxBytes=_FILE_MAX_BYTES,
            backupCount=_FILE_BACKUPS,
            encoding="utf-8",
        )
    except Exception:
        # Never let a logging failure break the editor.
        logging.getLogger(__name__).debug(
            "file log setup failed for %s", path, exc_info=True
        )
        return
    handler.setLevel(_FILE_LEVEL)
    handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    thor_logger.addHandler(handler)
    _installed_files.add(path)
    try:
        thor_logger.debug("file logging to %s", path)
    except Exception:
        pass


def setup_logging(level: int | None = None) -> None:
    """Configure ``thor`` loggers. Idempotent; safe to call multiple times.

    The ``thor`` logger is always DEBUG; ``level`` only gates the stderr
    handler (WARNING by default, DEBUG with ``THOR_DEBUG``).
    """
    if level is None:
        level = logging.DEBUG if _is_debug_env() else logging.WARNING
    thor_logger = logging.getLogger("thor")
    if not thor_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        thor_logger.addHandler(handler)
    # stderr gate only; keep the file handler at its own level.
    for handler in thor_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setLevel(level)
    thor_logger.setLevel(logging.DEBUG)  # file handler filters to its own level
    thor_logger.propagate = True
    _ensure_file_handler(thor_logger)


try:
    setup_logging()
except Exception:
    logging.getLogger(__name__).debug("setup_logging failed", exc_info=True)

__all__ = ["setup_logging", "_is_debug_env"]
