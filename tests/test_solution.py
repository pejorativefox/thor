"""Unit tests for solution discovery/parsing (headless, no GTK)."""

import os
import sys
import tempfile


from thor.csharp import solution


def test_find_solution_walks_up():
    with tempfile.TemporaryDirectory() as tmp:
        sub = os.path.join(tmp, "src", "App")
        os.makedirs(sub)
        sln = os.path.join(tmp, "App.sln")
        with open(sln, "w") as f:
            f.write("sln")
        found = solution.find_solution(os.path.join(sub, "Program.cs"))
        assert found == sln, found


def test_find_solution_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert solution.find_solution(tmp) is None


def test_find_solution_slnx():
    with tempfile.TemporaryDirectory() as tmp:
        sub = os.path.join(tmp, "src", "App")
        os.makedirs(sub)
        slnx = os.path.join(tmp, "App.slnx")
        with open(slnx, "w") as f:
            f.write("<Solution />")
        found = solution.find_solution(os.path.join(sub, "Program.cs"))
        assert found == slnx, found


def test_find_solution_prefers_sln_over_slnx():
    with tempfile.TemporaryDirectory() as tmp:
        sln = os.path.join(tmp, "App.sln")
        slnx = os.path.join(tmp, "App.slnx")
        for path in (slnx, sln):
            with open(path, "w") as f:
                f.write("x")
        assert solution.find_solution(tmp) == sln


def test_find_solution_nested_slnx_wins_over_parent_sln():
    """Closest solution to the file wins, regardless of extension."""
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "Root.sln"), "w") as f:
            f.write("x")
        sub = os.path.join(tmp, "src")
        os.makedirs(sub)
        slnx = os.path.join(sub, "Inner.slnx")
        with open(slnx, "w") as f:
            f.write("x")
        assert solution.find_solution(sub) == slnx


def test_find_solution_uppercase_extension():
    with tempfile.TemporaryDirectory() as tmp:
        sln = os.path.join(tmp, "APP.SLN")
        with open(sln, "w") as f:
            f.write("x")
        assert solution.find_solution(tmp) == sln


def test_load_solution_slnx_with_real_dotnet():
    """End-to-end: real `dotnet sln list` against an .slnx fixture."""
    import shutil

    dotnet = shutil.which("dotnet")
    if dotnet is None:
        return  # not a failure without a toolchain; CI has one
    with tempfile.TemporaryDirectory() as tmp:
        proj_dir = os.path.join(tmp, "src", "App")
        os.makedirs(proj_dir)
        csproj = os.path.join(proj_dir, "App.csproj")
        with open(csproj, "w") as f:
            f.write(
                "<Project Sdk=\"Microsoft.NET.Sdk\">"
                "<PropertyGroup><OutputType>Exe</OutputType>"
                "<TargetFramework>net10.0</TargetFramework></PropertyGroup>"
                "</Project>"
            )
        slnx = os.path.join(tmp, "App.slnx")
        with open(slnx, "w") as f:
            f.write(
                "<Solution>\n"
                f'  <Project Path="{os.path.relpath(csproj, tmp)}" />\n'
                "</Solution>\n"
            )
        model = solution.load_solution(os.path.join(proj_dir, "Program.cs"), dotnet)
        assert model.path == slnx, model.path
        assert [p.path for p in model.projects] == [csproj]
        assert model.projects[0].output_type == "Exe"


def test_parse_sln_list_output():
    text = """
Microsoft (R) Build Engine
Project(s)
----------
src/App/App.csproj
test/App.Tests/App.Tests.csproj
2 Project(s)
"""
    projects = solution.parse_sln_list_output(text, "/repo")
    assert projects == ["/repo/src/App/App.csproj", "/repo/test/App.Tests/App.Tests.csproj"]


def test_parse_csproj_test_detection():
    with tempfile.TemporaryDirectory() as tmp:
        csproj = os.path.join(tmp, "App.Tests.csproj")
        with open(csproj, "w") as f:
            f.write(
                "<Project Sdk=\"Microsoft.NET.Sdk\">"
                "<PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup>"
                "<ItemGroup><PackageReference Include=\"xunit\" Version=\"2.9.0\" /></ItemGroup>"
                "</Project>"
            )
        info = solution.parse_csproj(csproj)
        assert info.target_frameworks == ["net9.0"]
        assert info.is_test_project is True
        assert info.package_refs == ["xunit 2.9.0"]


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("// x")


def _flatten(nodes):
    out = []
    for node in nodes:
        out.append((node.name, node.is_dir))
        out.extend(_flatten(node.children))
    return out


def test_project_tree_nested_and_sorted():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "Top.cs"))
        _touch(os.path.join(tmp, "Zeta", "Deep.cs"))
        _touch(os.path.join(tmp, "Alpha", "Mid.cs"))
        _touch(os.path.join(tmp, "readme.md"))
        nodes = solution.project_tree(tmp)
        assert [(n.name, n.is_dir) for n in nodes] == [
            ("Alpha", True),
            ("Zeta", True),
            ("Top.cs", False),
        ]
        assert [(n.name, n.is_dir) for n in nodes[0].children] == [("Mid.cs", False)]


def test_project_tree_prunes_junk():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "bin", "Built.cs"))
        _touch(os.path.join(tmp, "obj", "Gen.cs"))
        _touch(os.path.join(tmp, ".hidden", "H.cs"))
        _touch(os.path.join(tmp, "Empty", "note.txt"))
        with tempfile.TemporaryDirectory() as elsewhere:
            _touch(os.path.join(elsewhere, "Linked.cs"))
            os.symlink(elsewhere, os.path.join(tmp, "link"))
        _touch(os.path.join(tmp, "Real.cs"))
        nodes = solution.project_tree(tmp)
        assert [(n.name, n.is_dir) for n in nodes] == [("Real.cs", False)]


def test_project_tree_depth_cap():
    with tempfile.TemporaryDirectory() as tmp:
        deep = os.path.join(tmp, "a", "b", "c")
        _touch(os.path.join(deep, "Deep.cs"))
        assert solution.project_tree(tmp, max_depth=1) == []
        assert _flatten(solution.project_tree(tmp, max_depth=3)) == [
            ("a", True),
            ("b", True),
            ("c", True),
            ("Deep.cs", False),
        ]


def test_load_solution_glob_fallback():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        proj_dir = os.path.join(tmp, "src", "App")
        os.makedirs(proj_dir)
        csproj = os.path.join(proj_dir, "App.csproj")
        with open(csproj, "w") as f:
            f.write("<Project Sdk=\"Microsoft.NET.Sdk\" />")
        model = solution.load_solution(proj_dir, dotnet="definitely-not-a-real-binary")
        assert model.path is None
        assert [p.path for p in model.projects] == [csproj]
