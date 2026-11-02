"""Demo gifts must wait through the same physical wake as ordinary sleep.

Scenes start with a natural public arrival and decisions. No pose, coordinate,
route, or action clock is injected; trust 60 ensures the first arrival has no gift.
"""
import pytest
from backyard_visitor.model import Engine
from backyard_visitor.interaction_model import BELLY_ROLL_SECONDS, CROUCH_RISE_SECONDS


def until(engine, predicate, seconds=90):
    for _ in range(round(seconds/.05)):
        if predicate():
            return
        engine.tick(.05)
    assert predicate(), 'Natural scene did not reach observation'


def sleeper():
    e = Engine(seed=0, demo=True, visit_limit=1)
    e.calm_mode = True
    e.cats['mist'].trust = 60
    e.open_gate(); assert e.invite('mist') == 'mist'
    until(e, lambda: e.visitors['mist'].state == 'sleeping')
    v = e.visitors['mist']
    assert not e.gifts and not v.carrying_gift
    return e, v


def feet(v):
    return v.x, v.y, v.gait_phase, v.run_phase


@pytest.mark.parametrize('scene,kind', [('gift', 'feather'), ('mouse', 'toy_mouse')])
def test_natural_sleep_demo_rolls_and_rises_before_showing_or_delivering_gift(scene, kind):
    e, v = sleeper()
    original = feet(v)
    assert e.demo_scene(scene)
    assert (v.posture_kind, v.posture_source, v.posture_target) == ('belly_roll', 'sleeping', 'play_crouch')
    assert v.posture_remaining == BELLY_ROLL_SECONDS
    assert not e.gifts and not v.carrying_gift and v.demo_gift_pending == kind
    seen = []
    for _ in range(round((BELLY_ROLL_SECONDS + CROUCH_RISE_SECONDS)/.05)):
        assert feet(v) == original
        assert v.posture_remaining > 0
        seen.append(v.posture_kind)
        assert not e.gifts and not v.carrying_gift
        if v.posture_kind == 'crouch_rise':
            assert (v.posture_source, v.posture_target) == ('play_crouch', 'standing')
        e.tick(.05)
    assert seen[0] == 'belly_roll' and 'crouch_rise' in seen
    until(e, lambda: bool(e.gifts), seconds=12)
    assert len(e.gifts) == 1 and e.gifts[0].kind == kind
    assert v.demo_gift_pending is None and not v.carrying_gift
    assert v.posture_remaining == 0 and v.state not in {'sleeping', 'waking', 'carried'}


@pytest.mark.parametrize('elapsed', [.2, .6], ids=['during_roll', 'during_rise'])
@pytest.mark.parametrize('action', ['close', 'carry'])
def test_urgent_interruption_clears_wake_and_preserves_existing_demo_gift_contract(action, elapsed):
    e, v = sleeper()
    assert e.demo_scene('mouse')
    for _ in range(round(elapsed/.05)):
        e.tick(.05)
    assert v.posture_kind == ('belly_roll' if elapsed < BELLY_ROLL_SECONDS else 'crouch_rise')
    assert not e.gifts and not v.carrying_gift
    if action == 'close':
        e.open_gate(False)
        assert v.demo_gift_pending is None
        assert v.posture_remaining == 0
        until(e, lambda: not e.visitors, seconds=30)
        assert not e.gifts
    else:
        assert e.carry(v.cat_id)
        assert v.posture_remaining == 0 and v.state == 'carried'
        assert v.demo_gift_pending == 'toy_mouse'
        e.tick(.5)
        assert not e.gifts and not v.carrying_gift
        assert e.release(v.cat_id, v.x, v.y)
        until(e, lambda: bool(e.gifts), seconds=12)
        assert len(e.gifts) == 1 and e.gifts[0].kind == 'toy_mouse'
        assert v.demo_gift_pending is None and v.posture_remaining == 0


def test_second_demo_choice_during_roll_changes_pending_gift_without_restarting_motion():
    e, v = sleeper()
    assert e.demo_scene('gift')
    e.tick(.2)
    original = feet(v)
    remaining = v.posture_remaining
    assert remaining > 0
    assert e.demo_scene('mouse')
    assert feet(v) == original and v.posture_remaining == remaining
    assert v.posture_kind == 'belly_roll' and v.demo_gift_pending == 'toy_mouse'
    assert not e.gifts and not v.carrying_gift
    until(e, lambda: bool(e.gifts), seconds=12)
    assert len(e.gifts) == 1 and e.gifts[0].kind == 'toy_mouse'
