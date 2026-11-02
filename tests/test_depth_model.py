"""Depth trips own motion until a safe, continuous return to the original foot."""
from dataclasses import asdict
import json
import ast
from pathlib import Path
import math

import pytest

from backyard_visitor.depth_visit import DepthRoute, TurningDepthVisit, return_pose
from backyard_visitor.model import Engine, Visitor

MASK = (((.84, .54), (.99, .54), (.99, .96), (.84, .96)),)


def fixture(offset=(0, 0), *, trust=80):
    sx, sy = offset
    e = Engine(seed=7, visit_limit=1)
    e.set_bounds(sx, sy, 1440, 850)
    e.open_gate()
    e._visit_countdown = 10000
    v = Visitor('amber', sx + .52*1440-72, e.floor_y, state='idle',
                next_action=10000, stay_remaining=10000)
    e.visitors['amber'] = v
    e.cats['amber'].trust = trust
    e.cats['amber'].discovered = True
    e.cats['amber'].neighbor_status = 'neighbor'
    e.cats['amber'].nickname = '小麦'
    return e, v, DepthRoute(polygons=MASK), (sx, sy, 1440, 900)


def begin(offset=(0, 0), *, trust=80):
    e, v, route, screen = fixture(offset, trust=trust)
    assert e.begin_depth_visit('amber', route, screen_rect=screen)
    return e, v, route, screen


def at_phase(e, v, phase):
    for _ in range(2500):
        if v.depth_pose and v.depth_pose['phase'] == phase:
            return
        assert v.depth_trip is not None
        e.tick(.02)
    pytest.fail('Phase not reached: '+phase)


def complete(e, v):
    for _ in range(2500):
        if v.depth_trip is None:
            return
        e.tick(.02)
    pytest.fail('Trip did not finish')


def position(v):
    return v.x, v.y, v.depth_scale, v.depth_layer


@pytest.mark.parametrize('offset', [(0, 0), (-1440, 80)])
def test_from_current_foot_and_safe_complete_without_persisting_runtime(offset):
    e, v, r, screen = fixture(offset)
    v.x -= 37.25  # Route's old start must not cause a teleport.
    origin = v.x, v.y
    assert e.begin_depth_visit('amber', r, screen_rect=screen)
    assert (v.x, v.y) == pytest.approx(origin)
    assert v.depth_trip.route.start[0] == pytest.approx((origin[0]+72-screen[0])/1440)
    assert v.depth_trip.route.cat_width == .1
    assert v.depth_trip.route.aspect_ratio == 1.6
    assert e.moving
    raw = json.dumps(e.export())
    assert 'depth_trip' not in raw and 'depth_pose' not in raw and 'depth_visiting' not in raw
    complete(e, v)
    assert (v.x, v.y) == pytest.approx(origin)
    assert v.depth_trip is v.depth_pose is v.depth_screen is None
    assert (v.depth_scale, v.depth_layer, v.depth_pending_action) == (1., 'front', '')
    assert v.state == 'idle'


@pytest.mark.parametrize('change', [
    {'state':'jumping'}, {'state':'carried'}, {'state':'bed_laying'}, {'state':'running'},
    {'state':'window_rear'}, {'arrival_state':'purring'}, {'arrival_state':'resting'},
    {'route_motion':'fleeing'}, {'window_edge_id':'real-window'}, {'window_target_id':'real-window'},
    {'route_stage':'approach'}, {'bed_route_revision':0}, {'bed_nap':True},
    {'carrying_gift':'feather'}, {'demo_gift_pending':'feather'}, {'meal_bowl':(20,30)},
    {'posture_remaining':.1}, {'landing_remaining':.1}, {'rise_remaining':.1},
    {'true_name_pending':True}, {'gift_response_pending':True}, {'y':100},
])
def test_rejects_unstable_or_committed_start_without_any_change(change):
    e, v, route, screen = fixture()
    for name, value in change.items():
        setattr(v, name, value)
    before = asdict(v)
    assert not e.begin_depth_visit('amber', route, screen_rect=screen)
    assert asdict(v) == before


def test_start_validation_and_active_teaser_do_not_get_cancelled():
    e, v, r, screen = fixture()
    for bad in ((0,0,144,900), (0,0,1440,0), (0,0,float('nan'),900), (0,0,1440), None):
        assert not e.begin_depth_visit('amber', r, screen_rect=bad)
    assert not e.begin_depth_visit('amber', object(), screen_rect=screen)
    assert e.start_teaser('amber')
    session = e._teaser
    assert not e.begin_depth_visit('amber', r, screen_rect=screen)
    assert e._teaser is session


def test_original_fast_timing_and_preview_semantics_are_preserved():
    reference = ast.literal_eval((Path(__file__).parent / "fixtures" / "depth_preview_reference.txt").read_text())
    r = DepthRoute(polygons=MASK)
    p = TurningDepthVisit(r)
    turns = 0
    duration = None
    for expected in reference["steps"]:
        a = p.step(reference["dt"])
        assert a == expected
        if a['phase'] == 'turn':
            turns += 1
            assert p._duration == pytest.approx(.4)
        if a['phase'] == 'recede':
            duration = p._duration
    assert a['done'] and turns
    assert duration == pytest.approx(3.4514565117280247)


def test_dt_splitting_pause_and_repeat_return_keep_route_clock():
    e, v, r, screen = begin()
    other, w, _, _ = begin()
    e.tick(4.9)
    for _ in range(49):
        other.tick(.1)
    for key in ('x', 'y', 'gait_phase', 'depth_scale'):
        assert getattr(v,key) == pytest.approx(getattr(w,key))
    trip = v.depth_trip
    trip.set_paused(True)
    before = trip.snapshot()
    e.tick(1)
    assert trip.snapshot() == before
    trip.set_paused(False)
    assert e.request_depth_return('amber','called')
    e.tick(.1)
    before = trip.snapshot()
    assert not e.request_depth_return('amber','called')
    assert trip.snapshot() == before
    complete(e, v)


@pytest.mark.parametrize('phase', ['front_approach','turn','recede','rear_traverse','hold',
                                   'rear_return','advance','front_return'])
def test_emergency_starts_running_immediately_without_return_or_teleport(phase):
    e, v, _, _ = begin(trust=10)
    at_phase(e, v, phase)
    before = position(v)
    assert e.startle('amber')
    assert position(v) == before
    assert v.depth_pending_action == 'fleeing' and e.cats['amber'].trust == 9
    assert v.depth_pose['phase'] == 'escape' and v.depth_pose['pose'] == 'running'
    initial_scale, initial_y, initial_layer = v.depth_scale, v.y, v.depth_layer
    for _ in range(10):
        if v.depth_trip is None:
            break
        snap = v.depth_trip.snapshot()
        assert not e.startle('amber')
        e._leave(v)
        assert v.depth_pending_action == 'fleeing'
        assert v.depth_trip.snapshot() == snap
        e.tick(.01)
    for _ in range(1000):
        if not e.visitors:
            break
        assert v.depth_pose['phase'] == 'escape'
        assert v.y == initial_y and v.depth_scale == initial_scale and v.depth_layer == initial_layer
        e.tick(.02)
    assert not e.visitors
    assert v.depth_trip is v.depth_pose is v.depth_viewport is None
    foot_x = v.x+72
    assert foot_x+72*initial_scale <= 0 or foot_x-72*initial_scale >= 1440
    assert len([x for x in e.drain_events() if x['kind']=='departure']) == 1


def test_return_while_outbound_turn_preserves_layer_and_finishes_turn_first():
    e, v, _, _ = begin()
    at_phase(e, v, 'turn')
    e.tick(.1)
    before = position(v)
    assert e.request_depth_return('amber', 'called')
    e.tick(.15)
    assert position(v) == before
    assert v.depth_pose['phase'] == 'turn'
    assert v.depth_pose['turn_progress'] == pytest.approx(.625)
    complete(e,v)
    assert v.depth_layer == 'front'


def test_pet_and_call_wait_for_front_and_deliver_once():
    e, v, _, _ = begin()
    at_phase(e,v,'rear_traverse')
    origin = position(v)
    assert e.call_cat('amber') == 'recognized'
    assert position(v) == origin
    assert e.pet('amber') and e.pet('amber')
    assert e.cats['amber'].trust == 80
    complete(e,v)
    assert v.state == 'purring' and e.cats['amber'].trust == 82
    assert len([x for x in e.drain_events() if x['kind']=='purr']) == 1


def test_drag_rejects_immediate_carry_and_never_leaves_stale_carry():
    e, v, _, _ = begin()
    at_phase(e,v,'recede')
    old = position(v)
    assert not e.carry('amber')
    assert position(v) == old
    assert not e.release('amber', 100,100)
    complete(e,v)
    assert v.state == 'idle'
    assert e.carry('amber') and v.state == 'carried'


def test_gate_closing_beats_queued_pet_and_departure_clock_does_not_restart():
    e,v,_,_ = begin()
    at_phase(e,v,'hold')
    assert e.pet('amber')
    old = position(v)
    e.open_gate(False)
    assert position(v) == old
    assert v.depth_pending_action == 'leaving'
    assert not e.pet('amber')
    complete(e,v)
    assert v.state == 'leaving' and e.cats['amber'].trust == 80


def test_food_bed_fight_teaser_and_windows_do_not_steal_trip():
    e,v,_,_ = begin()
    at_phase(e,v,'recede')
    e.cats['amber'].trust = 100
    assert e.adopt('amber')
    assert e.place_bed('amber')
    e.bed.credit = 10000
    assert e.feed()
    assert not e.start_teaser('amber') and e._teaser is None
    assert not e.trigger_fight()
    old = position(v)
    e.set_window_edges([('ordinary',0,1400,600)])
    assert position(v) == old
    e.tick(.05)
    assert v.depth_trip is not None and v.state == 'depth_visiting'
    assert e.food == 1 and not v.bed_nap


def test_work_area_resize_and_screen_rebase_do_not_replay_or_clamp_trip():
    e,v,_,_ = begin()
    at_phase(e,v,'rear_traverse')
    old = position(v)
    elapsed = v.depth_trip.elapsed
    e.set_bounds(-1440,80,1200,780)
    assert position(v) == old
    e.set_depth_screen_rect((-1440,80,1200,900))
    assert (v.x,v.y) == pytest.approx((old[0]-1440,old[1]+80))
    assert v.depth_scale == old[2] and v.depth_layer == old[3]
    assert v.depth_trip.elapsed == elapsed
    now = position(v)
    e.set_depth_screen_rect((-1440,80,1200,900))
    assert position(v) == now
    complete(e,v)
    assert v.depth_trip is None and v.depth_layer == 'front' and v.depth_scale == 1
    assert v.state in {'jumping','walking'}
    for _ in range(1000):
        if v.state == 'idle':
            break
        e.tick(.04)
    assert v.y == e.floor_y
    assert e.bounds[0] <= v.x <= e.bounds[0]+e.bounds[2]-144


def test_return_pose_uses_toward_art_and_no_mirrored_away_semantics():
    e,v,_,_ = begin()
    at_phase(e,v,'advance')
    assert return_pose(v.depth_pose,v.depth_trip.route)['kind'] == 'toward'


def test_opposite_facing_starts_with_stationary_brief_turn():
    e,v,r,screen = fixture()
    v.facing = -1
    old = position(v)
    assert e.begin_depth_visit('amber', r, screen_rect=screen)
    assert v.depth_pose['phase'] == 'turn'
    e.tick(.2)
    assert position(v) == old
    assert v.depth_pose['turn_progress'] == pytest.approx(.5)
    e.tick(.2)
    assert v.depth_pose['phase'] == 'front_approach'
    assert position(v) == old


def test_trip_is_cleared_before_followup_floor_migration(monkeypatch):
    e,v,_,_ = begin()
    at_phase(e,v,'recede')
    e.set_bounds(0,0,1440,820)
    observed=[]
    walk=e._walk
    def capture(visitor,*args,**kwargs):
        observed.append((visitor.depth_trip,visitor.depth_pose,visitor.depth_screen,
                         visitor.depth_scale,visitor.depth_layer,visitor.state))
        return walk(visitor,*args,**kwargs)
    monkeypatch.setattr(e,'_walk',capture)
    complete(e,v)
    assert observed and observed[0] == (None,None,None,1.,'front','idle')
    assert v.state == 'walking' and v.target_y == e.floor_y
    for _ in range(200):
        assert v.state != 'jumping'
        if v.state == 'idle':break
        e.tick(.025)
    assert v.state == 'idle' and v.y == e.floor_y


def test_start_is_explicit_and_withholding_tick_freezes_entire_visitor():
    e,v,r,screen=fixture()
    for _ in range(20):
        e.tick(.05)
    assert v.depth_trip is None
    assert e.begin_depth_visit('amber',r,screen_rect=screen)
    before=asdict(v)
    for _ in range(20):
        # Rendering and UI guards are read-only; Controller pause simply does
        # not call tick, so no trip-specific pause clock is required.
        v.depth_trip.snapshot()
        assert e.moving
    after=asdict(v)
    # asdict deep-copies the opaque trip object; compare its own value snapshot.
    assert before.pop('depth_trip').snapshot() == after.pop('depth_trip').snapshot()
    assert before == after


def test_minimum_legal_screen_mapping_does_not_change_non_depth_behavior():
    e = Engine(seed=7)
    e.set_bounds(0,0,144,112)
    e.set_depth_screen_rect((0,0,144,112))
    assert e.bounds == (0.,0.,144.,112.)
    assert not e.visitors
