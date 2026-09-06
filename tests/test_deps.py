"""Unit tests for startup dependency checks (headless, no GTK)."""

import os
import sys


from thor.csharp import deps, logging_util


def test_python_module_missing_reported():
    issues = deps.check_python_modules(find_spec=lambda _name: None)
    assert any("gi" in i.name for i in issues)
    assert all(not i.warn_only for i in issues)


def test_python_module_present_ok():
    issues = deps.check_python_modules(find_spec=lambda _name: object())
    assert issues == []


def test_toolchain_all_missing():
    issues = deps.check_toolchain(
        dotnet="definitely-not-dotnet-xyz",
        roslyn_server="definitely-not-roslyn-xyz",
    )
    names = [i.name for i in issues]
    assert any("dotnet" in n for n in names)
    assert any("roslyn" in n for n in names)


def test_error_logs_without_debug_env():
    logging_util.error("startup check test message")
    marker = logging_util.MARKER_PATH
    assert os.path.isfile(marker)
    with open(marker, encoding="utf-8") as f:
        assert "startup check test message" in f.read()
