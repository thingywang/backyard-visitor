"""Meal eligibility belongs to the cat, not a visit or an arrival callback."""
import json
import math
import pytest
from backyard_visitor.model import Engine, Visitor, MEAL_COOLDOWN_SECONDS


def setup(cid='amber', adopted=False, negative=False):
    e = Engine(seed=3, visit_limit=1)
    if negative:
        e.set_bounds(-1920, -100, 1920, 1080)
    e.open_gate(); assert e.invite(cid) == cid
    e._visit_countdown = 10000
    until(e, lambda: e.visitors[cid].state == 'idle')
    v = e.visitors[cid]
    v.next_action = v.stay_remaining = v.parkour_cooldown = 10000
    v.carrying_gift = None
    if adopted:
        e.cats[cid].trust = 100
        assert e.adopt(cid)
    return e, v


def advance(e, seconds):
    while seconds > 1e-9:
        step = min(.1, seconds)
        e.tick(step); seconds -= step


def until(e, predicate, seconds=60):
    for _ in range(round(seconds/.05)):
        if predicate():
            return
        e.tick(.05)
    assert predicate()


def start_meal(e, v):
    assert e.feed()
    until(e, lambda: v.state == 'eating')
    assert e.cats[v.cat_id].meal_cooldown_remaining == MEAL_COOLDOWN_SECONDS


@pytest.mark.parametrize('adopted', [False, True])
@pytest.mark.parametrize('negative', [False, True])
def test_long_stay_visitors_can_finish_two_meals_after_real_cooldown(adopted, negative):
    e, v = setup(adopted=adopted, negative=negative)
    start_meal(e, v)
    until(e, lambda: v.state == 'grooming')
    trust = e.cats[v.cat_id].trust
    until(e, lambda: v.state == 'idle')
    v.next_action = 10000
    assert e.feed()
    remaining = e.cats[v.cat_id].meal_cooldown_remaining
    advance(e, remaining-.1)
    assert e.food == 1 and not e.can_eat(v.cat_id)
    assert v.has_eaten and e.cats[v.cat_id].trust == trust
    until(e, lambda: v.state == 'eating', 10)
    assert e.food == 0 and e.cats[v.cat_id].meal_cooldown_remaining == 180
    until(e, lambda: v.state == 'grooming')
    assert e.cats[v.cat_id].trust == min(100, trust+22)
    assert sum(x['kind'] == 'eating' for x in e.drain_events()) == 2


def test_feed_route_and_empty_arrival_do_not_start_cooldown():
    e, v = setup()
    e.set_bowl(e.bounds[0]+24, e.floor_y+97)
    e.feed(); e.tick(.05)
    assert v.arrival_state == 'eating' and v.state in {'walking', 'jumping'}
    assert e.can_eat(v.cat_id) and e.cats[v.cat_id].meal_cooldown_remaining == 0
    e.food = 0
    until(e, lambda: v.state == 'idle')
    assert e.can_eat(v.cat_id) and not v.has_eaten
    assert not any(x['kind'] == 'eating' for x in e.drain_events())


def test_duplicate_arrival_does_not_consume_again_reset_clock_or_reward_twice():
    e, v = setup(); start_meal(e, v)
    e.feed(); advance(e, 1)
    remaining = e.cats[v.cat_id].meal_cooldown_remaining
    for _ in range(5):
        assert not e._begin_eating(v)
    assert v.state == 'eating' and v.state_age == pytest.approx(1)
    assert e.food == 1 and e.cats[v.cat_id].meal_cooldown_remaining == remaining
    advance(e, 8.5)
    assert e.cats[v.cat_id].trust == 22 and e.food == 1
    assert sum(x['kind'] == 'trust' for x in e.drain_events()) == 1


@pytest.mark.parametrize('urgent', ['carry', 'close'])
def test_interrupted_consumed_meal_keeps_cooldown_but_does_not_grant_reward(urgent):
    e, v = setup(); start_meal(e, v)
    if urgent == 'carry':
        assert e.carry(v.cat_id)
    else:
        e.open_gate(False)
    advance(e, 10)
    assert e.food == 0 and e.cats[v.cat_id].trust == 0
    assert e.cats[v.cat_id].meal_cooldown_remaining == pytest.approx(170)
    assert not any(x['kind'] == 'trust' for x in e.drain_events())


def test_close_then_reinvite_does_not_reset_identity_cooldown():
    e, v = setup(); start_meal(e, v); e.open_gate(False)
    until(e, lambda: not e.visitors)
    remaining = e.cats[v.cat_id].meal_cooldown_remaining
    e.open_gate(); assert e.invite(v.cat_id) == v.cat_id
    assert e.cats[v.cat_id].meal_cooldown_remaining == remaining
    e.feed(); advance(e, 25)
    assert e.food == 1 and not e.can_eat(v.cat_id)


@pytest.mark.parametrize('state', ['sleeping', 'carried'])
def test_cooldown_can_expire_without_waking_or_feeding_unsafe_state(state):
    e, v = setup(); start_meal(e, v)
    e._state(v, state); v.next_action = 10000
    e.feed(); position = (v.x, v.y)
    advance(e, 181)
    assert e.can_eat(v.cat_id) and v.state == state
    assert e.food == 1 and (v.x, v.y) == position


def test_cooldowns_are_independent_and_pause_does_not_use_wall_clock():
    e, v = setup(); start_meal(e, v)
    assert e.can_eat('mist') and not e.can_eat('amber')
    assert not e.can_eat('unknown') and not e.can_eat(None)
    snapshot = e.export(); e.tick(0)
    assert e.export() == snapshot
    # Existing wake protection bounds huge elapsed intervals, never simulates
    # an offline burst of meals. The app discards actual suspend gaps entirely.
    e.tick(1e9)
    assert e.cats['amber'].meal_cooldown_remaining == 175


@pytest.mark.parametrize('value,expected', [
    (None, 0), (True, 0), (False, 0), ('120', 0), (-1, 0),
    (float('nan'), 0), (float('inf'), 0), ([], 0), ({}, 0),
    (10**1000, 180), (1e100, 180), (12.75, 12.75), (0, 0),
])
def test_cooldown_restore_is_strict_finite_and_bounded(value, expected):
    e = Engine({'cats': {'amber': {'meal_cooldown_remaining': value}}})
    assert e.cats['amber'].meal_cooldown_remaining == expected
    assert e.can_eat('amber') == (expected == 0)
    assert math.isfinite(e.export()['cats']['amber']['meal_cooldown_remaining'])


def test_old_save_defaults_and_export_restore_preserve_only_remaining_time():
    assert Engine({'cats': {'amber': {'visits': 5}}}).can_eat('amber')
    e, v = setup(); start_meal(e, v); advance(e, 1.25)
    data = json.loads(json.dumps(e.export()))
    restored = Engine(data)
    assert restored.cats[v.cat_id].meal_cooldown_remaining == pytest.approx(178.75)
    assert not restored.visitors and not restored.gate_open and not restored.can_eat(v.cat_id)
    assert not any('timestamp' in key for key in data['cats'][v.cat_id])


def test_bowl_moved_en_route_replans_without_eating_at_the_old_spot():
    e, v = setup(negative=True)
    e.set_bowl(-1896, e.floor_y+97); e.feed(); e.tick(.05)
    old_target = (v.route_final_x or v.target_x, v.route_final_y or v.target_y)
    e.set_bowl(-24, e.floor_y+97)
    until(e, lambda: v.state == 'eating', 80)
    assert (v.x, v.y) != old_target and v.x+129.7 == pytest.approx(-24)
    assert e.food == 0 and e.cats[v.cat_id].meal_cooldown_remaining == 180


def test_two_cats_compete_for_one_portion_without_starting_losers_cooldown():
    e = Engine(seed=7); e.open_gate(); e.calm_mode = True; e._visit_countdown = 10000
    for cid, x in [('amber', 350), ('mist', 800)]:
        e.invite(cid)
        e.visitors[cid] = Visitor(cid, x, e.floor_y, state='idle', next_action=10000, stay_remaining=10000)
    e.feed(); until(e, lambda: any(v.state == 'eating' for v in e.visitors.values()))
    winner = next(cid for cid, v in e.visitors.items() if v.state == 'eating')
    loser = next(cid for cid in e.visitors if cid != winner)
    advance(e, 20)
    assert e.food == 0 and not e.can_eat(winner) and e.can_eat(loser)
    assert sum(x['kind'] == 'eating' for x in e.drain_events()) == 1


def test_window_peek_only_rises_for_food_after_identity_cooldown_expires():
    e, v = setup(); e.set_window_edges([('editor', 250, 1000, 550)])
    assert e._move_to_window(v, 'editor')
    until(e, lambda: v.state == 'idle'); assert e._begin_window_peek(v)
    v.has_eaten = True; e.cats[v.cat_id].meal_cooldown_remaining = .5
    e.feed()
    assert v.peek_after == 'idle'
    advance(e, .2); assert v.peek_after == 'idle'
    advance(e, .4); assert v.peek_after == 'eating' and v.peek_phase == 'rising'
    until(e, lambda: v.state == 'eating')
    assert e.food == 0 and e.cats[v.cat_id].meal_cooldown_remaining == 180
