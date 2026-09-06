# Thor — GtkSourceView editor
"""Thor editor package.

Direct GtkSourceView editor that bakes plugins in-process.
No libpeas, no typelib indirection.
"""
from __future__ import annotations

try:
    from .logging_config import setup_logging

    setup_logging()
except Exception:
    pass

__version__ = "0.1.0"
__app_id__ = "dev.thor.Editor"
