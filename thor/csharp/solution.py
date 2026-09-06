"""Solution / project discovery.

Strategy (deliberately thin):
- Walk up from the active document to find *.sln / *.slnx.
- Enumerate projects with `dotnet sln list` (no hand-rolled MSBuild parser).
- Parse each *.csproj as XML only for TargetFramework(s), OutputType, IsTestProject
  and PackageReferences (for a lightweight NuGet view).
"""

from __future__ import annotations

import glob
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

from . import dotnet_cli

logger = logging.getLogger(__name__)

#: .csproj files are small XML; anything this big is generated or corrupt —
#: skip the parse rather than stalling refresh on a huge solution.
_CSPROJ_MAX_BYTES = 1 << 20


@dataclass
class ProjectInfo:
    path: str
    name: str
    target_frameworks: List[str] = field(default_factory=list)
    output_type: str = "Library"
    is_test_project: bool = False
    package_refs: List[str] = field(default_factory=list)


@dataclass
class SolutionModel:
    path: Optional[str]
    root_dir: str
    projects: List[ProjectInfo] = field(default_factory=list)


def _glob_case(directory: str, *patterns: str) -> List[str]:
    """Glob for solution files, tolerating uppercase extensions (*.SLN)."""
    found: List[str] = []
    for pattern in patterns:
        try:
            found.extend(glob.glob(os.path.join(directory, pattern)))
        except OSError as e:
            logger.debug(f"_glob_case {directory}/{pattern} failed: {e!r}")
    return sorted(found)


def find_solution(start_path: str) -> Optional[str]:
    """Walk upward looking for *.sln then *.slnx. Returns absolute path or None.

    Matching is case-insensitive (``*.sln`` and ``*.SLN`` both count). When
    a directory holds several solutions the alphabetically-first ``*.sln``
    wins (logged); a nearer ``*.slnx`` still beats a farther ``*.sln``
    because the walk goes inside-out.
    """
    directory = os.path.abspath(start_path)
    if os.path.isfile(directory):
        directory = os.path.dirname(directory)
    while True:
        solutions = _glob_case(directory, "*.sln", "*.SLN")
        if solutions:
            if len(solutions) > 1:
                logger.debug(f"find_solution: {len(solutions)} .sln files, using {solutions[0]}")
            else:
                logger.debug(f"find_solution: {solutions[0]}")
            return solutions[0]
        slnx = _glob_case(directory, "*.slnx", "*.SLNX")
        if slnx:
            if len(slnx) > 1:
                logger.debug(f"find_solution: {len(slnx)} .slnx files, using {slnx[0]}")
            else:
                logger.debug(f"find_solution: {slnx[0]}")
            return slnx[0]
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


#: Directories never descended into during project discovery. Besides
#: speed, this matters for correctness: symlink farms (e.g. Wine
#: ``dosdevices/z:`` -> ``/``) drag the Roslyn server into ``/proc`` where
#: it aborts on dead PIDs (exit 134, ``No such process ... /proc/<pid>/cwd``).
_PRUNE_DIRS = frozenset({
    ".git", ".svn", ".hg", ".cache", ".dotnet", ".nuget",
    "bin", "obj", "node_modules", ".vs", ".idea",
    "dosdevices", "drive_c",
})

#: Max walk depth for the glob fallback (see find_projects_fallback).
#: One repo is shallow; deeper crawls wander into fixtures, SDK packs,
#: and (via symlinks) /proc.
FALLBACK_MAX_DEPTH = 4


def is_home_root(path: str) -> bool:
    """True when path is the user's home dir (or above it)."""
    try:
        real = os.path.realpath(os.path.abspath(path))
        home = os.path.realpath(os.path.expanduser("~"))
        return real == home or real == os.path.dirname(home) or real == "/"
    except Exception as e:
        logger.debug(f"is_home_root {path!r} failed: {e!r}")
        return False


def find_projects_fallback(root_dir: str) -> List[str]:
    """Glob fallback when `dotnet sln` is unavailable.

    Walks at most ``FALLBACK_MAX_DEPTH`` levels deep: one repo is
    shallow, and deeper crawls wander into fixtures, SDK packs, and
    (via symlinks) /proc. Symlinked dirs are never descended into.
    """
    if is_home_root(root_dir):
        logger.debug(f"find_projects_fallback: refusing to crawl {root_dir!r}")
        return []
    found = []

    def _on_error(err: OSError) -> None:
        logger.debug(f"find_projects_fallback walk error: {err!r}")

    for dirpath, dirnames, filenames in os.walk(root_dir, followlinks=False, onerror=_on_error):
        parts = dirpath.split(os.sep)
        if "obj" in parts or "bin" in parts:
            continue
        # Prune junk + symlinked dirs in place (os.walk honors this).
        dirnames[:] = [
            d for d in dirnames
            if d not in _PRUNE_DIRS
            and not d.startswith(".")
            and not os.path.islink(os.path.join(dirpath, d))
        ]
        for filename in filenames:
            if filename.lower().endswith(".csproj"):
                found.append(os.path.join(dirpath, filename))
        # Don't descend too deep for the fallback; one repo = shallow.
        depth = os.path.relpath(dirpath, root_dir).count(os.sep)
        if depth > FALLBACK_MAX_DEPTH:
            dirnames[:] = []
    return sorted(found)


@dataclass
class FileNode:
    name: str
    path: str
    is_dir: bool
    children: List["FileNode"] = field(default_factory=list)


def project_tree(root_dir: str, max_depth: int = 8) -> List[FileNode]:
    """Recursive .cs tree for the explorer (dirs first, alphabetical).

    Prunes build output, hidden dirs and symlinked dirs (same crawl-safety
    rationale as the fallback walk). Directories are only included when
    they (transitively) contain .cs files.
    """
    nodes: List[FileNode] = []
    try:
        entries = sorted(
            os.scandir(root_dir),
            key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
        )
    except OSError:
        return []
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                if (
                    entry.name in _PRUNE_DIRS
                    or entry.name.startswith(".")
                    or os.path.islink(entry.path)
                    or max_depth <= 0
                ):
                    continue
                children = project_tree(entry.path, max_depth - 1)
                if children:
                    nodes.append(FileNode(entry.name, entry.path, True, children))
            elif entry.name.endswith(".cs"):
                nodes.append(FileNode(entry.name, entry.path, False, []))
        except OSError as e:
            logger.debug(f"project_tree entry failed: {e!r}")
            continue
    return nodes


#: A project line from `dotnet sln list`: an optional console marker
#: (dashes, '>', whitespace) followed by a relative or absolute path
#: ending in .csproj (case-insensitive). Strict on purpose: header lines
#: ("Project(s)", "----------", "2 Project(s)") must not leak through as
#: projects, or the explorer shows phantom entries.
_SLN_PROJECT_LINE_RE = re.compile(
    r"^[\-\s>]*"
    r"(?P<path>[A-Za-z0-9_][\w\- .\\/\\\\:()~]*?\.csproj)"
    r"\s*$",
    re.IGNORECASE,
)


def parse_sln_list_output(text: str, solution_dir: str) -> List[str]:
    """Parse `dotnet sln list` output into absolute .csproj paths."""
    projects: List[str] = []
    for line in text.splitlines():
        match = _SLN_PROJECT_LINE_RE.match(line.strip())
        if not match:
            continue
        # Windows-style separators from `dotnet sln` on any host.
        candidate = match.group("path").replace("\\", os.sep).strip()
        abs_path = candidate if os.path.isabs(candidate) else os.path.join(solution_dir, candidate)
        projects.append(os.path.normpath(abs_path))
    return projects


def parse_csproj(path: str) -> ProjectInfo:
    name = os.path.splitext(os.path.basename(path))[0]
    info = ProjectInfo(path=path, name=name)
    try:
        if os.path.getsize(path) > _CSPROJ_MAX_BYTES:
            logger.debug(f"parse_csproj skipping huge file: {path}")
            return info
    except OSError:
        pass
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        tfms: List[str] = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            text = (elem.text or "").strip()
            if tag == "TargetFramework" and text:
                tfms.append(text)
            elif tag == "TargetFrameworks" and text:
                tfms.extend([t.strip() for t in text.split(";") if t.strip()])
            elif tag == "OutputType" and text:
                info.output_type = text
            elif tag == "IsTestProject" and text.lower() == "true":
                info.is_test_project = True
            elif tag == "PackageReference":
                inc = elem.attrib.get("Include", "").strip()
                ver = elem.attrib.get("Version", "").strip()
                if inc:
                    info.package_refs.append(f"{inc} {ver}".strip())
        # Heuristic: MSTest/xUnit/NUnit refs imply test project.
        if not info.is_test_project:
            blob = " ".join(info.package_refs).lower()
            if any(fw in blob for fw in ("mstest", "xunit", "nunit")):
                info.is_test_project = True
        info.target_frameworks = tfms
    except ET.ParseError as e:
        logger.debug(f"parse_csproj {path}: {e}")
    except FileNotFoundError:
        logger.debug(f"parse_csproj missing: {path}")
    except OSError as e:
        logger.debug(f"parse_csproj unreadable {path}: {e!r}")
    return info


def load_solution(start_path: str, dotnet: str = "dotnet") -> SolutionModel:
    start_path = os.path.abspath(start_path)
    sln = find_solution(start_path)
    root = os.path.dirname(sln) if sln else (
        start_path if os.path.isdir(start_path) else os.path.dirname(os.path.abspath(start_path))
    )
    projects: List[str] = []
    if sln:
        try:
            result = dotnet_cli.run_sync([dotnet, "sln", sln, "list"])
        except OSError as e:
            # run_sync already maps spawn failures to 127/126, but never
            # let discovery crash the refresh path on exotic hosts.
            logger.debug(f"`dotnet sln list` spawn failed: {e!r}")
            result = None
        if result is not None and result.returncode == 0:
            projects = parse_sln_list_output(result.stdout, os.path.dirname(sln))
        else:
            code = result.returncode if result is not None else "spawn-error"
            logger.debug(f"`dotnet sln list` failed ({code}), glob fallback")
    if not projects:
        projects = find_projects_fallback(root)
    if sln:
        root = os.path.dirname(sln)
    elif projects:
        # Tighten the workspace to the projects found: handing the server
        # a broad root (e.g. $HOME) makes it crawl symlink farms like Wine
        # dosdevices/z: -> /proc, where it aborts (exit 134).
        try:
            root = os.path.commonpath([os.path.dirname(p) for p in projects])
        except (ValueError, OSError) as e:
            logger.debug(f"load_solution commonpath failed: {e!r}")
    model = SolutionModel(path=sln, root_dir=root)
    for csproj in projects:
        if os.path.exists(csproj):
            model.projects.append(parse_csproj(csproj))
        else:
            logger.debug(f"project listed but missing on disk: {csproj}")
    logger.debug(f"load_solution sln={sln} projects={len(model.projects)}")
    return model
