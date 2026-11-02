"""Supported takeoff freezes feet; flight and replans have their own clock."""
import math
from dataclasses import asdict

import pytest

from backyard_visitor.model import Engine, Visitor, TAKEOFF_SECONDS, LANDING_SECONDS


def ground(bounds=(0, 0, 1280, 720)):
    e=Engine(seed=6,visit_limit=1);e.set_bounds(*bounds);e.open_gate()
    v=Visitor('amber',bounds[0]+300,e.floor_y,state='idle',facing=1,
              target_x=bounds[0]+300,target_y=e.floor_y,next_action=1000,
              stay_remaining=1000,gait_phase=.37)
    e.visitors[v.cat_id]=v;e._visit_countdown=1000
    return e,v


def windows(bounds=(0,0,1280,720)):
    e,v=ground(bounds);left,top,_,_=bounds
    edges=[('source',left+100,left+700,top+500),('target',left+350,left+1200,top+400)]
    e.set_window_edges(edges)
    v.y=top+500-112;v.window_edge_id='source'
    assert e._move_to_window(v,'target',left+800)
    return e,v,edges


@pytest.mark.parametrize('bounds',[(0,0,1280,720),(-1920,-100,1920,1080)])
def test_ground_takeoff_anchors_feet_and_counts_only_remaining_flight_time(bounds):
    e,v=ground(bounds);origin=v.x,v.y
    e._walk(v,v.x+150,v.y-100)
    assert v.takeoff_remaining==TAKEOFF_SECONDS and v.takeoff_edge_id is None and e.moving
    e.tick(.19)
    assert (v.x,v.y)==origin and v.jump_elapsed==0 and v.gait_phase==.37
    e.tick(.04)
    assert v.takeoff_remaining==0 and v.jump_elapsed==pytest.approx(.03)
    p=.03/v.jump_duration
    assert v.x==pytest.approx(origin[0]+64*p)
    assert v.y==pytest.approx(origin[1]-100*p-v.jump_arc*4*p*(1-p))
    assert v.gait_phase==.37


def test_coarse_tick_matches_fine_ticks_across_takeoff_boundary():
    a,va=ground();b,vb=ground()
    for e,v in ((a,va),(b,vb)):e._walk(v,v.x+150,v.y-100)
    a.tick(.25)
    for _ in range(5):b.tick(.05)
    assert (va.x,va.y,va.jump_elapsed)==pytest.approx((vb.x,vb.y,vb.jump_elapsed))
    assert va.jump_elapsed==pytest.approx(.05)


def test_takeoff_and_flight_are_runtime_and_pause_without_ticks():
    e,v=ground();e._walk(v,v.x+100,v.y-100);e.tick(.08)
    before=asdict(v)
    e.tick(0)
    e.set_window_edges([])
    assert asdict(v)==before
    assert all(key not in repr(e.export()) for key in ('takeoff_remaining','jump_elapsed','takeoff_edge_id'))


@pytest.mark.parametrize('bounds',[(0,0,1280,720),(-1920,-100,1920,1080)])
def test_window_takeoff_uses_source_support_not_destination(bounds):
    e,v,_=windows(bounds);origin=v.x,v.y
    assert v.takeoff_edge_id=='source' and v.window_target_id=='target'
    e.tick(TAKEOFF_SECONDS)
    assert (v.x,v.y)==origin and v.jump_elapsed==0
    e.tick(.04)
    assert v.takeoff_edge_id is None and v.x>origin[0]


def test_target_move_during_takeoff_preserves_remaining_crouch():
    e,v,edges=windows();e.tick(.08);origin=v.x,v.y
    e.set_window_edges([edges[0],('target',350,1200,420)])
    assert (v.x,v.y)==origin and v.jump_start_x==v.x and v.jump_start_y==v.y
    assert v.takeoff_remaining==pytest.approx(.12) and v.takeoff_edge_id=='source'
    e.tick(.12)
    assert (v.x,v.y)==origin and v.jump_elapsed==0
    e.tick(.04)
    assert v.jump_elapsed==pytest.approx(.04) and math.dist(origin,(v.x,v.y))>0


@pytest.mark.parametrize('change',['move','remove'])
def test_source_change_during_takeoff_immediately_releases_lost_support(change):
    e,v,edges=windows();e.tick(.08);origin=v.x,v.y
    updated=[edges[1]] if change=='remove' else [('source',100,700,550),edges[1]]
    e.set_window_edges(updated)
    assert (v.x,v.y)==origin and v.takeoff_remaining==0 and v.takeoff_edge_id is None
    e.tick(.04)
    assert v.jump_elapsed==pytest.approx(.04) and math.dist(origin,(v.x,v.y))>0


def test_target_disappearing_before_takeoff_returns_to_existing_support():
    e,v,edges=windows();e.tick(.08);origin=v.x,v.y
    e.set_window_edges([edges[0]])
    assert (v.x,v.y)==origin and v.takeoff_remaining==0
    e.tick(.04)
    assert v.state=='idle' and v.window_edge_id=='source' and (v.x,v.y)==origin


def test_airborne_target_move_restarts_arc_from_current_position_without_crouch():
    e,v,edges=windows();e.tick(.4);origin=v.x,v.y
    assert v.jump_elapsed>0
    e.set_window_edges([edges[0],('target',350,1200,420)])
    assert (v.x,v.y)==origin and (v.jump_start_x,v.jump_start_y)==origin
    assert v.takeoff_remaining==0 and v.jump_elapsed==0
    e.tick(.04)
    assert v.jump_elapsed==pytest.approx(.04) and math.dist(origin,(v.x,v.y))<30


def test_high_floor_climb_target_move_keeps_first_launch_eligibility():
    e,v=ground((0,30,1920,985));e.set_window_edges([('high',0,1900,172)])
    assert e._move_to_window(v,'high',v.x+100)
    e.tick(.08);origin=v.x,v.y
    e.set_window_edges([('high',0,1900,180)])
    assert (v.x,v.y)==origin and v.window_target_id=='high'
    assert v.takeoff_remaining==pytest.approx(.12) and v.jump_duration>4


def test_same_geometry_poll_does_not_reset_takeoff_or_flight():
    e,v,edges=windows();e.tick(.08)
    before=asdict(v);e.set_window_edges(edges);assert asdict(v)==before
    e.tick(.3);before=asdict(v);e.set_window_edges(edges);assert asdict(v)==before


def test_carried_release_falls_without_takeoff():
    e,v=ground();assert e.carry(v.cat_id)
    assert e.release(v.cat_id,500,e.floor_y-150)
    origin=v.x,v.y
    assert v.state=='jumping' and v.takeoff_remaining==0
    e.tick(.04)
    assert v.jump_elapsed==pytest.approx(.04) and math.dist(origin,(v.x,v.y))>0


@pytest.mark.parametrize('during_takeoff',[True,False])
def test_screen_shrink_replans_without_replaying_takeoff(during_takeoff):
    e,v=ground((0,0,1920,1080));e._walk(v,1200,v.y-300)
    e.tick(.08 if during_takeoff else .4)
    phase=v.gait_phase
    e.set_bounds(-800,-100,800,600);origin=v.x,v.y
    assert v.takeoff_remaining==0 and v.gait_phase==phase
    if v.state=='jumping':
        assert (v.jump_start_x,v.jump_start_y)==origin and v.jump_elapsed==0
        e.tick(.04)
        assert v.takeoff_remaining==0 and math.dist(origin,(v.x,v.y))<30


@pytest.mark.parametrize('action',['carry','startle','close'])
def test_urgent_actions_cancel_charge_immediately(action):
    e,v=ground();e._walk(v,500,v.y-100);e.tick(.08)
    if action=='carry':assert e.carry(v.cat_id)
    elif action=='startle':assert e.startle(v.cat_id)
    else:e.open_gate(False)
    assert v.state!='jumping' and v.takeoff_remaining==0 and v.jump_elapsed==0
    assert v.takeoff_edge_id is None


def test_landing_retains_existing_contact_duration_and_gait():
    e,v=ground();e._walk(v,350,v.y-100)
    for _ in range(200):
        e.tick(.01)
        if v.state=='idle':break
    assert v.state=='idle' and (v.x,v.y)==(350,e.floor_y-100)
    assert v.landing_remaining==LANDING_SECONDS and v.gait_phase==.37
    assert v.takeoff_remaining==0 and v.jump_elapsed==0


@pytest.mark.parametrize('change',['move','remove'])
def test_window_to_floor_route_also_observes_source_loss(change):
    e,v=ground();e.set_window_edges([('source',100,700,500)])
    v.y=388;v.window_edge_id='source'
    e._walk(v,500,e.floor_y)
    assert v.window_target_id is None and v.takeoff_edge_id=='source'
    e.tick(.08);origin=v.x,v.y
    e.set_window_edges([] if change=='remove' else [('source',100,700,550)])
    assert (v.x,v.y)==origin and v.takeoff_remaining==0 and v.takeoff_edge_id is None
    e.tick(.04)
    assert v.jump_elapsed==pytest.approx(.04) and math.dist(origin,(v.x,v.y))>0


@pytest.mark.parametrize('direction',[-1,1])
def test_destination_facing_is_resolved_before_crouch(direction):
    e,v=ground();v.facing=-direction
    e._walk(v,v.x+100*direction,v.y-100)
    assert v.takeoff_remaining==TAKEOFF_SECONDS and v.facing==direction
    e.tick(.1)
    assert v.facing==direction and v.jump_elapsed==0
