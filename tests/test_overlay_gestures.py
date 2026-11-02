"""Screen changes cancel the whole gesture, including queued move/release events."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter


@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication([])
    controller = Controller(qt, state_dir=tmp_path, hidden=True,
                            adapter=DesktopAdapter(), enable_native=False)
    yield controller
    controller.shutdown()
    qt.processEvents()


def event(window, kind, global_point=None):
    local = QPointF(window.width()/2, window.height()/2)
    global_point = global_point or QPointF(window.x()+local.x(), window.y()+local.y())
    move = kind == QMouseEvent.Type.MouseMove
    release = kind == QMouseEvent.Type.MouseButtonRelease
    return QMouseEvent(kind, local, global_point,
                       Qt.MouseButton.NoButton if move else Qt.MouseButton.LeftButton,
                       Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


@pytest.mark.parametrize("widget_name", ["gate", "bowl"])
def test_cancelled_fixed_overlay_drag_cannot_resume_on_old_mouse_events(c, widget_name):
    window = getattr(c, widget_name)
    window.mousePressEvent(event(window, QMouseEvent.Type.MouseButtonPress))
    c._cancel_screen_drags()
    before, bowl = window.pos(), c.engine.bowl
    new_point = QPointF(window.x()+200, window.y()-100)
    window.mouseMoveEvent(event(window, QMouseEvent.Type.MouseMove, new_point))
    window.mouseReleaseEvent(event(window, QMouseEvent.Type.MouseButtonRelease, new_point))
    assert window.pos() == before
    assert c.engine.bowl == bowl
    assert not c.engine.gate_open and c.engine.food == 0


def test_cancelled_gift_drag_cannot_discard_at_new_bin_position(c):
    c.engine._drop("toy_mouse", 150, 200, "amber")
    c.changed()
    gift = c.engine.gifts[0]
    window = c.gift_windows[gift.id]
    window.mousePressEvent(event(window, QMouseEvent.Type.MouseButtonPress))
    window.mouseMoveEvent(event(window, QMouseEvent.Type.MouseMove, QPointF(300, 220)))
    assert window.drag_active and c.dragging_gift
    c._cancel_screen_drags()
    c._sync()
    before = window.pos()
    point = QPointF(c.bin.geometry().center())
    window.mouseMoveEvent(event(window, QMouseEvent.Type.MouseMove, point))
    window.mouseReleaseEvent(event(window, QMouseEvent.Type.MouseButtonRelease, point))
    assert window.pos() == before
    assert gift in c.engine.gifts and gift.state == "idle"
    assert not c.dragging_gift and not c.bin.isVisible()


def test_cancelled_cat_press_cannot_call_cat_on_late_release(c):
    c.engine.open_gate()
    c.engine.invite("amber")
    c.changed()
    window = c.cat_windows["amber"]
    window.mousePressEvent(event(window, QMouseEvent.Type.MouseButtonPress))
    c._cancel_screen_drags()
    c.engine.call_cat = Mock()
    window.mouseReleaseEvent(event(window, QMouseEvent.Type.MouseButtonRelease))
    c.engine.call_cat.assert_not_called()
    assert not window.hold.isActive()


def ready_for_timed_gesture(c):
    """Isolated visible fixture; leave the real gesture timers unmodified."""
    from PyQt6.QtTest import QTest
    # Cocoa emits initial available-geometry updates on its first event loop.
    # Let startup settle before pressing; a real geometry change must still
    # cancel an in-progress gesture in production. The hold stays at 600ms.
    QTest.qWait(350)
    c.engine.open_gate()
    c.engine.invite('amber')
    c.engine.calm_mode = True
    c.engine.cats['amber'].trust = 60
    v = c.engine.visitors['amber']
    v.state = 'idle'
    v.x = v.target_x = c.engine.bounds[0] + c.engine.bounds[2] / 2 - 72
    v.y = v.target_y = c.engine.floor_y
    v.next_action = 30
    v.stay_remaining = 1000
    v.carrying_gift = None
    c.changed()
    c.qt.processEvents()
    return c.cat_windows['amber'], v


def test_real_hold_timer_then_edge_drag_releases_without_pet(c, monkeypatch):
    import time
    from PyQt6.QtTest import QSignalSpy
    w, v = ready_for_timed_gesture(c)
    cancellations = []
    original_cancel = c._cancel_screen_drags
    def record_cancel():
        import traceback
        cancellations.append([f.name for f in traceback.extract_stack(limit=8)])
        return original_cancel()
    monkeypatch.setattr(c, '_cancel_screen_drags', record_cancel)
    spy = QSignalSpy(w.hold.timeout)
    started = time.monotonic()
    QApplication.sendEvent(w, event(w, QMouseEvent.Type.MouseButtonPress))
    assert not w.carrying and v.state != 'carried'
    assert spy.wait(1500), repr({'pressed': w.pressed, 'stroking': w.stroking, 'carrying': w.carrying, 'hold_active': w.hold.isActive(), 'state': v.state, 'paused': c.paused, 'screen_ready': c._screen_ready, 'cancellations': cancellations})
    assert .50 <= time.monotonic() - started <= 1.5
    assert not cancellations
    assert len(spy) == 1 and w.carrying and v.state == 'carried'
    assert w.cursor().shape() == Qt.CursorShape.ClosedHandCursor
    original_trust = c.engine.cats['amber'].trust
    edge_x = c.engine.bounds[0] + 4
    # The carry offset belongs to the model scene. A native mouse event must
    # carry display coordinates, just like an actual pointer at this edge.
    release_point = QPointF(*c.to_display(edge_x + w.offset.x(), v.y + w.offset.y()))
    QApplication.sendEvent(w, event(w, QMouseEvent.Type.MouseMove, release_point))
    assert v.x == pytest.approx(edge_x, abs=1/c.artwork_scale)
    assert w.x() == pytest.approx(c.to_display(edge_x,v.y)[0], abs=1)
    QApplication.sendEvent(w, event(w, QMouseEvent.Type.MouseButtonRelease, release_point))
    assert not w.pressed and not w.carrying and not w.hold.isActive()
    assert w.cursor().shape() != Qt.CursorShape.ClosedHandCursor
    assert v.state == 'leaving' or v.route_motion == 'leaving'
    assert c.engine.cats['amber'].trust == original_trust
    assert 'amber' in c.engine.visitors  # Release begins departure, never teleports away.


def test_stroking_before_real_hold_deadline_cannot_turn_into_carry(c):
    from PyQt6.QtTest import QSignalSpy, QTest
    w, v = ready_for_timed_gesture(c)
    spy = QSignalSpy(w.hold.timeout)
    QApplication.sendEvent(w, event(w, QMouseEvent.Type.MouseButtonPress))
    point = QPointF(w.x() + w.width()/2 + 12, w.y() + w.height()/2)
    QApplication.sendEvent(w, event(w, QMouseEvent.Type.MouseMove, point))
    assert w.stroking and not w.hold.isActive()
    QTest.qWait(750)  # Real Qt event loop; beyond the untouched 600ms carry deadline.
    assert len(spy) == 0 and not w.carrying and v.state != 'carried'
    assert c.engine.cats['amber'].trust == 62
    QApplication.sendEvent(w, event(w, QMouseEvent.Type.MouseButtonRelease, point))
    assert c.engine.cats['amber'].trust == 62  # Release must not become a second pet.
    assert not w.pressed and not w.carrying
