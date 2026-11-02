"""Real pose transitions own stationary time without borrowing locomotion time."""
from dataclasses import asdict
import math
import pytest
from backyard_visitor.model import Engine, Visitor, LANDING_SECONDS, RISE_SECONDS
from backyard_visitor.interaction_model import (CROUCH_RISE_SECONDS, BELLY_ROLL_SECONDS,
                                               SWAT_SECONDS, TEASER_SECONDS)


def setup(state='idle'):
    e=Engine(seed=13,visit_limit=1);e.open_gate();e._visit_countdown=1000
    v=Visitor('amber',300,e.floor_y,state=state,next_action=1000,stay_remaining=1000,facing=1)
    e.visitors[v.cat_id]=v;e.cats[v.cat_id].discovered=True;e.cats[v.cat_id].trust=70
    e.cursor_moved(1000,e.floor_y+40,0)
    return e,v


def until(e,predicate,seconds=10,step=.01):
    for _ in range(math.ceil(seconds/step)):
        if predicate():return
        e.tick(step)
    assert predicate()


def freeze_snapshot(v):
    return v.x,v.y,v.gait_phase,v.run_phase


def test_watch_rise_has_exact_contract_and_only_leftover_time_moves():
    e,v=setup();e.start_teaser(v.cat_id);e.tick(.4)
    assert e.teaser_phase=='rise'
    assert (v.posture_kind,v.posture_source,v.posture_target)==('crouch_rise','play_crouch','standing')
    assert v.posture_remaining==pytest.approx(.2) and v.posture_duration==.2 and e.moving
    assert v.rise_remaining==0
    before=freeze_snapshot(v);e.tick(.19)
    assert freeze_snapshot(v)==before and e.teaser_phase=='rise'
    assert v.posture_remaining==pytest.approx(.01)
    e.tick(.05)
    assert v.x>before[0] and v.y==before[1] and e.teaser_phase=='chase'
    assert v.posture_kind==v.posture_source==v.posture_target==''
    assert v.posture_remaining==v.posture_duration==0
    # Only .04s accelerates from rest at 300px/s², not the full .05s.
    assert v.x-before[0]==pytest.approx(.5*300*.04**2)


def test_coarse_and_fine_ticks_agree_through_watch_rise_chase_boundary():
    a,va=setup();b,vb=setup()
    a.start_teaser('amber');b.start_teaser('amber')
    for dt in (.25,.25,.14):a.tick(dt)
    for _ in range(64):b.tick(.01)
    assert (va.x,va.y,va.run_phase,va.rise_remaining)==pytest.approx((vb.x,vb.y,vb.run_phase,vb.rise_remaining))
    assert a.teaser_phase==b.teaser_phase=='chase'


def test_initial_seated_rise_is_not_consumed_twice_or_restarted():
    e,v=setup();e.start_teaser('amber')
    assert v.rise_remaining==RISE_SECONDS
    e.tick(.2);assert v.rise_remaining==pytest.approx(RISE_SECONDS-.2)
    e.tick(.2);assert v.rise_remaining==0 and v.posture_remaining==pytest.approx(CROUCH_RISE_SECONDS)
    e.tick(.2);assert v.rise_remaining==0 and v.x==300 and e.teaser_phase=='chase'


def test_each_pounce_cycle_repeats_crouch_rise_after_landing_and_watch():
    e,v=setup();e.cursor_moved(430,v.y+30,0);e.start_teaser('amber')
    until(e,lambda:e.teaser_phase=='rise');e.tick(.21)
    until(e,lambda:v.state=='jumping')
    assert v.posture_remaining==0
    until(e,lambda:v.state=='teasing')
    assert v.landing_remaining==LANDING_SECONDS
    before=freeze_snapshot(v);e.tick(.2)
    assert freeze_snapshot(v)==before and v.posture_remaining==0
    until(e,lambda:e.teaser_phase=='rise')
    before=freeze_snapshot(v);e.tick(.1)
    assert freeze_snapshot(v)==before and v.posture_kind=='crouch_rise'


@pytest.mark.parametrize('state',['sleeping','swatting'])
def test_belly_roll_endpoint_contract_and_position_hold(state):
    e,v=setup('sleeping')
    if state=='sleeping':
        e.start_teaser('amber');target='play_crouch'
    else:
        e.rng.random=lambda:0;e.pet('amber');e.tick(SWAT_SECONDS);target='resting'
    assert (v.posture_kind,v.posture_source,v.posture_target)==('belly_roll',state,target)
    assert v.posture_duration==BELLY_ROLL_SECONDS and v.posture_remaining==pytest.approx(.55)
    before=freeze_snapshot(v);e.tick(.54)
    assert freeze_snapshot(v)==before and v.posture_source==state and e.moving
    e.tick(.01)
    assert freeze_snapshot(v)==before and v.posture_remaining==0
    assert v.posture_source==v.posture_target==''
    if state=='swatting':
        assert v.state=='resting' and v.state_age==pytest.approx(0) and not e.moving


def test_sleeping_window_cat_rolls_on_support_before_continuous_descent():
    e,v=setup('sleeping');e.set_window_edges([('editor',100,900,400)])
    v.y=288;v.window_edge_id='editor'
    old=freeze_snapshot(v);e.start_teaser('amber')
    assert v.state=='teasing' and v.posture_source=='sleeping' and v.window_edge_id=='editor'
    e.tick(.5);assert freeze_snapshot(v)==old
    e.tick(.05)
    assert v.state=='jumping' and v.posture_kind=='' and v.arrival_state=='teasing'
    assert freeze_snapshot(v)==old
    until(e,lambda:v.state=='teasing')
    assert v.y==e.floor_y and v.landing_remaining==LANDING_SECONDS


def test_swat_boundary_consumes_only_leftover_time_in_belly_roll():
    e,v=setup('sleeping');e.rng.random=lambda:0;e.pet('amber')
    e.tick(.5);assert v.state=='swatting' and not v.posture_kind
    e.tick(.25)
    assert v.state=='resting' and v.posture_remaining==pytest.approx(.45)


@pytest.mark.parametrize('source',['rise','sleeping','swatting'])
@pytest.mark.parametrize('action',['carry','close','startle','fight'])
def test_urgent_action_clears_transition_without_position_or_phase_jump(source,action):
    e,v=setup('sleeping' if source!='rise' else 'idle');e.cats['amber'].trust=5
    if source=='swatting':e.rng.random=lambda:0;e.pet('amber');e.tick(SWAT_SECONDS)
    else:
        e.start_teaser('amber')
        if source=='rise':e.tick(.4)
    assert v.posture_remaining>0
    before=freeze_snapshot(v)
    if action=='carry':assert e.carry('amber')
    elif action=='close':e.open_gate(False)
    elif action=='startle':assert e.startle('amber')
    else:
        e.visitors['mist']=Visitor('mist',350,v.y,state='idle');assert e.trigger_fight()
    assert freeze_snapshot(v)==before and v.posture_kind=='' and v.posture_remaining==0
    assert e.active_teaser_id is None


@pytest.mark.parametrize('source',['rise','sleeping'])
def test_deadline_includes_transition_time_and_stops_pending_pose(source):
    e,v=setup('sleeping' if source=='sleeping' else 'idle');e.rng.random=lambda:1
    e.start_teaser('amber')
    if source=='rise':e.tick(.4)
    e._teaser.remaining=.03
    before=freeze_snapshot(v);e.tick(.1)
    assert e.active_teaser_id is None and v.state=='resting'
    assert v.posture_remaining==0 and v.posture_kind=='' and freeze_snapshot(v)==before


def test_sleeping_roll_does_not_extend_fifteen_second_budget():
    e,v=setup('sleeping');e._cursor=None;e.rng.random=lambda:1;e.start_teaser('amber')
    for _ in range(60):e.tick(.25)
    assert e.active_teaser_id is None and v.state=='resting'
    assert len([x for x in e.drain_events() if x['kind']=='play_finished'])==1


def test_screen_change_clears_transition_but_identical_floor_geometry_does_not():
    e,v=setup();e.start_teaser('amber');e.tick(.45)
    before=asdict(v);e.set_bounds(*e.bounds)
    assert asdict(v)==before
    e.set_bounds(-1280,-100,1280,720)
    assert v.posture_remaining==0 and e.teaser_phase=='watch'
    assert e._cursor is None and v.run_phase==before['run_phase']
    e.tick(.1);assert v.posture_remaining==0 and v.y==e.floor_y


def test_landing_owns_time_before_a_pending_posture_and_jump_clears_it():
    e,v=setup();e.start_teaser('amber');e.tick(.4)
    v.landing_remaining=.24
    remaining=v.posture_remaining;before=freeze_snapshot(v)
    e.tick(.2)
    assert freeze_snapshot(v)==before and v.posture_remaining==remaining
    e.tick(.1)
    assert v.posture_remaining==pytest.approx(remaining-.06)
    e._walk(v,v.x+40,v.y-60)
    assert v.state=='jumping' and not v.posture_kind
    assert not e._begin_posture(v,'belly_roll',.55,'sleeping','play_crouch')


def test_posture_and_teaser_pause_and_export_are_transient():
    e,v=setup('sleeping');e.start_teaser('amber');e.tick(.2)
    before=asdict(v);session=asdict(e._teaser);e.tick(0)
    assert asdict(v)==before and asdict(e._teaser)==session
    saved=e.export()
    assert 'posture' not in repr(saved) and 'teaser' not in repr(saved)
    restored=Engine(saved);assert not restored.visitors and restored.active_teaser_id is None


def test_new_play_request_does_not_interrupt_an_unfinished_belly_roll():
    e,v=setup('sleeping');e.rng.random=lambda:0;e.pet('amber');e.tick(SWAT_SECONDS)
    before=asdict(v)
    assert v.state=='resting' and v.posture_kind=='belly_roll'
    assert not e.start_teaser('amber') and asdict(v)==before
    e.tick(BELLY_ROLL_SECONDS)
    assert e.start_teaser('amber')


@pytest.mark.parametrize('action',['pet','call'])
def test_nonurgent_touch_and_call_preserve_active_transition(action):
    e,v=setup('sleeping');e.cats['amber'].nickname='邻居';e.rng.random=lambda:0
    e.pet('amber');e.tick(SWAT_SECONDS)
    # Explicit transition protection must not depend on the scratch cooldown.
    v.pet_cooldown=0
    before=asdict(v);trust=e.cats['amber'].trust
    if action=='pet':assert not e.pet('amber')
    else:assert e.call_cat('amber')=='recognized'
    assert asdict(v)==before and e.cats['amber'].trust==trust
