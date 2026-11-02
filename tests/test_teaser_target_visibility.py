"""Toy loss/re-entry uses full-screen ownership and never alters a committed hop."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QPoint, QRect

from backyard_visitor import app
from backyard_visitor.app import Controller
from backyard_visitor.display_scale import SceneTransform
from backyard_visitor.model import Engine
from backyard_visitor.play_session import TeaserSession


def natural_engine(bounds=(0, 30, 1280, 770)):
    engine = Engine(seed=7, visit_limit=1)
    engine.set_bounds(*bounds)
    engine.open_gate(); assert engine.invite('amber')
    engine.cats['amber'].trust = 60
    visitor = engine.visitors['amber']
    until(engine, lambda: visitor.state == 'idle' and visitor.posture_remaining <= 0, 40)
    return engine, visitor


def until(engine, predicate, seconds=8):
    for _ in range(round(seconds/.01)):
        if predicate(): return
        engine.tick(.01)
    assert predicate()


def far_target(engine, visitor):
    left, _, width, _ = engine.bounds
    return (left+width-20 if visitor.x < left+width/2 else left+20), visitor.y+56


@pytest.mark.parametrize('bounds', [(0,30,1280,770), (-1280,-170,1280,770)])
def test_lost_ground_target_waits_then_reobserves_without_extending_deadline(bounds):
    engine, visitor = natural_engine(bounds)
    engine.cursor_moved(*far_target(engine, visitor), 0)
    started = engine._time
    assert engine.start_teaser('amber')
    until(engine, lambda: engine.teaser_phase == 'chase' and visitor.ground_speed > 0)
    before = visitor.x, visitor.y, visitor.facing, visitor.run_phase
    remaining = engine._teaser.remaining
    engine.clear_cursor()
    for _ in range(120):
        engine.tick(.01)
        assert (visitor.x, visitor.y, visitor.facing, visitor.run_phase) == before
        assert visitor.ground_speed == visitor.turn_remaining == 0
        assert visitor.turn_target_facing is None
        assert engine.teaser_phase == 'watch' and engine._teaser.wait == .4
    assert engine._teaser.remaining == pytest.approx(remaining-1.2)
    engine.cursor_moved(*far_target(engine, visitor), 0)
    engine.tick(.39)
    assert engine.teaser_phase == 'watch' and (visitor.x, visitor.y) == before[:2]
    until(engine, lambda: (visitor.x, visitor.y) != before[:2])
    until(engine, lambda: engine.active_teaser_id is None, 16)
    assert engine._time-started == pytest.approx(15., abs=.011)
    assert sum(event['kind'] == 'play_finished' for event in engine.drain_events()) == 1


def test_target_loss_during_pounce_keeps_arc_and_landing_then_waits():
    engine, visitor = natural_engine()
    engine.cursor_moved(visitor.x+120, visitor.y+30, 0)
    assert engine.start_teaser('amber')
    until(engine, lambda: visitor.state == 'jumping')
    engine.tick(.18)
    before = (visitor.x, visitor.y, visitor.jump_elapsed, visitor.jump_duration,
              visitor.target_x, visitor.target_y, visitor.jump_start_x, visitor.jump_start_y)
    engine.clear_cursor()
    assert (visitor.x, visitor.y, visitor.jump_elapsed, visitor.jump_duration,
            visitor.target_x, visitor.target_y, visitor.jump_start_x, visitor.jump_start_y) == before
    engine.tick(.05)
    assert visitor.state == 'jumping' and visitor.jump_elapsed > before[2]
    until(engine, lambda: visitor.state == 'teasing' and visitor.landing_remaining <= 0)
    landed = visitor.x, visitor.y
    assert visitor.y == engine.floor_y
    for _ in range(100):
        engine.tick(.01)
        assert (visitor.x, visitor.y) == landed and visitor.state == 'teasing'
        assert engine.teaser_phase == 'watch' and engine._teaser.wait == .4


def samplers(engine, rect, pointer, monkeypatch):
    def no_depth_guard():
        # This sampling fixture intentionally exercises only foreground cats.
        assert all(visitor.depth_trip is None and visitor.tree_trip is None for visitor in engine.visitors.values())

    screen = SimpleNamespace(geometry=lambda: QRect(rect), availableGeometry=lambda: rect.adjusted(0,30,0,-60))
    # These model-only fixtures use one scene unit per native desktop point;
    # expose the same conversion boundary as the real controller.
    transform = SceneTransform(1., rect.x(), rect.y())
    c = SimpleNamespace(engine=engine, screen=screen, paused=False, _quitting=False,
        artwork_scale=transform.scale, to_scene=transform.to_scene, to_display=transform.to_display,
        _screen_ready=True, _last_tick=100., _last_cursor=QPoint(pointer['point']),
        _recover_clock_gap=lambda *args:False, _reset_clock=lambda *args, **kwargs:None,
        settings={'window_paths':False}, _windows_checked=0., _events=lambda:False,
        _sync=lambda:None, _guard_depth_visits=no_depth_guard, _guard_tree_visits=no_depth_guard,
        _maybe_tree_visit=lambda:None, _continue_tree_play=lambda:None, _tick_guest_presence=lambda *args:None, save_timer=Mock(), wake=lambda **kwargs:None)
    monkeypatch.setattr(app, 'time', SimpleNamespace(monotonic=lambda:100.02, time=lambda:1000.02))
    monkeypatch.setattr(app.QCursor, 'pos', staticmethod(lambda:QPoint(pointer['point'])))
    canvas = SimpleNamespace(set_cursor_inside=Mock(), set_remaining=Mock(), set_cursor_direction=Mock())
    s = SimpleNamespace(c=c, _active=True, remaining=15., _screen=screen, _geometry=QRect(rect),
        cat_id='amber', _cursor_position=lambda:QPoint(pointer['point']), _canvas=canvas, _hint=None,
        _previous_cursor_x=pointer['point'].x(), stop=Mock(), error=None)
    return c, s


@pytest.mark.parametrize('order', [('controller','session'), ('session','controller')])
def test_neither_sampling_timer_can_restore_an_outside_target(order, monkeypatch):
    engine, visitor = natural_engine((-1280,-170,1280,770))
    rect = QRect(-1280,-200,1280,860)
    engine.cursor_moved(visitor.x+72,visitor.y+56,0); assert engine.start_teaser('amber')
    pointer = {'point':QPoint(10,100)}
    c, session = samplers(engine, rect, pointer, monkeypatch)
    operations = {'controller':lambda:Controller._tick(c), 'session':lambda:TeaserSession._pulse(session)}
    for _ in range(3):
        for name in order:
            operations[name]()
            assert engine._cursor is None
            assert not session.stop.called
    assert session._previous_cursor_x is None
    assert engine.teaser_phase == 'watch'


@pytest.mark.parametrize('point', [QPoint(-800,-190), QPoint(-800,650)])
def test_menu_bar_and_dock_strips_remain_valid_full_screen_toy_targets(point, monkeypatch):
    engine, visitor = natural_engine((-1280,-170,1280,770))
    rect = QRect(-1280,-200,1280,860)
    assert rect.contains(point) and not rect.adjusted(0,30,0,-60).contains(point)
    assert engine.start_teaser('amber')
    pointer = {'point':point}
    c, session = samplers(engine, rect, pointer, monkeypatch)
    for operation in (lambda:Controller._tick(c), lambda:TeaserSession._pulse(session)):
        operation()
        assert engine._cursor == (point.x(),point.y())
        assert not session.stop.called


def test_controller_keeps_normal_non_play_cursor_sampling_outside_screen(monkeypatch):
    engine, visitor = natural_engine()
    assert engine.active_teaser_id is None
    pointer = {'point':QPoint(-120,744)}
    c, _ = samplers(engine, QRect(0,0,1280,860), pointer, monkeypatch)
    Controller._tick(c)
    assert engine._cursor == (-120,744)


def test_model_alone_does_not_guess_full_screen_from_work_area():
    engine, visitor = natural_engine()
    assert engine.start_teaser('amber')
    assert not engine.cursor_moved(400,5,0)
    assert engine._cursor == (400.,5.)  # The model is not given the UI's full screen rect.
