"""project marker detection + thor-code launcher (headless)."""

import os
import tempfile

import thor.project as projectmode


def test_is_unsafe_root():
    home = os.path.realpath(os.path.expanduser("~"))
    assert projectmode.is_unsafe_root(home) is True
    assert projectmode.is_unsafe_root("/") is True
    assert projectmode.is_unsafe_root(os.path.dirname(home)) is True
    with tempfile.TemporaryDirectory() as tmp:
        assert projectmode.is_unsafe_root(tmp) is False


def _code_module():
    from importlib.machinery import SourceFileLoader

    path = os.path.join(os.path.dirname(__file__), "..", "thor-code")
    return SourceFileLoader("thor_code", path).load_module()


def test_code_resolve_target():
    thor_code = _code_module()
    assert thor_code.resolve_target(["thor-code"]) == os.path.abspath(".")
    assert thor_code.resolve_target(["thor-code", "--new-window"]) == os.path.abspath(".")
    assert thor_code.resolve_target(["thor-code", "-h"]) is None
    assert thor_code.resolve_target(["thor-code", "--help"]) is None
    with tempfile.TemporaryDirectory() as tmp:
        sub = os.path.join(tmp, "proj")
        assert thor_code.resolve_target(["thor-code", sub]) == os.path.abspath(sub)


def test_code_main_help_and_bad_dir():
    thor_code = _code_module()
    assert thor_code.main(["thor-code", "--help"]) == 0
    assert thor_code.main(["thor-code", "/no/such/dir"]) == 2
    assert thor_code.launch("/no/such/dir") == 2
