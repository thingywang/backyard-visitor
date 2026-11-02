"""Mouse gifts keep a sparse clock, then move smoothly even after the gate closes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter


@pytest.fixture
def c(tmp_path):
    q = QApplication.instance() or QApplication([])
    c = Controller(q, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(), enable_native=False)
    c.engine._drop('toy_mouse', 240, 300, 'amber')
    c.changed()
    yield c
    c.shutdown()
    q.processEvents()


def test_closed_gate_mouse_wait_and_escape_have_appropriate_clock(c):
    gift = c.engine.gifts[0]
    assert not c.engine.gate_open and not c.engine.visitors
    assert c.current_fps == 1 and c.timer.isActive()
    gift.wait_remaining = .03
    c.wake()
    assert 1 <= c.timer.interval() <= 31
    c.engine.tick(.03)
    c.changed()
    assert gift.state == 'escaping' and c.current_fps == 24
    assert c.timer.interval() == 42
    c.set_paused(True)
    assert not c.timer.isActive()
    c.set_paused(False)
    assert c.current_fps == 24


def test_press_pauses_mouse_and_release_catches_it(c):
    gift = c.engine.gifts[0]
    gift.state, gift.escape_direction = 'escaping', 1
    c.changed()
    window = c.gift_windows[gift.id]
    local = QPointF(19, 19)
    global_pos = QPointF(window.x()+19, window.y()+19)
    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, local, global_pos,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.mousePressEvent(press)
    x = gift.x
    c.engine.tick(1)
    assert gift.state == 'held' and gift.x == x
    assert c.current_fps == 0
    release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, local, global_pos,
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    window.mouseReleaseEvent(release)
    assert not c.engine.gifts and not c.gift_windows
    assert c.current_fps == 0


def test_escape_removes_window_and_saves_without_cat(c):
    gift = c.engine.gifts[0]
    gift.state, gift.escape_direction = 'escaping', 1
    gift.x = c.engine.bounds[0] + c.engine.bounds[2] - 1
    c.engine.tick(.05)
    c.changed()
    assert not c.engine.gifts and not c.gift_windows
    assert c.current_fps == 0
    assert c._save()
    import json
    assert json.loads(c.state_path.read_text())['gifts'] == []


def test_single_cat_demo_limit_prevents_another_visitor(tmp_path):
    q = QApplication.instance() or QApplication([])
    c = Controller(q, state_dir=tmp_path, demo=True, hidden=True, adapter=DesktopAdapter(), enable_native=False, visit_limit=1)
    try:
        c.demo_scene('feeding')
        assert len(c.engine.visitors) == 1
        assert c.engine.invite('ink') is None
        assert len(c.engine.visitors) == 1
    finally:
        c.shutdown()
