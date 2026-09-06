"""Unit tests for dotnet CLI helpers and testing helpers."""

import os
import sys


from thor.csharp import dotnet_cli, testing, intelligence


def test_resolve_dotnet_found():
    assert dotnet_cli.resolve_dotnet("dotnet") is not None


def test_run_sync_missing_binary():
    result = dotnet_cli.run_sync(["definitely-not-a-real-binary-xyz", "--info"])
    assert result.returncode == 127
    assert result.stderr


def test_parse_list_tests():
    text = """Build succeeded.
The following Tests are available:
    MyApp.Tests.UnitTest1.Test1
    MyApp.Tests.UnitTest1.Test2
"""
    assert testing.parse_list_tests(text) == [
        "MyApp.Tests.UnitTest1.Test1",
        "MyApp.Tests.UnitTest1.Test2",
    ]


def test_parse_test_output_outcomes():
    text = """Passed MyApp.Tests.UnitTest1.Test1 [12 ms]
Failed MyApp.Tests.UnitTest1.Test2 [3 ms]
Skipped MyApp.Tests.UnitTest1.Test3 [1 ms]
"""
    run = testing.parse_test_output(text, project="proj")
    assert (run.passed, run.failed, run.skipped, run.total) == (1, 1, 1, 3)


def test_parse_test_output_summary_fallback():
    text = "Passed!  - Failed: 1, Passed: 5, Skipped: 2, Total: 8"
    run = testing.parse_test_output(text)
    assert (run.passed, run.failed, run.skipped, run.total) == (5, 1, 2, 8)


def test_normalize_diagnostics():
    raw = [
        {
            "range": {"start": {"line": 4, "character": 6}},
            "severity": 1,
            "message": "Cannot convert\nsecond line",
            "code": "CS0030",
        }
    ]
    diags = intelligence.normalize_diagnostics("file:///tmp/A.cs", raw)
    assert len(diags) == 1
    assert (diags[0].line, diags[0].character) == (4, 6)
    assert diags[0].message == "Cannot convert"
    assert intelligence.summarize(diags) == "1 errors, 0 warnings"
