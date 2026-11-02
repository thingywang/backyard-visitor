"""Controller cadence and the actual overlay follow posture and recognition state."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter
from backyard_visitor.model import Visitor, SETTLE_SECONDS, RISE_SECONDS, TRUE_NAME_SECONDS


@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication([])
    controller = Controller(qt, state_dir=tmp_path, hidden=True,
                            adapter=DesktopAdapter(), enable_native=False)
    visitor = Visitor("amber", 200, controller.engine.floor_y, state="idle",
                      target_x=200, target_y=controller.engine.floor_y,
                      next_action=1000, stay_remaining=1000)
    controller.engine.visitors[visitor.cat_id] = visitor
    controller._sync()
    yield controller
    controller.shutdown()
    qt.processEvents()


def test_controller_keeps_posture_transition_active_then_returns_to_idle_cadence(c):
    visitor = c.engine.visitors["amber"]
    visitor.settle_remaining = SETTLE_SECONDS
    c.wake()
    assert c.current_fps == 24
    c.engine.tick(SETTLE_SECONDS + .01)
    c.wake()
    assert c.current_fps == 2
    c.engine._walk(visitor, 450, visitor.y)
    assert visitor.rise_remaining == RISE_SECONDS
    c.wake()
    assert c.current_fps == 24


def test_native_overlay_forwards_live_posture_progress(c):
    seen = []
    class Recorder:
        def paint(self, *args, **kwargs):
            seen.append(kwargs)
    c._art = Recorder()
    visitor = c.engine.visitors["amber"]
    visitor.settle_remaining = SETTLE_SECONDS / 2
    c.cat_windows["amber"].grab()
    assert seen[-1]["settle_progress"] == pytest.approx(.5)
    visitor.state = "walking"
    visitor.settle_remaining = 0
    visitor.rise_remaining = RISE_SECONDS / 4
    c.cat_windows["amber"].grab()
    assert seen[-1]["rise_progress"] == pytest.approx(.75)


def test_true_name_response_has_distinct_overlay_and_pause_freezes_it(c):
    visitor = c.engine.visitors["amber"]
    c.engine.cats["amber"].trust = 80
    c.engine._state(visitor, "purring")
    normal = c.cat_windows["amber"].grab().toImage()
    visitor.true_name_remaining = TRUE_NAME_SECONDS
    special = c.cat_windows["amber"].grab().toImage()
    assert normal.copy(QRect(65, 0, 79, 48)) != special.copy(QRect(65, 0, 79, 48))
    c.set_paused(True)
    c._tick()
    assert visitor.true_name_remaining == TRUE_NAME_SECONDS
    assert c.cat_windows["amber"].grab().toImage() == special


@pytest.mark.parametrize('source_edge,offset', [(None, 0), ('window-source', 6)])
@pytest.mark.parametrize('facing', [-1, 1])
def test_takeoff_keeps_source_baseline_and_gift_at_live_mouth(c, source_edge, offset, facing):
    from backyard_visitor.model import TAKEOFF_SECONDS
    poses, gifts = [], []
    class Recorder:
        def paint(self, painter, rect, *args, **kwargs):
            poses.append((rect.y(), kwargs))
        def takeoff_mouth(self, progress, direction):
            assert progress == pytest.approx(.5)
            assert direction == facing
            return (30 if direction < 0 else 114, 84)
    c._art = Recorder()
    c.paint_gift = lambda p, rect, *a, **kw: gifts.append(rect)
    visitor = c.engine.visitors['amber']
    visitor.state = 'jumping'
    visitor.takeoff_remaining = TAKEOFF_SECONDS / 2
    visitor.takeoff_edge_id = source_edge
    visitor.window_target_id = 'destination'
    visitor.facing = facing
    visitor.carrying_gift = 'mouse'
    c.cat_windows['amber'].grab()
    assert poses[-1][0] == offset
    assert poses[-1][1]['takeoff_progress'] == pytest.approx(.5)
    assert gifts[-1].center().x() == (30 if facing < 0 else 114)
    assert gifts[-1].center().y() == 84 + offset
    assert gifts[-1].width() == 38
    c.set_paused(True)
    c._tick()
    c.cat_windows['amber'].grab()
    assert visitor.takeoff_remaining == TAKEOFF_SECONDS / 2
    assert poses[-1][1]['takeoff_progress'] == pytest.approx(.5)


@pytest.mark.parametrize('facing', [-1, 1])
def test_gift_click_shows_brief_head_response_and_pause_freezes_it(c, facing):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    e = c.engine; v = e.visitors['amber']
    e.open_gate(); e._visit_countdown = 10000
    e.cats['amber'].trust = 80; e.cats['amber'].discovered = True
    v.facing = facing
    e._state(v, 'purring')
    normal = c.cat_windows['amber'].grab().toImage()
    e._drop('feather', v.x, v.y, v.cat_id)
    gid = e.gifts[-1].id
    c.changed()
    QTest.mouseClick(c.gift_windows[gid], Qt.MouseButton.LeftButton, pos=QPoint(19, 19))
    assert e.collected_count('feather') == 1
    assert not e.collect_gift(gid)
    assert v.state == 'purring' and v.gift_response_remaining == 3
    assert c.current_fps == 24 and gid not in c.gift_windows
    special = c.cat_windows['amber'].grab().toImage()
    assert normal.copy(QRect(0, 0, 144, 30)) != special.copy(QRect(0, 0, 144, 30))
    c.set_paused(True); c._tick()
    assert v.gift_response_remaining == 3
    assert c.cat_windows['amber'].grab().toImage() == special
    c.set_paused(False)
    for _ in range(30): e.tick(.1)
    c.changed()
    assert v.gift_response_remaining == 0 and v.state == 'idle'
    assert c.current_fps == 2


def test_gift_response_does_not_replace_true_name_visual_or_duration(c):
    e = c.engine; v = e.visitors['amber']
    e.open_gate(); e._visit_countdown = 10000
    e.cats['amber'].discovered = True
    e.cats['amber'].neighbor_status = 'neighbor'  # Naming feedback requires an established neighbour.
    e.name_cat('amber', e.cats['amber'].true_name)
    before = c.cat_windows['amber'].grab().toImage()
    e._drop('bottle_cap', v.x, v.y, 'amber')
    assert e.collect_gift(e.gifts[-1].id)
    c.changed()
    assert v.true_name_remaining == TRUE_NAME_SECONDS
    assert not v.gift_response_pending and v.gift_response_remaining == 0
    assert c.cat_windows['amber'].grab().toImage() == before


def test_emergency_close_removes_gift_response_without_extra_overlay(c):
    e = c.engine; v = e.visitors['amber']
    e.open_gate(); e._visit_countdown = 10000
    e._drop('feather', v.x, v.y, 'amber')
    assert e.collect_gift(e.gifts[-1].id)
    c.changed()
    assert v.gift_response_remaining > 0
    c.close_gate()
    assert not v.gift_response_pending and v.gift_response_remaining == 0
    assert v.state in {'leaving', 'jumping'}
    assert c._teaser_session is None and c._scratches == []
