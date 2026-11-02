"""Upper arrivals use real low-headroom geometry and never replace visible cats."""
from dataclasses import asdict
import pytest
from backyard_visitor.model import Engine
from backyard_visitor.desktop import Rect,DesktopTarget,visible_window_supports


def incoming(*,seed=3,demo=False,limit=1):
    e=Engine(seed=seed,demo=demo,visit_limit=limit);e.set_bounds(0,24,1280,696);e.calm_mode=True
    e.set_window_edges([('max:top:0',0,1280,24)],low_headroom_ids={'max:top:0'},screen_top=0)
    e.open_gate();e.invite('amber');v=e.visitors['amber']
    return e,v


@pytest.mark.parametrize('delay',[0.,.25,.6])
@pytest.mark.parametrize('interrupt',['gate','startle','close','move','occlude','screen'])
def test_upper_arrival_interruptions_withdraw_upward_without_teleport(delay,interrupt):
    e,v=incoming();assert v.state=='top_entering';e.tick(delay);before=v.x,v.y
    if interrupt=='gate':e.open_gate(False)
    elif interrupt=='startle':assert e.startle(v.cat_id)
    elif interrupt=='close':e.set_window_edges([])
    elif interrupt=='move':e.set_window_edges([('max:top:0',0,1280,30)],low_headroom_ids={'max:top:0'},screen_top=0)
    elif interrupt=='occlude':
        full=Rect(0,0,1280,720);area=Rect(0,24,1280,696)
        blocker=DesktopTarget('block','block',Rect(v.x-5,30,154,50))
        edges,low=visible_window_supports([blocker,DesktopTarget('max','max',area)],area,screen_rect=full)
        e.set_window_edges(edges,low_headroom_ids=low,screen_top=0)
    else:
        e.set_bounds(-1280,124,1280,596);before=(before[0]-1280,before[1]+100)
    assert (v.x,v.y)==before and v.state=='top_leaving'
    last=v.y
    for _ in range(120):
        e.tick(.025)
        assert v.y<=last+1e-9 and abs(v.y-last)<6
        last=v.y
        if v.cat_id not in e.visitors:break
    assert v.cat_id not in e.visitors and v.y+168<e.screen_top


def test_repeated_close_or_window_polls_do_not_restart_the_retreat():
    e,v=incoming();e.tick(.4);e.open_gate(False);e.tick(.1)
    before=asdict(v);e.open_gate(False);e.set_window_edges([]);e._leave(v,fleeing=True)
    assert asdict(v)==before


def test_actual_top_entry_is_not_carryable_petable_or_playable_before_landing():
    e,v=incoming();e.cats[v.cat_id].trust=100;before=asdict(v)
    assert not e.carry(v.cat_id) and not e.pet(v.cat_id) and not e.start_teaser(v.cat_id)
    assert asdict(v)==before


def test_zero_effective_time_preserves_top_entry_and_retreat():
    e,v=incoming();before=asdict(v);e.tick(0);assert asdict(v)==before
    e.open_gate(False);before=asdict(v);e.tick(0);assert asdict(v)==before


def test_two_natural_top_arrivals_have_separate_landing_footprints():
    e,v=incoming(demo=True,limit=2);assert v.state=='top_entering'
    e.invite('ink');other=e.visitors['ink'];assert other.state=='top_entering'
    assert abs(v.target_x-other.target_x)>=168
    for _ in range(60):e.tick(.025)
    assert all(item.state=='window_rear' for item in e.visitors.values())


def test_a_late_window_poll_cannot_relocate_an_existing_side_visitor():
    e=Engine(seed=3);e.open_gate();e.invite('amber');v=e.visitors['amber'];before=asdict(v)
    assert v.state=='peeking'
    e.set_window_edges([('max',0,1280,0)],low_headroom_ids={'max'},screen_top=0)
    assert asdict(v)==before


def test_top_arrival_is_probabilistic_and_requires_an_open_gate_and_real_support():
    states=set()
    for seed in range(20):
        e,v=incoming(seed=seed);states.add(v.state)
    assert states=={'top_entering','peeking'}
    e=Engine(seed=3);e.set_window_edges([('max',0,1280,0)],low_headroom_ids={'max'},screen_top=0)
    assert e.invite('amber') is None and not e.visitors
    e.open_gate();e.set_window_edges([]);e.invite('amber')
    assert e.visitors['amber'].state=='peeking'


def test_ordinary_high_window_floor_launch_remains_available():
    e=Engine(seed=3);e.open_gate();e.invite('amber');v=e.visitors['amber']
    for _ in range(1000):
        e.tick(.025)
        if v.state=='idle':break
    e.set_window_edges([('ordinary',0,1280,200)])
    assert e._move_to_window(v,'ordinary') and v.state=='jumping'


def test_available_top_rebase_then_full_top_refresh_never_removes_visible_tail():
    e,v=incoming();e.tick(.4)
    e.set_bounds(0,64,1280,656)
    assert v.state=='top_leaving'
    before=v.x,v.y
    e.set_window_edges([],screen_top=0)
    assert (v.x,v.y)==before
    for _ in range(200):
        previous=v.y;e.tick(.025)
        assert v.y<=previous+1e-9 and abs(v.y-previous)<6
        if v.cat_id not in e.visitors:break
    assert v.cat_id not in e.visitors and v.y+168<0
