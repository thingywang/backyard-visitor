"""Public social actions on naturally sleeping cats must preserve the belly pose.

Only seed/trust/calm preference and ordinary public arrival/adoption/window APIs
prepare scenes. No intermediate visitor pose, position, or clock is injected.
"""
import json
import pytest
from backyard_visitor.model import Engine, TRUE_NAME_SECONDS
from backyard_visitor.interaction_model import BELLY_ROLL_SECONDS


def advance(engine, seconds, step=.05):
    remaining = seconds
    while remaining > 1e-9:
        dt = min(step, remaining)
        engine.tick(dt)
        remaining -= dt


def until(engine, predicate, seconds=120):
    for _ in range(round(seconds/.05)):
        if predicate():
            return
        engine.tick(.05)
    assert predicate(), 'Natural scene did not reach required observation'


def sleeping_cat(*, window=False, trust=100, seed=None):
    cid = 'ink' if window else 'mist'
    engine = Engine(seed=(2 if window else 0) if seed is None else seed,
                    demo=True, visit_limit=1)
    engine.calm_mode = True
    engine.cats[cid].trust = trust
    engine.open_gate()
    assert engine.invite(cid) == cid
    engine.name_cat(cid, 'Audit nickname')
    if trust == 100:
        assert engine.adopt(cid)
    if window:
        engine.set_window_edges([('review-window', 0, 1280, 500)])
    until(engine, lambda: engine.visitors[cid].state == 'sleeping'
          and (not window or bool(engine.visitors[cid].window_edge_id)), seconds=400)
    visitor = engine.visitors[cid]
    assert visitor.posture_remaining == 0
    return engine, visitor


def start_response(engine, visitor, action):
    if action == 'pet':
        assert engine.pet(visitor.cat_id)
    elif action == 'call':
        assert engine.call_cat(visitor.cat_id) == 'recognized'
    else:
        # Compare only the outcome; never print or serialize the secret input.
        result = engine.name_cat(visitor.cat_id, engine.cats[visitor.cat_id].true_name)
        assert result == 'true_name'


@pytest.mark.parametrize('action', ['pet', 'call', 'true_name'])
def test_sleeping_social_response_retains_pose_and_original_nap(action):
    engine, visitor = sleeping_cat()
    advance(engine, .8)
    original_age, original_next = visitor.state_age, visitor.next_action
    position = (visitor.x, visitor.y, visitor.gait_phase, visitor.run_phase)
    assert visitor.on_back
    start_response(engine, visitor, action)
    assert visitor.state == 'purring' and visitor.sleeping_purr and visitor.on_back
    assert visitor.sleeping_purr_age == pytest.approx(original_age)
    assert visitor.posture_remaining == 0
    assert (visitor.x, visitor.y, visitor.gait_phase, visitor.run_phase) == position
    assert visitor.next_action == original_next
    if action == 'true_name':
        assert engine.cats[visitor.cat_id].trust == 100
        assert visitor.true_name_remaining == TRUE_NAME_SECONDS
    duration = TRUE_NAME_SECONDS if action == 'true_name' else 4.0
    advance(engine, duration + .05)
    assert visitor.state == 'sleeping' and visitor.on_back
    assert not visitor.sleeping_purr and visitor.sleeping_purr_age == 0
    assert visitor.state_age == pytest.approx(original_age + duration + .05, abs=.051)
    assert visitor.next_action == original_next
    assert (visitor.x, visitor.y) == position[:2]


def test_completed_nap_wakes_through_existing_roll_instead_of_starting_new_nap():
    engine, visitor = sleeping_cat()
    deadline = visitor.next_action * 3
    advance(engine, deadline - 2)
    assert visitor.state == 'sleeping'
    assert engine.pet(visitor.cat_id)
    advance(engine, 4.1)
    assert visitor.state == 'waking'
    assert visitor.posture_kind == 'belly_roll'
    assert 0 < visitor.posture_remaining <= BELLY_ROLL_SECONDS
    assert not visitor.sleeping_purr


def test_repeat_input_does_not_restart_response_or_lose_elapsed_sleep():
    engine, visitor = sleeping_cat()
    advance(engine, .8)
    original_age = visitor.state_age
    assert engine.pet(visitor.cat_id)
    advance(engine, 1)
    response_age = visitor.state_age
    assert not engine.pet(visitor.cat_id)
    assert engine.call_cat(visitor.cat_id) == 'recognized'
    assert visitor.state_age == pytest.approx(response_age)
    start_response(engine, visitor, 'true_name')
    assert visitor.sleeping_purr_age == pytest.approx(original_age + 1)
    advance(engine, 1)
    remaining = visitor.true_name_remaining
    start_response(engine, visitor, 'true_name')
    assert visitor.true_name_remaining == pytest.approx(remaining)
    advance(engine, remaining + .05)
    assert visitor.state == 'sleeping'
    assert visitor.state_age == pytest.approx(original_age + 1 + TRUE_NAME_SECONDS + .05, abs=.051)


@pytest.mark.parametrize('urgent', ['carry', 'close', 'startle'])
def test_urgent_action_cancels_sleep_origin_without_replaying_it(urgent):
    engine, visitor = sleeping_cat()
    assert engine.pet(visitor.cat_id)
    advance(engine, .3)
    if urgent == 'carry':
        assert engine.carry(visitor.cat_id)
    elif urgent == 'close':
        engine.open_gate(False)
    else:
        # Reach the accepted fear boundary after a natural sleep/pet sequence;
        # this changes only a profile fixture, never pose/position/route clocks.
        engine.cats[visitor.cat_id].trust = 10
        assert engine.startle(visitor.cat_id)
    assert not visitor.sleeping_purr and visitor.sleeping_purr_age == 0
    assert not visitor.on_back
    if urgent == 'carry':
        assert engine.release(visitor.cat_id, visitor.x, engine.floor_y)
        advance(engine, .1)
        assert visitor.state != 'purring'
    else:
        until(engine, lambda: visitor.cat_id not in engine.visitors, seconds=30)
    assert not visitor.sleeping_purr


@pytest.mark.parametrize('action', ['pet', 'true_name'])
def test_support_loss_cancels_sleep_response_and_does_not_replay_after_landing(action):
    engine, visitor = sleeping_cat(window=True)
    start_response(engine, visitor, action)
    before = (visitor.x, visitor.y)
    engine.set_window_edges([])
    assert (visitor.x, visitor.y) == before
    assert visitor.state == 'jumping'
    assert not visitor.sleeping_purr and visitor.sleeping_purr_age == 0
    assert not visitor.true_name_pending and visitor.true_name_remaining == 0
    until(engine, lambda: visitor.state == 'idle', seconds=20)
    assert visitor.y == engine.floor_y
    assert not visitor.sleeping_purr and not visitor.true_name_pending
    advance(engine, .5)
    assert visitor.state != 'purring'


def test_teaser_rolls_out_of_belly_response_and_cancel_does_not_restore_old_purr():
    engine, visitor = sleeping_cat()
    assert engine.pet(visitor.cat_id)
    assert engine.start_teaser(visitor.cat_id)
    assert visitor.posture_kind == 'belly_roll'
    assert visitor.posture_source == 'sleeping'
    assert visitor.posture_remaining == BELLY_ROLL_SECONDS
    assert not visitor.sleeping_purr
    advance(engine, .2)
    assert engine.stop_teaser(visitor.cat_id)
    assert engine.active_teaser_id is None
    assert not visitor.sleeping_purr
    advance(engine, 5)
    assert visitor.state != 'purring'


def test_recognized_call_cannot_disable_belly_scratch_boundary(monkeypatch):
    engine, visitor = sleeping_cat(trust=60)
    assert engine.call_cat(visitor.cat_id) == 'recognized'
    before = engine.cats[visitor.cat_id].trust
    # A deterministic successful scratch draw isolates the existing chance rule.
    monkeypatch.setattr(engine.rng, 'random', lambda: 0.0)
    assert not engine.pet(visitor.cat_id)
    assert visitor.state == 'swatting' and not visitor.sleeping_purr
    assert engine.cats[visitor.cat_id].trust == before
    assert any(event['kind'] == 'scratch' for event in engine.drain_events())


def test_pending_collected_gift_does_not_wake_a_sleeping_cat():
    engine, visitor = sleeping_cat(seed=3)
    assert engine.gifts  # Seed 3 naturally carries and drops a feather on entry.
    gift = engine.gifts[0]
    assert gift.kind == 'feather'
    assert engine.collect_gift(gift.id)
    assert visitor.state == 'sleeping' and visitor.on_back
    assert not visitor.sleeping_purr
    assert visitor.gift_response_pending and visitor.gift_response_remaining == 0


def test_sleep_source_is_runtime_only_and_does_not_enter_save():
    engine, visitor = sleeping_cat()
    assert engine.pet(visitor.cat_id)
    state = engine.export()
    serialized = json.dumps(state)
    assert 'sleeping_purr' not in serialized
    restored = Engine(state)
    assert not restored.visitors and not restored.gate_open
    assert restored.cats[visitor.cat_id].adopted
