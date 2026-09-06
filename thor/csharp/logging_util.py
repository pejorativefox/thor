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

    def _is_debug_env() -> bool:  # type: ignore[no-redef]
        return os.environ.get("THOR_DEBUG", "").strip().lower() not in ("", "0", "false", "no", "off")


MARKER_PATH = f"/tmp/thor-csharp-{os.getuid()}.log"


def setup_logging(level: int | None = None) -> None:
    if _setup_thor_logging is not None:
        try:
            _setup_thor_logging(level=level)
            return
        except Exception:
            pass
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
    pass

_logger = logging.getLogger("thor.csharp")


def is_debug() -> bool:
    return _logger.isEnabledFor(logging.DEBUG) or _is_debug_env()


def _append_marker(event: str) -> None:
    try:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(MARKER_PATH, "a", encoding="utf-8") as f:
            f.write(f"{stamp} pid={os.getpid()} {event}\n")
    except Exception:
        pass


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
