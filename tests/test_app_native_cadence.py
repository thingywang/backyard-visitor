"""Native result cadence changes must not look like sleep or advance a new jump."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from backyard_visitor import app
from backyard_visitor.desktop import DesktopAdapter, DesktopTarget, Rect
from backyard_visitor.model import SPRITE_HEIGHT
from test_app_lifecycle import Desktop


@pytest.fixture
def perched(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication(["native-cadence-test"])
    c = app.Controller(Desktop(qt), state_dir=tmp_path, hidden=True,
                       adapter=DesktopAdapter(), enable_native=False, seed=9)
    c.engine.open_gate()
    c.engine.invite("amber")
    v = c.engine.visitors["amber"]
    c.engine.cats["amber"].trust = 100
    c.settings["window_paths"] = True
    clock = {"mono": 100.0, "wall": 1000.0}
    monkeypatch.setattr(app.time, "monotonic", lambda: clock["mono"])
    monkeypatch.setattr(app.time, "time", lambda: clock["wall"])
    c._windows = [DesktopTarget("B", "own helper", Rect(200, 400, 400, 70))]
    c._windows_checked = clock["mono"]
    c._rebuild_paths()
    # A settled native perch is the fixture; the movement under test is real
    # DesktopTarget geometry processing, followed by production Controller ticks.
    v.state = "idle"
    scene_x, support_top = c.to_scene(300, 400)
    v.x = v.target_x = scene_x
    v.y = v.target_y = support_top - SPRITE_HEIGHT
    assert c.to_display(v.x, v.y + SPRITE_HEIGHT) == pytest.approx((300, 400))
    v.window_edge_id = "B:top:0"
    v.window_target_id = v.route_edge_id = v.route_stage = None
    v.landing_remaining = v.takeoff_remaining = v.settle_remaining = v.turn_remaining = 0
    c.changed()
    assert c.timer.interval() == 500
    yield c, v, clock
    c.shutdown()
    qt.processEvents()


def advance(clock, delta):
    clock["mono"] += delta
    clock["wall"] += delta


def native(c, clock, *, moved=False):
    rect = Rect(224, 420, 400, 70) if moved else Rect(200, 400, 400, 70)
    return {"context_epoch": c._desktop_epoch, "requested_at": clock["mono"],
            "windows_time": clock["mono"], "windows_ok": True,
            "windows": [DesktopTarget("B", "own helper", rect)]}


def test_native_support_move_accelerates_cadence_without_false_sleep(perched):
    c, v, clock = perched
    epoch = c._desktop_epoch
    original_position = (v.x, v.y)
    target_y = c.to_scene(300, 420)[1] - SPRITE_HEIGHT
    advance(clock, .46)
    c._native_result(native(c, clock, moved=True))
    assert c.current_fps == 24 and c.timer.interval() == 42
    assert v.state == "walking" and v.route_stage == "step"
    assert v.target_y == pytest.approx(target_y)
    assert c.to_display(v.target_x, v.target_y + SPRITE_HEIGHT)[1] == pytest.approx(420)
    assert (v.x, v.y) == original_position
    advance(clock, .042)
    c._tick()
    assert c._desktop_epoch == epoch
    assert set(c.engine.window_edges) == {"B:top:0"}
    left, right, top = c.engine.window_edges["B:top:0"]
    assert c.to_display(left, top) == pytest.approx((224, 420))
    assert c.to_display(right, top) == pytest.approx((624, 420))
    assert v.window_target_id == "B:top:0"
    # The same body-wide support moved only 20px. Follow continuously without
    # adding an arc or consuming the preceding idle .46 seconds.
    assert v.takeoff_remaining == v.jump_elapsed == 0
    assert v.state_age == pytest.approx(.042)
    foot_y = c.to_display(v.x, v.y + SPRITE_HEIGHT)[1]
    assert 400 <= foot_y < 420
    assert foot_y - 400 <= 66 * c.artwork_scale * .042 + 1e-7
    for _ in range(40):
        before = c.to_display(v.x, v.y + SPRITE_HEIGHT)[1]
        advance(clock, .042); c._tick()
        foot_y = c.to_display(v.x, v.y + SPRITE_HEIGHT)[1]
        assert v.state != "jumping" and before <= foot_y <= 420 + 1e-7
        # A native support shift preserves the normal walking speed and must
        # not consume the preceding idle interval as movement time.
        assert foot_y - before <= 66 * c.artwork_scale * .042 + 1e-7
        if v.state == "idle":
            break
    assert v.y == pytest.approx(target_y) and v.window_edge_id == "B:top:0"
    assert foot_y == pytest.approx(420)


def test_same_geometry_native_results_preserve_tick_baseline(perched):
    c, v, clock = perched
    baseline = c._last_tick
    age = v.age
    for _ in range(4):
        advance(clock, .1)
        c._native_result(native(c, clock))
        assert c._last_tick == baseline
        assert c.timer.interval() == 500
    advance(clock, .1)
    c._tick()
    assert v.age - age == pytest.approx(.5)
    assert c.engine.window_edges
    assert c._desktop_epoch == 0


def test_real_long_gap_still_invalidates_native_context(perched):
    c, v, clock = perched
    epoch = c._desktop_epoch
    position = (v.x, v.y)
    advance(clock, 20)
    result = native(c, clock, moved=True)
    c._native_result(result)
    assert c._desktop_epoch == epoch + 1
    assert not c.engine.window_edges and not c._windows
    assert (v.x, v.y) == position
