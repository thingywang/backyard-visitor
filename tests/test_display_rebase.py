"""Changing artwork size preserves settled activity in display coordinates."""
from dataclasses import asdict
import copy

import pytest

from backyard_visitor.desktop import Rect
from backyard_visitor.display_scale import SceneTransform
from backyard_visitor.display_rebase import is_scale_safe, rebase_scene
from backyard_visitor.model import Engine, Visitor, Gift
from backyard_visitor import resident_bed


def fixture(*, scale=1., origin=(0., 0.), state='walking'):
    old = SceneTransform(scale, *origin)
    native = Rect(origin[0], origin[1]+24, 1440, 876)
    scene = old.rect_to_scene(native)
    e = Engine(seed=5)
    e.set_bounds(scene.x, scene.y, scene.width, scene.height)
    e.screen_top = origin[1]
    e.gate_open = True; e._visit_countdown = 1000
    v = Visitor('amber', scene.x+360, e.floor_y, state=state,
                target_x=scene.x+640, target_y=e.floor_y, age=17,
                state_age=3, next_action=11, stay_remaining=223, pet_cooldown=4,
                gait_phase=.42, run_phase=.23, ground_speed=58)
    e.visitors[v.cat_id] = v
    e.set_bowl(scene.x+500, scene.y+scene.height-80)
    e._events.clear()
    return e, v, old


def bounds_for(scale, origin=(0., 0.), width=1440, height=876):
    t = SceneTransform(scale, *origin)
    r = t.rect_to_scene(Rect(origin[0], origin[1]+24, width, height))
    return t, (r.x, r.y, r.width, r.height)


def clocks(v):
    return (v.state, v.age, v.state_age, v.next_action, v.stay_remaining,
            v.pet_cooldown, v.gait_phase, v.run_phase, v.ground_speed,
            v.true_name_pending, v.true_name_remaining, v.sleeping_purr_age)


@pytest.mark.parametrize('state', ['idle', 'walking', 'running', 'purring', 'sleeping', 'resting', 'playing'])
@pytest.mark.parametrize('scale', [.75, 1.5])
def test_ground_foot_horizontal_target_bowl_and_lifecycle_survive_resize(state, scale):
    e, v, old = fixture(state=state)
    if state == 'purring':
        v.true_name_remaining = 3.5; v.sleeping_purr = True; v.sleeping_purr_age = 7
    foot = old.to_display(v.x+72, v.y+108)
    target = old.to_display(v.target_x+72, v.target_y+108)
    bowl = old.to_display(*e.bowl)
    before, rng, clock = clocks(v), e.rng.getstate(), e._time
    e.set_paths([(350, e.floor_y)]); e._cursor = (400, 500)
    new, bounds = bounds_for(scale)
    assert is_scale_safe(e)
    rebase_scene(e, old, new, bounds)
    assert new.to_display(v.x+72, v.y+108)[0] == pytest.approx(foot[0])
    assert new.to_display(v.target_x+72, v.target_y+108)[0] == pytest.approx(target[0])
    assert new.to_display(*e.bowl) == pytest.approx(bowl)
    assert v.y == v.target_y == e.floor_y
    assert clocks(v) == before and e._time == clock and e.rng.getstate() == rng
    assert not e.paths and e._cursor is None and not e.window_edges
    assert not e.drain_events()


@pytest.mark.parametrize('sleeping_purr', [False, True])
def test_resident_nap_retains_bed_alignment_revision_and_rest_credit(sleeping_purr):
    e, v, old = fixture(state='purring' if sleeping_purr else 'sleeping')
    e.cats['amber'].discovered = True; e.cats['amber'].trust = 100
    e.cats['amber'].neighbor_status = 'neighbor'
    assert e.adopt('amber'); assert e.place_bed('amber', 700)
    v.x = v.target_x = 628; v.bed_nap = True
    v.sleeping_purr = sleeping_purr
    e.bed.active_elapsed = 91; e.bed.sleep_elapsed = 27
    e.bed.credit = 55; e.bed.nap_elapsed = 6
    before, revision = asdict(e.bed), e._bed_revision
    old_x = old.to_display(e.bed.x, e.bed_y)[0]
    new, bounds = bounds_for(1.5)
    rebase_scene(e, old, new, bounds)
    assert v.bed_nap and v.state == ('purring' if sleeping_purr else 'sleeping')
    assert v.sleeping_purr == sleeping_purr and resident_bed.at_bed(e, v)
    assert e._bed_revision == revision
    assert {k:value for k,value in asdict(e.bed).items() if k!='x'} == {k:value for k,value in before.items() if k!='x'}
    assert new.to_display(e.bed.x, e.bed_y)[0] == pytest.approx(old_x)
    assert not v.posture_kind and v.posture_remaining == 0
    if not sleeping_purr:
        e.tick(.01)
        assert v.state == 'sleeping' and v.bed_nap


def test_ground_bed_walk_keeps_revision_but_retargets_the_rebased_bed():
    e, v, old = fixture()
    e.cats['amber'].discovered = True; e.cats['amber'].trust = 100
    e.cats['amber'].neighbor_status = 'neighbor'
    e.adopt('amber'); e.place_bed('amber', 700)
    v.bed_route_revision = e._bed_revision; v.arrival_state = 'bed'; v.target_x = 628
    new, bounds = bounds_for(1.5)
    rebase_scene(e, old, new, bounds)
    assert v.bed_route_revision == e._bed_revision
    assert v.arrival_state == 'bed' and v.state == 'walking'
    assert v.target_x == pytest.approx(e.bed.x-72)
    assert v.target_y == e.floor_y


def test_gift_centres_and_mouse_wait_or_escape_are_preserved_without_rng_draws():
    e, v, old = fixture()
    e.gifts = [Gift('static','feather',400,400,'amber'),
               Gift('wait','toy_mouse',200,450,'amber',wait_remaining=5.25),
               Gift('run','toy_mouse',550,450,'amber',state='escaping',wait_remaining=0,escape_direction=-1,facing=-1)]
    centres = {g.id:old.to_display(g.x+19,g.y+19) for g in e.gifts}
    states = {g.id:(g.state,g.wait_remaining,g.escape_direction,g.facing) for g in e.gifts}
    rng = e.rng.getstate()
    new, bounds = bounds_for(1.5)
    rebase_scene(e, old, new, bounds)
    for gift in e.gifts:
        assert new.to_display(gift.x+19,gift.y+19) == pytest.approx(centres[gift.id])
        assert (gift.state,gift.wait_remaining,gift.escape_direction,gift.facing) == states[gift.id]
    assert e.rng.getstate() == rng


def test_zero_visitors_startup_preserves_furniture_and_clamps_larger_canvases():
    e, v, old = fixture(); e.visitors.clear()
    e.cats['amber'].discovered = True; e.cats['amber'].trust = 100
    e.cats['amber'].neighbor_status = 'neighbor'
    e.adopt('amber'); e.place_bed('amber', 1340)
    e.set_bowl(1416, 876)
    e.gifts = [Gift('edge','toy_mouse',1402,862,'amber',wait_remaining=6.1)]
    new, bounds = bounds_for(2.)
    rebase_scene(e, old, new, bounds)
    left, top, width, height = e.bounds
    assert not e.visitors
    assert left+31 <= e.bowl[0] <= left+width-31
    assert top+20 <= e.bowl[1] <= top+height-20
    assert e.bed.x <= left+width-86
    gift = e.gifts[0]
    assert left <= gift.x <= left+width-38 and top <= gift.y <= top+height-38
    assert gift.wait_remaining == 6.1


@pytest.mark.parametrize('translate', [True, False])
def test_negative_monitor_origin_can_preserve_relative_or_absolute_points(translate):
    e, v, old = fixture(origin=(-1600.,80.))
    e.set_bowl(e.bowl[0], 600)  # Both coordinate policies fit the destination.
    # Keep absolute points inside the wide destination when not translating.
    new = SceneTransform(1.25,-1800.,0.)
    area = new.rect_to_scene(Rect(-1800,24,2880,876))
    expected = old.to_display(v.x+72,v.y+108)[0] + (new.origin_x-old.origin_x if translate else 0)
    bowl = old.to_display(*e.bowl)
    if translate: bowl = (bowl[0]+new.origin_x-old.origin_x,bowl[1]+new.origin_y-old.origin_y)
    rebase_scene(e,old,new,(area.x,area.y,area.width,area.height),translate_origin=translate)
    assert new.to_display(v.x+72,v.y+108)[0] == pytest.approx(expected)
    assert new.to_display(*e.bowl) == pytest.approx(bowl)
    assert e.screen_top == new.to_scene(new.origin_x,old.origin_y+(new.origin_y-old.origin_y if translate else 0))[1]


@pytest.mark.parametrize('changes', [
    {'state':'jumping'}, {'state':'carried'}, {'state':'peeking'}, {'state':'entering'},
    {'state':'leaving'}, {'state':'fleeing'}, {'state':'fighting'}, {'state':'eating'},
    {'state':'grooming'}, {'state':'climbing'}, {'state':'tree_visiting'}, {'state':'depth_visiting'},
    {'window_edge_id':'window:top:0'}, {'window_target_id':'window:top:0'},
    {'takeoff_edge_id':'window:top:0'}, {'route_edge_id':'window:top:0'},
    {'climb_side_id':'side'}, {'clip_y':5}, {'rear_blend':.1}, {'depth_scale':.5},
    {'tree_scale':.65}, {'depth_layer':'back'}, {'posture_remaining':.2},
    {'takeoff_remaining':.2}, {'landing_remaining':.2}, {'rise_remaining':.2},
    {'settle_remaining':.2}, {'turn_remaining':.2}, {'arrival_state':'eating'},
    {'meal_bowl':(100,200)}, {'target_y':100}, {'y':100}, {'route_stage':'approach'},
    {'route_final_x':500}, {'bed_nap':True}, {'bed_route_revision':3},
])
def test_unsafe_states_wait_and_reject_without_mutating_the_model(changes):
    e, v, old = fixture()
    for key,value in changes.items(): setattr(v,key,value)
    visitor, exported, rng = copy.deepcopy(asdict(v)), e.export(), e.rng.getstate()
    assert not is_scale_safe(e)
    new,bounds=bounds_for(1.5)
    with pytest.raises(ValueError,match='settled foreground'): rebase_scene(e,old,new,bounds)
    assert asdict(v)==visitor and e.export()==exported and e.rng.getstate()==rng


@pytest.mark.parametrize('busy', ['teaser','fight','held-gift'])
def test_engine_owned_interactions_also_block_rebase_without_advancing_them(busy):
    e,v,old=fixture()
    if busy=='teaser':e._teaser=object()
    elif busy=='fight':e._fight_pair=('amber','mist')
    else:e.gifts=[Gift('held','feather',300,300,'amber',state='held')]
    assert not is_scale_safe(e)


@pytest.mark.parametrize('bad', [None,(0,0,143,900),(0,0,1440,111),(True,0,1440,900),(0,0,float('nan'),900),(0,0,10**1000,900)])
def test_invalid_new_bounds_leave_live_scene_untouched(bad):
    e,v,old=fixture();before=e.export(),asdict(v),e.rng.getstate()
    with pytest.raises(ValueError):rebase_scene(e,old,SceneTransform(1.5,0,0),bad)
    assert (e.export(),asdict(v),e.rng.getstate())==before
