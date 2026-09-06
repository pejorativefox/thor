"""Startup dependency checks. Soft-only: report, never raise."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass


@dataclass
class DependencyIssue:
    name: str
    detail: str
    hint: str
    warn_only: bool = False

    def log_line(self) -> str:
        kind = "missing optional dependency" if self.warn_only else "missing dependency"
        return f"{kind}: {self.name} ({self.detail}). {self.hint}"


def check_python_modules(find_spec=None) -> list[DependencyIssue]:
    find = find_spec or importlib.util.find_spec
    issues: list[DependencyIssue] = []
    for module in ("gi",):
        try:
            found = find(module)
        except Exception:
            found = None
        if found is None:
            issues.append(DependencyIssue(
                name=f"python module '{module}'",
                detail="required for all GTK integration",
                hint="install python3-gi (PyGObject) for your distro",
            ))
    return issues


def check_gi_namespaces(require_version=None) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        import gi as _gi
    except Exception as e:
        issues.append(DependencyIssue(
            name="gi typelibs",
            detail=f"cannot import gi: {e!r}",
            hint="install python3-gi with GIR support",
        ))
        return issues
    require = require_version or _gi.require_version
    for namespace, version in (("Gtk", "3.0"), ("Gdk", "3.0"), ("Pango", "1.0")):
        try:
            require(namespace, version)
        except Exception as e:
            issues.append(DependencyIssue(
                name=f"{namespace}-{version} typelib",
                detail=str(getattr(e, "message", e) or e),
                hint="install GIR typelibs; private paths are self-registered at import",
            ))
    try:
        require("GtkSource", "4")
    except Exception:
        issues.append(DependencyIssue(
            name="GtkSource-4 typelib",
            detail="framework completion falls back to the custom popup",
            hint="install gir1.2-gtksource-4 (or equivalent) for editor-native completion",
            warn_only=True,
        ))
    return issues


def check_toolchain(dotnet="dotnet", roslyn_server="~/.dotnet/tools/roslyn-language-server") -> list[DependencyIssue]:
    from . import dotnet_cli
    from . import roslyn as roslyn_mod
    issues: list[DependencyIssue] = []
    try:
        resolved = dotnet_cli.resolve_dotnet(dotnet or "dotnet")
    except Exception:
        resolved = None
    if not resolved:
        issues.append(DependencyIssue(
            name="dotnet SDK",
            detail=f"configured {dotnet!r} not found",
            hint="install .NET SDK 9/10 and ensure 'dotnet' is on PATH",
        ))
    try:
        argv = roslyn_mod.resolve_server_command(roslyn_server or "")
    except Exception:
        argv = None
    if not argv:
        issues.append(DependencyIssue(
            name="roslyn-language-server",
            detail=f"configured {roslyn_server!r} not found",
            hint="dotnet tool install --global roslyn-language-server",
        ))
    return issues


def check_all(dotnet="dotnet", roslyn_server="~/.dotnet/tools/roslyn-language-server",
              include_gi=True) -> list[DependencyIssue]:
    issues = check_python_modules()
    if include_gi and not any(i.name.startswith("python module") for i in issues):
        issues.extend(check_gi_namespaces())
    issues.extend(check_toolchain(dotnet, roslyn_server))
    return issues
# Compatibility alias — original spec checks `check_deps`
check_deps = check_all
check_dependencies = check_all
