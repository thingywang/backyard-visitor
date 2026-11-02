"""Window geometry stays transient; native polling must never move a cat instantly."""
import math

import pytest

from backyard_visitor.model import Engine, SPRITE_HEIGHT, SPRITE_WIDTH, WINDOW_PEEK_DEPTH


def visitor_on_floor(engine, cat_id="amber", x=350):
    engine.open_gate()
    engine.invite(cat_id)
    v = engine.visitors[cat_id]
    v.x = v.target_x = x
    v.y = v.target_y = engine.floor_y
    v.state = "idle"
    v.state_age = 0
    v.stay_remaining = 1000
    v.next_action = 1000
    v.parkour_cooldown = 1000
    engine._visit_countdown = 1000
    return v


def until(engine, predicate, limit=20, step=0.05):
    for _ in range(math.ceil(limit / step)):
        if predicate():
            return
        engine.tick(step)
    assert predicate(), "simulation did not reach the expected state"


def perched(seed=18):
    engine = Engine(seed=seed)
    v = visitor_on_floor(engine)
    engine.set_window_edges([("editor", 250, 800, 550)])
    engine._move_to_window(v, "editor")
    until(engine, lambda: v.state == "idle")
    return engine, v


def peeking(phase="holding"):
    engine, v = perched()
    assert engine._begin_window_peek(v)
    until(engine, lambda: v.peek_phase == phase)
    return engine, v


def test_edge_validation_clips_whole_sprite_and_preserves_front_to_back_order():
    engine = Engine()
    engine.set_window_edges([
        ("front", -50, 400, 300),
        ("back", 600, 1500, 400),
        ("narrow", 20, 20 + SPRITE_WIDTH - 1, 300),
        ("offscreen", 1200, 1500, 300),
        ("above", 0, 500, SPRITE_HEIGHT - 1),
        ("below", 0, 500, 721),
        ("bad", 0, float("nan"), 300),
        ("bool", False, 500, 300),
        ("", 0, 500, 300),
        ("front", 0, 1000, 600),  # Duplicate IDs cannot overwrite front geometry.
        None,
        ("incomplete", 0, 500),
    ])
    assert engine.window_edges == {"front": (0, 400, 300), "back": (600, 1280, 400)}
    assert list(engine.window_edges) == ["front", "back"]


def test_natural_selection_prefers_foremost_reachable_window_and_limits_height():
    engine = Engine(seed=8)
    v = visitor_on_floor(engine)
    engine.set_window_edges([("too_high", 250, 800, 200), ("front", 250, 800, 550), ("back", 250, 800, 580)])
    engine.rng.random = lambda: 0.1
    engine._choose_action(v)
    assert v.window_target_id == "front"
    assert v.state == "jumping"
    assert (v.x, v.y) == (350, engine.floor_y)
    until(engine, lambda: v.state == "idle")
    assert v.window_edge_id == "front"
    assert v.window_target_id is None
    assert v.y + SPRITE_HEIGHT == 550
    engine._choose_action(v)
    assert v.state == "window_peeking"


def test_negative_screen_coordinates_support_landing_and_full_peek_cycle():
    engine = Engine(seed=11)
    engine.set_bounds(-1440, -400, 1440, 900)
    v = visitor_on_floor(engine, x=-1000)
    engine.set_window_edges([("left-screen", -1200, -600, 300)])
    engine._move_to_window(v, "left-screen", -950)
    until(engine, lambda: v.state == "idle")
    assert (v.x, v.y) == (-950, 300 - SPRITE_HEIGHT)
    assert engine._begin_window_peek(v)
    samples = []
    while v.peek_phase == "lowering":
        samples.append(v.y)
        engine.tick(0.05)
    assert samples == sorted(samples)
    assert v.y == 300 - 34
    assert v.peek_progress == 1
    assert v.clip_y == 300
    assert not engine.moving  # Holding is a true idle interval, not a 24fps loop.
    until(engine, lambda: v.peek_phase == "rising")
    assert engine.moving
    samples = []
    while v.state == "window_peeking":
        samples.append(v.y)
        engine.tick(0.05)
    assert samples == sorted(samples, reverse=True)
    assert v.y == 300 - SPRITE_HEIGHT
    assert v.peek_progress == 0 and v.peek_phase == "" and v.clip_y is None
    assert v.window_edge_id == "left-screen"


def test_identical_poll_keeps_jump_and_peek_progress():
    engine = Engine(seed=4)
    v = visitor_on_floor(engine)
    edges = [("editor", 250, 800, 550)]
    engine.set_window_edges(edges)
    engine._move_to_window(v, "editor")
    engine.tick(0.3)
    before = (v.x, v.y, v.state_age, v.jump_start_x, v.jump_start_y)
    engine.set_window_edges(edges)
    assert before == (v.x, v.y, v.state_age, v.jump_start_x, v.jump_start_y)
    until(engine, lambda: v.state == "idle")
    engine._begin_window_peek(v)
    engine.tick(0.4)
    before = (v.x, v.y, v.peek_progress, v.peek_elapsed)
    engine.set_window_edges(edges)
    assert before == (v.x, v.y, v.peek_progress, v.peek_elapsed)


@pytest.mark.parametrize("during_jump", [False, True])
def test_moved_window_retargets_from_actual_position_without_teleport(during_jump):
    engine = Engine(seed=3)
    v = visitor_on_floor(engine)
    engine.set_window_edges([("editor", 250, 800, 550)])
    engine._move_to_window(v, "editor")
    if during_jump:
        engine.tick(0.35)
    else:
        until(engine, lambda: v.state == "idle")
    old = (v.x, v.y)
    engine.set_window_edges([("editor", 400, 1000, 470)])
    assert (v.x, v.y) == old
    assert (v.jump_start_x, v.jump_start_y) == old
    assert v.window_edge_id is None and v.window_target_id == "editor"
    engine.tick(0.02)
    assert 0 < math.dist(old, (v.x, v.y)) < 15
    until(engine, lambda: v.state == "idle")
    assert v.window_edge_id == "editor"
    assert 400 <= v.x <= 1000 - SPRITE_WIDTH
    assert v.y + SPRITE_HEIGHT == 470


@pytest.mark.parametrize("phase", ["lowering", "holding", "rising"])
def test_permission_off_or_closed_window_clears_occlusion_and_returns_to_floor(phase):
    engine, v = peeking(phase)
    old = (v.x, v.y)
    engine.set_window_edges([])
    assert (v.x, v.y) == old
    assert v.window_edge_id is None and v.window_target_id is None
    assert v.clip_y is None and v.peek_progress == 0 and v.peek_phase == ""
    assert v.state in {"jumping", "walking"}
    engine.tick(0.02)
    assert math.dist(old, (v.x, v.y)) < 15
    until(engine, lambda: v.state == "idle")
    assert v.y == engine.floor_y


def test_disappearing_inflight_target_lands_on_other_visible_edge():
    engine = Engine(seed=4)
    v = visitor_on_floor(engine)
    engine.set_window_edges([("editor", 250, 800, 550), ("other", 400, 1100, 560)])
    engine._move_to_window(v, "editor")
    engine.tick(0.4)
    old = (v.x, v.y)
    engine.set_window_edges([("other", 400, 1100, 560)])
    assert (v.x, v.y) == old
    assert v.window_target_id == "other"
    until(engine, lambda: v.state == "idle")
    assert v.window_edge_id == "other"
    assert v.y + SPRITE_HEIGHT == 560


def test_far_moved_or_too_narrow_window_is_not_chased():
    for edges in [[("editor", 1100, 1280, 200)], [("editor", 350, 450, 550)]]:
        engine, v = perched()
        old = (v.x, v.y)
        engine.set_window_edges(edges)
        assert (v.x, v.y) == old
        assert v.window_target_id is None
        until(engine, lambda: v.state == "idle")
        assert v.y == engine.floor_y


def test_cross_window_jump_retains_arc_for_real_height_change():
    engine, v = perched()
    engine.set_window_edges([("editor", 250, 800, 550), ("other", 600, 1050, 450)])
    engine.rng.random = lambda: 0.7
    engine._choose_action(v)
    assert v.route_edge_id == "other" and v.route_stage == "approach"
    until(engine, lambda: v.state == "jumping")
    assert v.window_target_id == "other" and abs(v.target_x-v.x) <= 64
    baseline = v.y
    engine.tick(v.takeoff_remaining)
    engine.tick(v.jump_duration / 2)
    assert v.y < baseline - 50
    until(engine, lambda: v.state == "idle")
    assert v.window_edge_id == "other" and v.y == baseline - 100


def test_low_edge_remains_standable_without_offscreen_peek():
    engine = Engine(seed=9)
    v = visitor_on_floor(engine)
    engine.set_window_edges([("low", 350, 350 + SPRITE_WIDTH, 700)])
    engine._move_to_window(v, "low")
    until(engine, lambda: v.state == "idle")
    assert not engine._begin_window_peek(v)
    assert v.window_edge_id == "low"
    assert engine.bounds[1] <= v.y <= engine.floor_y
    assert v.clip_y is None


def test_peek_affection_reverses_continuously_before_purring():
    engine, v = peeking("lowering")
    engine.tick(0.45)
    engine.cats["amber"].trust = 80
    old_y = v.y
    assert engine.pet("amber")
    assert v.y == old_y and v.peek_phase == "rising"
    assert v.state == "window_peeking"
    engine.tick(0.05)
    assert v.y < old_y
    until(engine, lambda: v.state == "purring")
    assert v.window_edge_id == "editor"
    assert v.y == 550 - SPRITE_HEIGHT and v.clip_y is None


@pytest.mark.parametrize("action", ["call", "true_name"])
def test_recognized_name_rises_before_responding(action):
    engine, v = peeking()
    engine.cats["amber"].nickname = "小客"
    engine.cats["amber"].trust = 70
    old_y = v.y
    if action == "call":
        assert engine.call_cat("amber") == "recognized"
    else:
        assert engine.name_cat("amber", engine.cats["amber"].true_name) == "true_name"
    assert v.y == old_y and v.peek_phase == "rising"
    until(engine, lambda: v.state == "purring")
    assert v.window_edge_id == "editor"


def test_meal_has_priority_over_peek_affection_and_consumes_only_on_arrival():
    engine, v = peeking()
    engine.cats["amber"].trust = 70
    engine.feed()
    assert v.peek_phase == "rising" and engine.food == 1
    assert engine.pet("amber")
    assert v.peek_after == "eating"
    until(engine, lambda: v.state == "eating")
    assert engine.food == 0 and v.has_eaten
    assert v.window_edge_id is None and v.clip_y is None
    assert v.y >= 550 - SPRITE_HEIGHT + WINDOW_PEEK_DEPTH


@pytest.mark.parametrize("action,state", [("close", "leaving"), ("startle", "fleeing"), ("carry", "carried")])
def test_urgent_action_interrupts_peek_without_position_jump(action, state):
    engine, v = peeking()
    old = (v.x, v.y)
    if action == "close":
        engine.open_gate(False)
    elif action == "startle":
        assert engine.startle("amber")
    else:
        assert engine.carry("amber")
    assert (v.x, v.y) == old
    assert v.state == ("carried" if action == "carry" else "jumping")
    if action != "carry":
        assert v.route_motion == state and v.takeoff_remaining == 0
    assert v.window_edge_id is None and v.clip_y is None
    if action == "carry":
        engine.set_window_edges([])
        engine.tick(0.5)
        assert (v.x, v.y) == old and v.state == "carried"
        assert engine.release("amber", v.x, v.y)
        until(engine, lambda: v.state == "idle")
        assert v.y == engine.floor_y


def test_true_name_midjump_does_not_freeze_cat_and_pet_does_not_reward_midair():
    engine = Engine(seed=14)
    v = visitor_on_floor(engine)
    engine.set_window_edges([("editor", 250, 800, 550)])
    engine._move_to_window(v, "editor")
    engine.tick(0.3)
    engine.cats["amber"].trust = 75
    assert not engine.pet("amber")
    assert engine.cats["amber"].trust == 75
    old = (v.x, v.y)
    engine.name_cat("amber", engine.cats["amber"].true_name)
    assert v.state == "jumping" and (v.x, v.y) == old
    until(engine, lambda: v.state == "purring")
    assert v.window_edge_id == "editor"


def test_screen_change_invalidates_window_geometry_and_settles_cat():
    engine, v = peeking()
    engine.set_bounds(-1280, 0, 1280, 720)
    assert not engine.window_edges
    assert v.window_edge_id is None and v.clip_y is None
    until(engine, lambda: v.state == "idle")
    assert -1280 <= v.x <= -SPRITE_WIDTH and v.y == engine.floor_y


def test_window_routes_do_not_persist_or_replace_icon_paths():
    engine, v = perched()
    engine.set_paths([(100, 500)])
    engine.set_window_edges([])
    assert engine.paths == [(100, 500)]
    engine.set_window_edges([("sensitive-window-id", 250, 800, 550)])
    saved = engine.export()
    assert "sensitive-window-id" not in repr(saved)
    restored = Engine(saved)
    assert not restored.window_edges and not restored.visitors
