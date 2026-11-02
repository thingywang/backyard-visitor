"""The tree model is part of the real Engine, with ordinary lifecycle ownership."""
from pathlib import Path
import json
import math

import pytest

from backyard_visitor.model import Engine, Visitor
from backyard_visitor.scenery import load_scenes
from backyard_visitor.tree_geometry import build_tree_layout


def fixture(*, portrait=False, offset=(0, 0), resident=False, trust=80):
    e = Engine(seed=7, visit_limit=2)
    w, h = (720, 1280) if portrait else (1440, 900)
    e.set_bounds(*offset, w, h-40)
    e.open_gate(); e._visit_countdown = 10000
    e.calm_mode = True
    v = Visitor('amber', offset[0]+150, e.floor_y, state='idle', next_action=10000, stay_remaining=1000)
    e.visitors[v.cat_id] = v
    e.cats[v.cat_id].discovered = True
    e.cats[v.cat_id].neighbor_status = "neighbor"
    e.cats[v.cat_id].nickname = '暖暖'
    e.cats[v.cat_id].trust = trust
    if resident:
        e.cats[v.cat_id].trust = 100
        assert e.adopt(v.cat_id)
    sid = 'backyard-cat-tree-portrait' if portrait else 'backyard-cat-tree-landscape-morning'
    scene = load_scenes(Path(__file__).parents[1]/'backyard_visitor/assets')[sid]
    layout = build_tree_layout(scene, (*offset, w, h))
    assert layout is not None
    return e, v, layout


def until(e, v, predicate, seconds=180):
    for _ in range(int(seconds/.04)):
        if predicate(): return
        e.tick(.04)
    pytest.fail('Expected tree lifecycle condition was not reached')


def begin(**kwargs):
    e, v, layout = fixture(**kwargs)
    assert e.begin_tree_visit(v.cat_id, layout)
    return e, v, layout


@pytest.mark.parametrize('portrait', [False, True])
@pytest.mark.parametrize('offset', [(0, 0), (-1440, 30)])
def test_engine_roundtrip_foot_scale_state_and_export(portrait, offset):
    e, v, layout = fixture(portrait=portrait, offset=offset)
    before = v.x, v.y
    assert e.begin_tree_visit(v.cat_id, layout)
    assert (v.x, v.y) == before and e.moving
    raw = json.dumps(e.export())
    assert all(key not in raw for key in ('tree_trip', 'tree_pose', 'tree_visiting'))
    poses = set()
    while v.tree_trip is not None:
        e.tick(.04)
        if v.tree_pose:
            poses.add(v.tree_pose['pose'])
            assert (v.x+72, v.y+108) == pytest.approx(v.tree_pose['foot'])
    assert {'scratching', 'sleeping', 'grooming'} <= poses
    assert (v.x, v.y) == pytest.approx(before)
    assert v.tree_scale == 1 and v.tree_pose is None and v.tree_pending_action == ''
    assert v.state == 'idle'
    assert not e._tree_occupied()
    assert any(row['kind'] == 'tree_return' for row in e.drain_events())


@pytest.mark.parametrize('change', [dict(state='jumping'),dict(state='carried'),dict(state='bed_laying'),
    dict(state='window_rear'),dict(arrival_state='eating'),dict(carrying_gift='feather'),
    dict(posture_remaining=.3),dict(bed_nap=True),dict(bed_route_revision=1),dict(stay_remaining=20)])
def test_committed_cats_cannot_start_a_tree_excursion(change):
    e, v, layout = fixture()
    for key, value in change.items(): setattr(v, key, value)
    assert not e.begin_tree_visit(v.cat_id, layout)
    assert v.tree_trip is None


def test_invalid_ids_layout_and_active_play_do_not_cancel_existing_state():
    e, v, layout = fixture()
    for bad in (None, [], 'missing'):
        assert not e.begin_tree_visit(bad, layout)
        assert not e.request_tree_return(bad)
        assert not e.start_teaser(bad)
    assert not e.begin_tree_visit(v.cat_id, object())
    assert e.start_teaser(v.cat_id)
    session = e._teaser
    assert not e.begin_tree_visit(v.cat_id, layout) and e._teaser is session


@pytest.mark.parametrize('action', ['call', 'pet', 'carry', 'play', 'gate'])
def test_interactions_request_a_continuous_return_without_stale_carry_or_play(action):
    e, v, layout = begin()
    until(e, v, lambda: v.tree_pose['pose'] == 'sleeping')
    before = v.x, v.y, v.tree_scale
    trust = e.cats[v.cat_id].trust
    if action == 'call': assert e.call_cat(v.cat_id) == 'recognized'
    elif action == 'pet': assert e.pet(v.cat_id)
    elif action == 'carry': assert not e.carry(v.cat_id)
    elif action == 'play': assert not e.start_teaser(v.cat_id)
    else: e.open_gate(False)
    assert (v.x, v.y, v.tree_scale) == before
    assert v.tree_trip.returning and v.state == 'tree_visiting'
    assert e.active_teaser_id is None
    until(e, v, lambda: v.tree_trip is None)
    if action in {'call', 'pet'}:
        assert v.state == 'purring'
    elif action == 'gate':
        assert v.state == 'leaving' or v.route_motion == 'leaving'
    else:
        assert v.state == 'idle'
    assert e.cats[v.cat_id].trust == trust+(2 if action == 'pet' else 0)


def test_low_trust_startle_never_walks_away_instead_of_running():
    e, v, layout = begin(trust=0)
    until(e, v, lambda: v.tree_pose['pose'] == 'scratching')
    assert e.startle(v.cat_id)
    assert not e.startle(v.cat_id)
    until(e, v, lambda: v.tree_trip is None)
    assert v.state == 'fleeing' or v.route_motion == 'fleeing'


def test_low_trust_pet_returns_but_does_not_grant_purring_or_trust():
    e, v, _ = begin(trust=0)
    assert not e.pet(v.cat_id)
    assert v.tree_trip.returning
    until(e, v, lambda: v.tree_trip is None)
    assert v.state == 'idle' and e.cats[v.cat_id].trust == 0


def test_two_visitors_share_different_platforms_and_release_all_reservations():
    e, a, layout = begin()
    b = Visitor('mist', 300, e.floor_y, state='idle', next_action=10000, stay_remaining=1000)
    e.visitors[b.cat_id] = b
    e.cats[b.cat_id].discovered = True
    assert not e.begin_tree_visit(b.cat_id, layout)  # The first owns the entrance.
    until(e, a, lambda: a.tree_pose['pose'] == 'sleeping')
    assert e.begin_tree_visit(b.cat_id, layout)
    both_present = False
    for _ in range(5000):
        e.tick(.04)
        if a.tree_trip and b.tree_trip:
            both_present = True
            assert not a.tree_trip.reserved_supports & b.tree_trip.reserved_supports
        if a.tree_trip is None and b.tree_trip is None: break
    assert both_present and a.tree_trip is b.tree_trip is None
    assert e._tree_occupied() == set()


def test_resident_sleep_uses_same_bed_credit_once_and_public_tree_does_not_adopt():
    e, v, layout = fixture(resident=True)
    assert e.place_bed(v.cat_id)
    e.bed.credit = 30
    assert e.begin_tree_visit(v.cat_id, layout)
    until(e, v, lambda: v.tree_pose['pose'] == 'sleeping')
    active, slept, credit = e.bed.active_elapsed, e.bed.sleep_elapsed, e.bed.credit
    e.tick(.2)
    assert e.bed.active_elapsed-active == pytest.approx(.2)
    assert e.bed.sleep_elapsed-slept == pytest.approx(.2)
    assert e.bed.credit-credit == pytest.approx((e.bed.sleep_fraction-1)*.2)
    assert not v.bed_nap
    other, visitor, other_layout = begin()
    until(other, visitor, lambda: visitor.tree_pose['pose'] == 'sleeping')
    assert not other.cats[visitor.cat_id].adopted and other.bed is None


def test_resident_without_credit_does_not_get_a_second_full_nap():
    e, v, layout = fixture(resident=True)
    e.place_bed(v.cat_id); e.bed.credit = -20
    assert e.begin_tree_visit(v.cat_id, layout)
    poses = set()
    while v.tree_trip:
        e.tick(.1)
        if v.tree_pose: poses.add(v.tree_pose['pose'])
    assert 'sleeping' not in poses and e.bed.sleep_elapsed == 0


def test_cancel_residence_mid_tree_does_not_teleport_or_reset_tree_clock():
    e, v, layout = fixture(resident=True)
    e.place_bed(v.cat_id); e.bed.credit = 30
    assert e.begin_tree_visit(v.cat_id, layout)
    until(e, v, lambda: v.tree_pose['pose'] == 'sleeping')
    before = v.x, v.y, v.tree_scale, v.tree_trip.age
    assert e.cancel_residence(v.cat_id)
    assert (v.x, v.y, v.tree_scale, v.tree_trip.age) == before
    assert e.bed is None and e.cats[v.cat_id].trust == 50
    until(e, v, lambda: v.tree_trip is None)


def test_unrelated_native_windows_do_not_replace_tree_supports_or_motion():
    e, v, _ = begin()
    until(e, v, lambda: v.tree_pose['pose'] == 'sleeping')
    before = v.x, v.y, v.tree_trip.age, v.tree_trip.reserved_supports
    e.set_window_edges([('editor', 100, 600, 300)])
    e.set_window_edges([])
    assert (v.x, v.y, v.tree_trip.age, v.tree_trip.reserved_supports) == before


def test_screen_bounds_then_fullscreen_rebase_is_continuous_and_finite():
    e, v, _ = begin()
    until(e, v, lambda: v.tree_pose['pose'] == 'sleeping')
    before = v.x, v.y, v.tree_scale
    e.set_bounds(-1280, 25, 1280, 760)
    assert (v.x, v.y, v.tree_scale) == before
    e.set_depth_screen_rect((-1280, 0, 1280, 800))
    assert (v.x, v.y, v.tree_scale) == pytest.approx((before[0]-1280, before[1], before[2]))
    until(e, v, lambda: v.tree_trip is None)
    assert math.isfinite(v.x) and v.tree_scale == 1
    until(e, v, lambda: abs(v.y-e.floor_y) < .01)
