"""Unit tests for the fuzzy finder (headless, no GTK)."""

import os
import shutil
import sys
import tempfile
import types

import pytest


from thor.fuzzy import files as files_mod
from thor.fuzzy import find_project_root
from thor.fuzzy.matcher import FuzzyIndex, fuzzy_find, fuzzy_match, fuzzy_score
import thor.fuzzy as fuzzyfinder


def test_score_orders_tight_over_gappy():
    tight = fuzzy_score("abc", "abc")
    gappy = fuzzy_score("abc", "aXbXc")
    assert tight is not None and gappy is not None
    assert tight > gappy


def test_score_prefers_prefix_and_segments():
    assert fuzzy_score("con", "Console") > fuzzy_score("con", "XenonConsole")
    assert fuzzy_score("sce", "ScenePresets.cs") > fuzzy_score("sce", "XenonScene.cs")


def test_score_case_insensitive_and_empty():
    assert fuzzy_score("cou", "count") == fuzzy_score("cou", "Count")
    assert fuzzy_score("", "anything") == 0
    assert fuzzy_score("xyz", "abc") is None
    assert fuzzy_score("abcd", "abc") is None


def test_score_prefers_consecutive_and_word_starts():
    assert fuzzy_score("fb", "foo/bar") > fuzzy_score("fb", "fobble")
    assert fuzzy_score("amo", "app/models/post") > fuzzy_score("amo", "aamoo")
    assert fuzzy_score("ad", "AutomatorDocument") > fuzzy_score("ad", "axxxxxxd")


def test_score_finds_optimal_alignment_not_greedy():
    # Greedy left-to-right would pin 'a' at 0 and pay a long gap; the
    # optimal alignment matches the later consecutive run instead.
    assert fuzzy_score("ab", "axxbab") > fuzzy_score("ab", "axbxbxb")
    hit = fuzzy_match("ab", "axxbab")
    assert hit is not None
    _, positions = hit
    assert positions == [4, 5]


def test_positions_valid_subsequence():
    for query, candidate in (
        ("scene", "source/CoreForge/ScenePresets.cs"),
        ("cp", "CoreForge/Places.cs"),
        ("fb", "FooBar.cs"),
    ):
        hit = fuzzy_match(query, candidate)
        assert hit is not None
        _, positions = hit
        assert len(positions) == len(query)
        assert positions == sorted(positions)
        assert "".join(candidate[i] for i in positions).lower() == query.lower()


def test_smart_case():
    # Lowercase query: case-insensitive.
    assert fuzzy_score("scene", "ScenePresets.cs") is not None
    # Uppercase query: case-sensitive.
    assert fuzzy_score("Scene", "ScenePresets.cs") is not None
    assert fuzzy_score("Scene", "scenepresets.cs") is None
    assert fuzzy_score("FBB", "FooBarBaz") is not None
    assert fuzzy_score("FBB", "foobarbaz") is None


def test_multi_term_and_semantics():
    paths = [
        "source/CoreForge/Places.cs",
        "source/CoreForge/Scene.cs",
        "tests/places_notes.md",
    ]
    assert fuzzy_find("core places", paths) == ["source/CoreForge/Places.cs"]
    assert fuzzy_find("core zzz", paths) == []
    assert fuzzy_match("core places", "source/CoreForge/Scene.cs") is None


def test_exact_basename_boost():
    assert fuzzy_find(
        "places.cs", ["source/CoreForge/Places.cs", "source/places.cs.bak"]
    )[0] == "source/CoreForge/Places.cs"


def test_index_matches_direct_scoring():
    paths = [
        "source/CoreForge/ScenePresets.cs",
        "tests/CoreForge.Tests/Places.cs",
        "source/CoreForge/Program.cs",
        "README.md",
    ]
    index = FuzzyIndex(paths)
    for query in ("scene", "prog", "core cs", "md", "zzz", ""):
        assert index.search(query) == fuzzy_find(query, paths)
        assert index.search(query, limit=2) == fuzzy_find(query, paths, limit=2)


def test_find_ranks_limits_and_culls():
    paths = [
        "/repo/tests/CoreForge.Tests/Places.cs",
        "/repo/source/CoreForge/ScenePresets.cs",
        "/repo/source/CoreForge/Program.cs",
    ]
    found = fuzzy_find("scene", paths)
    assert found == ["/repo/source/CoreForge/ScenePresets.cs"]
    assert fuzzy_find("zzz", paths) == []
    assert fuzzy_find("", paths, limit=2) == paths[:2]
    many = [f"/repo/f{i:03d}.cs" for i in range(10)]
    assert len(fuzzy_find("", many, limit=3)) == 3


from conftest import _touch  # shared helper (see tests/conftest.py)


def test_list_files_skips_hidden_pruned_and_links():
    with tempfile.TemporaryDirectory() as tmp:
        # Force the walk fallback even inside a git checkout.
        _touch(os.path.join(tmp, "Real.cs"))
        _touch(os.path.join(tmp, "sub", "Deep.md"))
        _touch(os.path.join(tmp, "bin", "Built.cs"))
        _touch(os.path.join(tmp, "node_modules", "lib.js"))
        _touch(os.path.join(tmp, ".hidden", "H.cs"))
        with tempfile.TemporaryDirectory() as elsewhere:
            _touch(os.path.join(elsewhere, "Linked.cs"))
            os.symlink(elsewhere, os.path.join(tmp, "link"))
        old = os.environ.get("GIT_CEILING_DIRECTORIES")
        os.environ["GIT_CEILING_DIRECTORIES"] = tmp
        try:
            found = files_mod.list_project_files(tmp)
        finally:
            if old is None:
                del os.environ["GIT_CEILING_DIRECTORIES"]
            else:
                os.environ["GIT_CEILING_DIRECTORIES"] = old
        assert os.path.join(tmp, "Real.cs") in found
        assert os.path.join(tmp, "sub", "Deep.md") in found
        assert not any("bin" in p or "node_modules" in p for p in found)
        assert not any(".hidden" in p or "link" in p for p in found)


def test_list_files_respects_gitignore():
    if shutil.which("git") is None:
        pytest.skip("no git")
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "Keep.cs"))
        _touch(os.path.join(tmp, "Skip.log"))
        with open(os.path.join(tmp, ".gitignore"), "w") as f:
            f.write("*.log\n")
        import subprocess

        subprocess.run(["git", "-C", tmp, "init", "-q"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        found = files_mod.list_project_files(tmp)
        assert os.path.join(tmp, "Keep.cs") in found
        assert os.path.join(tmp, "Skip.log") not in found


def test_list_files_bad_root():
    assert files_mod.list_project_files("") == []
    assert files_mod.list_project_files("/no/such/dir-xyz") == []


def _window_with_panel(children):
    side = types.SimpleNamespace(get_children=lambda: list(children))
    return types.SimpleNamespace(get_side_panel=lambda: side)


def test_find_project_root_from_browser():
    with tempfile.TemporaryDirectory() as tmp:
        browser = types.SimpleNamespace(_root_dir=tmp)
        window = types.SimpleNamespace(_thor_project_browser=browser)
        assert find_project_root(window) == os.path.abspath(tmp)


def test_find_project_root_missing():
    assert find_project_root(None) is None
    assert find_project_root(types.SimpleNamespace(_thor_project_browser=None)) is None
    assert find_project_root(
        types.SimpleNamespace(_thor_project_browser=types.SimpleNamespace())
    ) is None
    broken = types.SimpleNamespace(_thor_project_browser=object())
    assert find_project_root(broken) is None


def test_global_key_ctrl_p_shows_finder():

    mgr = fuzzyfinder._FuzzyFinderManager(window=None)
    shown: list = []
    mgr._show_finder = lambda: shown.append(True)  # type: ignore[method-assign]
    assert mgr._handle_global_key("p", True, False, False) is True
    assert shown == [True]
    assert mgr._handle_global_key("p", False, False, False) is False
    assert mgr._handle_global_key("p", True, True, False) is False
    assert mgr._handle_global_key("p", True, False, True) is False
    assert mgr._handle_global_key("x", True, False, False) is False


def test_markup_highlight_bolds_runs_and_escapes():
    from thor.fuzzy.matcher import markup_highlight

    assert markup_highlight("src/Foo.cs", [4, 5, 6]) == "src/<b>Foo</b>.cs"
    assert markup_highlight("ab", [0, 1]) == "<b>ab</b>"
    assert markup_highlight("ab", []) == "ab"
    assert markup_highlight("a<b>&c", [0]) == "<b>a</b>&lt;b&gt;&amp;c"
    # Out-of-range and duplicate indices are ignored, never raw markup.
    assert markup_highlight("ab", [-1, 0, 0, 2, 99]) == "<b>a</b>b"
    assert "<b></b>" not in markup_highlight("ab", [5])


def test_search_top_limit_matches_full_sort():
    paths = [f"src/module{i:04d}/file{i % 97}.cs" for i in range(2000)]
    paths += ["core/places.cs", "CoreForge/Scene.cs", "source/app/models/post.rb"]
    for query in ("ab", "core cs", ""):
        got = FuzzyIndex(paths).search(query, limit=5)
        if not query.strip():
            assert got == paths[:5]
            continue
        scored = []
        for path in paths:
            score = fuzzy_score(query, path)
            if score is not None:
                scored.append((score, len(path), path))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        assert got == [path for _, _, path in scored[:5]]
    assert FuzzyIndex(paths).search("ab", limit=0) == []


def test_order_with_recent():

    items = [("b.txt", "/root/b.txt"), ("a.txt", "/root/a.txt"), ("c.txt", "/root/c.txt")]
    assert fuzzyfinder.order_with_recent(items, []) == items
    ordered = fuzzyfinder.order_with_recent(items, ["/root/c.txt", "/root/a.txt"])
    assert [path for _display, path in ordered] == ["/root/c.txt", "/root/a.txt", "/root/b.txt"]
    # Missing recent entries are dropped; non-recent order stays stable.
    assert fuzzyfinder.order_with_recent(items, ["/root/gone.txt", "/root/b.txt"]) == [
        ("b.txt", "/root/b.txt"),
        ("a.txt", "/root/a.txt"),
        ("c.txt", "/root/c.txt"),
    ]


def test_recent_and_cache_helpers():

    mgr = fuzzyfinder._FuzzyFinderManager(window=None)
    assert mgr._cached_files("/no/such/dir-xyz") is None
    with tempfile.TemporaryDirectory() as tmp:
        key = os.path.abspath(tmp)
        assert mgr._cached_files(tmp) is None
        mgr._file_cache[key] = [os.path.join(tmp, "a.txt")]
        # Fresh mtime required: the real cache drops entries whose root
        # changed on disk since it was stored.
        mgr._file_cache_mtime[key] = os.path.getmtime(tmp)
        assert mgr._cached_files(tmp) == [os.path.join(tmp, "a.txt")]
    # Recency: prepend, dedupe, truncate to MAX_RECENT.
    mgr._remember_recent("/root/b.txt")
    mgr._remember_recent("/root/a.txt")
    mgr._remember_recent("/root/b.txt")
    assert mgr._recent == ["/root/b.txt", "/root/a.txt"]
    for i in range(fuzzyfinder.MAX_RECENT + 5):
        mgr._remember_recent(f"/root/f{i}.txt")
    assert len(mgr._recent) == fuzzyfinder.MAX_RECENT
    assert mgr._recent[0] == f"/root/f{fuzzyfinder.MAX_RECENT + 4}.txt"
    # Background load caches existing roots (recent-first) and never bad ones.
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "a.txt"))
        _touch(os.path.join(tmp, "sub", "b.txt"))
        received: list = []
        dialog = types.SimpleNamespace(
            set_files=lambda items: received.append(list(items)),
            set_indexing=lambda on: received.append(("indexing", on)),
        )
        saved_glib = getattr(fuzzyfinder, "GLib", None)
        fuzzyfinder.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
        try:
            loader = fuzzyfinder._FuzzyFinderManager(window=None)
            loader._recent = [os.path.join(tmp, "sub", "b.txt")]
            loader._load_in_background(tmp, dialog)
        finally:
            fuzzyfinder.GLib = saved_glib
        assert received and received[-1][0][1].endswith(os.path.join("sub", "b.txt"))
        assert loader._cached_files(tmp) is not None
    nosuch = fuzzyfinder._FuzzyFinderManager(window=None)
    fuzzyfinder.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
    try:
        nosuch._load_in_background("/no/such/dir-xyz", dialog)
    finally:
        fuzzyfinder.GLib = saved_glib
    assert nosuch._cached_files("/no/such/dir-xyz") is None


def test_selection_css_is_explicit():
    css = fuzzyfinder.selection_css()
    # Both focused and unfocused states must use the same vivid colour, because
    # the search entry keeps focus while the results list is navigated.
    assert "treeview.view:selected" in css
    assert "treeview.view:selected:focus" in css
    assert "background-color" in css


def test_dialog_applies_selection_css():
    if fuzzyfinder.Gtk is None:
        pytest.skip("no Gtk")
    try:
        dialog = fuzzyfinder.FuzzyFinderDialog(parent=None)
    except Exception as e:
        pytest.skip(f"no display: {e}")
    try:
        assert dialog._selection_css is not None
    finally:
        try:
            dialog.destroy()
        except Exception:
            pass


def test_entry_ctrl_n_p_and_paging():

    if fuzzyfinder.Gtk is None or fuzzyfinder.Gdk is None:
        pytest.skip("no Gtk/Gdk")
    try:
        dialog = fuzzyfinder.FuzzyFinderDialog(parent=None)
    except Exception as e:
        pytest.skip(f"no display: {e}")
    try:
        items = [(f"file{i:02d}.txt", f"/root/file{i:02d}.txt") for i in range(30)]
        dialog.set_files(items)
        Gdk = fuzzyfinder.Gdk
        ctrl = int(Gdk.ModifierType.CONTROL_MASK)

        def press(name, state=0):
            return dialog._on_entry_key(
                dialog._entry,
                types.SimpleNamespace(keyval=Gdk.keyval_from_name(name), state=state),
            )

        assert dialog._current_index() == 0
        assert press("n", ctrl) is True
        assert dialog._current_index() == 1
        assert press("p", ctrl) is True
        assert dialog._current_index() == 0
        assert press("Page_Down") is True
        assert dialog._current_index() == 10
        assert press("Page_Up") is True
        assert dialog._current_index() == 0
        assert press("End") is True
        assert dialog._current_index() == 29
        assert press("Home") is True
        assert dialog._current_index() == 0
        # Plain n/p without Ctrl still filters text instead of navigating.
        assert press("n") is False
        assert press("p") is False
    finally:
        try:
            dialog.destroy()
        except Exception:
            pass


def test_direct_file_path_out_of_tree():
    if fuzzyfinder.Gtk is None:
        pytest.skip("no Gtk")
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"out of tree content")
        out_path = f.name
    try:
        try:
            dialog = fuzzyfinder.FuzzyFinderDialog(parent=None)
        except Exception as e:
            pytest.skip(f"no display: {e}")
        try:
            dialog.set_files([("in_tree.txt", "/project/in_tree.txt")])
            dialog._entry.set_text(out_path)
            # Refilter should recognize the out of tree path and put it at top of store
            assert len(dialog._store) >= 1
            selected = dialog._selected_path()
            assert selected == out_path

            opened: list[str] = []
            dialog.connect("open-file", lambda _w, p: opened.append(p))
            dialog._activate_selected()
            assert opened == [out_path]
        finally:
            try:
                dialog.destroy()
            except Exception:
                pass
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)