"""Dock opt-in, worker lifecycle and virtual-gift drops, without native AX calls."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import threading
import time

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter, Rect
from backyard_visitor.model import Gift


class FakeRecycleAdapter(DesktopAdapter):
    """No native libraries, subprocesses, permissions or real files are involved."""
    def __init__(self):
        super().__init__()
        self.capabilities["recycle_rect"] = True
        self.permission_result = True
        self.permission_threads = []
        self.query_threads = []
        self.cache_reads = 0
        self.permission_gate = threading.Event()
        self.permission_gate.set()
        self.query_gate = threading.Event()
        self.query_gate.set()
        self.revoke_on_query = False
        self.query_error = None
        self.now = 10.0
        self.cached_at = 10.0
        self.cached = None
        self.target = Rect(100, 100, 80, 80)

    def request_recycle_access(self):
        self.permission_threads.append(threading.get_ident())
        if not self.permission_gate.wait(2):
            raise TimeoutError("test permission worker timed out")
        self.recycle_enabled = self.permission_result
        if not self.permission_result:
            self.last_error = "Fake permission denied"
        return self.permission_result

    def enable_recycle(self, enabled=True):
        self.recycle_enabled = bool(enabled)
        self.cached = None
        return self.recycle_enabled

    def query_recycle_rect(self):
        self.query_threads.append(threading.get_ident())
        if not self.query_gate.wait(2):
            raise TimeoutError("test query worker timed out")
        if self.query_error:
            raise self.query_error
        if self.revoke_on_query:
            self.recycle_enabled = False
        if not self.recycle_enabled:
            self.cached = None
            return None
        self.cached = self.target
        self.cached_at = self.now
        return self.cached

    def recycle_rect(self):
        self.cache_reads += 1
        if self.recycle_enabled and self.now - self.cached_at <= 1:
            return self.cached
        return None


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["recycle-controller-tests"])


@pytest.fixture
def controller(qt, tmp_path):
    adapter = FakeRecycleAdapter()
    c = Controller(qt, state_dir=tmp_path, hidden=True, adapter=adapter, enable_native=True)
    yield c
    adapter.permission_gate.set()
    adapter.query_gate.set()
    c.end_gift_drag()
    wait_until(qt, lambda: not c._workers)
    c.shutdown()
    qt.processEvents()


def wait_until(qt, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qt.processEvents()
        time.sleep(0.005)
    qt.processEvents()
    assert predicate(), "Qt worker did not reach expected state"


def enable(c, qt):
    c.set_recycle_enabled(True)
    wait_until(qt, lambda: not c._workers)
    assert c.settings["recycle_enabled"]


def gift_window(c, name="gift-under-test"):
    c.engine.gifts.append(Gift(name, "feather", 20, 30, "amber"))
    c.changed()
    return c.gift_windows[name]


def press(window, x, y):
    point = QPointF(x, y)
    event = QMouseEvent(QEvent.Type.MouseButtonPress,
                        point - QPointF(window.x(), window.y()), point,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    window.mousePressEvent(event)


def drag_to(window, x, y):
    point = QPointF(x, y)
    event = QMouseEvent(QEvent.Type.MouseMove,
                        point - QPointF(window.x(), window.y()), point,
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    window.mouseMoveEvent(event)


def release(window, x, y):
    global_point = QPointF(x, y)
    local_point = global_point - QPointF(window.x(), window.y())
    event = QMouseEvent(QEvent.Type.MouseButtonRelease, local_point, global_point,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
    window.mouseReleaseEvent(event)


def test_default_and_passive_actions_never_request_ax(controller, qt):
    c, adapter = controller, controller.adapter
    assert not c.settings["recycle_enabled"]
    c.feed()
    c.set_paused(True)
    c.set_paused(False)
    c.changed()
    c.begin_gift_drag()
    QTest.qWait(430)
    c.end_gift_drag()
    assert adapter.permission_threads == []
    assert adapter.query_threads == []
    assert not c.recycle_timer.isActive()
    assert c._journal is None


def test_explicit_permission_is_worker_only_and_does_not_start_polling(controller, qt):
    c, adapter = controller, controller.adapter
    main_thread = threading.get_ident()
    enable(c, qt)
    assert adapter.permission_threads and adapter.permission_threads[0] != main_thread
    assert adapter.query_threads == []
    assert not c.recycle_timer.isActive()
    assert not c.dragging_gift


def test_denied_permission_leaves_virtual_bin_available_without_retry(controller, qt):
    c, adapter = controller, controller.adapter
    adapter.permission_result = False
    c.set_recycle_enabled(True)
    wait_until(qt, lambda: not c._workers)
    c.begin_gift_drag()
    QTest.qWait(430)
    c.end_gift_drag()
    assert not c.settings["recycle_enabled"]
    assert not c.recycle_timer.isActive()
    assert len(adapter.permission_threads) == 1
    assert adapter.query_threads == []
    window = gift_window(c)
    press(window, window.x() + 10, window.y() + 10)
    point = c.bin.geometry().center()
    drag_to(window, point.x(), point.y())
    release(window, point.x(), point.y())
    assert c.engine.gifts == []


def test_enabled_drag_starts_worker_timer_and_end_stops_it(controller, qt):
    c, adapter = controller, controller.adapter
    main_thread = threading.get_ident()
    enable(c, qt)
    c.begin_gift_drag()
    assert c.dragging_gift and c.recycle_timer.isActive()
    assert c.recycle_timer.interval() == 400
    wait_until(qt, lambda: len(adapter.query_threads) >= 2)
    assert all(thread != main_thread for thread in adapter.query_threads)
    c.end_gift_drag()
    wait_until(qt, lambda: not c._workers)
    count = len(adapter.query_threads)
    QTest.qWait(450)
    assert not c.dragging_gift and not c.recycle_timer.isActive()
    assert len(adapter.query_threads) == count
    assert len(adapter.permission_threads) == 1


def test_no_native_mode_does_not_start_drag_queries(controller):
    c, adapter = controller, controller.adapter
    c._enable_native = False
    c.settings["recycle_enabled"] = adapter.recycle_enabled = True
    c.begin_gift_drag()
    assert c.dragging_gift
    assert not c.recycle_timer.isActive()
    assert "recycle-poll" not in c._workers
    assert adapter.query_threads == []
    c.end_gift_drag()


def test_inflight_query_is_not_duplicated_by_timer_and_end_does_not_restart(controller, qt):
    c, adapter = controller, controller.adapter
    enable(c, qt)
    adapter.query_gate.clear()
    c.begin_gift_drag()
    wait_until(qt, lambda: len(adapter.query_threads) == 1)
    for _ in range(3):
        c.recycle_timer.timeout.emit()
    assert len(adapter.query_threads) == 1
    c.end_gift_drag()
    adapter.query_gate.set()
    wait_until(qt, lambda: not c._workers)
    assert len(adapter.query_threads) == 1
    assert not c.recycle_timer.isActive()


def test_disabling_during_permission_request_ignores_late_approval(controller, qt):
    c, adapter = controller, controller.adapter
    adapter.permission_gate.clear()
    c.set_recycle_enabled(True)
    wait_until(qt, lambda: len(adapter.permission_threads) == 1)
    c.set_recycle_enabled(False)
    adapter.permission_gate.set()
    wait_until(qt, lambda: not c._workers)
    assert not c.settings["recycle_enabled"]
    assert not adapter.recycle_enabled
    assert adapter.cached is None
    assert not c.recycle_timer.isActive()


def test_revoked_permission_stops_drag_poll_without_new_prompt(controller, qt):
    c, adapter = controller, controller.adapter
    enable(c, qt)
    adapter.revoke_on_query = True
    c.begin_gift_drag()
    wait_until(qt, lambda: not c._workers)
    assert not c.settings["recycle_enabled"]
    assert not c.recycle_timer.isActive()
    assert c.save_timer.isActive()
    c._poll_recycle()
    assert len(adapter.query_threads) == 1
    assert len(adapter.permission_threads) == 1


def test_query_exception_disables_adapter_and_clears_drop_cache(controller, qt):
    c, adapter = controller, controller.adapter
    enable(c, qt)
    adapter.cached = adapter.target
    adapter.query_error = RuntimeError("unexpected native failure")
    c.begin_gift_drag()
    wait_until(qt, lambda: not c._workers)
    assert not c.settings["recycle_enabled"]
    assert not adapter.recycle_enabled
    assert adapter.cached is None
    assert not c.recycle_timer.isActive()
    assert c.save_timer.isActive()


def test_drop_on_verified_cache_discards_only_virtual_gift(controller):
    c, adapter = controller, controller.adapter
    c.settings["recycle_enabled"] = adapter.recycle_enabled = True
    adapter.cached = adapter.target
    window = gift_window(c)
    c.engine.gifts.append(Gift("keep", "bottle_cap", 200, 230, "mist"))
    window.dragged = window.drag_active = True
    c.dragging_gift = True
    trust = c.engine.cats["amber"].trust
    assert not c.bin.geometry().contains(120, 120)
    release(window, 120, 120)
    assert [gift.id for gift in c.engine.gifts] == ["keep"]
    assert c.engine.cats["amber"].trust == trust  # Disposal is not gift collection.
    assert adapter.cache_reads == 1
    assert adapter.query_threads == []  # Release uses cached geometry only.
    assert adapter.permission_threads == []
    assert not c.dragging_gift and not c.recycle_timer.isActive()
    assert c.save_timer.isActive()


@pytest.mark.parametrize("mode", ["expired", "disabled", "outside"])
def test_uncertain_or_missed_dock_target_keeps_gift(controller, mode):
    c, adapter = controller, controller.adapter
    c.settings["recycle_enabled"] = adapter.recycle_enabled = True
    adapter.cached = adapter.target
    x, y = 120, 120
    if mode == "expired":
        adapter.now += 2
    elif mode == "disabled":
        c.set_recycle_enabled(False)
    else:
        x, y = 250, 250
    window = gift_window(c)
    window.dragged = window.drag_active = True
    window.move(x - 10, y - 10)
    release(window, x, y)
    assert len(c.engine.gifts) == 1
    assert adapter.query_threads == []
    assert adapter.permission_threads == []


def test_click_collection_does_not_consult_dock(controller):
    c, adapter = controller, controller.adapter
    window = gift_window(c)
    press(window, window.x() + 10, window.y() + 10)
    release(window, window.x() + 10, window.y() + 10)
    assert c.engine.gifts == []
    assert c.engine.cats["amber"].trust == 3
    assert adapter.cache_reads == 0
    assert adapter.query_threads == []
    assert adapter.permission_threads == []


def test_previous_opt_in_restores_without_requesting_permission(qt, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"version": 1, "settings": {"recycle_enabled": True}}))
    adapter = FakeRecycleAdapter()
    c = Controller(qt, state_dir=tmp_path, hidden=True, adapter=adapter, enable_native=True)
    try:
        assert c.settings["recycle_enabled"]
        assert adapter.permission_threads == []
        assert adapter.query_threads == []
        c.begin_gift_drag()
        wait_until(qt, lambda: not c._workers)
        c.end_gift_drag()
        assert len(adapter.query_threads) == 1
        assert adapter.permission_threads == []
    finally:
        c.end_gift_drag()
        c.shutdown()


def test_shutdown_stops_active_recycle_timer(controller, qt):
    c = controller
    enable(c, qt)
    c.begin_gift_drag()
    wait_until(qt, lambda: not c._workers)
    assert c.recycle_timer.isActive()
    c.shutdown()
    assert not c.recycle_timer.isActive()


def test_real_gift_mouse_gesture_starts_and_ends_optional_dock_poll(controller, qt):
    c, adapter = controller, controller.adapter
    enable(c, qt)
    window = gift_window(c)
    start = QPointF(window.x() + 10, window.y() + 10)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(10, 10), start,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    window.mousePressEvent(press)
    near = start + QPointF(3, 0)
    window.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(13, 10), near,
                                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                     Qt.KeyboardModifier.NoModifier))
    assert not c.dragging_gift and not c.recycle_timer.isActive()
    far = start + QPointF(40, 40)
    window.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(50, 50), far,
                                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                     Qt.KeyboardModifier.NoModifier))
    assert c.dragging_gift and window.drag_active
    assert c.bin.isVisible() and c.recycle_timer.isActive()
    wait_until(qt, lambda: not c._workers)
    assert len(adapter.query_threads) == 1
    release(window, 250, 250)
    assert not c.dragging_gift and not window.drag_active
    assert not c.bin.isVisible() and not c.recycle_timer.isActive()
    assert len(c.engine.gifts) == 1
