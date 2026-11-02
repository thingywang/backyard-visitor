"""Continuous pointer pressure cannot restart an already committed escape."""
from dataclasses import asdict
import pytest
from backyard_visitor.model import Engine,Visitor,SPRITE_WIDTH


def perched(left=0, direction=-1):
    e=Engine(seed=7,visit_limit=1);e.set_bounds(left,-100,1280,720);e.open_gate();e._visit_countdown=1000
    e.set_window_edges([('ledge',left+100,left+1200,212)])
    x=left+(500 if direction<0 else 800)
    v=Visitor('amber',x,100,state='idle',target_x=x,target_y=100,window_edge_id='ledge',
              next_action=1000,stay_remaining=1000)
    e.visitors['amber']=v;e.cats['amber'].trust=10
    return e,v


@pytest.mark.parametrize('left',[0,-1920])
@pytest.mark.parametrize('direction',[-1,1])
def test_continuous_fast_pointer_only_startles_once_and_escape_finishes(left,direction):
    e,v=perched(left,direction)
    assert e.cursor_moved(v.x+72,v.y+56,1200)
    assert v.state=='jumping' and v.route_motion=='fleeing'
    initial=(v.jump_start_x,v.jump_start_y,v.jump_duration)
    previous_elapsed=0
    for _ in range(500):
        if not e.visitors:break
        e.tick(.04)
        if not e.visitors:break
        before=asdict(v)
        assert not e.cursor_moved(v.x+72,v.y+56,1200)
        assert asdict(v)==before and e.cats['amber'].trust==9
        if v.state=='jumping':
            assert v.jump_elapsed>previous_elapsed
            assert (v.jump_start_x,v.jump_start_y,v.jump_duration)==initial
            previous_elapsed=v.jump_elapsed
    assert not e.visitors
    assert v.x+SPRITE_WIDTH<left or v.x>left+1280
    assert v.y==e.floor_y
    assert len([event for event in e.drain_events() if event['kind']=='startle'])==1


@pytest.mark.parametrize('motion',['fleeing','leaving'])
def test_repeated_close_and_leave_keep_current_descent_and_trust(motion):
    e,v=perched()
    if motion=='fleeing':e.startle('amber')
    else:e.open_gate(False)
    e.tick(.3);before=asdict(v);trust=e.cats['amber'].trust
    e.open_gate(False)
    assert asdict(v)==before
    for _ in range(15):
        before=asdict(v)
        assert not e.open_gate(False)
        assert not e.startle('amber')
        e._leave(v)
        assert asdict(v)==before and e.cats['amber'].trust==trust
        e.tick(.04)
    assert v.route_motion==motion or v.state==motion
