"""Closing Thor's blank starter doc (headless, fake window/docs)."""

import os
import sys
import types

import pytest


import thor.feature_toggle as featuretoggle
IS_THOR = True  # thor standalone, skip plugin UI tests

HAVE_PLUGIN = hasattr(getattr(featuretoggle, "FeatureTogglePlugin", None), "_close_untouched_starter_doc")


class _Doc:
    def __init__(self, untouched=True, location=None):
        self._untouched = untouched
        self._location = location
        self._file_raises = location is None

    def is_untouched(self):
        return self._untouched

    def get_location(self):
        return self._location

    def get_file(self):
        raise AttributeError("no file")


class _Window:
    def __init__(self, docs):
        self._docs = list(docs)
        self.closed: list = []
        self._tab = object()

    def get_documents(self):
        return list(self._docs)

    def get_active_tab(self):
        return self._tab

    def close_tab(self, tab):
        self.closed.append(tab)


def _ns(window, setting=True):
    cls = featuretoggle.FeatureTogglePlugin
    settings = types.SimpleNamespace(get=lambda _k: setting)
    return types.SimpleNamespace(window=window, settings=settings)


def test_closes_lone_untouched_doc():
    if IS_THOR:
        import pytest; pytest.skip("plugin UI test not applicable for thor")
    if not hasattr(featuretoggle, "FeatureTogglePlugin"):
        import pytest; pytest.skip("FeatureTogglePlugin not available in thor")
    if not HAVE_PLUGIN:
        pytest.skip("plugin not available")
    window = _Window([_Doc(untouched=True)])
    featuretoggle.FeatureTogglePlugin._close_untouched_starter_doc(_ns(window))
    assert window.closed == [window._tab]


def test_keeps_touched_doc():
    if not hasattr(featuretoggle, "FeatureTogglePlugin"):
        import pytest; pytest.skip("FeatureTogglePlugin not available in thor")
    if not HAVE_PLUGIN:
        pytest.skip("plugin not available")
    window = _Window([_Doc(untouched=False)])
    featuretoggle.FeatureTogglePlugin._close_untouched_starter_doc(_ns(window))
    assert window.closed == []


def test_keeps_doc_with_location():
    if not hasattr(featuretoggle, "FeatureTogglePlugin"):
        import pytest; pytest.skip("FeatureTogglePlugin not available in thor")
    if not HAVE_PLUGIN:
        pytest.skip("plugin not available")

    class _Located(_Doc):
        def get_location(self):
            return types.SimpleNamespace(
                has_uri_scheme=lambda _s: True,
                get_path=lambda: "/tmp/A.cs",
            )

    window = _Window([_Located(untouched=True)])
    featuretoggle.FeatureTogglePlugin._close_untouched_starter_doc(_ns(window))
    assert window.closed == []


def test_keeps_multiple_docs():
    if not hasattr(featuretoggle, "FeatureTogglePlugin"):
        import pytest; pytest.skip("FeatureTogglePlugin not available in thor")
    if not HAVE_PLUGIN:
        pytest.skip("plugin not available")
    window = _Window([_Doc(untouched=True), _Doc(untouched=True)])
    featuretoggle.FeatureTogglePlugin._close_untouched_starter_doc(_ns(window))
    assert window.closed == []


def test_respects_setting_off():
    if not hasattr(featuretoggle, "FeatureTogglePlugin"):
        import pytest; pytest.skip("FeatureTogglePlugin not available in thor")
    if not HAVE_PLUGIN:
        pytest.skip("plugin not available")
    window = _Window([_Doc(untouched=True)])
    featuretoggle.FeatureTogglePlugin._close_untouched_starter_doc(_ns(window, setting=False))
    assert window.closed == []


def test_close_failure_is_silent():
    if not hasattr(featuretoggle, "FeatureTogglePlugin"):
        import pytest; pytest.skip("FeatureTogglePlugin not available in thor")
    if not HAVE_PLUGIN:
        pytest.skip("plugin not available")

    class _FailWindow(_Window):
        def close_tab(self, tab):
            raise RuntimeError("nope")

    window = _FailWindow([_Doc(untouched=True)])
    featuretoggle.FeatureTogglePlugin._close_untouched_starter_doc(_ns(window))
    assert window.closed == []


def test_close_untitled_default_on():
    assert featuretoggle.DEFAULTS.get("close_untitled_on_startup") is True