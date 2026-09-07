#!/usr/bin/env python3
"""doctor: headless self-check for Thor install/load problems."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    from thor import xdg
    THOR_MARKER = xdg.marker_log_path()
    ROSLYN_LOG_DIR = xdg.roslyn_log_dir()
except Exception:
    THOR_MARKER = f"/tmp/thor-csharp-{os.getuid()}.log"
    ROSLYN_LOG_DIR = os.path.expanduser("~/.local/state/thor/logs")

failures: list[str] = []
warnings: list[str] = []


def check(label: str, ok: bool, hint: str = "", warn_only: bool = False) -> None:
    status = "ok" if ok else ("warn" if warn_only else "FAIL")
    print(f"[{status}] {label}")
    if not ok:
        print(f"       -> {hint}")
        (warnings if warn_only else failures).append(label)


def main() -> int:
    print("== Thor doctor ==\n-- files & assets --")
    check(
        "thor package importable",
        os.path.isdir(os.path.join(REPO_DIR, "thor")),
        "thor/ directory missing",
    )
    check(
        "styles available",
        os.path.isfile(os.path.join(REPO_DIR, "styles", "atom-one-dark.xml"))
        or any(os.path.isfile(os.path.join(d, "atom-one-dark.xml")) for d in (xdg.styles_dirs() if "xdg" in globals() else [])),
        "styles/atom-one-dark.xml missing",
    )
    check(
        "csharp.lang available",
        os.path.isfile(os.path.join(REPO_DIR, "lang", "csharp.lang"))
        or any(os.path.isfile(os.path.join(d, "csharp.lang")) for d in (xdg.lang_dirs() if "xdg" in globals() else [])),
        "lang/csharp.lang missing",
    )
    check(
        "desktop entry",
        os.path.isfile(os.path.join(REPO_DIR, "data", "dev.thor.Editor.desktop"))
        or os.path.isfile(os.path.expanduser("~/.local/share/applications/dev.thor.Editor.desktop")),
        "data/dev.thor.Editor.desktop missing",
    )
    check(
        "scalable icon (svg)",
        os.path.isfile(os.path.join(REPO_DIR, "data", "icons", "dev.thor.Editor.svg"))
        or os.path.isfile(os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/dev.thor.Editor.svg")),
        "data/icons/dev.thor.Editor.svg missing",
    )
    check(
        "raster icon (png)",
        os.path.isfile(os.path.join(REPO_DIR, "data", "icons", "dev.thor.Editor.png"))
        or os.path.isfile(os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/dev.thor.Editor.png")),
        "data/icons/dev.thor.Editor.png missing",
    )
    check(
        "thor-cli launcher",
        os.path.isfile(os.path.join(REPO_DIR, "thor-cli")) or shutil.which("thor-cli") is not None,
        "thor-cli missing",
    )
    check(
        "thor-code launcher",
        os.path.isfile(os.path.join(REPO_DIR, "thor-code")) or shutil.which("thor-code") is not None,
        "thor-code missing",
    )
    check(
        "thor-open launcher",
        os.path.isfile(os.path.join(REPO_DIR, "thor-open")) or shutil.which("thor-open") is not None,
        "thor-open missing",
    )
    check(
        "thor on PATH (or thor-cli fallback)",
        shutil.which("thor") is not None or os.path.isfile(os.path.join(REPO_DIR, "thor-cli")),
        "pip install -e . to provide `thor`, or use thor-cli",
        warn_only=True,
    )

    print("\n-- Thor GI plumbing --")
    gi_ok = False
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkSource", "4")
        from gi.repository import Gtk as _Gtk  # type: ignore
        from gi.repository import GtkSource as _GS  # type: ignore

        gi_ok = _Gtk is not None and _GS is not None
    except Exception:
        gi_ok = False
    check(
        "Gtk + GtkSourceView4 available",
        gi_ok,
        "install gir1.2-gtk-3.0 / gir1.2-gtksource-4 / python3-gi (Thor needs DISPLAY)",
        warn_only=True,
    )

    print("\n-- toolchain --")
    check("dotnet on PATH", shutil.which("dotnet") is not None, "install .NET SDK 9/10")
    # roslyn check
    roslyn_ok = False
    roslyn_hint = "dotnet tool install --global roslyn-language-server"
    try:
        roslyn_bin = os.path.expanduser("~/.dotnet/tools/roslyn-language-server")
        if os.path.isfile(roslyn_bin):
            roslyn_ok = True
            roslyn_hint = ""
        elif shutil.which("roslyn-language-server"):
            roslyn_ok = True
            roslyn_hint = ""
    except Exception:
        pass
    check("roslyn-language-server", roslyn_ok, roslyn_hint, warn_only=True)

    print("\n-- Thor state & cache --")
    # thor processes: one per window, one window per folder. Second
    # launch for a live root focuses the owner via per-root IPC socket.
    tprocs = _thor_processes()
    if tprocs:
        print(f"[info] Thor running: {', '.join(tprocs[:3])}")
        print("       -> each window is its own process; second open focuses via IPC")

    roslyn_log = os.path.join(ROSLYN_LOG_DIR, "roslyn-stderr.log")
    legacy_roslyn_log = os.path.expanduser("~/.cache/thor/thor-csharp/roslyn-logs/roslyn-stderr.log")
    if os.path.isfile(roslyn_log):
        print(f"[info] Thor roslyn stderr log exists: {roslyn_log}")
    elif os.path.isfile(legacy_roslyn_log):
        print(f"[info] Thor legacy roslyn stderr log exists: {legacy_roslyn_log}")

    print("\n-- marker logs --")
    legacy_marker = f"/tmp/thor-csharp-{os.getuid()}.log"
    marker_to_read = THOR_MARKER if os.path.isfile(THOR_MARKER) else (legacy_marker if os.path.isfile(legacy_marker) else None)
    if marker_to_read:
        try:
            with open(marker_to_read, "rb") as f:
                try:
                    f.seek(-65536, 2)
                except OSError:
                    f.seek(0)
                tail = f.read().decode("utf-8", errors="replace")
            tlines = tail.strip().splitlines()
        except (OSError, UnicodeError) as e:
            print(f"thor marker log unreadable ({marker_to_read}): {e}")
            tlines = []
        print(f"thor marker log exists ({marker_to_read}, {len(tlines)} lines shown from tail), tail:")
        for line in tlines[-5:]:
            print(f"  {line}")
    else:
        print("no thor marker log yet; run with THOR_DEBUG=1 to create it:")
        print(f"  THOR_DEBUG=1 thor   (or THOR_DEBUG=1 python -m thor)")
        print(f"  Target location: {THOR_MARKER}")
    print()
    if failures:
        print(f"{len(failures)} hard failure(s). Fix the FAIL items above.")
        return 1
    if warnings:
        print(f"ok with {len(warnings)} warning(s) (see above).")
        return 0
    print("all checks ok.")
    return 0


def _thor_processes() -> list[str]:
    try:
        out = subprocess.run(["pgrep", "-a", "thor"], capture_output=True, text=True, timeout=2)
        if out.returncode != 0:
            return []
        procs = [line for line in out.stdout.splitlines() if "thor" in line.lower()]
        return procs
    except Exception:
        return []


if __name__ == "__main__":
    argparse.ArgumentParser(description="Thor doctor").parse_args()
    sys.exit(main())
