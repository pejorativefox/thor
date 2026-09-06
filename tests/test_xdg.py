"""Unit tests for thor.xdg Base Directory resolution (headless, no GTK)."""

import os
import tempfile
from thor import xdg


def test_xdg_defaults_without_env():
    saved_env = dict(os.environ)
    for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_BIN_HOME", "THOR_STYLE_DIR", "THOR_LANG_DIR"):
        os.environ.pop(k, None)
    try:
        home = os.path.expanduser("~")
        assert xdg.data_home() == os.path.join(home, ".local", "share")
        assert xdg.config_home() == os.path.join(home, ".config")
        assert xdg.cache_home() == os.path.join(home, ".cache")
        assert xdg.state_home() == os.path.join(home, ".local", "state")
        assert xdg.bin_home() == os.path.join(home, ".local", "bin")
        assert xdg.desktop_dir() == os.path.join(home, ".local", "share", "applications")
        assert xdg.icon_hicolor_dir("scalable", "apps") == os.path.join(home, ".local", "share", "icons", "hicolor", "scalable", "apps")
        assert xdg.icon_hicolor_dir("256x256", "apps") == os.path.join(home, ".local", "share", "icons", "hicolor", "256x256", "apps")
        assert xdg.log_dir() == os.path.join(home, ".local", "state", "thor", "logs")
        assert xdg.marker_log_path() == os.path.join(home, ".local", "state", "thor", "logs", "thor-csharp.log")
        assert xdg.pending_root_path() == os.path.join(home, ".cache", "thor", "project-mode", "pending-root")
    finally:
        os.environ.clear()
        os.environ.update(saved_env)


def test_xdg_env_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        d_data = os.path.join(tmp, "my_data")
        d_config = os.path.join(tmp, "my_config")
        d_cache = os.path.join(tmp, "my_cache")
        d_state = os.path.join(tmp, "my_state")
        d_bin = os.path.join(tmp, "my_bin")
        d_styles = os.path.join(tmp, "custom_styles")
        d_lang = os.path.join(tmp, "custom_lang")

        saved_env = dict(os.environ)
        os.environ["XDG_DATA_HOME"] = d_data
        os.environ["XDG_CONFIG_HOME"] = d_config
        os.environ["XDG_CACHE_HOME"] = d_cache
        os.environ["XDG_STATE_HOME"] = d_state
        os.environ["XDG_BIN_HOME"] = d_bin
        os.environ["THOR_STYLE_DIR"] = d_styles
        os.environ["THOR_LANG_DIR"] = d_lang

        try:
            assert xdg.data_home() == os.path.abspath(d_data)
            assert xdg.config_home() == os.path.abspath(d_config)
            assert xdg.cache_home() == os.path.abspath(d_cache)
            assert xdg.state_home() == os.path.abspath(d_state)
            assert xdg.bin_home() == os.path.abspath(d_bin)
            assert xdg.desktop_dir() == os.path.join(os.path.abspath(d_data), "applications")
            assert xdg.log_dir() == os.path.join(os.path.abspath(d_state), "thor", "logs")
            assert xdg.marker_log_path() == os.path.join(os.path.abspath(d_state), "thor", "logs", "thor-csharp.log")
            assert xdg.pending_root_path() == os.path.join(os.path.abspath(d_cache), "thor", "project-mode", "pending-root")

            s_dirs = xdg.styles_dirs()
            assert os.path.abspath(d_styles) in s_dirs
            assert os.path.join(os.path.abspath(d_data), "thor", "styles") in s_dirs
            assert os.path.join(os.path.abspath(d_data), "gtksourceview-4", "styles") in s_dirs

            l_dirs = xdg.lang_dirs()
            assert os.path.abspath(d_lang) in l_dirs
            assert os.path.join(os.path.abspath(d_data), "gtksourceview-4", "language-specs") in l_dirs
        finally:
            os.environ.clear()
            os.environ.update(saved_env)


def test_ensure_dir_creates_path():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "a", "b", "c")
        assert not os.path.exists(target)
        result = xdg.ensure_dir(target)
        assert result == target
        assert os.path.isdir(target)
