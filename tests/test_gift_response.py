"""Collection rewards persist; gratitude is a brief, safely scheduled pose."""
from dataclasses import asdict
import json
import pytest
from backyard_visitor.model import Engine, Visitor, GIFT_RESPONSE_SECONDS, TRUE_NAME_SECONDS


def setup(state='idle'):
    e = Engine(seed=3); e.open_gate(); e.invite('amber')
    v = Visitor('amber', 350, e.floor_y, state=state, next_action=10000, stay_remaining=10000)
    e.visitors['amber'] = v; e._visit_countdown = e._fight_countdown = 10000
    v.parkour_cooldown = 10000
    return e, v


def gift(e, kind='feather', cid='amber'):
    e._drop(kind, 400, e.floor_y+74, cid)
    return e.gifts[-1].id


def until(e, predicate, seconds=40):
    for _ in range(round(seconds/.05)):
        if predicate(): return
        e.tick(.05)
    assert predicate()


@pytest.mark.parametrize('kind', ['feather', 'bottle_cap', 'toy_mouse'])
def test_successful_collection_has_exactly_one_reward_and_three_second_response(kind):
    e, v = setup(); gid = gift(e, kind)
    assert e.collect_gift(gid)
    assert v.state == 'purring' and v.gift_response_remaining == GIFT_RESPONSE_SECONDS
    assert not v.gift_response_pending and e.moving
    assert e.cats[v.cat_id].trust == 3 and e.collected_count(kind) == 1
    assert not e.collect_gift(gid)
    e.tick(2.9); assert v.state == 'purring' and v.gift_response_remaining > 0
    e.tick(.1)
    assert v.state == 'idle' and v.gift_response_remaining == 0 and not e.moving
    kinds = [x['kind'] for x in e.drain_events()]
    assert kinds.count('gift_response') == 1
    assert kinds.index('gift_collected') < kinds.index('gift_response')


def test_ordinary_purr_near_timeout_gets_full_gratitude_but_repeated_gifts_do_not_extend():
    e, v = setup('purring'); v.state_age = 3.9
    assert e.collect_gift(gift(e)); e.tick(1)
    assert v.state == 'purring' and v.gift_response_remaining == 2
    assert e.collect_gift(gift(e, 'bottle_cap'))
    assert v.gift_response_remaining == 2 and not v.gift_response_pending
    e.tick(2)
    assert v.state == 'idle' and v.gift_response_remaining == 0
    assert e.collected_count('feather') == e.collected_count('bottle_cap') == 1
    assert e.cats[v.cat_id].trust == 6


@pytest.mark.parametrize('state', ['sleeping', 'eating', 'grooming', 'playing', 'teasing', 'resting', 'swatting', 'peeking', 'entering', 'window_peeking'])
def test_collection_does_not_change_an_unsafe_pose_or_position(state):
    e, v = setup(state); before = (v.state, v.x, v.y, v.state_age)
    assert e.collect_gift(gift(e))
    assert before == (v.state, v.x, v.y, v.state_age)
    assert v.gift_response_pending and v.gift_response_remaining == 0
    assert e.collect_gift(gift(e)) and v.gift_response_pending
    assert not any(x['kind'] == 'gift_response' for x in e.drain_events())


@pytest.mark.parametrize('timer', ['landing_remaining', 'settle_remaining', 'rise_remaining', 'posture_remaining'])
def test_idle_response_waits_for_existing_body_transition(timer):
    e, v = setup(); setattr(v, timer, .5)
    assert e.collect_gift(gift(e))
    assert v.state == 'idle' and v.gift_response_pending
    setattr(v, timer, 0); e.tick(.05)
    assert v.state == 'purring' and v.gift_response_remaining == 3


def test_committed_jump_and_meal_finish_before_response():
    e, v = setup(); e.feed(); e.set_bowl(700, e.floor_y+97)
    e._walk(v, 700, v.y-60, arrival='eating')
    e.tick(.1); before = (v.x, v.y, v.target_x, v.target_y, v.jump_elapsed)
    e.collect_gift(gift(e))
    assert before == (v.x, v.y, v.target_x, v.target_y, v.jump_elapsed)
    until(e, lambda: v.state == 'eating')
    assert v.gift_response_pending and not v.gift_response_remaining
    until(e, lambda: v.state == 'grooming')
    assert v.gift_response_pending and not v.gift_response_remaining
    until(e, lambda: v.gift_response_remaining > 0)
    assert v.state == 'purring' and not v.gift_response_pending


def test_sleeping_is_not_woken_and_resting_finishes_before_response():
    e, v = setup('sleeping'); e.collect_gift(gift(e)); e.tick(5)
    assert v.state == 'sleeping' and v.gift_response_pending
    e._state(v, 'resting'); v.next_action = 8
    e.tick(5); assert v.state == 'resting' and v.gift_response_pending
    until(e, lambda: v.gift_response_remaining > 0)
    assert v.state == 'purring'


@pytest.mark.parametrize('urgent', ['close', 'carry', 'startle', 'fight'])
@pytest.mark.parametrize('active', [False, True])
def test_urgent_action_cancels_pending_or_active_gratitude(urgent, active):
    e, v = setup('idle' if active else 'walking')
    e.collect_gift(gift(e))
    assert bool(v.gift_response_remaining) == active
    if urgent == 'close': e.open_gate(False)
    elif urgent == 'carry': assert e.carry(v.cat_id)
    elif urgent == 'startle': assert e.startle(v.cat_id)
    else:
        if active:
            # A high-priority fight state can also interrupt the shown response.
            e._state(v, 'fighting')
        else:
            e.invite('mist')
            e.visitors['mist'] = Visitor('mist', v.x+30, v.y, state='idle')
            assert e.trigger_fight()
    assert not v.gift_response_pending and v.gift_response_remaining == 0


@pytest.mark.parametrize('state', ['carried', 'fighting', 'fleeing', 'leaving'])
def test_collecting_while_owner_is_unavailable_never_queues_response(state):
    e, v = setup(state); e.collect_gift(gift(e))
    assert v.state == state and not v.gift_response_pending and not v.gift_response_remaining
    assert e.cats[v.cat_id].trust == 3


def test_absent_owner_and_airborne_departure_are_not_recalled():
    e, v = setup(); e.visitors.clear()
    e.collect_gift(gift(e)); assert not e.visitors
    e.visitors[v.cat_id] = v; v.y -= 200; e.open_gate(False)
    assert v.state == 'jumping' and v.route_motion == 'leaving'
    route = (v.target_x, v.target_y, v.jump_elapsed)
    e.collect_gift(gift(e))
    assert route == (v.target_x, v.target_y, v.jump_elapsed)
    assert not v.gift_response_pending and not v.gift_response_remaining


@pytest.mark.parametrize('order', ['name_first', 'gift_first', 'pending_name'])
def test_true_name_response_subsumes_gratitude_without_extending_or_shortening(order):
    e, v = setup('eating' if order == 'pending_name' else 'idle')
    if order == 'gift_first':
        e.collect_gift(gift(e)); e.tick(.5)
    e.name_cat(v.cat_id, e.cats[v.cat_id].true_name)
    before = v.true_name_remaining
    if order != 'gift_first': e.collect_gift(gift(e))
    assert v.true_name_remaining == before
    assert not v.gift_response_pending and v.gift_response_remaining == 0
    until(e, lambda: v.true_name_remaining > 0)
    assert v.true_name_remaining == (TRUE_NAME_SECONDS if order == 'pending_name' else before)
    e.tick(3); assert v.state == 'purring' and v.true_name_remaining > 0


def test_window_support_change_interrupts_active_feedback_without_restarting_it():
    e, v = setup(); e.set_window_edges([('editor', 250, 1000, 550)])
    e._move_to_window(v, 'editor'); until(e, lambda: v.state == 'idle' and v.settle_remaining == 0)
    e.collect_gift(gift(e)); assert v.gift_response_remaining == 3
    e.set_window_edges([])
    assert v.state == 'jumping' and not v.gift_response_pending and v.gift_response_remaining == 0
    until(e, lambda: v.state == 'idle')
    e.tick(1); assert v.gift_response_remaining == 0


def test_fur_discard_escape_and_failed_collection_have_no_response():
    e, v = setup(); assert e.collect_gift(gift(e, 'fur'))
    assert e.discard_gift(gift(e)); assert not e.collect_gift('missing')
    mouse_id = gift(e, 'toy_mouse'); mouse = e.gifts[-1]
    mouse.wait_remaining = 0
    until(e, lambda: all(g.id != mouse_id for g in e.gifts))
    assert v.state == 'idle' and not v.gift_response_pending and not v.gift_response_remaining
    assert e.cats[v.cat_id].trust == 0 and sum(e.collection_counts.values()) == 0


def test_pause_freezes_feedback_but_transient_fields_never_persist():
    e, v = setup(); e.collect_gift(gift(e)); e.tick(.5)
    before = asdict(v); e.tick(0); assert asdict(v) == before
    data = json.loads(json.dumps(e.export()))
    assert 'gift_response' not in json.dumps(data)
    restored = Engine(data)
    assert restored.collected_count('feather') == 1 and restored.cats[v.cat_id].trust == 3
    assert not restored.visitors


def test_actual_teaser_and_belly_roll_complete_before_pending_gratitude():
    e, v = setup('sleeping'); e.cats[v.cat_id].trust = 70
    e.cursor_moved(v.x+200, v.y+50, 0)
    assert e.start_teaser(v.cat_id)
    assert v.posture_kind == 'belly_roll' and v.posture_remaining > 0
    e.collect_gift(gift(e))
    assert e.active_teaser_id == v.cat_id and v.gift_response_pending
    assert v.posture_kind == 'belly_roll' and v.gift_response_remaining == 0
    until(e, lambda: e.active_teaser_id is None, 20)
    assert v.gift_response_pending and v.gift_response_remaining == 0
    until(e, lambda: v.state == 'resting')
    assert v.gift_response_pending and v.gift_response_remaining == 0
    until(e, lambda: v.gift_response_remaining > 0)
    assert v.state == 'purring' and v.gift_response_remaining == 3
