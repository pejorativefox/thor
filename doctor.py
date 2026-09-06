#!/usr/bin/env python3
"""doctor: headless self-check for Thor install/load problems."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
THOR_CACHE_DIR = os.path.expanduser("~/.cache/thor/project-mode")
THOR_MARKER = f"/tmp/thor-csharp-{os.getuid()}.log"

failures: list[str] = []
warnings: list[str] = []


def check(label: str, ok: bool, hint: str = "", warn_only: bool = False) -> None:
    status = "ok" if ok else ("warn" if warn_only else "FAIL")
    print(f"[{status}] {label}")
    if not ok:
        print(f"       -> {hint}")
        (warnings if warn_only else failures).append(label)


def main() -> int:
    print("== Thor doctor ==\n-- files --")
    check(
        "thor package importable",
        os.path.isdir(os.path.join(REPO_DIR, "thor")),
        "thor/ directory missing",
    )
    check(
        "styles installed",
        os.path.isfile(os.path.join(REPO_DIR, "styles", "atom-one-dark.xml")),
        "styles/atom-one-dark.xml missing",
    )
    check(
        "csharp.lang installed",
        os.path.isfile(os.path.join(REPO_DIR, "lang", "csharp.lang")),
        "lang/csharp.lang missing",
    )
    check(
        "thor-cli launcher",
        os.path.isfile(os.path.join(REPO_DIR, "thor-cli")),
        "thor-cli missing",
    )
    check(
        "thor-code launcher",
        os.path.isfile(os.path.join(REPO_DIR, "thor-code")),
        "thor-code missing",
    )
    check(
        "thor-open launcher",
        os.path.isfile(os.path.join(REPO_DIR, "thor-open")),
        "thor-open missing",
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

    print("\n-- Thor state --")
    # pending-root handoff
    pending = os.path.join(THOR_CACHE_DIR, "pending-root")
    try:
        if os.path.isfile(pending):
            age = time.time() - os.path.getmtime(pending)
            check(
                "no stale pending-root handoff",
                age < 60,
                f"stale {pending} ({age:.0f}s old) — rm it or ignore (auto-expires)",
                warn_only=True,
            )
        else:
            check("no stale pending-root handoff", True, "")
    except Exception:
        pass

    # thor process
    tprocs = _thor_processes()
    if tprocs:
        print(f"[info] Thor running: {', '.join(tprocs[:3])}")
        print("       -> quit all Thor windows first to reload (single-instance Gtk.Application)")

    roslyn_log = os.path.expanduser("~/.cache/thor/thor-csharp/roslyn-logs/roslyn-stderr.log")
    if os.path.isfile(roslyn_log):
        print(f"[info] Thor roslyn stderr log exists: {roslyn_log}")

    print("\n-- marker logs --")
    if os.path.isfile(THOR_MARKER):
        with open(THOR_MARKER, encoding="utf-8") as f:
            tlines = f.read().strip().splitlines()
        print(f"thor marker log exists ({len(tlines)} lines), tail:")
        for line in tlines[-5:]:
            print(f"  {line}")
    else:
        print("no thor marker log yet; run with THOR_DEBUG=1 to create it:")
        print(f"  THOR_DEBUG=1 thor   (or THOR_DEBUG=1 python -m thor)")
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
