"""Unit tests for XDG Desktop Entry and application icons (headless)."""

import configparser
import os

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP_PATH = os.path.join(REPO_DIR, "data", "dev.thor.Editor.desktop")
ICON_SVG_PATH = os.path.join(REPO_DIR, "data", "icons", "dev.thor.Editor.svg")
ICON_PNG_PATH = os.path.join(REPO_DIR, "data", "icons", "dev.thor.Editor.png")
INSTALL_SCRIPT_PATH = os.path.join(REPO_DIR, "install.sh")


def test_desktop_entry_exists_and_valid():
    assert os.path.isfile(DESKTOP_PATH), f"Missing {DESKTOP_PATH}"
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(DESKTOP_PATH, encoding="utf-8")

    assert "Desktop Entry" in parser.sections()
    entry = parser["Desktop Entry"]
    assert entry.get("Type") == "Application"
    assert entry.get("Name") == "Thor"
    assert "thor" in entry.get("Exec", "")
    assert entry.get("Icon") == "dev.thor.Editor"
    assert entry.get("Terminal") == "false"
    assert "TextEditor" in entry.get("Categories", "")
    assert entry.get("StartupWMClass") == "dev.thor.Editor"
    assert "NewWindow" in entry.get("Actions", "")

    assert "Desktop Action NewWindow" in parser.sections()
    action = parser["Desktop Action NewWindow"]
    assert "thor --new-window" in action.get("Exec", "")


def test_icon_assets_exist():
    assert os.path.isfile(ICON_SVG_PATH), f"Missing {ICON_SVG_PATH}"
    assert os.path.isfile(ICON_PNG_PATH), f"Missing {ICON_PNG_PATH}"

    with open(ICON_SVG_PATH, encoding="utf-8") as f:
        svg_content = f.read()
    assert "<svg" in svg_content
    assert "</svg>" in svg_content

    # PNG magic bytes
    with open(ICON_PNG_PATH, "rb") as f:
        header = f.read(8)
    assert header.startswith(b"\x89PNG\r\n\x1a\n")


def test_install_script_installs_xdg_assets():
    with open(INSTALL_SCRIPT_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "dev.thor.Editor.desktop" in text
    assert "dev.thor.Editor.svg" in text
    assert "dev.thor.Editor.png" in text
    assert "gtksourceview-4/styles" in text
    assert "thor/styles" in text
    assert "gtksourceview-4/language-specs" in text
    assert "thor/logs" in text
