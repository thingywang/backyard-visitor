"""Tree excursions have real supports, shared reservations and finite returns."""
from dataclasses import replace
from pathlib import Path
import math

import pytest

from backyard_visitor.scenery import load_scenes
from backyard_visitor.tree_geometry import build_tree_layout
from backyard_visitor.tree_visit import TreeVisit


def layout(portrait=False, offset=(0, 0)):
    sid = 'backyard-cat-tree-portrait' if portrait else 'backyard-cat-tree-landscape-morning'
    scene = load_scenes(Path(__file__).parents[1]/'backyard_visitor/assets')[sid]
    return build_tree_layout(scene, (*offset, 720, 1280) if portrait else (*offset, 1440, 900))


def trip(portrait=False, offset=(0, 0)):
    tree = layout(portrait, offset)
    return TreeVisit(tree, (offset[0]+200, offset[1]+(1276 if portrait else 896)), sleep_seconds=5)


def until(t, predicate, max_seconds=140):
    for _ in range(int(max_seconds/.04)):
        if predicate(t.snapshot()):
            return
        t.step(.04)
    pytest.fail('Tree trip did not reach expected phase')


@pytest.mark.parametrize('portrait', [False, True])
@pytest.mark.parametrize('offset', [(0, 0), (-1440, -40)])
def test_complete_trip_uses_real_supports_all_activities_and_continuous_feet(portrait, offset):
    t = trip(portrait, offset)
    initial = t.foot
    phases, poses, supports, directions = set(), set(), set(), set()
    previous = t.snapshot()
    for _ in range(4500):
        t.step(.02); s = t.snapshot()
        phases.add(s['phase']); poses.add(s['pose'])
        if s['support_id']:
            supports.add(s['support_id'])
        assert math.dist(s['foot'], previous['foot']) < 12
        assert abs(s['scale']-previous['scale']) < .01
        assert .65 <= s['scale'] <= 1.
        if s['phase'] == 'rest':
            assert t.layout.by_id[s['support_id']].supports_foot(s['foot'], s['scale'])
        if s['pose'] == 'jumping':
            directions.add(-1 if s['jump_vy'] < 0 else 1)
            assert s['jump_vertical_span'] >= 56*t.layout.scale
            assert s['support_id'] in t.reserved_supports
            assert s['target_support'] in t.reserved_supports
        previous = s
        if t.done:
            break
    assert t.done and t.foot == pytest.approx(initial) and t.scale == 1
    assert supports == set(t.layout.by_id)
    assert {'scratching', 'grooming', 'sleeping', 'jumping', 'walking'} <= poses
    assert {'recede', 'advance', 'return'} <= phases
    assert directions == {-1, 1}
    assert not t.reserved_supports


def test_small_displacement_on_a_supported_board_does_not_hop():
    t = trip()
    until(t, lambda s: s['pose'] == 'scratching')
    point = t.foot
    t._queue.clear()
    t._queue.append(t._move((point[0]+2, point[1]), t.scale, 'walk', support=t.support_id))
    t._current = None; t._next(set())
    assert t.pose == 'walking' and t.phase == 'walk'


@pytest.mark.parametrize('phase', ['approach', 'recede', 'jump', 'rest'])
def test_return_interrupt_never_teleports_or_restarts_an_airborne_arc(phase):
    t = trip()
    until(t, lambda s: s['phase'] == phase)
    t.step(.1)
    before = t.foot, t.scale
    current = t._current
    assert t.request_return('called')
    assert (t.foot, t.scale) == before
    if phase == 'jump':
        assert t._current is current
    elapsed = t._elapsed
    assert not t.request_return('called') and t._elapsed == elapsed
    until(t, lambda s: s['done'])
    assert t.scale == 1 and t.foot == t.start


def test_layout_invalidation_descends_from_actual_foot_without_old_supports():
    t = trip(); until(t, lambda s: s['support_id'] == t.layout.bed_id and s['phase'] == 'rest')
    before = t.foot, t.scale
    assert t.request_return('desktop_obscured')
    assert (t.foot, t.scale) == before
    assert not t.reserved_supports and t.pose == 'jumping'
    y = t.foot[1]
    t.step(.05)
    assert t.foot[1] > y
    assert not t.request_return('desktop_obscured')
    until(t, lambda s: s['done'])
    assert t.foot == t.start


def test_fleeing_keeps_jump_then_uses_running_after_return_not_walk():
    t = trip(); until(t, lambda s: s['pose'] == 'sleeping')
    t.request_return('fleeing')
    poses = set()
    while not t.done:
        t.step(.04)
        if t.moving:
            poses.add(t.pose)
    assert 'running' in poses and 'jumping' in poses
    assert 'walking' not in poses


def test_waiting_second_cat_yields_instead_of_deadlocking_returning_first_cat():
    first = trip(); until(first, lambda s: s['pose'] == 'sleeping')
    second = trip()
    first.sleep_seconds = 0  # Existing finite sleep already scheduled.
    overlap = False
    for _ in range(5000):
        first.step(.04, second.reserved_supports)
        second.step(.04, first.reserved_supports)
        overlap |= bool(first.reserved_supports & second.reserved_supports)
        if first.done and second.done:
            break
    assert first.done and second.done
    assert not overlap


def test_pause_and_tick_partition_do_not_move_the_cat_twice():
    one, many = trip(), trip()
    one.step(13.)
    for _ in range(130): many.step(.1)
    for key in ('scale', 'gait_phase', 'age'):
        assert one.snapshot()[key] == pytest.approx(many.snapshot()[key])
    assert one.foot == pytest.approx(many.foot)
    one.paused = True; before = one.snapshot(); one.step(100)
    assert one.snapshot() == before
    with pytest.raises(ValueError): one.step(float('nan'))


def test_screen_change_preserves_apparent_size_and_changes_origin_once():
    t = trip(); until(t, lambda s: s['pose'] == 'sleeping')
    foot, scale = t.foot, t.scale
    t.rebase_screen((-1440, 30, 1280, 800))
    assert t.foot == pytest.approx((foot[0]-1440, foot[1]+30))
    assert t.scale == scale and not t.reserved_supports
    unchanged = t.foot
    t.rebase_screen((-1440, 30, 1280, 800))
    assert t.foot == unchanged
    until(t, lambda s: s['done'])
    assert -1440+72 <= t.foot[0] <= -1440+1280-72 and t.foot[1] == 826


@pytest.mark.parametrize('bad', [None, object(), (1,2)])
def test_invalid_layout_is_refused(bad):
    with pytest.raises(ValueError): TreeVisit(bad, (100, 100))


def test_invalid_start_or_scaled_layout_is_refused():
    with pytest.raises(ValueError): TreeVisit(layout(), (100, float('nan')))
    with pytest.raises(ValueError): TreeVisit(replace(layout(), scale=.01), (100, 100))


def test_call_while_still_in_foreground_reverses_immediately():
    t = trip(); t.step(.3)
    before = math.dist(t.foot, t.start)
    assert before > 0 and t.phase == 'approach'
    t.request_return('called'); t.step(.1)
    assert math.dist(t.foot, t.start) < before


def test_high_jump_has_real_apex_and_supported_takeoff_and_landing_phases():
    t = trip()
    until(t, lambda s: s['phase'] == 'takeoff')
    foot = t.foot
    assert t.target_support in t.reserved_supports
    assert t.snapshot()['takeoff_progress'] == pytest.approx(0., abs=.2)
    t.step(.04); assert t.foot == foot
    until(t, lambda s: s['phase'] == 'jump')
    assert t._current['target'][1] < t.foot[1]
    velocities = []
    while t.phase == 'jump':
        t.step(.005)
        if t.phase == 'jump': velocities.append(t.jump_vy)
    assert min(velocities) < 0 < max(velocities)
    assert t.phase == 'landing' and t.snapshot()['land_progress'] is not None
    landing_foot = t.foot
    assert t.layout.by_id[t.support_id].supports_foot(landing_foot)
    t.step(.04); assert t.foot == landing_foot
