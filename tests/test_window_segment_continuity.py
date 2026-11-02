"""Unrelated occlusion must not restart a physically unchanged perch or jump."""
from dataclasses import asdict
import pytest
from backyard_visitor.desktop import DesktopTarget, Rect, visible_window_edges
from backyard_visitor.model import Engine, SPRITE_WIDTH, SPRITE_HEIGHT

SCREEN = Rect(0, 0, 1280, 720)
EDITOR = DesktopTarget('editor', 'editor', Rect(150, 450, 950, 220))
DIALOG = DesktopTarget('dialog', 'dialog', Rect(300, 400, 100, 100))
PLAIN = visible_window_edges([EDITOR], SCREEN)
SPLIT = visible_window_edges([DIALOG, EDITOR], SCREEN)
REFS = ('window_edge_id', 'takeoff_edge_id', 'window_target_id', 'route_edge_id')


def natural_perch():
    e = Engine(seed=1, visit_limit=1); e.calm_mode = True; e.cats['ink'].trust = 60
    e.open_gate(); e.invite('ink'); e.set_window_edges(PLAIN)
    for _ in range(8000):
        e.tick(.05); v = e.visitors.get('ink')
        if v and v.state == 'idle' and v.window_edge_id == 'editor:top:0':
            return e, v
    pytest.fail('Natural public visit did not reach the editor')


def unchanged_except_references(before, v):
    after = asdict(v)
    assert {k: x for k, x in before.items() if k not in REFS} == {
        k: x for k, x in after.items() if k not in REFS}


@pytest.mark.parametrize('activity', ['idle', 'walking', 'running', 'lowering', 'holding', 'rising'])
def test_unrelated_dialog_split_and_merge_keep_grounded_activity(activity):
    e, v = natural_perch()
    if activity in {'walking', 'running'}:
        e._walk(v, 800, v.y, motion=activity)
        e.tick(.1)
    elif activity in {'lowering', 'holding', 'rising'}:
        assert e._begin_window_peek(v)
        for _ in range(160):
            e.tick(.05)
            if v.peek_phase == activity:
                break
        assert v.peek_phase == activity
    for edges, expected in [(SPLIT, 'editor:top:1'), (PLAIN, 'editor:top:0')]*3:
        before = asdict(v)
        e.set_window_edges(edges)
        unchanged_except_references(before, v)
        assert v.window_edge_id == expected


@pytest.mark.parametrize('in_air', [False, True])
def test_unrelated_split_keeps_takeoff_and_landing_schedule(in_air):
    e, v = natural_perch()
    # A second, lower support provides an actual window-to-window route.
    lower = ('lower', 400., 1000., 600.)
    e.set_window_edges(PLAIN + [lower])
    e._move_to_window(v, 'lower', 650.)
    e.tick(.35 if in_air else .02)
    assert v.state == 'jumping'
    before = asdict(v)
    e.set_window_edges(SPLIT + [lower])
    unchanged_except_references(before, v)
    if not in_air:
        assert v.takeoff_edge_id == 'editor:top:1'


def test_inflight_destination_segment_renumber_keeps_exact_trajectory():
    e, v = natural_perch()
    e._walk(v, v.x, e.floor_y)
    for _ in range(150):
        e.tick(.05)
        if v.state == 'idle': break
    assert v.y == e.floor_y
    e._move_to_window(v, 'editor:top:0', 800.)
    e.tick(.35)
    assert v.state == 'jumping' and v.takeoff_remaining == 0
    before = asdict(v)
    e.set_window_edges(SPLIT)
    unchanged_except_references(before, v)
    assert v.window_target_id == v.route_edge_id == 'editor:top:1'
    expected_elapsed = v.jump_elapsed + .05
    e.tick(.05)
    assert v.jump_elapsed == pytest.approx(expected_elapsed)


def test_split_across_remaining_walk_must_replan_even_if_feet_still_supported():
    e, v = natural_perch(); e._walk(v, 160., v.y)
    before = v.x, v.y
    e.set_window_edges(SPLIT)
    assert (v.x, v.y) == before
    assert not (v.state == 'walking' and v.target_x == 160.)
    assert v.target_x >= 400 or v.target_y == e.floor_y


@pytest.mark.parametrize('change', ['covers_cat', 'moves_top', 'closes'])
def test_support_changes_replan_without_teleport(change):
    e, v = natural_perch(); before = v.x, v.y
    if change == 'covers_cat':
        cover = DesktopTarget('cover', 'cover', Rect(v.x-10, 320, SPRITE_WIDTH+20, 250))
        edges = visible_window_edges([cover, EDITOR], SCREEN)
    elif change == 'moves_top':
        edges = visible_window_edges([DesktopTarget('editor', 'editor', Rect(150, 480, 950, 220))], SCREEN)
    else: edges = []
    e.set_window_edges(edges)
    assert (v.x, v.y) == before
    if change == 'moves_top':
        # A 30px shift of the same full-body support is below half-height.
        assert v.state == 'walking' and v.route_stage == 'step'
        assert v.window_edge_id == 'editor:top:0'
        assert v.target_x == before[0] and v.target_y == 480-SPRITE_HEIGHT
        for _ in range(100):
            previous_y = v.y
            e.tick(.05)
            assert v.state != 'jumping'
            assert v.x == before[0]
            assert previous_y <= v.y <= 480-SPRITE_HEIGHT
            if v.state == 'idle':
                break
        assert v.state == 'idle' and v.y == 480-SPRITE_HEIGHT
        assert v.window_edge_id == 'editor:top:0'
    else:
        # Occlusion and closure still remove the real support and require a fall.
        assert v.state == 'jumping' and v.window_edge_id is None
        assert v.target_y != before[1] or v.target_x != before[0]


def test_shrunk_destination_replans_launch_before_attempting_short_hop():
    e, v = natural_perch()
    target = ('target:top:0', 700., 1200., 370.)
    e.set_window_edges(PLAIN + [target])
    assert e._move_to_window(v, 'target:top:0', 1000.)
    assert v.route_stage == 'approach' and v.target_x == 636.
    before = v.x, v.y
    e.set_window_edges(PLAIN + [('target:top:0', 900., 1200., 370.)])
    assert (v.x, v.y) == before
    assert v.route_stage == 'approach' and v.target_x == 836.
    for _ in range(200):
        e.tick(.05)
        if v.state == 'jumping': break
    assert v.state == 'jumping'
    assert abs(v.target_x-v.jump_start_x) <= 64.


def test_new_occlusion_across_landing_finish_never_walks_through_the_gap():
    e, v = natural_perch(); e._walk(v, v.x, e.floor_y)
    for _ in range(150):
        e.tick(.05)
        if v.state == 'idle': break
    e._move_to_window(v, 'editor:top:0', 900.)
    for _ in range(200):
        e.tick(.05)
        if v.route_stage == 'finish': break
    assert v.route_stage == 'finish' and v.state == 'walking'
    cover = DesktopTarget('cover', 'cover', Rect(800, 400, 100, 100))
    before = v.x, v.y
    e.set_window_edges(visible_window_edges([cover, EDITOR], SCREEN))
    assert (v.x, v.y) == before
    for _ in range(200):
        e.tick(.05)
        if v.state in {'walking', 'running'} and v.window_edge_id:
            left, right, top = e.window_edges[v.window_edge_id]
            assert left <= v.x <= right-SPRITE_WIDTH
            assert v.y+SPRITE_HEIGHT == pytest.approx(top)
        if v.state == 'idle': break
    assert v.state == 'idle' and v.x <= 800-SPRITE_WIDTH
