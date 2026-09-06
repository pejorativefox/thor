# -*- coding: utf-8 -*-
"""Thor C# logging — stdlib ``logging`` wrapper.

Replaces the previous custom ``THOR_DEBUG``-gated ``sys.stderr.write`` helpers
with the standard :pymod:`logging` module.

* ``THOR_DEBUG`` env var (``1``/``true``/``yes``/``on``) enables ``DEBUG``
  level for the ``thor`` loggers; otherwise level is ``WARNING`` (so
  ``error``/``warning`` are always visible but debug traces are silent).
* All messages go through :pymod:`logging` (stderr via ``StreamHandler``).
  The legacy marker file ``/tmp/thor-csharp-<uid>.log`` is still appended
  for ``error`` (always) and for ``debug``/``marker`` when debugging is on,
  so ``doctor.py`` keeps working.
* New code should prefer ``logging.getLogger(__name__)`` directly; the
  ``debug``/``error``/``marker``/``is_debug`` symbols remain for
  backwards-compatibility and delegate to the loggers.
"""

from __future__ import annotations

import datetime
import logging
import os

try:
    from thor.logging_config import _is_debug_env, setup_logging as _setup_thor_logging
except Exception:  # headless / import cycle
    _setup_thor_logging = None  # type: ignore

    def _is_debug_env() -> bool:  # type: ignore[misc]
        return os.environ.get("THOR_DEBUG", "").lower() in ("1", "true", "yes", "on")

try:
    from thor.xdg import marker_log_path as _get_marker_path
    MARKER_PATH = _get_marker_path()
except Exception:
    MARKER_PATH = f"/tmp/thor-csharp-{os.getuid()}.log"
MARKER_MAX_BYTES = 1 << 20  # 1 MiB cap for the debug marker file.


def setup_logging(level: int | None = None) -> None:
    # Truncate the marker file on start when it exceeds the cap.
    try:
        if os.path.getsize(MARKER_PATH) > MARKER_MAX_BYTES:
            with open(MARKER_PATH, "w", encoding="utf-8") as f:
                f.write("--- thor-csharp marker rotated on start (exceeded 1MiB) ---\n")
    except OSError:
        logging.getLogger(__name__).debug("marker rotation on start failed", exc_info=True)
    if _setup_thor_logging is not None:
        try:
            _setup_thor_logging(level=level)
            return
        except Exception:
            logging.getLogger(__name__).debug("thor setup_logging failed", exc_info=True)
    # Fallback if thor.logging_config unavailable (headless import)
    if level is None:
        level = logging.DEBUG if _is_debug_env() else logging.WARNING
    thor_logger = logging.getLogger("thor")
    if not thor_logger.handlers:
        import sys

        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        thor_logger.addHandler(handler)
    thor_logger.setLevel(level)
    thor_logger.propagate = False

# Auto-configure on import (headless-safe, never raises).
try:
    setup_logging()
except Exception:
    logging.getLogger(__name__).debug("logging auto-config failed", exc_info=True)

_logger = logging.getLogger("thor.csharp")


def is_debug() -> bool:
    return _logger.isEnabledFor(logging.DEBUG) or _is_debug_env()


def _rotate_marker_if_large() -> None:
    """Truncate the marker file when it grows past the cap (best effort)."""
    try:
        if os.path.getsize(MARKER_PATH) <= MARKER_MAX_BYTES:
            return
    except OSError:
        return
    try:
        with open(MARKER_PATH, "w", encoding="utf-8") as f:
            f.write("--- thor-csharp marker rotated (exceeded 1MiB) ---\n")
    except OSError:
        _logger.debug("marker rotation failed", exc_info=True)


def _append_marker(event: str) -> None:
    try:
        _rotate_marker_if_large()
        os.makedirs(os.path.dirname(MARKER_PATH), exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(MARKER_PATH, "a", encoding="utf-8") as f:
            f.write(f"{stamp} pid={os.getpid()} {event}\n")
    except Exception:
        _logger.debug("marker append failed", exc_info=True)


def debug(msg: str) -> None:
    _logger.debug(msg)
    if _is_debug_env():
        _append_marker(msg)


def marker(event: str) -> None:
    """Append a line to the debug marker file (only when debugging is on)."""
    if not _is_debug_env():
        _logger.debug(event)
        return
    _logger.debug(event)
    _append_marker(event)


def error(msg: str) -> None:
    _logger.error(msg)
    _append_marker(f"ERROR {msg}")


__all__ = ["debug", "error", "marker", "is_debug", "setup_logging", "MARKER_PATH"]
