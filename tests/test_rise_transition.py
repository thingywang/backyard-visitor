"""Sitting reversal uses pose progress and consumes no walking distance."""
import math
import pytest
from backyard_visitor.model import Engine,Visitor,RISE_SECONDS,SETTLE_SECONDS,GAIT_STRIDE,TAKEOFF_SECONDS


def setup():
    e=Engine(seed=6)
    v=Visitor('amber',350,e.floor_y,state='idle',facing=1,target_x=350,target_y=e.floor_y,
              next_action=1000,stay_remaining=1000,gait_phase=.37)
    e.visitors[v.cat_id]=v
    return e,v


def test_fully_seated_cat_rises_in_place_then_only_uses_leftover_time_to_walk():
    e,v=setup();e._walk(v,650,v.y)
    assert v.rise_remaining==RISE_SECONDS and e.moving
    e.tick(.2)
    assert v.x==350 and v.gait_phase==.37 and v.ground_speed==0
    assert v.rise_remaining==pytest.approx(.16)
    e.tick(.2)
    assert v.rise_remaining==0
    assert v.x-350==pytest.approx(.5*300*.04**2)
    assert v.gait_phase==pytest.approx(.37+(v.x-350)/GAIT_STRIDE)


def test_playing_respects_the_same_seated_start_without_in_place_steps():
    e,v=setup();e.cats[v.cat_id].trust=80
    e.cursor_moved(v.x+72+120,v.y+56,0);e._state(v,'playing')
    e.tick(.2)
    assert v.x==350 and v.gait_phase==.37 and v.rise_remaining>0
    e.tick(.2)
    assert 350<v.x<=350+90*.04 and v.rise_remaining==0


@pytest.mark.parametrize('seated',[0,.25,.5,.9,1])
def test_interrupted_sitting_reverses_only_the_current_pose_progress(seated):
    e,v=setup();v.settle_remaining=SETTLE_SECONDS*(1-seated)
    e._walk(v,650,v.y)
    assert v.settle_remaining==0
    assert v.rise_remaining==pytest.approx(RISE_SECONDS*seated)
    if seated:
        e.tick(RISE_SECONDS*seated)
        assert v.x==350 and v.gait_phase==.37
    e.tick(.04)
    assert v.x>350 and v.rise_remaining==0


def test_route_retarget_during_rise_preserves_the_remaining_pose():
    e,v=setup();e._walk(v,650,v.y);e.tick(.1)
    remaining=v.rise_remaining
    e._walk(v,550,v.y)
    assert v.rise_remaining==remaining
    e.tick(remaining)
    assert v.x==350 and v.gait_phase==.37
    e.tick(.04)
    assert v.x>350 and v.gait_phase>.37


@pytest.mark.parametrize('action',['jump','flee','leave','carry'])
def test_urgent_activity_and_jumps_do_not_wait_for_a_seated_rise(action):
    e,v=setup();e._walk(v,650,v.y);e.tick(.1)
    assert v.rise_remaining>0
    if action=='jump':e._walk(v,500,v.y-100)
    elif action=='carry':assert e.carry(v.cat_id)
    else:e._leave(v,fleeing=action=='flee')
    assert v.rise_remaining==0
    before=(v.x,v.y);e.tick(.04)
    if action=='jump':
        assert (v.x,v.y)==before and v.takeoff_remaining==pytest.approx(TAKEOFF_SECONDS-.04)
        e.tick(v.takeoff_remaining+.04)
        assert math.dist(before,(v.x,v.y))>0
    elif action!='carry':assert math.dist(before,(v.x,v.y))>0
    if action in ('jump','carry'):assert v.gait_phase==.37


def test_peeking_entry_has_no_seated_delay():
    e=Engine(seed=3);e.open_gate();e.invite('amber');v=e.visitors['amber']
    e.tick(3)
    assert v.state=='entering' and v.rise_remaining==0
    before=v.x;e.tick(.04)
    assert v.x!=before


def test_no_distance_route_does_not_make_a_seated_cat_pop_back_to_standing():
    e,v=setup();e._walk(v,v.x,v.y);e.tick(.04)
    assert v.state=='idle' and v.rise_remaining==0 and v.settle_remaining==0
    assert v.gait_phase==.37 and not e.moving


def test_sitting_clock_stops_after_completion_and_is_not_saved():
    e,v=setup();v.settle_remaining=SETTLE_SECONDS
    assert e.moving
    e.tick(SETTLE_SECONDS)
    assert not e.moving and v.settle_remaining==0
    e._walk(v,650,v.y)
    assert v.rise_remaining>0
    assert not Engine(e.export()).visitors
