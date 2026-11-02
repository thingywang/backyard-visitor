"""Ordinary routes never add height just to change an equal-height support."""
from dataclasses import asdict
import pytest
from backyard_visitor.model import Engine, Visitor


def perched(x=300.):
    e=Engine(seed=13,visit_limit=1);e.open_gate()
    e.set_window_edges([('source',100.,700.,500.),('next',250.,1000.,500.)])
    v=Visitor('amber',x,388.,state='idle',target_x=x,target_y=388.,
              next_action=1000,stay_remaining=1000,parkour_cooldown=1000)
    v.window_edge_id='source';e.visitors['amber']=v;e._visit_countdown=1000
    return e,v


def finish_without_hop(e,v,y):
    for _ in range(1000):
        assert v.state!='jumping' and v.y==y
        if v.state=='idle':return
        e.tick(.025)
    pytest.fail('horizontal route did not finish')


@pytest.mark.parametrize('motion',('walking','running'))
def test_equal_height_underfoot_target_walks_without_a_bounce(motion):
    e,v=perched();origin=v.x,v.y
    assert e._move_to_window(v,'next',650.,motion=motion)
    assert (v.x,v.y)==origin and v.window_edge_id=='next'
    finish_without_hop(e,v,388.)
    assert v.x==650.


def test_new_equal_height_window_underfoot_replaces_lost_support_without_hop():
    e,v=perched();origin=v.x,v.y
    e.set_window_edges([('replacement',200.,900.,500.)])
    assert (v.x,v.y)==origin and v.window_edge_id=='replacement'
    finish_without_hop(e,v,388.)


@pytest.mark.parametrize('automatic',('ordinary','parkour'))
def test_ordinary_and_parkour_choices_do_not_hop_between_equal_supports(automatic):
    e,v=perched();e.rng.random=lambda:0.7 if automatic=='ordinary' else 0.
    if automatic=='parkour':
        v.parkour_remaining=10
        assert e._parkour_action(v)
    else:
        e._parkour_action=lambda visitor:False
        e._choose_action(v)
    assert v.state!='jumping' and v.y==388.


@pytest.mark.parametrize('next_left',(600.,750.))
def test_equal_height_target_without_current_full_support_is_not_a_hop(next_left):
    e,v=perched(x=500.)
    e.set_window_edges([('source',100.,700.,500.),('next',next_left,1100.,500.)])
    before=asdict(v)
    assert 'next' not in e._reachable_window_edges(v)
    assert not e._move_to_window(v,'next',850.)
    assert asdict(v)==before


@pytest.mark.parametrize('target_top',(420.,580.))
def test_real_height_change_still_uses_jump(target_top):
    e,v=perched();e.set_window_edges([('source',100.,700.,500.),('next',250.,1000.,target_top)])
    assert e._move_to_window(v,'next',450.)
    assert v.state=='jumping' and v.target_y!=v.jump_start_y


def test_screen_clamp_to_floor_finishes_flight_as_horizontal_motion():
    e,v=perched();e.set_window_edges([])
    # The actual existing flight is rebased onto a shorter screen. Both its
    # present position and destination now have the same ground support.
    e._walk(v,800.,e.floor_y);e.tick(.05)
    assert v.state=='jumping'
    e.set_bounds(0.,0.,1280.,300.)
    assert v.y==v.target_y==e.floor_y and v.state!='jumping'
    finish_without_hop(e,v,e.floor_y)
    assert v.x==800.
