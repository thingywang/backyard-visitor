"""Demo requests wait for a visible full-body mouth rather than popping behind it."""
import pytest

from backyard_visitor.model import Engine, Visitor, SPRITE_HEIGHT, SETTLE_SECONDS


def ready(state='idle'):
    e=Engine(seed=4,demo=True,visit_limit=1);e.open_gate()
    v=Visitor('amber',400,e.floor_y,state=state,facing=-1,
              target_x=500,target_y=e.floor_y,stay_remaining=1000)
    e.visitors['amber']=v;e._visit_countdown=1000
    return e,v


def until_gift(e,seconds=12):
    for _ in range(int(seconds/.04)):
        e.tick(.04)
        if e.gifts:return e.gifts[-1]
    pytest.fail('Demo request was never delivered')


def test_full_window_peek_rises_before_release():
    e,v=ready();e.set_window_edges([('app',100,1100,500)])
    v.window_edge_id='app';v.y=500-SPRITE_HEIGHT
    assert e._begin_window_peek(v)
    for _ in range(33):e.tick(.04)
    assert v.peek_progress==1
    y=v.y
    assert e.demo_scene('mouse')
    assert v.y==y and v.peek_phase=='rising'
    assert not e.gifts and v.carrying_gift is None
    while v.state=='window_peeking':
        e.tick(.04)
        if v.state=='window_peeking':assert not e.gifts and v.carrying_gift is None
    assert v.y==388 and v.settle_remaining==SETTLE_SECONDS
    assert len(e.gifts)==1 and e.gifts[0].y==468
    assert e.gifts[0].y<500


@pytest.mark.parametrize('state',['sleeping','carried','eating','grooming','purring'])
def test_unsafe_postures_do_not_generate_or_display_gift_immediately(state):
    e,v=ready(state)
    assert e.demo_scene('mouse')
    assert not e.gifts and v.carrying_gift is None
    assert v.demo_gift_pending=='toy_mouse'
    if state=='carried':
        e.tick(1);assert not e.gifts and v.carrying_gift is None
        assert e.release(v.cat_id,v.x,v.y-80)
    gift=until_gift(e)
    assert gift.kind=='toy_mouse' and v.demo_gift_pending is None
    assert v.state not in {'sleeping','carried','eating','grooming','purring'}


@pytest.mark.parametrize('state',['fighting','fleeing','leaving'])
def test_urgent_postures_reject_gift_demo(state):
    e,v=ready(state)
    assert not e.demo_scene('mouse')
    assert not e.gifts and v.carrying_gift is None and v.demo_gift_pending is None


def test_rising_walk_does_not_show_gift_against_seated_head():
    e,v=ready();e._walk(v,500,v.y)
    assert v.rise_remaining>0
    assert e.demo_scene('mouse')
    assert v.carrying_gift is None
    e.tick(.2)
    assert v.rise_remaining>0 and v.carrying_gift is None and not e.gifts
    e.tick(.2)
    assert v.rise_remaining==0 and v.carrying_gift=='toy_mouse'
    assert until_gift(e).kind=='toy_mouse'


def test_picking_up_gift_carrier_hides_gift_until_release():
    e,v=ready('walking');v.carrying_gift='toy_mouse'
    assert e.carry(v.cat_id)
    assert v.carrying_gift is None and v.demo_gift_pending=='toy_mouse'
    e.tick(.5);assert not e.gifts
    e.release(v.cat_id,500,v.y-80)
    assert until_gift(e).kind=='toy_mouse'


def test_close_gate_cancels_queued_gift_without_dropping():
    e,v=ready('sleeping');e.demo_scene('mouse')
    assert v.demo_gift_pending
    e.open_gate(False)
    assert v.demo_gift_pending is None
    for _ in range(1000):e.tick(.04)
    assert not e.gifts and not e.visitors


def test_queued_demo_gift_is_runtime_only():
    e,v=ready('carried');e.demo_scene('mouse')
    state=e.export()
    assert 'demo_gift_pending' not in repr(state)
    restored=Engine(state,seed=4,demo=True)
    assert not restored.visitors and not restored.gifts
