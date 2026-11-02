"""Trust changes actual occupied time, not merely an RNG argument.

Normal-mode engines run for two effective hours with their real arrival,
movement, meal, fight and departure transitions. Peeking and departing count
as occupied too; neither trust nor stay timers are forced during simulation.
"""
from functools import lru_cache

import pytest

from backyard_visitor.model import Engine, SPRITE_HEIGHT, SPRITE_WIDTH, GENERATED_CAT_IDS

CAT_IDS = ('amber', 'mist', 'ink', 'onyx', *GENERATED_CAT_IDS)
SEEDS = (0, 1, 4, 7, 19, 42, 113, 231)


def advance(engine, seconds):
    remaining = seconds
    while remaining > 1e-9:
        dt = min(.25, remaining)
        engine.tick(dt)
        remaining -= dt


def until(engine, predicate, timeout):
    for _ in range(round(timeout/.25)):
        if predicate():
            return
        engine.tick(.25)
    assert predicate(), 'The expected natural lifecycle event did not occur'


@lru_cache(maxsize=None)
def occupancy(trust, seed, limit, food=0, calm=False):
    seconds = 7200
    engine = Engine({'version': 1, 'food': food, 'calm_mode': calm,
        'cats': {cid: {'trust': trust} for cid in CAT_IDS}}, seed=seed, visit_limit=limit)
    engine.open_gate()
    occupied = visible = overlap = max_visitors = meals = 0
    left, top, width, height = engine.bounds
    for _ in range(round(seconds/.25)):
        engine.tick(.25)
        count = len(engine.visitors)
        occupied += bool(count)
        visible += any(v.x < left+width and v.x+SPRITE_WIDTH > left and
                       v.y < top+height and v.y+SPRITE_HEIGHT > top
                       for v in engine.visitors.values())
        overlap += count == 2
        max_visitors = max(max_visitors, count)
        meals += sum(event['kind'] == 'trust' for event in engine.drain_events())
    return {'ratio': occupied*.25/seconds, 'visible_ratio': visible*.25/seconds,
            'overlap': overlap*.25/seconds,
            'max_visitors': max_visitors, 'meals': meals,
            'trust_total': sum(cat.trust for cat in engine.cats.values())}


@pytest.mark.parametrize('seed', SEEDS)
@pytest.mark.parametrize('limit', [1, 2])
def test_actual_occupancy_rises_from_quiet_strangers_to_familiar_company(seed, limit):
    low = occupancy(0, seed, limit)
    middle = occupancy(50, seed, limit)
    high = occupancy(100, seed, limit)
    assert 0 < low['ratio'] < .5
    assert middle['ratio'] > low['ratio']+.15
    assert .85 < high['visible_ratio'] <= high['ratio'] < 1
    assert high['ratio'] > middle['ratio']+.15
    assert all(result['max_visitors'] <= limit for result in (low, middle, high))
    if limit == 2:
        assert high['overlap'] > 0  # The occupancy bound does not disable peers.


@pytest.mark.parametrize('seed', SEEDS)
@pytest.mark.parametrize('limit', [1, 2])
def test_fresh_food_attracts_and_rewards_without_majority_occupancy(seed, limit):
    plain = occupancy(0, seed, limit)
    fed = occupancy(0, seed, limit, food=3)
    assert plain['ratio'] < fed['ratio'] < .5
    assert fed['max_visitors'] <= limit
    assert fed['meals'] == 3 and fed['trust_total'] >= 54


@pytest.mark.parametrize('seed', [0, 7])
def test_calm_mode_does_not_need_fights_to_keep_fresh_garden_quiet(seed):
    assert occupancy(0, seed, 2, food=3, calm=True)['ratio'] < .5


def test_new_save_starts_at_zero_and_saved_trust_is_not_reset():
    fresh = Engine(seed=1)
    assert all(cat.trust == 0 for cat in fresh.cats.values())
    saved = Engine({'version': 1, 'cats': {'amber': {'trust': 64},
                                          'mist': {'trust_level': 27}}}, seed=1)
    assert saved.cats['amber'].trust == 64
    assert saved.cats['mist'].trust == 27
    saved.open_gate()
    advance(saved, 400)
    assert saved.cats['amber'].trust == 64
    assert saved.cats['mist'].trust == 27


@pytest.mark.parametrize('food', [0, 3])
def test_repeated_gate_opening_after_first_meeting_never_gets_a_fast_stranger_visit(food):
    engine = Engine({'version': 1, 'food': food,
        'cats': {'amber': {'discovered': True, 'visits': 1}}}, seed=4)
    for _ in range(4):
        engine.open_gate()
        advance(engine, 150)
        assert not engine.visitors
        assert not engine.open_gate()  # An idempotent call does not reschedule.
        engine.open_gate(False)
        advance(engine, 20)
        assert not engine.visitors
    engine.open_gate()
    until(engine, lambda: bool(engine.visitors), 361)


def test_food_added_after_first_meeting_cannot_create_four_second_arrival():
    engine = Engine({'cats': {'amber': {'discovered': True, 'visits': 1}}}, seed=7)
    engine.open_gate()
    advance(engine, 30)
    for _ in range(3):
        assert engine.feed()
    assert not engine.feed()
    advance(engine, 150)
    assert not engine.visitors
    until(engine, lambda: bool(engine.visitors), 210)


@pytest.mark.parametrize('reopen_while_departing', [False, True])
def test_last_departure_starts_real_quiet_interval_after_two_explicit_guests(reopen_while_departing):
    engine = Engine(seed=19, visit_limit=2)
    engine.calm_mode = True
    engine.open_gate()
    assert engine.invite('amber') == 'amber'
    assert engine.invite('ink') == 'ink'
    assert engine.invite('mist') is None
    if reopen_while_departing:
        advance(engine, 25)
        engine.open_gate(False)
        engine.open_gate()
    until(engine, lambda: not engine.visitors, 200)
    # Count from actual departure, rather than from the earlier arrival or
    # timeout. This also covers closing/reopening while the cats are leaving.
    advance(engine, 239)
    assert not engine.visitors
    until(engine, lambda: bool(engine.visitors), 122)


def test_closing_gate_leaves_and_suppresses_automatic_arrivals():
    engine = Engine(seed=42)
    engine.open_gate()
    assert engine.invite('amber') == 'amber'
    advance(engine, 25)
    engine.open_gate(False)
    until(engine, lambda: not engine.visitors, 70)
    advance(engine, 600)
    assert not engine.visitors


def test_demo_retains_prompt_arrivals_and_short_visit():
    engine = Engine(seed=4, demo=True)
    engine.open_gate()
    until(engine, lambda: bool(engine.visitors), 5)
    assert next(iter(engine.visitors.values())).stay_remaining == 45
    assert engine._time <= 5


def test_missing_resident_still_returns_promptly_with_a_guest_present():
    engine = Engine({'version': 1, 'cats': {'amber': {
        'trust': 100, 'discovered': True, 'adopted': True}}}, seed=231)
    engine.open_gate()
    assert engine.invite('ink') == 'ink'
    until(engine, lambda: 'amber' in engine.visitors, 31)
    assert set(engine.visitors) == {'amber', 'ink'}
