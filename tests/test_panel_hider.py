"""Panel hider shortcuts and pane visibility logic (headless)."""

import types

import thor.panel_hider as panelhider


class _FakeWidget:
    def __init__(self, visible=True, effective=None):
        self._visible = visible
        self._effective = visible if effective is None else effective
        self.calls: list = []
        self._parent = None

    def get_visible(self):
        return self._visible

    def is_visible(self):
        return self._effective

    def set_visible(self, value):
        self.calls.append(bool(value))
        self._visible = bool(value)
        self._effective = bool(value)

    def set_parent(self, parent):
        self._parent = parent

    def get_parent(self):
        return self._parent


class _FakePaned:
    def __init__(self, position, max_position=1000, child1=None, child2=None):
        self._position = position
        self._max = max_position
        self._child1 = child1
        self._child2 = child2
        self.sets: list = []

    def get_position(self):
        return self._position

    def set_position(self, value):
        self.sets.append(value)
        self._position = value

    def get_property(self, name):
        assert name == "max-position"
        return self._max

    def get_child1(self):
        return self._child1

    def get_child2(self):
        return self._child2


def _window_with(side=None, bottom=None, panel_state=None):
    return types.SimpleNamespace(
        get_side_panel=lambda: side,
        get_bottom_panel=lambda: bottom,
        _panel_state=panel_state or {},
    )


def test_hide_all_panels_sets_both_false():
    side = _FakeWidget(visible=True)
    bottom = _FakeWidget(visible=True)
    window = _window_with(side=side, bottom=bottom)
    panelhider._hide_all_panels(window)
    assert side.calls == [False]
    assert bottom.calls == [False]


def test_show_all_panels_sets_both_true():
    side = _FakeWidget(visible=False)
    bottom = _FakeWidget(visible=False)
    window = _window_with(side=side, bottom=bottom)
    panelhider._show_all_panels(window)
    assert side.calls == [True]
    assert bottom.calls == [True]


def test_toggle_bottom_flips_current_state():
    bottom = _FakeWidget(visible=True)
    window = _window_with(bottom=bottom)
    panelhider._toggle_bottom_panel(window)
    panelhider._toggle_bottom_panel(window)
    assert bottom.calls == [False, True]


def test_toggle_side_only_touches_side():
    side = _FakeWidget(visible=True)
    bottom = _FakeWidget(visible=True)
    window = _window_with(side=side, bottom=bottom)
    panelhider._toggle_side_panel(window)
    assert side.calls == [False]
    assert bottom.calls == []


def test_toggle_all_hides_when_anything_visible():
    side = _FakeWidget(visible=False)
    bottom = _FakeWidget(visible=True)
    window = _window_with(side=side, bottom=bottom)
    panelhider._toggle_all_panels(window)
    assert side.calls == [False]
    assert bottom.calls == [False]


def test_toggle_all_shows_when_both_hidden():
    side = _FakeWidget(visible=False, effective=False)
    bottom = _FakeWidget(visible=False, effective=False)
    window = _window_with(side=side, bottom=bottom)
    panelhider._toggle_all_panels(window)
    assert side.calls == [True]
    assert bottom.calls == [True]


def test_toggle_reads_effective_visibility():
    bottom = _FakeWidget(visible=True, effective=False)
    window = _window_with(bottom=bottom)
    panelhider._toggle_bottom_panel(window)
    assert bottom.calls == [True]


def test_bottom_collapsed_at_max_restores():
    widget = _FakeWidget(visible=True, effective=True)
    paned = _FakePaned(2147483647, max_position=2147483647, child2=widget)
    widget.set_parent(paned)
    window = _window_with(bottom=widget, panel_state={"bottom_panel_size": 287})
    assert panelhider._fix_pane_size(window, "bottom") is True
    assert paned.sets == [2147483647 - 287]


def test_bottom_at_zero_restores():
    widget = _FakeWidget(visible=True, effective=True)
    paned = _FakePaned(0, max_position=1000, child2=widget)
    widget.set_parent(paned)
    window = _window_with(bottom=widget, panel_state={"bottom_panel_size": 287})
    assert panelhider._fix_pane_size(window, "bottom") is True
    assert paned.sets == [713]


def test_sane_pane_size_untouched():
    widget = _FakeWidget(visible=True, effective=True)
    paned = _FakePaned(500, max_position=1000, child2=widget)
    widget.set_parent(paned)
    window = _window_with(bottom=widget, panel_state={"bottom_panel_size": 287})
    assert panelhider._fix_pane_size(window, "bottom") is False
    assert paned.sets == []


def test_side_collapsed_at_zero_restores():
    widget = _FakeWidget(visible=True, effective=True)
    paned = _FakePaned(0, max_position=900, child1=widget)
    widget.set_parent(paned)
    window = _window_with(side=widget, panel_state={"side_panel_size": 265})
    assert panelhider._fix_pane_size(window, "side") is True
    assert paned.sets == [265]


def test_pane_size_falls_back_to_default_without_state():
    widget = _FakeWidget(visible=True, effective=True)
    paned = _FakePaned(0, max_position=1000, child2=widget)
    widget.set_parent(paned)
    window = _window_with(bottom=widget)
    assert panelhider._fix_pane_size(window, "bottom") is True
    assert paned.sets == [700]  # max_position - default 300


def test_global_keys_dispatch_toggles():
    calls: list[str] = []
    window = _window_with()
    saved_hide = panelhider._hide_all_panels
    saved_bottom = panelhider._toggle_bottom_panel
    saved_side = panelhider._toggle_side_panel
    panelhider._hide_all_panels = lambda w: calls.append("hide")
    panelhider._toggle_bottom_panel = lambda w: calls.append("bottom")
    panelhider._toggle_side_panel = lambda w: calls.append("side")
    try:
        assert panelhider._handle_global_key(window, "b", True, False, False) is True
        assert panelhider._handle_global_key(window, "j", True, False, False) is True
        assert panelhider._handle_global_key(window, "e", True, False, False) is True
    finally:
        panelhider._hide_all_panels = saved_hide
        panelhider._toggle_bottom_panel = saved_bottom
        panelhider._toggle_side_panel = saved_side
    assert calls == ["hide", "bottom", "side"]


def test_global_keys_reject_wrong_modifiers():
    window = _window_with()
    assert panelhider._handle_global_key(window, "b", False, False, False) is False
    assert panelhider._handle_global_key(window, "b", True, True, False) is False
    assert panelhider._handle_global_key(window, "b", True, False, True) is False
    assert panelhider._handle_global_key(window, "x", True, False, False) is False
