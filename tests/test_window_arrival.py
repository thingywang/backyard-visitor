"""Reachability regressions for real, high application-window title bars."""
import math

import pytest

from backyard_visitor.model import Engine, SPRITE_HEIGHT, WINDOW_JUMP_Y


def desktop_visitor(left=0, top=30):
    engine = Engine(seed=25)
    engine.set_bounds(left, top, 1920, 985)
    engine.open_gate()
    engine.invite("amber")
    v = engine.visitors["amber"]
    v.state = "idle"
    v.x = v.target_x = left + 700
    v.y = v.target_y = engine.floor_y
    v.state_age = 0
    v.next_action = v.stay_remaining = 1000
    v.parkour_cooldown = 1000
    engine._visit_countdown = 1000
    engine.rng.random = lambda: 0.1
    return engine, v


def land(engine, v):
    elapsed = 0.0
    while v.state in {"jumping", "walking"} and elapsed < 30:
        engine.tick(0.02)
        elapsed += 0.02
    assert v.state == "idle"
    return elapsed


@pytest.mark.parametrize("window_top", [172, 236])
def test_real_desktop_title_bar_is_selected_naturally_from_floor(window_top):
    engine, v = desktop_visitor()
    engine.set_window_edges([("editor", 110, 1670, window_top)])
    assert engine.floor_y == 903
    assert engine.floor_y - (window_top - SPRITE_HEIGHT) > WINDOW_JUMP_Y
    origin = (v.x, v.y)
    engine._choose_action(v)
    assert (v.x, v.y) == origin
    assert v.state == "jumping" and v.window_target_id == "editor"
    assert v.jump_duration >= 4.5  # An eight-body ascent must not be a quick hop.
    assert v.jump_arc > 64
    elapsed, max_speed = 0.0, 0.0
    previous = origin
    while v.state == "jumping":
        engine.tick(0.02)
        elapsed += 0.02
        max_speed = max(max_speed, math.dist(previous, (v.x, v.y)) / 0.02)
        assert window_top - SPRITE_HEIGHT <= v.y <= previous[1]
        assert engine.bounds[1] <= v.y <= engine.floor_y
        if elapsed <= 1:
            assert v.y > window_top - SPRITE_HEIGHT + 400
        previous = (v.x, v.y)
        assert elapsed < 6
    assert max_speed < 320
    assert elapsed >= 4.5
    assert v.window_edge_id == "editor" and v.window_target_id is None
    assert v.y + SPRITE_HEIGHT == window_top
    assert engine._begin_window_peek(v)  # The reached window supports the feature.


def test_late_first_native_poll_preserves_edge_entry_before_initial_climb():
    engine = Engine(seed=19)
    engine.set_bounds(0, 30, 1920, 985)
    engine.open_gate()
    engine.invite("amber")
    v = engine.visitors["amber"]
    v.stay_remaining = engine._visit_countdown = 1000
    origin = (v.x, v.y)
    engine.set_window_edges([("editor", 0, 1920, 172)])
    assert (v.x, v.y) == origin and v.state == "peeking"
    previous = origin
    for _ in range(2000):
        engine.tick(0.02)
        assert v.y == engine.floor_y  # No teleport up the screen's side.
        assert abs(v.x - previous[0]) <= 66 * 0.02 + 1e-8
        previous = (v.x, v.y)
        if v.state == "idle":
            break
    assert v.state == "idle"
    engine.rng.random = lambda: 0.1
    engine._choose_action(v)
    assert v.window_target_id == "editor"
    assert land(engine, v) >= 4.5
    assert v.window_edge_id == "editor"


def test_low_window_is_preferred_as_a_stepping_stone():
    engine, v = desktop_visitor()
    engine.set_window_edges([("high-front", 0, 1900, 172), ("low", 0, 1900, 850)])
    engine._choose_action(v)
    assert v.window_target_id == "low"
    assert v.jump_duration < 2


def test_initial_climb_does_not_relax_later_interwindow_limit():
    engine, v = desktop_visitor()
    engine.set_window_edges([("editor", 0, 1900, 172)])
    engine._choose_action(v)
    land(engine, v)
    engine.set_window_edges([("editor", 0, 1900, 172), ("near", 0, 1900, 330), ("far", 0, 1900, 480)])
    assert engine._reachable_window_edges(v) == ["editor", "near"]
    engine.rng.random = lambda: 0.7
    engine._choose_action(v)
    assert v.window_target_id == "near"
    land(engine, v)
    assert v.window_edge_id == "near"


def test_arbitrary_midair_position_cannot_use_floor_launch_exception():
    engine, v = desktop_visitor()
    v.y = v.target_y = 600
    engine.set_window_edges([("editor", 0, 1900, 172)])
    assert engine._reachable_window_edges(v) == []
    engine._choose_action(v)
    assert v.window_target_id is None and v.target_y == engine.floor_y


def test_high_window_on_negative_monitor_is_reachable_without_coordinate_assumptions():
    engine, v = desktop_visitor(left=-1920, top=-1030)
    window_top = -888
    engine.set_window_edges([("negative", -1810, -250, window_top)])
    engine._choose_action(v)
    assert v.window_target_id == "negative"
    assert land(engine, v) >= 4.5
    assert v.y == window_top - SPRITE_HEIGHT
    assert -1920 <= v.x <= -144
    assert v.window_edge_id == "negative"


@pytest.mark.parametrize("change", ["close", "move"])
def test_high_climb_native_geometry_change_remains_continuous(change):
    engine, v = desktop_visitor()
    engine.set_window_edges([("editor", 0, 1900, 172)])
    engine._choose_action(v)
    engine.tick(1.2)
    before = (v.x, v.y)
    edges = [] if change == "close" else [("editor", 0, 1900, 236)]
    engine.set_window_edges(edges)
    assert (v.x, v.y) == before
    assert v.clip_y is None
    # With the target still too high for a midair redirect, it calmly returns
    # to ground instead of restarting an unlimited ascent from midair.
    assert v.window_target_id is None
    engine.tick(0.02)
    assert math.dist(before, (v.x, v.y)) < 15
    land(engine, v)
    assert v.y == engine.floor_y


def test_identical_poll_keeps_the_long_climb_duration_and_progress():
    engine, v = desktop_visitor()
    edges = [("editor", 0, 1900, 172)]
    engine.set_window_edges(edges)
    engine._choose_action(v)
    engine.tick(1.0)
    previous = (v.x, v.y, v.state_age, v.jump_duration, v.jump_arc)
    engine.set_window_edges(edges)
    assert (v.x, v.y, v.state_age, v.jump_duration, v.jump_arc) == previous
    land(engine, v)
    assert v.window_edge_id == "editor"
