"""Short lateral jumps retain a reachable supported approach and final goal."""
import math
from dataclasses import asdict
import pytest
from backyard_visitor.model import Engine, Visitor, WINDOW_JUMP_X, LANDING_SECONDS


def setup(left=0, top=0):
    e=Engine(seed=1,visit_limit=1);e.set_bounds(left,top,1280,720);e.open_gate()
    v=Visitor('amber',left+250,e.floor_y,state='idle',facing=1,next_action=1000,
              stay_remaining=1000,parkour_cooldown=1000)
    e.visitors[v.cat_id]=v;e._visit_countdown=1000
    return e,v


def finish(e,v,limit=2000,*,allow_shallow=False):
    final_y=v.route_final_y if v.route_final_y is not None else v.target_y
    for _ in range(limit):
        if not v.moving:return
        before=(v.x,v.y);old=v.state;phase=v.gait_phase
        e.tick(.02)
        assert math.dist(before,(v.x,v.y))<15
        if old=='jumping':
            assert abs(v.target_x-v.jump_start_x)<=WINDOW_JUMP_X+1e-7 if v.state=='jumping' else True
            assert v.gait_phase==phase
        elif v.state!='jumping':
            if allow_shallow:
                assert min(before[1],final_y)-1e-7 <= v.y <= max(before[1],final_y)+1e-7
            else:
                assert v.y==before[1]
    assert not v.moving


@pytest.mark.parametrize('offset',[(0,0),(-1920,-100)])
@pytest.mark.parametrize('dy',[-1,-20,-56,-200])
def test_vertical_route_uses_half_height_jump_threshold(offset,dy):
    e,v=setup(*offset);origin=v.x,v.y;goal=(v.x+420,v.y+dy)
    e._walk(v,*goal)
    if abs(dy)<56:
        assert v.state=='walking' and (v.target_x,v.target_y)==goal
    else:
        assert v.state=='jumping' and v.target_x-origin[0]==64
        assert (v.route_final_x,v.route_final_y)==goal
    assert (v.x,v.y)==origin
    finish(e,v,allow_shallow=abs(dy)<56)
    assert (v.x,v.y)==goal and v.state=='idle' and not v.route_stage


def test_landing_buffer_holds_feet_before_finish_segment():
    e,v=setup();e._walk(v,700,v.y-90)
    while v.state=='jumping':e.tick(.01)
    assert v.state=='walking' and v.route_stage=='finish' and v.landing_remaining==LANDING_SECONDS
    origin=(v.x,v.y);phase=v.gait_phase
    e.tick(.20)
    assert (v.x,v.y)==origin and v.gait_phase==phase
    e.tick(.08)
    assert v.x>origin[0] and v.y==origin[1]


def test_far_floor_window_approach_stays_on_floor_then_uses_short_hop():
    e,v=setup();e.set_window_edges([('target',800,1200,500)])
    assert e._move_to_window(v,'target',1000)
    assert v.route_stage=='approach' and v.state=='walking' and v.target_x==736
    floor=e.floor_y
    while v.route_stage=='approach':
        e.tick(.05);assert v.y==floor
    assert v.state=='jumping' and v.target_x-v.x==64
    finish(e,v)
    assert (v.x,v.y)==(1000,388) and v.window_edge_id=='target'


def test_source_platform_approach_and_unreachable_gap_are_distinct():
    e,v=setup();e.set_window_edges([('source',100,700,500),('near',600,1000,420),('far',800,1200,420)])
    v.x,v.y=250,388;v.window_edge_id='source'
    before=asdict(v)
    assert not e._move_to_window(v,'far') and asdict(v)==before
    assert 'far' not in e._reachable_window_edges(v)
    assert e._move_to_window(v,'near',800) and v.route_stage=='approach'
    while v.route_stage=='approach':
        e.tick(.05);assert 100<=v.x<=556 and v.y==388
    assert v.jump_start_x==536 and v.target_x==600
    finish(e,v)
    assert (v.x,v.y)==(800,308) and v.window_edge_id=='near'


@pytest.mark.parametrize('change',['remove_target','move_target','remove_source'])
def test_window_change_during_approach_preserves_position_and_replans(change):
    e,v=setup();source=('source',100,700,500);target=('target',600,1000,420)
    e.set_window_edges([source,target]);v.x,v.y=250,388;v.window_edge_id='source'
    e._move_to_window(v,'target',800);e.tick(.4);before=v.x,v.y
    rows=[source] if change=='remove_target' else [source,('target',900,1250,420)] if change=='move_target' else [target]
    e.set_window_edges(rows)
    assert (v.x,v.y)==before
    finish(e,v)
    assert v.window_edge_id=='source' or v.y==e.floor_y


@pytest.mark.parametrize('action',['carry','close','startle'])
def test_urgent_action_clears_old_destination_without_diagonal_escape(action):
    e,v=setup();e._walk(v,900,v.y-200);e.tick(.5);before=v.x,v.y
    if action=='carry':e.carry(v.cat_id)
    elif action=='close':e.open_gate(False)
    else:e.startle(v.cat_id)
    assert (v.x,v.y)==before and v.route_final_x!=900
    if action=='carry':assert not v.route_stage
    else:
        assert v.route_motion in {'fleeing','leaving'} and v.takeoff_remaining==0
        for _ in range(1600):
            if not e.visitors:break
            old=v.state;y=v.y;e.tick(.02)
            if old in {'leaving','fleeing'}:assert v.y==y
        assert not e.visitors


def test_meal_facing_and_gift_wait_for_final_goal():
    e,v=setup();v.carrying_gift='toy_mouse';e.food=1
    goal=(700,v.y-20)
    e._walk(v,*goal,arrival='eating',arrival_facing=-1)
    assert v.state=='walking'
    for _ in range(2000):
        if not v.moving:break
        assert v.state!='jumping' and not e.gifts and v.carrying_gift=='toy_mouse'
        before=v.x,v.y;e.tick(.02)
        assert math.dist(before,(v.x,v.y))<15
    assert (v.x,v.y)==goal
    assert v.state=='eating' and v.facing==-1 and len(e.gifts)==1
    assert e.gifts[0].facing==-1 and e.gifts[0].x==v.x+21-19


def test_route_fields_do_not_persist_and_pausing_does_not_advance():
    e,v=setup();e._walk(v,900,v.y-100);e.tick(.1);before=asdict(v)
    e.tick(0);assert asdict(v)==before
    assert 'route_final_x' not in repr(e.export()) and not Engine(e.export()).visitors


def test_emergency_descent_cannot_requeue_gifts_or_true_name_response():
    e,v=setup();e.demo=True;e.cats[v.cat_id].discovered=True
    e.cats[v.cat_id].neighbor_status="neighbor"
    e._walk(v,650,v.y-200);e.tick(.5);e._leave(v)
    assert v.state=='jumping' and v.route_motion=='leaving'
    assert not e.demo_scene('gift')
    assert e.name_cat(v.cat_id,e.cats[v.cat_id].true_name)=='true_name'
    assert not v.true_name_pending and v.demo_gift_pending is None


def test_running_ground_pair_can_fight_and_clears_autonomous_routes():
    e,v=setup();e.visitor_limit=2
    other=Visitor('ink',v.x+80,e.floor_y,state='running',target_x=v.x+100,target_y=e.floor_y)
    e.visitors['ink']=other
    v.state='running';v.parkour_remaining=other.parkour_remaining=8
    assert e.trigger_fight()
    assert all(x.state=='fighting' and x.parkour_remaining==0 and not x.route_stage for x in (v,other))
