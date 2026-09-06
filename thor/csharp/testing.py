"""Test discovery/execution helpers (dotnet test / vstest output parsing)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

from . import dotnet_cli
_LIST_TESTS_RE = re.compile(r"^\s*(?P<name>[\w\.\+\-`<>_]+)\s*$")
_OUTCOME_RE = re.compile(
    r"(?P<outcome>Passed|Failed|Skipped|Passed!|Failed!)\s+(?P<name>[\w\.\+\-`<>_,\s\(\)]+?)(?:\s+\[(?P<duration>[^\]]+)\])?\s*$"
)


@dataclass
class TestCase:
    name: str
    outcome: str = "NotRun"
    duration: str = ""
    message: str = ""


@dataclass
class TestRun:
    project: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    cases: List[TestCase] = field(default_factory=list)


def parse_list_tests(text: str) -> List[str]:
    """Parse `dotnet test --list-tests` output into test names."""
    names: List[str] = []
    capture = False
    for line in text.splitlines():
        stripped = line.strip()
        if "following tests are available" in stripped.lower():
            capture = True
            continue
        if not capture:
            continue
        if not stripped or stripped.startswith(("Build ", "Determining ", "Restored ", "  Determining")):
            continue
        match = _LIST_TESTS_RE.match(line)
        if match and "." in match.group("name"):
            names.append(match.group("name"))
    logger.debug(f"parse_list_tests: {len(names)} tests")
    return names


def parse_test_output(text: str, project: str = "") -> TestRun:
    """Parse console `dotnet test` output (best-effort, version tolerant)."""
    run = TestRun(project=project)
    for line in text.splitlines():
        match = _OUTCOME_RE.search(line.strip())
        if match:
            outcome = match.group("outcome").rstrip("!")
            name = match.group("name").strip()
            duration = match.group("duration") or ""
            run.cases.append(TestCase(name=name, outcome=outcome, duration=duration))
    run.total = len(run.cases)
    run.passed = sum(1 for c in run.cases if c.outcome == "Passed")
    run.failed = sum(1 for c in run.cases if c.outcome == "Failed")
    run.skipped = sum(1 for c in run.cases if c.outcome == "Skipped")
    # Fallback to summary lines like "Passed!  - Failed: 0, Passed: 5, ...".
    if run.total == 0:
        summary = re.search(
            r"Failed:\s*(?P<f>\d+),\s*Passed:\s*(?P<p>\d+),\s*Skipped:\s*(?P<s>\d+)",
            text,
        )
        if summary:
            run.failed = int(summary.group("f"))
            run.passed = int(summary.group("p"))
            run.skipped = int(summary.group("s"))
            run.total = run.failed + run.passed + run.skipped
    return run


def list_tests(dotnet: str, project: str) -> List[str]:
    result = dotnet_cli.run_sync([dotnet, "test", project, "--list-tests", "-v", "q", "--nologo"])
    if result.returncode != 0:
        logger.debug(f"list_tests failed: {result.stderr[:500]}")
        return []
    return parse_list_tests(result.stdout)
