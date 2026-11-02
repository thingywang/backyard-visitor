import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication
from backyard_visitor import app
from backyard_visitor.desktop import DesktopAdapter, DesktopTarget, Rect
from backyard_visitor.model import SPRITE_WIDTH


@pytest.fixture
def controller(tmp_path):
    qt = QApplication.instance() or QApplication(['window-tests'])
    c = app.Controller(qt, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=5)
    yield c
    c.shutdown()
    qt.processEvents()


def perched(c):
    c.engine.open_gate()
    c.engine.invite('amber')
    v = c.engine.visitors['amber']
    c.engine.set_window_edges([('edge', 100, 600, 300)])
    v.state = 'idle'
    v.x, v.y = 300, 188
    v.target_x, v.target_y = v.x, v.y
    v.window_edge_id = 'edge'
    c.engine.cats['amber'].trust = 80
    return v


def test_only_visible_window_segments_enter_the_ecology(controller):
    c = controller
    c.settings['window_paths'] = True
    c._windows = [DesktopTarget('front', 'front', Rect(210, 120, 150, 250)),
                  DesktopTarget('rear', 'rear', Rect(100, 300, 500, 250))]
    c._windows_checked = time.monotonic()
    c._rebuild_paths()
    rear = [edge for key, edge in c.engine.window_edges.items() if key.startswith('rear:')]
    # At the small offscreen display's automatic scale, the left 110-point
    # fragment now fits the 108-point cat as well as the larger right fragment.
    assert SPRITE_WIDTH * c.artwork_scale == pytest.approx(108)
    native_rear = [(c.to_display(left, top)[0], c.to_display(right, top)[0],
                    c.to_display(left, top)[1]) for left, right, top in rear]
    assert len(native_rear) == 2
    assert native_rear[0] == pytest.approx((100, 210, 300))
    assert native_rear[1] == pytest.approx((360, 600, 300))
    assert c.engine.paths == []


def test_disabled_window_access_releases_perch_without_teleport(controller):
    c = controller
    v = perched(c)
    before = v.x, v.y
    c.set_window_paths(False)
    assert (v.x, v.y) == before
    assert v.window_edge_id is None and v.window_target_id is None
    assert v.moving
    assert not c.engine.window_edges


def test_peek_visual_clip_matches_clickable_region(controller):
    c = controller
    v = perched(c)
    assert c.engine._begin_window_peek(v)
    c.engine.tick(1.5)
    assert v.peek_phase == 'holding' and v.peek_progress == 1
    c._sync()
    w = c.cat_windows['amber']
    assert w.mask().contains(QPoint(72, 20))
    assert not w.mask().contains(QPoint(72, 60))
    image = w.grab().toImage()
    ratio = image.devicePixelRatio()
    assert any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()) for y in range(int(34*ratio)))
    assert all(image.pixelColor(x, y).alpha() == 0 for x in range(image.width()) for y in range(int(35*ratio), image.height()))
    c.engine.carry('amber')
    c._sync()
    assert v.clip_y is None
    assert w.mask().contains(QPoint(72, 60))


def test_peek_holding_is_two_fps_and_window_tracking_is_adaptive(controller):
    c = controller
    v = perched(c)
    c.engine._begin_window_peek(v)
    c.wake()
    assert c.current_fps == 24 and c.native_timer.interval() == 500
    c.engine.tick(1.5)
    c.wake()
    assert c.current_fps == 2 and c.native_timer.interval() == 500
    c.engine.carry('amber')
    c.wake()
    assert c.native_timer.interval() == 2000
