"""project-mode tree background CSS (headless)."""

import os
import sys

import pytest


import thor.project as projectmode


def test_tree_bg_css_darkens_theme_base_by_20pct():
    css = projectmode.tree_bg_css()
    assert "shade(@theme_base_color, 0.8)" in css


def test_tree_bg_css_covers_gtk3_selectors():
    css = projectmode.tree_bg_css()
    assert "treeview.view" in css
    assert "GtkTreeView" in css
    assert "background-color" in css


def test_tree_bg_css_parses_when_gtk_available():
    if projectmode.Gtk is None:
        pytest.skip("no Gtk")
    provider = projectmode.Gtk.CssProvider()
    provider.load_from_data(projectmode.tree_bg_css().encode("utf-8"))


def _display():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        result = Gtk.init_check()
        ok = result[0] if isinstance(result, tuple) else bool(result)
        if not ok:
            return None
        return Gtk
    except Exception:
        return None


def _pump(Gtk, seconds):
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.05)


def _row_col(browser, label, col):
    store = browser.store
    found = {}

    def walk(tree_iter):
        current = tree_iter
        while current is not None:
            found[store.get_value(current, 1)] = store.get_value(current, col)
            try:
                child = store.iter_children(current)
            except Exception:
                child = None
            if child is not None:
                walk(child)
            try:
                current = store.iter_next(current)
            except Exception:
                break

    walk(store.get_iter_first())
    return found.get(label)


def _row_fg(browser, label):
    return _row_col(browser, label, 4)


def _row_fg_set(browser, label):
    return _row_col(browser, label, browser._col_fg_set)


def test_rebuild_keeps_colors_when_status_unchanged():
    """Rebuild after an edit that keeps git status must not drop colors.

    Needs a display; skipped headless like the popup tests.
    """
    import shutil
    import subprocess
    import tempfile

    Gtk = _display()
    if Gtk is None or projectmode.Gtk is None:
        pytest.skip("no display")
    if shutil.which("git") is None:
        pytest.skip("no git")
    tmp = tempfile.mkdtemp(prefix="browser-recolor-")

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    git("init")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    target = os.path.join(tmp, "a.txt")
    with open(target, "w") as f:
        f.write("one\n")
    clean = os.path.join(tmp, "b.txt")
    with open(clean, "w") as f:
        f.write("clean\n")
    git("add", "a.txt", "b.txt")
    git("commit", "-m", "init")
    with open(target, "w") as f:
        f.write("one\ntwo\n")  # unstaged modification

    browser = projectmode.ProjectBrowser()
    win = Gtk.Window()
    win.add(browser)
    win.show_all()
    try:
        browser.set_root(tmp)
        _pump(Gtk, 8)
        assert _row_fg(browser, "a.txt") == projectmode._gitstatus.COLOR_MODIFIED
        assert _row_fg_set(browser, "a.txt") is True
        assert _row_fg(browser, "b.txt") is None
        assert _row_fg_set(browser, "b.txt") is False
        assert browser._col_fg_set == 5
        assert browser.store.get_n_columns() == 6
        assert _row_fg_set(browser, os.path.basename(tmp) + "/") is True
        with open(target, "w") as f:
            f.write("one\ntwo\nthree\n")  # external edit, still M
        _pump(Gtk, 8)
        assert _row_fg(browser, "a.txt") == projectmode._gitstatus.COLOR_MODIFIED
        assert _row_fg_set(browser, "a.txt") is True
        assert _row_fg(browser, "b.txt") is None
        assert _row_fg_set(browser, "b.txt") is False
        assert _row_fg(browser, os.path.basename(tmp) + "/") == projectmode._gitstatus.COLOR_MODIFIED
        assert _row_fg_set(browser, os.path.basename(tmp) + "/") is True
    finally:
        try:
            browser.cleanup()
        except Exception:
            pass
        win.destroy()
