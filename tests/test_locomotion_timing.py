"""Limb timing follows traveled ground distance, independently of state clocks."""
import math

import pytest

from backyard_visitor.model import (Engine, Visitor, GAIT_STRIDE, GROUND_ACCELERATION,
                                    LANDING_SECONDS, SETTLE_SECONDS, PLAY_DEAD_ZONE, SPRITE_WIDTH,
                                    TURN_SECONDS, RUN_STRIDE, RUN_SPEED)


def present(engine, x=350.0, y=None, *, standing=False):
    y = engine.floor_y if y is None else y
    v = Visitor('amber', x, y, state='idle', facing=1, target_x=x, target_y=y,
                stay_remaining=1000, next_action=1000, gait_phase=.137)
    # Ground-speed tests can start from an explicitly planted standing pose.
    v.settle_remaining = SETTLE_SECONDS if standing else 0.0
    engine.visitors[v.cat_id] = v
    return v


def assert_phase_step(v, before, phase):
    traveled = math.dist(before, (v.x, v.y))
    assert v.gait_phase == pytest.approx((phase + traveled / GAIT_STRIDE) % 1, abs=1e-11)
    assert 0 <= v.gait_phase < 1
    return traveled


@pytest.mark.parametrize('state', ['entering', 'walking', 'leaving'])
def test_every_ground_route_counts_only_actual_distance(state):
    engine = Engine(seed=8)
    v = present(engine)
    v.state = state
    v.target_x = 900
    cap = 195 if state == 'fleeing' else 66
    total = 0
    initial_phase = v.gait_phase
    for _ in range(20):
        before, phase = (v.x, v.y), v.gait_phase
        engine.tick(.05)
        distance = assert_phase_step(v, before, phase)
        assert distance <= cap * .05 + 1e-9
        total += distance
    assert total > 0
    assert v.gait_phase == pytest.approx((initial_phase + total / GAIT_STRIDE) % 1)


def test_real_edge_arrival_preserves_phase_when_peeking_changes_to_entering():
    engine = Engine(seed=4)
    engine.open_gate()
    engine.invite('amber')
    v = engine.visitors['amber']
    v.gait_phase = .73
    before = (v.x, v.y)
    engine.tick(3)
    assert v.state == 'entering' and (v.x, v.y) == before
    assert v.gait_phase == .73
    engine.tick(.05)
    assert assert_phase_step(v, before, .73) > 0


def test_idle_age_and_state_changes_never_advance_or_reset_phase():
    engine = Engine(seed=7)
    v = present(engine)
    for state in ('idle', 'sleeping', 'purring', 'grooming'):
        engine._state(v, state)
        engine.tick(.2)
        assert v.gait_phase == .137
    v.age = 4000
    engine._walk(v, v.x + GAIT_STRIDE, v.y)
    assert v.gait_phase == .137
    for _ in range(100):
        engine.tick(.05)
        if v.state == 'idle':
            break
    assert v.state == 'idle'
    assert v.x == pytest.approx(350 + GAIT_STRIDE)
    assert v.gait_phase == pytest.approx(.137)
    phase = v.gait_phase
    engine.tick(1)
    assert v.gait_phase == phase and v.ground_speed == 0


def test_short_route_accelerates_and_stops_without_overstepping_target():
    engine = Engine(seed=9)
    v = present(engine, standing=True)
    engine._walk(v, v.x + 14, v.y)
    distances = []
    speeds = []
    for _ in range(100):
        before, phase = (v.x, v.y), v.gait_phase
        engine.tick(.02)
        distances.append(assert_phase_step(v, before, phase))
        speeds.append(v.ground_speed)
        assert 350 <= v.x <= 364
        assert v.y == engine.floor_y
        if v.state == 'idle':
            break
    assert 0 < distances[0] < 66 * .02
    assert speeds[0] <= GROUND_ACCELERATION * .02 + 1e-9
    assert any(b < a for a, b in zip(speeds[:-1], speeds[1:-1]))
    assert v.x == 364 and v.state == 'idle' and v.ground_speed == 0
    assert v.gait_phase == pytest.approx((.137 + 14 / GAIT_STRIDE) % 1)


def test_reversal_plants_feet_without_clock_driven_steps_then_resumes():
    engine = Engine(seed=2)
    v = present(engine, standing=True)
    engine._walk(v, 650, v.y)
    engine.tick(.4)
    assert v.facing == 1 and v.ground_speed == 66
    engine._walk(v, 200, v.y)
    before, phase = (v.x, v.y), v.gait_phase
    engine.tick(TURN_SECONDS * .4)
    assert (v.x, v.y) == before and v.gait_phase == phase
    assert v.facing == 1 and v.turn_remaining > 0 and v.ground_speed == 0
    engine.tick(TURN_SECONDS * .4)
    assert (v.x, v.y) == before and v.gait_phase == phase
    engine.tick(TURN_SECONDS * .4)
    assert v.facing == -1 and v.x < before[0] and v.turn_remaining == 0
    assert_phase_step(v, before, phase)


def test_cancelled_turn_and_urgent_departure_do_not_get_stuck():
    engine = Engine(seed=4)
    v = present(engine, standing=True)
    engine._walk(v, 150, v.y)
    engine.tick(.04)
    assert v.turn_remaining > 0
    engine._walk(v, 650, v.y)
    before = v.x
    engine.tick(.04)
    assert v.facing == 1 and v.x > before and v.turn_remaining == 0
    engine._walk(v, 150, v.y)
    engine.tick(.04)
    assert v.turn_remaining > 0
    assert engine.startle(v.cat_id)
    assert v.state == 'fleeing' and v.turn_remaining == 0
    before, phase = (v.x, v.y), v.run_phase
    engine.tick(.02)
    assert v.x < before[0]
    assert v.run_phase == pytest.approx((phase+math.dist(before,(v.x,v.y))/RUN_STRIDE)%1)


def test_playing_tracks_actual_distance_and_stops_outside_cursor_dead_zone():
    engine = Engine(seed=3)
    v = present(engine)
    engine.cats[v.cat_id].trust = 80
    engine._state(v, 'playing')
    v.next_action = 1000
    engine.cursor_moved(v.x + SPRITE_WIDTH/2 + 120, v.y + 56, 0)
    for _ in range(150):
        before, phase = (v.x, v.y), v.gait_phase
        engine.tick(.02)
        assert assert_phase_step(v, before, phase) <= 90 * .02 + 1e-9
        if v.state == 'idle':
            break
    assert v.state == 'idle' and v.ground_speed == 0
    assert v.settle_remaining == SETTLE_SECONDS
    assert math.dist((v.x, v.y), (v.target_x, v.target_y)) == pytest.approx(PLAY_DEAD_ZONE)
    phase = v.gait_phase
    engine.tick(.5)
    assert v.gait_phase == phase


def test_jump_does_not_step_and_landing_keeps_a_short_animation_clock():
    engine = Engine(seed=6)
    v = present(engine)
    phase = v.gait_phase
    engine._walk(v, 400, v.y - 140)
    assert v.state == 'jumping' and v.landing_remaining == 0
    for _ in range(200):
        engine.tick(.02)
        assert v.gait_phase == phase and v.ground_speed == 0
        if v.state != 'jumping':
            break
    assert (v.x, v.y) == (400, engine.floor_y - 140)
    assert v.state == 'idle' and v.landing_remaining == LANDING_SECONDS
    assert engine.moving
    engine.tick(.10)
    assert 0 < v.landing_remaining < LANDING_SECONDS and engine.moving
    engine.tick(.15)
    assert v.landing_remaining == 0 and v.settle_remaining > 0 and engine.moving
    engine.tick(SETTLE_SECONDS)
    assert not engine.moving and v.gait_phase == phase


def test_carry_reposition_and_release_do_not_count_as_footsteps():
    engine = Engine(seed=5)
    v = present(engine)
    v.landing_remaining = LANDING_SECONDS
    assert engine.carry(v.cat_id)
    assert v.landing_remaining == 0
    v.x, v.y = 500, 300  # Overlay drag owns this direct position update.
    engine.tick(.25)
    assert v.gait_phase == .137 and v.ground_speed == 0
    engine.gate_open = True
    engine._visit_countdown = 1000
    assert engine.release(v.cat_id, 650, 330)
    assert v.gait_phase == .137 and v.state == 'jumping'
    engine.tick(.1)
    assert v.gait_phase == .137


def test_monitor_rebase_is_not_travel_and_new_negative_route_remains_bounded():
    engine = Engine(seed=5)
    v = present(engine, standing=True)
    engine._walk(v, 650, v.y)
    engine.tick(.2)
    phase = v.gait_phase
    engine.set_bounds(-1280, -100, 1280, 720)
    assert v.gait_phase == phase
    before = (v.x, v.y)
    engine.tick(.1)
    assert_phase_step(v, before, phase)
    assert -1280 <= v.x <= -SPRITE_WIDTH and v.y == engine.floor_y


def test_locomotion_fields_are_runtime_only_and_independent_per_visitor():
    engine = Engine(seed=7)
    first = present(engine, standing=True)
    second = Visitor('ink', 800, engine.floor_y, state='idle', facing=-1,
                     target_x=800, target_y=engine.floor_y, next_action=1000)
    engine.visitors[second.cat_id] = second
    engine._walk(first, 650, first.y)
    engine.tick(.2)
    assert first.gait_phase != .137 and second.gait_phase == 0
    saved = engine.export()
    assert 'visitors' not in saved
    assert not Engine(saved).visitors


@pytest.mark.parametrize('jump', [False, True])
def test_idle_arrival_keeps_animation_clock_until_sitting_completes(jump):
    engine = Engine(seed=9)
    v = present(engine)
    engine._walk(v, 390, v.y - 90 if jump else v.y)
    for _ in range(200):
        engine.tick(.02)
        if v.state == 'idle':
            break
    assert v.state == 'idle' and v.settle_remaining == SETTLE_SECONDS
    phase = v.gait_phase
    engine.tick(LANDING_SECONDS + .01)
    assert v.settle_remaining > 0 and v.landing_remaining == 0
    assert engine.moving  # The standing-to-seated sequence now animates at 24FPS.
    engine.tick(SETTLE_SECONDS)
    assert v.settle_remaining == 0 and v.gait_phase == phase and not engine.moving


@pytest.mark.parametrize('arrival', ['eating', 'purring'])
@pytest.mark.parametrize('jump', [False, True])
def test_settling_does_not_postpone_food_or_affection(arrival, jump):
    engine = Engine(seed=9)
    v = present(engine)
    engine.food = 1
    engine._walk(v, 390, v.y - 90 if jump else v.y, arrival=arrival)
    for _ in range(200):
        engine.tick(.02)
        if v.state == arrival:
            break
    assert v.state == arrival and v.settle_remaining == 0
    assert v.landing_remaining == (LANDING_SECONDS if jump else 0)
    if arrival == 'eating':
        assert v.has_eaten and engine.food == 0


@pytest.mark.parametrize('action', ['carry', 'escape', 'jump', 'walk'])
def test_new_activity_clears_the_previous_settling_pose(action):
    engine = Engine(seed=8)
    v = present(engine)
    v.settle_remaining = SETTLE_SECONDS
    if action == 'carry':
        assert engine.carry(v.cat_id)
    elif action == 'escape':
        assert engine.startle(v.cat_id)
    else:
        engine._walk(v, 500, v.y - 100 if action == 'jump' else v.y)
    assert v.settle_remaining == 0


def test_stride_matches_measured_accepted_paw_support_distance():
    # Measured near-front-paw anchors in the accepted locomotion source frames.
    # Render scale .15625 produces physical pixels, then DPR2 gives logical px.
    source_support_start, source_support_end = 205, 677
    support_distance = (source_support_end - source_support_start) * .15625 / 2
    assert support_distance == pytest.approx(36.875)
    assert abs(GAIT_STRIDE / 2 - support_distance) <= .25
    assert GAIT_STRIDE / 66 == pytest.approx(1.1212121212)


@pytest.mark.parametrize('left,top', [(0,0),(-1920,-100)])
@pytest.mark.parametrize('target_x,target_y', [(1700,600),(300,400)])
def test_shrinking_screen_midjump_does_not_replay_the_old_outside_origin(left, top, target_x, target_y):
    engine = Engine(seed=7)
    engine.set_bounds(left, top, 1920, 1080)
    v = present(engine, left+1600)
    engine._walk(v, left+target_x, top+target_y)
    engine.tick(.2)
    phase = v.gait_phase
    engine.set_bounds(1920, 30, 800, 600)
    require_x, require_y = v.x, v.y
    assert v.gait_phase == phase
    if v.state == 'jumping':
        assert (v.jump_start_x,v.jump_start_y) == (require_x,require_y)
    for _ in range(200):
        before = (v.x,v.y)
        phase = v.gait_phase
        jumping = v.state == 'jumping'
        engine.tick(.04)
        assert 1920 <= v.x <= 1920+800-SPRITE_WIDTH
        assert 30 <= v.y <= engine.floor_y
        assert math.dist(before,(v.x,v.y)) < 30
        if jumping:
            assert v.gait_phase == phase
        else:
            assert_phase_step(v, before, phase)
            assert v.y == before[1]
        if v.state == 'idle':
            break
    assert v.state == 'idle'
    assert (v.x,v.y) == (v.target_x,v.target_y)


def test_repeated_unchanged_screen_bounds_preserve_an_unbound_jump():
    engine = Engine(seed=7)
    v = present(engine)
    engine._walk(v, 500, v.y-100)
    engine.tick(.2)
    before = (v.x,v.y,v.jump_start_x,v.jump_start_y,v.state_age,v.jump_duration,v.gait_phase)
    engine.set_bounds(*engine.bounds)
    assert (v.x,v.y,v.jump_start_x,v.jump_start_y,v.state_age,v.jump_duration,v.gait_phase) == before
