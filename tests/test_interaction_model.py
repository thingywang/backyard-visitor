"""Deliberate play is bounded, interruptible, and never bypasses route physics."""
from dataclasses import asdict
import math
import pytest
from backyard_visitor.model import Engine,Visitor,LANDING_SECONDS,RUN_STRIDE,RUN_SPEED
from backyard_visitor.interaction_model import TEASER_SECONDS,SWAT_SECONDS


def setup(state='idle', trust=70, bounds=(0,0,1280,720)):
    e=Engine(seed=13,visit_limit=1);e.set_bounds(*bounds);e.open_gate()
    v=Visitor('amber',bounds[0]+300,e.floor_y,state=state,facing=1,
              target_x=bounds[0]+300,target_y=e.floor_y,next_action=1000,stay_remaining=1000)
    e.visitors[v.cat_id]=v;e.cats[v.cat_id].discovered=True;e.cats[v.cat_id].trust=trust
    e._visit_countdown=1000
    return e,v


def until(e,predicate,seconds=30,step=.02):
    for _ in range(math.ceil(seconds/step)):
        if predicate():return
        e.tick(step)
    assert predicate()


@pytest.mark.parametrize('state',['idle','sleeping','walking','running','playing','resting','purring'])
def test_start_supported_state_preserves_position_and_cancels_parkour(state):
    e,v=setup(state);v.parkour_remaining=8;old=v.x,v.y
    assert e.start_teaser(v.cat_id)
    assert (v.x,v.y)==old and v.state=='teasing' and v.parkour_remaining==0
    assert e.active_teaser_id==v.cat_id and e.teaser_phase=='watch' and e.moving
    assert not e.start_teaser(v.cat_id)


@pytest.mark.parametrize('state',['carried','fighting','leaving','fleeing','jumping','swatting','eating','peeking','entering'])
def test_uninterruptible_states_reject_play_without_mutation(state):
    e,v=setup(state);before=asdict(v)
    assert not e.start_teaser(v.cat_id) and asdict(v)==before and e.active_teaser_id is None


def test_no_gate_missing_invalid_or_gift_carrying_cat_cannot_start():
    e,v=setup();e.open_gate(False)
    assert not e.start_teaser(v.cat_id)
    e.open_gate();v.state='idle'
    for invalid in ['missing',None,[],1]:assert not e.start_teaser(invalid)
    v.carrying_gift='toy_mouse'
    assert not e.start_teaser(v.cat_id)


@pytest.mark.parametrize('step',[.01,.1,.25,5])
def test_exact_fifteen_second_session_expires_and_recovers_without_teleport(step):
    e,v=setup();e.rng.random=lambda:1.0
    assert e.start_teaser(v.cat_id)
    elapsed=0.0
    while elapsed+step<TEASER_SECONDS-1e-9:
        e.tick(step);elapsed+=step
        assert e.active_teaser_id=='amber'
    e.tick(TEASER_SECONDS-elapsed)
    assert e.active_teaser_id is None and e.teaser_phase is None
    assert v.state=='resting' and (v.x,v.y)==(300,e.floor_y)
    assert 8<=v.next_action<=14 and not e.moving
    e.tick(5);assert v.state=='resting'
    until(e,lambda:v.state=='idle')
    assert len([x for x in e.drain_events() if x['kind']=='play_finished'])==1


def test_watch_to_chase_uses_only_leftover_dt_and_actual_run_distance():
    e,v=setup();e.cursor_moved(1000,e.floor_y+50,0);e.start_teaser(v.cat_id)
    phase=v.run_phase;walkphase=v.gait_phase;x=v.x
    e.tick(.39)
    assert v.x==x and v.run_phase==phase and e.teaser_phase=='watch'
    e.tick(.21)
    assert v.x==x and e.teaser_phase=='chase'
    e.tick(.04)
    traveled=v.x-x
    assert 0<traveled<=RUN_SPEED*.04+1e-8 and e.teaser_phase=='chase'
    assert v.run_phase==pytest.approx((phase+traveled/RUN_STRIDE)%1)
    assert v.gait_phase==walkphase and v.y==e.floor_y


def test_pounce_is_short_hop_then_landing_buffer_before_next_chase():
    e,v=setup();e.cursor_moved(v.x+120,v.y+30,0);e.start_teaser(v.cat_id)
    until(e,lambda:v.state=='jumping')
    phase=v.run_phase
    assert e.teaser_phase=='pounce' and abs(v.target_x-v.x)<=64
    assert v.arrival_state=='teasing'
    until(e,lambda:v.state=='teasing')
    assert v.run_phase==phase and v.landing_remaining==LANDING_SECONDS
    x,y=v.x,v.y;e.tick(.20)
    assert (v.x,v.y)==(x,y) and v.run_phase==phase
    assert v.landing_remaining>0


def test_stop_airborne_finishes_current_arc_before_resting():
    e,v=setup();e.cursor_moved(v.x+120,v.y+30,0);e.start_teaser(v.cat_id)
    until(e,lambda:v.state=='jumping');e.tick(.3);before=v.x,v.y
    assert e.stop_teaser(v.cat_id)
    assert (v.x,v.y)==before and v.state=='jumping' and v.arrival_state=='resting'
    assert e.active_teaser_id is None and not e.stop_teaser(v.cat_id)
    until(e,lambda:v.state=='resting')
    assert v.y==e.floor_y and v.landing_remaining==LANDING_SECONDS


@pytest.mark.parametrize('action',['close','carry','startle','fight'])
def test_emergency_action_immediately_clears_session_and_wins(action):
    e,v=setup(trust=5);e.start_teaser(v.cat_id);before=v.x,v.y
    if action=='close':e.open_gate(False);expected='leaving'
    elif action=='carry':e.carry(v.cat_id);expected='carried'
    elif action=='startle':e.startle(v.cat_id);expected='fleeing'
    else:
        e.visitors['mist']=Visitor('mist',v.x+50,v.y,state='idle');assert e.trigger_fight();expected='fighting'
    assert e.active_teaser_id is None and v.state==expected and (v.x,v.y)==before
    assert not e.stop_teaser(v.cat_id)
    assert not any(x['kind']=='play_finished' for x in e.drain_events())


def test_fast_cursor_exempts_only_active_cat_not_another_nearby_visitor():
    e,v=setup(trust=5);e.visitors['mist']=Visitor('mist',v.x+30,v.y,state='idle')
    e.start_teaser(v.cat_id)
    assert e.cursor_moved(v.x+75,v.y+50,2500)
    assert v.state=='teasing' and e.active_teaser_id==v.cat_id
    assert e.visitors['mist'].state=='fleeing'


def test_stale_session_and_orphaned_teasing_pose_are_recovered():
    e,v=setup();e.start_teaser(v.cat_id);del e.visitors[v.cat_id]
    assert e.active_teaser_id is None and e.teaser_phase is None
    e.visitors[v.cat_id]=v;e.tick(.04)
    assert v.state=='idle' and e.active_teaser_id is None


@pytest.mark.parametrize('airborne',[False,True])
def test_screen_change_preserves_session_safety_and_clears_stale_cursor(airborne):
    e,v=setup();e.cursor_moved(v.x+120,v.y+30,0);e.start_teaser(v.cat_id)
    if airborne:until(e,lambda:v.state=='jumping')
    e.set_bounds(-1280,-100,1280,720)
    phase=v.run_phase
    for _ in range(100):
        e.tick(.02)
        assert -1280<=v.x<=-144 and -100<=v.y<=e.floor_y
    assert e.active_teaser_id==v.cat_id and e.teaser_phase=='watch'
    assert v.run_phase==phase


def test_window_play_approach_descends_continuously_before_chase():
    e,v=setup();e.set_window_edges([('editor',100,900,400)]);v.y=288;v.window_edge_id='editor'
    origin=v.x,v.y;assert e.start_teaser(v.cat_id)
    assert (v.x,v.y)==origin and v.state=='jumping' and e.teaser_phase=='approach'
    until(e,lambda:v.state=='teasing')
    assert v.y==e.floor_y and v.window_edge_id is None and v.landing_remaining==LANDING_SECONDS


def test_pause_export_and_reload_do_not_advance_or_persist_sessions():
    e,v=setup();e.start_teaser(v.cat_id);e.tick(.2)
    before=asdict(v);session=asdict(e._teaser)
    e.tick(0);assert asdict(v)==before and asdict(e._teaser)==session
    saved=e.export();assert 'teaser' not in repr(saved)
    restored=Engine(saved);assert restored.active_teaser_id is None and restored.teaser_phase is None


@pytest.mark.parametrize('trust',[0,54,55,99.9])
def test_sleeping_belly_may_swat_below_full_trust_with_cooldown(trust):
    e,v=setup('sleeping',trust);e.rng.random=lambda:0
    old=v.x,v.y;assert not e.pet(v.cat_id)
    assert v.state=='swatting' and (v.x,v.y)==old and e.moving
    assert e.cats[v.cat_id].trust==trust and v.pet_cooldown==8
    assert len([x for x in e.drain_events() if x['kind']=='scratch'])==1
    assert not e.pet(v.cat_id)
    e.tick(SWAT_SECONDS)
    assert v.state=='resting' and v.posture_kind=='belly_roll' and e.moving
    e.tick(.55)
    assert v.posture_remaining==0 and not e.moving
    assert not e.pet(v.cat_id) and not e.drain_events()


def test_full_trust_belly_touch_never_scratches_even_zero_random_draw():
    e,v=setup('sleeping',100);e.rng.random=lambda:0
    assert e.pet(v.cat_id) and v.state=='purring'
    assert not any(x['kind']=='scratch' for x in e.drain_events())


def test_no_scratch_draw_preserves_existing_affection_and_low_trust_boundary():
    for trust,expected,result in [(70,'purring',True),(10,'sleeping',False)]:
        e,v=setup('sleeping',trust);e.rng.random=lambda:1
        assert e.pet(v.cat_id)==result and v.state==expected
        assert not any(x['kind']=='scratch' for x in e.drain_events())
