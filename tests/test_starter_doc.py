"""Closing Thor's blank starter doc (headless, fake window/docs)."""

import types

import thor.feature_toggle as featuretoggle


class _Doc:
    def __init__(self, untouched=True, location=None):
        self._untouched = untouched
        self._location = location

    def is_untouched(self):
        return self._untouched

    def get_location(self):
        return self._location

    def get_file(self):
        raise AttributeError("no file")


class _Window:
    def __init__(self, docs, settings=None):
        self._docs = list(docs)
        self.closed: list = []
        self._tab = object()
        self._thor_feature_settings = settings

    def get_documents(self):
        return list(self._docs)

    def get_active_tab(self):
        return self._tab

    def close_tab(self, tab):
        self.closed.append(tab)


def _settings(enabled=True):
    return types.SimpleNamespace(get=lambda _k, _default=None: enabled)


def test_closes_lone_untouched_doc():
    window = _Window([_Doc(untouched=True)])
    featuretoggle._close_untouched_starter_doc(window)
    assert window.closed == [window._tab]


def test_keeps_touched_doc():
    window = _Window([_Doc(untouched=False)])
    featuretoggle._close_untouched_starter_doc(window)
    assert window.closed == []


def test_keeps_doc_with_location():
    class _Located(_Doc):
        def get_location(self):
            return types.SimpleNamespace(
                has_uri_scheme=lambda _s: True,
                get_path=lambda: "/tmp/A.cs",
            )

    window = _Window([_Located(untouched=True)])
    featuretoggle._close_untouched_starter_doc(window)
    assert window.closed == []


def test_keeps_multiple_docs():
    window = _Window([_Doc(untouched=True), _Doc(untouched=True)])
    featuretoggle._close_untouched_starter_doc(window)
    assert window.closed == []


def test_respects_setting_off():
    window = _Window([_Doc(untouched=True)], settings=_settings(False))
    featuretoggle._close_untouched_starter_doc(window)
    assert window.closed == []


def test_close_failure_is_silent():
    class _FailWindow(_Window):
        def close_tab(self, tab):
            raise RuntimeError("nope")

    window = _FailWindow([_Doc(untouched=True)])
    featuretoggle._close_untouched_starter_doc(window)
    assert window.closed == []


def test_close_untitled_default_on():
    assert featuretoggle.DEFAULTS.get("close_untitled_on_startup") is True


def test_attach_schedules_close_and_detaches():
    """attach() stores settings + schedules the starter-doc close via idle."""

    class _IdleWindow(_Window):
        def __init__(self, docs):
            super().__init__(docs)
            self.idle_fns = []

        def schedule(self, fn):
            self.idle_fns.append(fn)
            return 1

    window = _IdleWindow([_Doc(untouched=True)])
    saved = featuretoggle.GLib
    featuretoggle.GLib = types.SimpleNamespace(idle_add=window.schedule)
    try:
        assert featuretoggle.attach(window) is True
    finally:
        featuretoggle.GLib = saved
    assert window._thor_feature_settings is not None
    assert len(window.idle_fns) == 1
    # Running the scheduled idle closes the starter doc.
    for fn in window.idle_fns:
        fn()
    assert window.closed == [window._tab]
    featuretoggle.detach(window)
    assert not hasattr(window, "_thor_feature_settings")
