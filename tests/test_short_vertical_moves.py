"""Ordinary motion steps below half-height without crossing unsupported gaps."""
import math
import pytest
from backyard_visitor.model import Engine, Visitor, SPRITE_HEIGHT


def setup(y=400):
    e = Engine(seed=8, visit_limit=1); e.open_gate()
    v = Visitor('amber', 300, y, state='idle', target_x=300, target_y=y,
                next_action=1000, stay_remaining=1000, parkour_cooldown=1000)
    e.visitors['amber'] = v; e._visit_countdown = e._fight_countdown = 10000
    return e, v


def finish(e, v):
    states = []
    for _ in range(1600):
        states.append(v.state)
        before = v.x, v.y
        if v.state == 'idle':
            return states
        e.tick(.025)
        if states[-1] != 'jumping' and v.state != 'jumping':
            assert math.dist(before, (v.x, v.y)) < 10
    pytest.fail('ordinary route did not finish')


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('distance', [55.999, 56, 56.001])
def test_front_layer_strict_half_height_boundary(direction, distance):
    e, v = setup(); origin = v.x, v.y
    target = v.x+90, v.y+direction*distance
    e._walk(v, *target)
    assert (v.x, v.y) == origin
    if distance < SPRITE_HEIGHT/2:
        assert v.state == 'walking'
        states = finish(e, v)
        assert 'jumping' not in states
    else:
        assert v.state == 'jumping'
        assert 'jumping' in finish(e, v)
    assert (v.x, v.y) == pytest.approx(target)


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('distance', [55.999, 56, 56.001])
def test_real_window_small_step_uses_shared_support_and_large_steps_jump(direction, distance):
    e, v = setup(388)
    e.set_window_edges([('source', 100, 700, 500), ('target', 400, 1000, 500+direction*distance)])
    v.window_edge_id = 'source'
    assert 'target' in e._reachable_window_edges(v)
    assert e._move_to_window(v, 'target', 650)
    states = finish(e, v)
    assert ('jumping' in states) is (distance >= SPRITE_HEIGHT/2)
    assert v.window_edge_id == 'target'
    assert (v.x, v.y) == pytest.approx((650, 388+direction*distance))


@pytest.mark.parametrize('direction', [-1, 1])
def test_short_window_gap_is_skipped_instead_of_hopped_or_walked(direction):
    e, v = setup(388); v.x = 500
    e.set_window_edges([('source', 100, 700, 500), ('target', 600, 1000, 500+direction*40)])
    v.window_edge_id = 'source'; before = v.x, v.y, v.state, v.window_edge_id
    assert 'target' not in e._reachable_window_edges(v)
    assert not e._move_to_window(v, 'target', 700)
    assert (v.x, v.y, v.state, v.window_edge_id) == before


@pytest.mark.parametrize('state', ['jumping', 'carried'])
def test_existing_airborne_or_held_cat_is_not_converted_to_ground_walk(state):
    e, v = setup(); v.state = state; v.takeoff_remaining = 0
    e._walk(v, 350, 420)
    assert v.state == 'jumping'


def test_lost_support_can_still_fall_safely_even_near_floor():
    e, v = setup(); v.y = e.floor_y-20
    e.set_window_edges([('source', 100, 700, v.y+SPRITE_HEIGHT)])
    v.window_edge_id = 'source'
    e.set_window_edges([])
    assert v.state == 'jumping'
    finish(e, v)
    assert v.y == e.floor_y


def test_low_window_to_floor_steps_without_extra_jump():
    e, v = setup(); v.y = e.floor_y-30
    e.set_window_edges([('source', 100, 700, v.y+SPRITE_HEIGHT)])
    v.window_edge_id = 'source'
    e._walk(v, 800, e.floor_y)
    assert 'jumping' not in finish(e, v)
    assert (v.x, v.y, v.window_edge_id) == (800, e.floor_y, None)


def test_small_wall_attach_enters_climb_and_high_mantle_retains_needed_ascent():
    e, v = setup(); v.y = e.floor_y; v.x = 712
    edge = ('editor', 250., 800., 260.)
    e.set_window_edges([edge], climb_sides=[('wall', 'editor', 800., 260., 700., -1)])
    assert e._move_to_window(v, 'editor', 400)
    assert v.state == 'climbing'  # Four-pixel attachment is a reach, not a hop.
    seen = finish(e, v)
    assert 'jumping' in seen  # Existing mantle rises 88px, above half-height.
    assert v.window_edge_id == 'editor'


def test_explicit_teaser_pounce_remains_separate_from_ordinary_travel():
    e, v = setup(608)
    v.target_x, v.target_y = v.x+35, v.y
    v.arrival_state = 'teasing'
    e._begin_jump(v)
    assert v.state == 'jumping' and v.jump_arc > 0


@pytest.mark.parametrize('shift', [-20, 20])
def test_same_window_small_shift_keeps_support_and_continuous_motion(shift):
    e,v=setup(388)
    e.set_window_edges([('source',100,700,500)]);v.window_edge_id='source'
    before=v.x,v.y
    e.set_window_edges([('source',100,700,500+shift)])
    assert (v.x,v.y)==before and v.target_y==388+shift
    assert v.state=='walking' and v.route_stage=='step'
    assert 'jumping' not in finish(e,v)
    assert (v.x,v.y,v.window_edge_id)==(300,388+shift,'source')


def test_same_window_shift_out_from_under_body_does_not_create_air_walk():
    e,v=setup(388)
    e.set_window_edges([('source',100,700,500)]);v.window_edge_id='source'
    e.set_window_edges([('source',650,1150,520)])
    assert v.state=='jumping' and v.target_y==e.floor_y
