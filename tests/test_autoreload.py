"""Unit tests for autoreload (headless, no GTK)."""

import os
import sys
import tempfile
import types


import thor.autoreload as ar


class FakeLocation:
    def __init__(self, path="/tmp/Foo.cs"):
        self._path = path

    def get_path(self):
        return self._path


class FakeIter:
    def __init__(self, line=41):
        self._line = line

    def get_line(self):
        return self._line


class FakeDoc:
    def __init__(self, modified=False, location="set", line=41, text="",
                 source_file="auto", lines=100):
        self._modified = modified
        if location == "set":
            self._location = FakeLocation()
        elif isinstance(location, str):
            self._location = FakeLocation(location)
        else:
            self._location = location
        self._line = line
        self._text = text
        self._source_file = object() if source_file == "auto" else source_file
        self._lines = lines
        self.placed_cursor = []

    def get_location(self):
        return self._location

    def get_file(self):
        if isinstance(self._source_file, Exception):
            raise self._source_file
        return self._source_file

    def get_modified(self):
        return self._modified

    def get_insert(self):
        return object()

    def get_iter_at_mark(self, _mark):
        return FakeIter(self._line)

    def get_start_iter(self):
        return object()

    def get_end_iter(self):
        return object()

    def get_text(self, _start, _end, _hidden):
        return self._text

    def get_line_count(self):
        return self._lines

    def get_iter_at_line(self, line):
        return FakeIter(line)

    def place_cursor(self, it):
        self.placed_cursor.append(it.get_line())


class FakeTab:
    STATE_NORMAL = 0
    STATE_MODIFIED = 13

    def __init__(self, doc, state=0):
        self._doc = doc
        self._state = state

    def get_state(self):
        return self._state

    def get_document(self):
        return self._doc


class FakeWindow:
    def __init__(self, docs=()):
        self._docs = list(docs)
        self.connected = []
        self.disconnected = []

    def get_documents(self):
        return list(self._docs)

    def get_tab_from_location(self, location):
        for doc in self._docs:
            if doc.get_location() is location:
                return FakeTab(doc, state=13)
        return None

    def connect(self, signal, _handler):
        self.connected.append(signal)
        return len(self.connected)

    def disconnect(self, handler_id):
        self.disconnected.append(handler_id)


class FakeGLib:
    def __init__(self):
        self.timers = {}
        self.removed = []
        self._next = 1

    def timeout_add(self, _ms, callback, *args):
        timer_id = self._next
        self._next += 1
        self.timers[timer_id] = (callback, args)
        return timer_id

    def source_remove(self, timer_id):
        self.removed.append(timer_id)
        self.timers.pop(timer_id, None)


class FakeMonitor:
    def __init__(self):
        self.cancelled = False
        self.disconnects = []

    def connect(self, _signal, *_args):
        return 7

    def disconnect(self, handler_id):
        self.disconnects.append(handler_id)

    def cancel(self):
        self.cancelled = True


class FakeGioFile:
    def __init__(self, path):
        self.path = path
        self.monitor = FakeMonitor()
        self.flags = None

    def monitor_file(self, flags, _cancellable):
        self.flags = flags
        return self.monitor


class FakeGio:
    FileMonitorFlags = types.SimpleNamespace(WATCH_MTIME=2)
    FileMonitorEvent = types.SimpleNamespace(
        CHANGED=0, CHANGES_DONE_HINT=1, CREATED=3, ATTRIBUTE_CHANGED=4,
        RENAMED=8, MOVED_IN=9, MOVED_OUT=10,
    )
    files = {}

    @classmethod
    def reset(cls):
        cls.files = {}

    def __init__(self):
        raise AssertionError("use classmethods")


def _fake_gio_new_for_path(path):
    f = FakeGioFile(path)
    FakeGio.files[path] = f
    return f


FakeGio.File = types.SimpleNamespace(new_for_path=staticmethod(_fake_gio_new_for_path))


class FakeGsFile:
    def __init__(self):
        self.location = None

    def set_location(self, location):
        self.location = location


class FakeLoader:
    created = []

    def __init__(self, buffer, gfile):
        self.buffer = buffer
        self.gfile = gfile
        self.async_calls = []
        self.finish_result = True
        self.finish_error = None

    @classmethod
    def new(cls, buffer, gfile):
        inst = cls(buffer, gfile)
        cls.created.append(inst)
        return inst

    @classmethod
    def reset(cls):
        cls.created = []

    def load_async(self, _prio, _canc, _prog, _prog_data, callback, user_data):
        self.async_calls.append((callback, user_data))

    def load_finish(self, _result):
        if self.finish_error is not None:
            raise self.finish_error
        return self.finish_result


FakeGtkSource = types.SimpleNamespace(
    File=types.SimpleNamespace(new=staticmethod(FakeGsFile)),
    FileLoader=FakeLoader,
)


def _mgr():
    mgr = ar.AutoReloadManager.__new__(ar.AutoReloadManager)
    mgr._signal_ids = []
    mgr._monitors = {}
    mgr._pending = {}
    mgr._loading = set()
    return mgr


def _patched_module(**overrides):
    saved = {}
    for name, value in overrides.items():
        saved[name] = getattr(ar, name)
        setattr(ar, name, value)
    return saved


def _restore_module(saved):
    for name, value in saved.items():
        setattr(ar, name, value)


def test_should_reload_clean_doc():
    assert ar.should_reload(FakeDoc(modified=False)) is True


def test_should_reload_skips_modified_doc():
    assert ar.should_reload(FakeDoc(modified=True)) is False


def test_should_reload_skips_untitled_doc():
    assert ar.should_reload(FakeDoc(location=None)) is False


def test_maybe_reload_clean_externally_modified_tab():
    saved = _patched_module(GtkSource=FakeGtkSource)
    FakeLoader.reset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "F.cs")
            _write(path, "on disk")
            doc = FakeDoc(modified=False, location=path, line=41, text="buffer")
            window = FakeWindow(docs=[doc])
            mgr = _mgr()
            tab = FakeTab(doc, state=13)
            assert mgr._maybe_reload(tab, window) is True
            assert len(FakeLoader.created) == 1
            loader = FakeLoader.created[0]
            assert loader.buffer is doc
            assert loader.gfile is doc.get_file()
            assert len(loader.async_calls) == 1
            callback, _ud = loader.async_calls[0]
            callback(loader, object(), None)
            assert doc.placed_cursor == [41]
    finally:
        _restore_module(saved)


def test_reload_uses_new_source_file_when_doc_has_none():
    saved = _patched_module(GtkSource=FakeGtkSource)
    FakeLoader.reset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "F2.cs")
            _write(path, "on disk")
            doc = FakeDoc(modified=False, location=path, text="buffer",
                          source_file=AttributeError("no file"))
            window = FakeWindow(docs=[doc])
            mgr = _mgr()
            assert mgr._check_and_reload(window, doc, "test") is True
            assert len(FakeLoader.created) == 1
            assert FakeLoader.created[0].gfile.location.get_path() == path
    finally:
        _restore_module(saved)


def test_reload_skips_second_load_while_in_flight():
    saved = _patched_module(GtkSource=FakeGtkSource)
    FakeLoader.reset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "F3.cs")
            _write(path, "on disk")
            doc = FakeDoc(modified=False, location=path, text="buffer")
            window = FakeWindow(docs=[doc])
            mgr = _mgr()
            assert mgr._check_and_reload(window, doc, "test") is True
            assert mgr._check_and_reload(window, doc, "test") is False
            assert len(FakeLoader.created) == 1
    finally:
        _restore_module(saved)


def test_reload_failure_restores_cursor_and_clears_loading():
    saved = _patched_module(GtkSource=FakeGtkSource)
    FakeLoader.reset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "F4.cs")
            _write(path, "on disk")
            doc = FakeDoc(modified=False, location=path, text="buffer")
            window = FakeWindow(docs=[doc])
            mgr = _mgr()
            assert mgr._check_and_reload(window, doc, "test") is True
            loader = FakeLoader.created[0]
            loader.finish_result = False
            callback, _ud = loader.async_calls[0]
            callback(loader, object(), None)
            assert doc.placed_cursor == []
            assert mgr._loading == set()
    finally:
        _restore_module(saved)


def test_maybe_reload_skips_modified_doc():
    mgr = _mgr()
    tab = FakeTab(FakeDoc(modified=True), state=13)
    assert mgr._maybe_reload(tab, FakeWindow()) is False


def test_maybe_reload_ignores_normal_state():
    mgr = _mgr()
    tab = FakeTab(FakeDoc(modified=False), state=0)
    assert mgr._maybe_reload(tab, FakeWindow()) is False


def test_state_changed_handler_finds_tab_in_args():
    window = FakeWindow()
    mgr = _mgr()
    seen = []
    mgr._maybe_reload = lambda tab, w: seen.append((tab, w)) or True  # type: ignore[method-assign]
    tab = FakeTab(FakeDoc(), state=13)
    mgr._on_tab_state_changed(window, tab)
    assert seen == [(tab, window)]


def test_sweep_reloads_externally_modified_docs():
    doc = FakeDoc(modified=False)
    window = FakeWindow(docs=[doc])
    mgr = _mgr()
    seen = []
    mgr._maybe_reload = lambda tab, w: seen.append(w) or False  # type: ignore[method-assign]
    mgr._sweep(window)
    assert seen == [window]


def test_attach_detach_wires_signals():
    window = FakeWindow()
    mgr = _mgr()
    mgr._attach(window)
    assert set(window.connected) == {
        "tab-added", "tab-removed", "active-tab-changed", "active-tab-state-changed",
    }, window.connected
    mgr._detach()
    assert sorted(window.disconnected) == [1, 2, 3, 4]


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_file_differs_detects_changes():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "A.cs")
        _write(path, "new content")
        assert ar.file_differs(FakeDoc(text="old content"), path) is True
        assert ar.file_differs(FakeDoc(text="new content"), path) is False
        assert ar.file_differs(FakeDoc(text="x"), os.path.join(tmp, "Missing.cs")) is None


def test_check_and_reload_only_when_content_differs():
    saved = _patched_module(GtkSource=FakeGtkSource)
    FakeLoader.reset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "B.cs")
            _write(path, "on disk")
            window = FakeWindow()
            mgr = _mgr()
            changed = FakeDoc(modified=False, location=path, text="old buffer")
            assert mgr._check_and_reload(window, changed, "test") is True
            assert len(FakeLoader.created) == 1
            same = FakeDoc(modified=False, location=path, text="on disk")
            assert mgr._check_and_reload(window, same, "test") is False
            assert len(FakeLoader.created) == 1
            dirty = FakeDoc(modified=True, location=path, text="old buffer")
            assert mgr._check_and_reload(window, dirty, "test") is False
            assert len(FakeLoader.created) == 1
    finally:
        _restore_module(saved)


def test_check_and_reload_skips_deleted_file():
    saved = _patched_module(GtkSource=FakeGtkSource)
    FakeLoader.reset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = _mgr()
            doc = FakeDoc(modified=False, location=os.path.join(tmp, "Gone.cs"), text="x")
            assert mgr._check_and_reload(FakeWindow(), doc, "test") is False
            assert FakeLoader.created == []
    finally:
        _restore_module(saved)


def test_file_event_debounces_and_fires():
    glib = FakeGLib()
    saved = _patched_module(GLib=glib)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "C.cs")
            _write(path, "disk v2")
            doc = FakeDoc(modified=False, location=path, text="buffer v1")
            window = FakeWindow(docs=[doc])
            mgr = _mgr()
            mgr._monitors[path] = (FakeMonitor(), 7)
            mgr._on_file_event(None, None, None, 1, window, path)
            mgr._on_file_event(None, None, None, 0, window, path)
            assert len(glib.timers) == 1
            assert glib.removed, "first timer must be cancelled"
            fires = []
            real_check = mgr._check_and_reload
            mgr._check_and_reload = lambda w, d, reason: fires.append(reason) or True  # type: ignore[method-assign]
            [(callback, args)] = list(glib.timers.values())
            callback(*args)
            assert fires == ["file changed on disk"]
            assert real_check is not None
    finally:
        _restore_module(saved)


def test_file_event_ignores_unwatched_codes():
    glib = FakeGLib()
    saved = _patched_module(GLib=glib)
    try:
        mgr = _mgr()
        mgr._on_file_event(None, None, None, 2, FakeWindow(), "/tmp/X.cs")
        assert glib.timers == {}
    finally:
        _restore_module(saved)


def test_fire_unwatches_closed_doc():
    mgr = _mgr()
    monitor = FakeMonitor()
    mgr._monitors["/tmp/Closed.cs"] = (monitor, 7)
    assert mgr._fire(FakeWindow(docs=[]), "/tmp/Closed.cs") is False
    assert "/tmp/Closed.cs" not in mgr._monitors
    assert monitor.cancelled


def test_sync_watches_adds_and_removes():
    FakeGio.reset()
    saved = _patched_module(Gio=FakeGio)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            p1 = os.path.join(tmp, "D.cs")
            p2 = os.path.join(tmp, "E.cs")
            _write(p1, "a")
            _write(p2, "b")
            window = FakeWindow(docs=[FakeDoc(location=p1), FakeDoc(location=p2)])
            mgr = _mgr()
            mgr._sync_watches(window)
            assert set(mgr._monitors) == {p1, p2}
            assert set(FakeGio.files) == {p1, p2}
            window = FakeWindow(docs=[FakeDoc(location=p1)])
            mgr._sync_watches(window)
            assert set(mgr._monitors) == {p1}
            assert FakeGio.files[p2].monitor.cancelled
    finally:
        _restore_module(saved)


def test_sync_watches_without_gio_is_noop():
    saved = _patched_module(Gio=None)
    try:
        mgr = _mgr()
        mgr._sync_watches(FakeWindow(docs=[FakeDoc()]))
        assert mgr._monitors == {}
    finally:
        _restore_module(saved)


def test_file_differs_ignores_implicit_trailing_newline():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "N.cs")
        _write(path, "one\ntwo\n")
        assert ar.file_differs(FakeDoc(text="one\ntwo"), path) is False
        assert ar.file_differs(FakeDoc(text="one\ntwo\n"), path) is False
        assert ar.file_differs(FakeDoc(text="one\nTWO"), path) is True
        assert ar.file_differs(FakeDoc(text="x"), os.path.join(tmp, "Missing.cs")) is None


class FakeAdj:
    """Adjustment with mutable extent; records every set_value."""

    def __init__(self, upper=2000.0, page=500.0, value=0.0):
        self._upper = float(upper)
        self._page = float(page)
        self.value = float(value)
        self.sets = []

    def get_upper(self):
        return self._upper

    def get_page_size(self):
        return self._page

    def get_value(self):
        return self.value

    def set_value(self, v):
        self.value = float(v)
        self.sets.append(self.value)


class FakeView:
    def __init__(self, mapped=True, vadj=None, hadj=None):
        self.mapped = mapped
        self.vadj = vadj or FakeAdj()
        self.hadj = hadj or FakeAdj()
        self.handlers = {}
        self._next = 1

    def get_vadjustment(self):
        return self.vadj

    def get_hadjustment(self):
        return self.hadj

    def get_mapped(self):
        return self.mapped

    def connect(self, signal, handler):
        hid = self._next
        self._next += 1
        self.handlers.setdefault(signal, []).append((hid, handler))
        return hid

    def disconnect(self, hid):
        for signal, lst in self.handlers.items():
            for i, (h, _handler) in enumerate(lst):
                if h == hid:
                    lst.pop(i)
                    return

    def fire(self, signal, *args):
        for _hid, handler in list(self.handlers.get(signal, [])):
            handler(*args)


class FakeIdleGLib:
    """Records idle callbacks so tests can step the main-loop deferral."""

    def __init__(self):
        self.idles = []
        self._next = 1

    def idle_add(self, callback):
        self.idles.append(callback)
        return self._next

    def step(self):
        """Run one queued idle (LIFO like GLib priority), if any."""
        if not self.idles:
            return None
        cb = self.idles.pop()
        cb()
        return cb


class FakeViewTab:
    def __init__(self, view):
        self._view = view

    def get_view(self):
        return self._view


class FakeViewWindow:
    def __init__(self, doc, view):
        self._doc = doc
        self._view = view

    def get_tab_from_location(self, _location):
        return FakeViewTab(self._view)


def _view_snapshot(line=41, offset=0, v=1500.0, h=60.0):
    return (line, offset, v, h)


def test_restore_position_defers_scroll_until_idle():
    """Regression: scroll must not be set synchronously after a reload, when
    the text view's extents are still stale — it is applied on an idle."""
    glib = FakeIdleGLib()
    vadj = FakeAdj(upper=2000.0, page=500.0)
    hadj = FakeAdj(upper=4000.0, page=500.0)
    view = FakeView(mapped=True, vadj=vadj, hadj=hadj)
    doc = FakeDoc(modified=False)
    window = FakeViewWindow(doc, view)
    saved = _patched_module(GLib=glib)
    try:
        assert ar.restore_position(window, doc, _view_snapshot()) is True
        # cursor restored immediately (mark-based, layout-independent)
        assert doc.placed_cursor == [41]
        # scroll deferred: nothing set until the idle runs
        assert vadj.sets == []
        assert hadj.sets == []
        glib.step()
        assert vadj.value == 1500.0
        assert hadj.value == 60.0
        # target fits the final extent: no further retries queued
        assert glib.idles == []
    finally:
        _restore_module(saved)


def test_restore_position_retries_until_extent_finalizes():
    """When the first idle still sees a stale ~page-sized extent, the apply
    re-queues and lands on the saved offset once GTK grows the extent."""
    glib = FakeIdleGLib()
    # stale extent right after reload: upper ~ page size -> would clamp to ~0
    vadj = FakeAdj(upper=600.0, page=500.0)
    hadj = FakeAdj(upper=4000.0, page=500.0)
    view = FakeView(mapped=True, vadj=vadj, hadj=hadj)
    doc = FakeDoc(modified=False)
    window = FakeViewWindow(doc, view)
    saved = _patched_module(GLib=glib)
    try:
        ar.restore_position(window, doc, _view_snapshot())
        glib.step()  # first idle: extent still stale -> clamped low, retry queued
        assert vadj.value == min(1500.0, 600.0 - 500.0)
        assert glib.idles, "stale extent must re-queue the apply"
        # GTK recomputes the real extent before the next idle
        vadj._upper = 64000.0
        glib.step()
        assert vadj.value == 1500.0
        assert hadj.value == 60.0
    finally:
        _restore_module(saved)


def test_restore_position_unmapped_view_waits_for_map():
    """Hidden notebook pages have no final extents until mapped: the scroll
    apply must wait for the view's map signal."""
    glib = FakeIdleGLib()
    vadj = FakeAdj(upper=2000.0, page=500.0)
    hadj = FakeAdj(upper=4000.0, page=500.0)
    view = FakeView(mapped=False, vadj=vadj, hadj=hadj)
    doc = FakeDoc(modified=False)
    window = FakeViewWindow(doc, view)
    saved = _patched_module(GLib=glib)
    try:
        assert ar.restore_position(window, doc, _view_snapshot()) is True
        assert view.handlers.get("map"), "unmapped view must wait for map"
        assert vadj.sets == []
        # user switches to the tab: map fires, then the idle applies
        view.fire("map")
        assert glib.idles
        glib.step()
        assert vadj.value == 1500.0
        assert hadj.value == 60.0
        # one-shot: handler disconnected after firing
        assert view.handlers.get("map") == []
    finally:
        _restore_module(saved)


def test_restore_position_clamps_to_shorter_doc():
    """A reloaded doc that got shorter clamps cursor + scroll to fit."""
    glib = FakeIdleGLib()
    vadj = FakeAdj(upper=1600.0, page=500.0)  # e.g. 100 lines at 16px
    hadj = FakeAdj(upper=4000.0, page=500.0)
    view = FakeView(mapped=True, vadj=vadj, hadj=hadj)
    doc = FakeDoc(modified=False, lines=100)
    window = FakeViewWindow(doc, view)
    saved = _patched_module(GLib=glib)
    try:
        ar.restore_position(window, doc, _view_snapshot(line=1500, v=24012.0))
        assert doc.placed_cursor == [99]  # last line of the shorter doc
        glib.step()
        assert vadj.value == 1600.0 - 500.0  # pinned at the new bottom
        # extent stops growing (genuinely shorter): clamp is final, no loop
        assert glib.idles
        glib.step()
        assert vadj.value == 1600.0 - 500.0
        assert glib.idles == []
    finally:
        _restore_module(saved)
