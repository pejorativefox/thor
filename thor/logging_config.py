# -*- coding: utf-8 -*-
"""Central stdlib logging configuration for Thor.

All ``thor.*`` modules should use ``logging.getLogger(__name__)``.
This module configures the ``thor`` logger hierarchy from the
``THOR_DEBUG`` env var (truthy → DEBUG, else WARNING) and ensures a
``StreamHandler`` on stderr with a consistent formatter.

Importing this module has the side-effect of configuring logging
(headless-safe, idempotent, never raises).  ``setup_logging`` can be
called again to re-evaluate ``THOR_DEBUG`` (e.g. after env changes in
tests).
"""

from __future__ import annotations

import logging
import os
import sys

_THOR_DEBUG_VALUES = ("", "0", "false", "no", "off")


def _is_debug_env() -> bool:
    return os.environ.get("THOR_DEBUG", "").strip().lower() not in _THOR_DEBUG_VALUES


def setup_logging(level: int | None = None) -> None:
    """Configure ``thor`` loggers. Idempotent; safe to call multiple times."""
    if level is None:
        level = logging.DEBUG if _is_debug_env() else logging.WARNING
    thor_logger = logging.getLogger("thor")
    if not thor_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        thor_logger.addHandler(handler)
    thor_logger.setLevel(level)
    thor_logger.propagate = False


try:
    setup_logging()
except Exception:
    pass

__all__ = ["setup_logging"]
