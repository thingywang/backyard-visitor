"""Running uses distance phase and bounded personality-driven decisions."""
from dataclasses import asdict
import math
import pytest
from backyard_visitor.model import Engine,Visitor,RUN_STRIDE,RUN_SPEED,PARKOUR_CHANCE


def setup(cat='amber'):
    e=Engine(seed=9,visit_limit=1);e.open_gate();v=Visitor(cat,300,e.floor_y,state='idle',facing=1,
        next_action=1000,stay_remaining=1000,settle_remaining=.7)
    e.visitors[cat]=v;e._visit_countdown=1000
    return e,v


@pytest.mark.parametrize('state',['running','fleeing'])
def test_run_phase_counts_only_actual_distance_and_uses_independent_stride(state):
    e,v=setup();e._walk(v,1000,v.y,motion=state);walkphase=v.gait_phase;total=0
    for _ in range(35):
        x=v.x;phase=v.run_phase;e.tick(.04);distance=abs(v.x-x);total+=distance
        assert distance<=RUN_SPEED*.04+1e-8
        assert v.run_phase==pytest.approx((phase+distance/RUN_STRIDE)%1)
        assert v.gait_phase==walkphase
    assert total>220  # A genuine run, substantially faster than walking.


def test_running_jump_and_pause_do_not_advance_run_phase():
    e,v=setup();v.run_phase=.36;e._walk(v,650,v.y-120,motion='running')
    for _ in range(20):
        before=v.run_phase;e.tick(.02);assert v.run_phase==before
    before=asdict(v);e.tick(0);assert asdict(v)==before


def test_probability_is_per_decision_and_ordered_by_personality():
    assert PARKOUR_CHANCE['mischievous']>PARKOUR_CHANCE['graceful']>PARKOUR_CHANCE['shy']>0
    observed=[]
    for cid in ['amber','ink','mist']:
        e,v=setup(cid);e.rng.random=lambda:.06
        observed.append(e._parkour_action(v))
    assert observed==[True,True,False]


def test_parkour_duration_cooldown_and_no_restart_each_tick():
    e,v=setup();e.rng.random=lambda:0
    assert e._parkour_action(v);duration=v.parkour_remaining;cooldown=v.parkour_cooldown
    assert 7<=duration<=12 and 40<=cooldown<=65 and v.state=='running'
    for _ in range(300):e.tick(.05)
    assert v.parkour_remaining==0 and v.parkour_cooldown>0 and v.state!='running'
    assert len([x for x in e.drain_events() if x['kind']=='parkour'])==1
    assert not e._parkour_action(v)


@pytest.mark.parametrize('action',['calm','carry','close','startle','fight','playing'])
def test_interruptions_cancel_parkour_and_do_not_resume_stale_route(action):
    e,v=setup();e.rng.random=lambda:0;e._parkour_action(v)
    if action=='calm':e.calm_mode=True;e.tick(.04)
    elif action=='carry':e.carry(v.cat_id)
    elif action=='close':e.open_gate(False)
    elif action=='startle':e.startle(v.cat_id)
    else:e._state(v,'fighting' if action=='fight' else 'playing')
    assert v.parkour_remaining==0 and v.parkour_cooldown>0
    assert v.route_motion!='running'


def test_calm_never_starts_parkour_and_exports_no_runtime_run_fields():
    e,v=setup();e.calm_mode=True;e.rng.random=lambda:0
    assert not e._parkour_action(v) and v.state=='idle'
    e.calm_mode=False;e._parkour_action(v)
    assert all(name not in repr(e.export()) for name in ['run_phase','parkour_remaining','parkour_cooldown'])


def test_window_running_stays_on_support_until_short_jump():
    e,v=setup();e.set_window_edges([('source',100,700,500),('next',600,1100,400)])
    v.y=388;v.window_edge_id='source';v.parkour_remaining=10
    e._move_to_window(v,'next',850,motion='running')
    assert v.state=='running' and v.route_stage=='approach'
    while v.state=='running':
        e.tick(.04);assert 100<=v.x<=556
    assert v.state=='jumping' and abs(v.target_x-v.x)<=64
