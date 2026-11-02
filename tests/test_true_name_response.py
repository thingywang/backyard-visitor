"""A rare name match has distinct feedback without interrupting unsafe activity."""
import json
import pytest
from backyard_visitor.model import Engine, Visitor, TRUE_NAME_SECONDS


def setup(state='idle'):
    e=Engine(seed=3)
    e.open_gate();assert e.invite('amber')=='amber';e.open_gate(False)
    v=Visitor('amber',350,e.floor_y,state=state,facing=1,target_x=370,target_y=e.floor_y,
              next_action=1000,stay_remaining=1000)
    e.visitors[v.cat_id]=v
    return e,v


def match(e,v):
    assert e.name_cat(v.cat_id,e.cats[v.cat_id].true_name)=='true_name'
    assert e.cats[v.cat_id].trust==100


def until(e,predicate,seconds=30):
    for _ in range(int(seconds/.04)):
        if predicate():return
        e.tick(.04)
    assert predicate()


def test_name_match_is_distinct_from_an_ordinary_recognized_call():
    e,v=setup(); ordinary,other=setup()
    ordinary.cats[other.cat_id].nickname='用户昵称';ordinary.cats[other.cat_id].trust=35
    ordinary.call_cat(other.cat_id)
    match(e,v)
    assert v.state==other.state=='purring'
    assert not other.true_name_pending and other.true_name_remaining==0
    assert not v.true_name_pending and v.true_name_remaining==TRUE_NAME_SECONDS and e.moving
    e.tick(4.1);ordinary.tick(4.1)
    assert v.state=='purring' and v.true_name_remaining>0 and other.state=='idle'
    e.tick(3)
    assert v.state=='idle' and v.true_name_remaining==0 and not e.moving


@pytest.mark.parametrize('state',['peeking','entering'])
def test_entry_remembers_name_until_the_cat_finishes_its_route(state):
    e,v=setup(state);before=(v.x,v.y,v.target_x,v.target_y)
    match(e,v)
    assert v.state==state and v.true_name_pending and v.true_name_remaining==0
    assert before==(v.x,v.y,v.target_x,v.target_y)
    until(e,lambda:v.true_name_remaining>0)
    assert (v.x,v.y)==(v.target_x,v.target_y) and v.state=='purring'
    assert not v.true_name_pending and v.true_name_remaining==TRUE_NAME_SECONDS


def test_jump_and_committed_meal_finish_before_the_pending_response():
    e,v=setup();e.food=1
    e._walk(v,500,v.y-120,arrival='eating')
    e.tick(.15);before=(v.x,v.y,v.target_x,v.target_y)
    match(e,v)
    assert v.state=='jumping' and v.arrival_state=='eating'
    assert before==(v.x,v.y,v.target_x,v.target_y) and v.true_name_pending
    until(e,lambda:v.state=='eating')
    assert e.food==0 and v.true_name_remaining==0 and v.true_name_pending
    until(e,lambda:v.state=='grooming')
    assert v.true_name_remaining==0 and v.true_name_pending
    until(e,lambda:v.true_name_remaining>0)
    assert v.state=='purring' and e.cats[v.cat_id].trust==100


def test_name_during_eating_waits_for_grooming_to_finish():
    e,v=setup('eating');v.has_eaten=True
    match(e,v);e.tick(4)
    assert v.state=='grooming' and v.true_name_pending and v.true_name_remaining==0
    e.tick(5)
    assert v.state=='purring' and v.true_name_remaining==TRUE_NAME_SECONDS


def test_window_peek_rises_continuously_before_response():
    e,v=setup();e.set_window_edges([('editor',250,800,550)])
    e._move_to_window(v,'editor');until(e,lambda:v.state=='idle')
    assert e._begin_window_peek(v)
    e.tick(.3);before=(v.x,v.y,v.peek_progress)
    match(e,v)
    assert v.state=='window_peeking' and v.peek_phase=='rising'
    assert (v.x,v.y,v.peek_progress)==before and v.true_name_pending
    until(e,lambda:v.true_name_remaining>0)
    assert v.window_edge_id=='editor' and v.y==550-112 and v.state=='purring'


@pytest.mark.parametrize('action',['close','carry','flee'])
def test_urgent_actions_cancel_a_pending_response(action):
    e,v=setup();e.gate_open=True;e._visit_countdown=1000
    e._walk(v,500,v.y-120);match(e,v)
    assert v.true_name_pending
    if action=='close':e.open_gate(False)
    elif action=='carry':assert e.carry(v.cat_id)
    else:e._leave(v,fleeing=True)
    assert not v.true_name_pending and v.true_name_remaining==0
    expected=v.state;e.tick(.1)
    assert v.state==expected and not v.true_name_pending


@pytest.mark.parametrize('state',['carried','fighting','leaving','fleeing'])
def test_matching_during_an_urgent_state_does_not_override_it(state):
    e,v=setup(state)
    match(e,v)
    assert v.state==state and not v.true_name_pending and v.true_name_remaining==0


def test_carry_cancels_active_feedback_and_repeated_match_does_not_extend_it():
    e,v=setup();match(e,v);e.tick(1.2)
    remaining=v.true_name_remaining
    match(e,v)
    assert v.true_name_remaining==remaining
    assert sum(x['kind']=='true_name_response' for x in e.drain_events())==1
    assert e.carry(v.cat_id)
    assert v.true_name_remaining==0 and not v.true_name_pending


def test_disappearing_window_defers_active_response_until_safe_again():
    e,v=setup();e.set_window_edges([('editor',250,800,550)])
    e._move_to_window(v,'editor');until(e,lambda:v.state=='idle')
    match(e,v);e.tick(.2);before=(v.x,v.y)
    e.set_window_edges([])
    assert (v.x,v.y)==before and v.state=='jumping'
    assert v.true_name_pending and v.true_name_remaining==0
    until(e,lambda:v.true_name_remaining>0)
    assert v.state=='purring' and v.y==e.floor_y


def test_response_does_not_serialize_runtime_or_disclose_the_secret_in_events():
    e,v=setup('entering');match(e,v)
    assert v.true_name_pending
    events=json.dumps(e.drain_events(),ensure_ascii=False)
    assert e.cats[v.cat_id].true_name not in events
    state=e.export();serialized=json.dumps(state)
    for field in ('true_name_pending','true_name_remaining','true_name'):
        assert '"'+field+'"' not in serialized
    # The user's submitted nickname remains their ordinary saved nickname.
    restored=Engine(state)
    assert not restored.visitors and not restored.gate_open
    assert restored.cats[v.cat_id].trust==100
