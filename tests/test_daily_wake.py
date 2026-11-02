"""Ordinary sleep must roll and rise before acting; natural entry reproductions.

No visitor position, intermediate pose, or elapsed model time is assigned. The
only initial fixture choices are seed, one visitor, trust 60 and calm demo mode.
"""
import pytest
from backyard_visitor.model import Engine, EATING_MUZZLE_LEFT, EATING_MUZZLE_RIGHT
from backyard_visitor.interaction_model import BELLY_ROLL_SECONDS, CROUCH_RISE_SECONDS


def advance_until(engine, condition, seconds=90, step=.05):
    for _ in range(round(seconds/step)):
        if condition():
            return
        engine.tick(step)
    assert condition(), 'Natural scene did not reach the required observation'


def sleeping_visitor(seed=0):
    engine = Engine(seed=seed, demo=True, visit_limit=1)
    engine.calm_mode = True
    engine.cats['mist'].trust = 60
    engine.open_gate()
    assert engine.invite('mist') == 'mist'
    advance_until(engine, lambda: 'mist' not in engine.visitors or engine.visitors['mist'].state == 'sleeping')
    visitor = engine.visitors['mist']
    assert visitor.state == 'sleeping' and visitor.posture_remaining == 0
    return engine, visitor


def position(visitor):
    return visitor.x, visitor.y, visitor.gait_phase, visitor.run_phase


def start_natural_wake(engine, visitor, *, feed=True):
    before = position(visitor)
    if feed:
        assert engine.feed()
    advance_until(engine, lambda: visitor.state != 'sleeping' or visitor.posture_remaining > 0, seconds=50)
    assert (visitor.posture_kind, visitor.posture_source, visitor.posture_target) == ('belly_roll', 'sleeping', 'play_crouch')
    assert visitor.posture_duration == BELLY_ROLL_SECONDS
    assert position(visitor) == before
    return before


@pytest.mark.parametrize('seed', [0, 3])
def test_natural_sleep_then_food_begins_with_roll_before_route(seed):
    engine, visitor = sleeping_visitor(seed)
    start_natural_wake(engine, visitor)
    assert visitor.posture_remaining > 0 and engine.moving
    assert engine.food == 1 and not visitor.has_eaten
    assert visitor.meal_bowl is None


def test_natural_wake_without_food_also_rolls_in_place():
    engine, visitor = sleeping_visitor()
    start_natural_wake(engine, visitor, feed=False)
    assert engine.food == 0


def test_roll_then_rise_stays_in_place_and_only_then_reaches_food():
    engine, visitor = sleeping_visitor()
    before = start_natural_wake(engine, visitor)
    phases = []
    for _ in range(30):
        if visitor.posture_remaining <= 0:
            break
        phases.append(visitor.posture_kind)
        assert position(visitor) == before
        assert engine.food == 1 and not visitor.has_eaten
        if visitor.posture_kind == 'crouch_rise':
            assert visitor.posture_source == 'play_crouch' and visitor.posture_target == 'standing'
            assert visitor.posture_duration == CROUCH_RISE_SECONDS
        engine.tick(.05)
    assert phases[0] == 'belly_roll' and 'crouch_rise' in phases
    assert visitor.posture_remaining == 0
    assert visitor.arrival_state == 'eating'
    advance_until(engine, lambda: visitor.state in {'eating', 'leaving', 'fleeing'}, seconds=12)
    assert visitor.state == 'eating' and visitor.has_eaten and engine.food == 0


def test_bowl_moved_during_wake_is_replanned_after_standing():
    engine, visitor = sleeping_visitor()
    before = start_natural_wake(engine, visitor)
    engine.tick(.2)
    engine.set_bowl(760, 670)
    assert position(visitor) == before and engine.food == 1
    advance_until(engine, lambda: visitor.posture_remaining == 0, seconds=2)
    assert visitor.meal_bowl == engine.bowl
    advance_until(engine, lambda: visitor.state in {'eating', 'leaving', 'fleeing'}, seconds=15)
    assert visitor.state == 'eating'
    muzzle = EATING_MUZZLE_RIGHT if visitor.facing > 0 else EATING_MUZZLE_LEFT
    assert visitor.x+muzzle == pytest.approx(engine.bowl[0])
    assert engine.food == 0


@pytest.mark.parametrize('action', ['close', 'carry', 'startle'])
def test_urgent_action_cancels_wake_without_resuming_meal(action):
    engine, visitor = sleeping_visitor()
    before = start_natural_wake(engine, visitor)
    engine.tick(.2)
    if action == 'close':
        engine.open_gate(False)
    elif action == 'carry':
        assert engine.carry('mist')
    else:
        # Exercise the public fear path after a deliberately low-trust fixture;
        # no pose or coordinate is injected, and normal waking used trust 60.
        engine.cats['mist'].trust = 5
        assert engine.startle('mist')
    assert position(visitor) == before
    assert visitor.posture_remaining == 0 and visitor.posture_kind == ''
    for _ in range(30):
        engine.tick(.05)
    assert visitor.state in {'carried', 'leaving', 'fleeing'} or 'mist' not in engine.visitors
    assert engine.food == 1 and not visitor.has_eaten


def test_reported_1600_by_1000_seed3_scene_uses_the_same_wake_sequence():
    engine = Engine(seed=3, demo=True, visit_limit=1)
    engine.set_bounds(0, 0, 1600, 1000)
    engine.calm_mode = True
    engine.cats['mist'].trust = 60
    engine.open_gate()
    engine.invite('mist')
    visitor = engine.visitors['mist']
    advance_until(engine, lambda: visitor.state == 'sleeping')
    start_natural_wake(engine, visitor)
    assert visitor.posture_kind == 'belly_roll'


def window_sleeping_visitor():
    engine = Engine(seed=10, demo=True, visit_limit=1)
    engine.set_bounds(-1280, -720, 1280, 720)
    engine.calm_mode = True
    engine.cats['amber'].trust = 60
    engine.set_window_edges([('editor', -1200, -180, -300)])
    engine.open_gate()
    engine.invite('amber')
    visitor = engine.visitors['amber']
    advance_until(engine, lambda: visitor.state == 'sleeping' and visitor.window_edge_id == 'editor')
    return engine, visitor


def test_negative_coordinate_window_holds_support_until_standing_then_descends():
    engine, visitor = window_sleeping_visitor()
    before = start_natural_wake(engine, visitor)
    assert visitor.y == -300-112
    phases = set()
    for _ in range(30):
        if visitor.posture_remaining == 0:
            break
        phases.add(visitor.posture_kind)
        assert position(visitor) == before
        assert visitor.window_edge_id == 'editor'
        assert engine.food == 1
        engine.set_window_edges([('editor', -1200, -180, -300)])
        engine.tick(.05)
    assert phases == {'belly_roll', 'crouch_rise'}
    assert visitor.state == 'jumping' and visitor.arrival_state == 'eating'
    assert visitor.takeoff_edge_id == 'editor'
    assert position(visitor) == before
    engine.tick(.05)
    assert visitor.x == before[0] and visitor.y == before[1]  # Supported takeoff crouch.


def test_vanished_window_interrupts_wake_to_a_valid_descent():
    engine, visitor = window_sleeping_visitor()
    before = start_natural_wake(engine, visitor)
    engine.tick(.2)
    engine.set_window_edges([])
    assert position(visitor) == before
    assert visitor.posture_kind == '' and visitor.posture_remaining == 0
    assert visitor.state == 'jumping'
    assert visitor.window_edge_id is None and visitor.window_target_id is None
    assert visitor.route_final_y == engine.floor_y
    assert engine.food == 1
    engine.tick(.1)
    assert visitor.state != 'waking'


def test_daily_roll_and_rise_do_not_finish_before_their_full_duration():
    engine, visitor = sleeping_visitor()
    before = start_natural_wake(engine, visitor)
    engine.tick(BELLY_ROLL_SECONDS-.01)
    assert visitor.posture_kind == 'belly_roll' and position(visitor) == before
    engine.tick(.01)
    assert visitor.posture_kind == 'crouch_rise' and position(visitor) == before
    engine.tick(CROUCH_RISE_SECONDS-.01)
    assert visitor.posture_remaining == pytest.approx(.01) and position(visitor) == before
    engine.tick(.01)
    assert visitor.posture_remaining == 0 and visitor.rise_remaining == 0
    assert position(visitor) == before  # First route frame has not consumed a movement tick.


def test_pending_affection_feedback_does_not_interrupt_wake():
    engine, visitor = sleeping_visitor()
    before = start_natural_wake(engine, visitor)
    visitor.true_name_pending = True
    visitor.gift_response_pending = True
    remaining = visitor.posture_remaining
    assert not engine._deliver_true_name(visitor)
    # The special recognition response takes priority over gift gratitude,
    # but both are prevented from replacing the physical wake transition.
    assert visitor.true_name_pending
    assert visitor.posture_remaining == remaining
    assert visitor.posture_kind == 'belly_roll'
    engine.tick(.1)
    assert visitor.state == 'waking'
    assert position(visitor) == before
    assert visitor.posture_remaining == pytest.approx(remaining-.1)
