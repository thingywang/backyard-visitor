"""Side climbing must retain real wall support and continuous interruptions."""
import math
import pytest
from backyard_visitor.model import Engine, SPRITE_WIDTH, SPRITE_HEIGHT, WINDOW_JUMP_X
from backyard_visitor.climb_routes import PAW_X, PAW_TOP, CLIMB_SPEED, CLIMB_STRIDE

EDGE = ('editor:top:0', 250., 800., 260.)
SIDES = {
    'right': ('editor:side:right', EDGE[0], 800., 260., 700., -1),
    'left': ('editor:side:left', EDGE[0], 250., 260., 700., 1),
}


def floor_cat(side='right', x=None):
    e=Engine(seed=3);e.open_gate();e.invite('amber')
    v=e.visitors['amber'];v.state='idle'
    v.x=v.target_x=(400. if x is None else x);v.y=v.target_y=e.floor_y
    v.stay_remaining=v.next_action=v.parkour_cooldown=1000
    e._visit_countdown=e._fight_countdown=1000
    e.set_window_edges([EDGE],climb_sides=[SIDES[side]])
    return e,v


def until(e,predicate,seconds=30):
    for _ in range(round(seconds/.025)):
        if predicate():return
        e.tick(.025)
    assert predicate(), 'route did not finish'


def climbing(side='right'):
    e,v=floor_cat(side)
    assert e._move_to_window(v,EDGE[0],400.)
    until(e,lambda:v.state=='climbing')
    return e,v


@pytest.mark.parametrize('side', ('left','right'))
def test_walk_to_wall_climb_then_short_mantle_and_horizontal_finish(side):
    e,v=floor_cat(side);initial_y=v.y
    assert e._move_to_window(v,EDGE[0],400.)
    assert v.route_stage=='climb_approach' and v.state=='walking'
    while v.route_stage=='climb_approach':
        assert v.y==initial_y
        e.tick(.025)
    until(e,lambda:v.state=='climbing')
    wall=SIDES[side][2];contact=PAW_X if side=='right' else SPRITE_WIDTH-PAW_X
    assert v.x+contact==wall
    before=v.y;phase=v.climb_phase
    e.tick(.1)
    assert v.y==pytest.approx(before-CLIMB_SPEED*.1)
    assert v.climb_phase==pytest.approx((phase+CLIMB_SPEED*.1/CLIMB_STRIDE)%1)
    until(e,lambda:v.state=='jumping')
    assert v.jump_start_y==pytest.approx(EDGE[3]-PAW_TOP)
    assert abs(v.target_x-v.jump_start_x)==56.<=WINDOW_JUMP_X
    assert v.target_y==EDGE[3]-SPRITE_HEIGHT
    until(e,lambda:v.state=='idle')
    assert (v.x,v.y)==(400.,148.)
    assert v.window_edge_id==EDGE[0] and v.climb_side_id is None


def test_short_attach_jump_uses_vertical_air_motion_before_gripping():
    e,v=floor_cat(x=712.)
    side=list(SIDES['right']);side[4]=600.
    e.set_window_edges([EDGE],climb_sides=[side])
    assert e._move_to_window(v,EDGE[0])
    assert v.state=='jumping' and v.route_stage=='climb_attach'
    assert v.jump_start_x==v.target_x==712.
    assert v.jump_start_y-v.target_y<=220
    until(e,lambda:v.state=='climbing')
    assert v.y<=504. and v.facing==-1


def test_side_too_high_to_grip_does_not_start_climbing():
    e,v=floor_cat()
    side=list(SIDES['right']);side[4]=375.
    e.set_window_edges([EDGE],climb_sides=[side])
    assert e._move_to_window(v,EDGE[0])
    assert v.climb_side_id is None and not v.route_stage.startswith('climb')


def test_geometry_without_explicit_visible_sides_never_invents_climbing():
    e,v=floor_cat();e.set_window_edges([EDGE])
    assert not e.window_climb_sides
    assert e._move_to_window(v,EDGE[0])
    assert v.climb_side_id is None


@pytest.mark.parametrize('change',('removed','occluded','moved'))
@pytest.mark.parametrize('stage',('climb_approach','climbing','climb_attach'))
def test_lost_wall_replans_from_current_position_without_teleport(change,stage):
    e,v=floor_cat(x=712. if stage=='climb_attach' else 400.)
    sides=[SIDES['right']]
    if stage=='climb_attach':
        sides=[(*SIDES['right'][:4],600.,-1)]
        e.set_window_edges([EDGE],climb_sides=sides)
    e._move_to_window(v,EDGE[0])
    if stage=='climbing':until(e,lambda:v.state=='climbing')
    e.tick(.05);before=v.x,v.y
    if change=='removed':e.set_window_edges([])
    elif change=='occluded':e.set_window_edges([EDGE])
    else:e.set_window_edges([('editor:top:0',270.,820.,260.)],climb_sides=[('editor:side:right',EDGE[0],820.,260.,700.,-1)])
    assert (v.x,v.y)==before and v.climb_side_id is None
    assert v.clip_y is None and v.window_edge_id is None
    until(e,lambda:v.state=='idle')
    assert v.y==e.floor_y


def test_identical_polls_and_segment_renumbering_keep_climb_phase():
    e,v=climbing();e.tick(.15)
    before=v.x,v.y,v.state_age,v.climb_phase
    e.set_window_edges([EDGE],climb_sides=[SIDES['right']])
    assert (v.x,v.y,v.state_age,v.climb_phase)==before
    renamed=('editor:top:2',*EDGE[1:]);side=(SIDES['right'][0],renamed[0],*SIDES['right'][2:])
    e.set_window_edges([renamed],climb_sides=[side])
    assert (v.x,v.y,v.state_age,v.climb_phase)==before
    assert v.route_edge_id==renamed[0]
    until(e,lambda:v.state=='idle')
    assert v.window_edge_id==renamed[0]


def test_carry_ends_climb_and_release_descends_normally():
    e,v=climbing();before=v.x,v.y
    assert e.carry('amber')
    assert v.state=='carried' and v.climb_side_id is None and (v.x,v.y)==before
    assert e.release('amber',v.x,v.y)
    until(e,lambda:v.state=='idle')
    assert v.y==e.floor_y


@pytest.mark.parametrize('reason',('close','startle'))
def test_escape_interrupts_wall_and_is_not_restarted(reason):
    e,v=climbing();e.tick(.1);before=v.x,v.y
    if reason=='close':e.open_gate(False)
    else:assert e.startle('amber')
    assert (v.x,v.y)==before and v.climb_side_id is None
    assert v.route_motion==('leaving' if reason=='close' else 'fleeing')
    e.tick(.2);arc=(v.jump_start_x,v.jump_start_y,v.jump_elapsed)
    e.open_gate(False)
    assert (v.jump_start_x,v.jump_start_y,v.jump_elapsed)==arc
    until(e,lambda:'amber' not in e.visitors)


def test_social_feedback_cannot_freeze_cat_on_a_wall():
    e,v=climbing();e.cats['amber'].trust=100
    assert not e.pet('amber') and not e.start_teaser('amber')
    e._affection(v)
    assert v.state=='climbing' and v.arrival_state=='purring'
    until(e,lambda:v.state=='purring')
    assert v.window_edge_id==EDGE[0]


def test_calm_mode_and_expiring_parkour_finish_existing_climb():
    e,v=climbing();v.parkour_remaining=.01;e.calm_mode=True
    e.tick(.1)
    assert v.state=='climbing' and v.parkour_remaining==0
    until(e,lambda:v.state=='idle')
    assert v.window_edge_id==EDGE[0]


def test_climb_geometry_and_progress_never_enter_saved_relationship_state():
    e,v=climbing();state=e.export()
    assert 'window_climb_sides' not in state and 'climb_phase' not in state
    assert not Engine(state).window_climb_sides


@pytest.mark.parametrize('dy',(-300.,0.,200.))
def test_jump_pose_metrics_follow_actual_arc_without_changing_position(dy):
    e,v=floor_cat();v.x=400.;v.y=350.
    v.target_x=430.;v.target_y=350.+dy
    e._begin_jump(v,support=(False,None))
    e.tick(.025);before=v.y
    e.tick(.025)
    assert v.jump_vy==pytest.approx((v.y-before)/.025)
    assert 0<=v.jump_pose_phase<=1 and math.isfinite(v.jump_vertical_span)
    if dy==0:assert v.jump_vertical_span==64.
    else:assert v.jump_vertical_span>=abs(dy)


@pytest.mark.parametrize('side', ('left', 'right'))
@pytest.mark.parametrize('change', ('resize', 'split'))
def test_same_wall_shrunk_top_clamps_finish_to_connected_segment(side, change):
    e,v=floor_cat(side)
    final_x=600. if side=='left' else 400.
    assert e._move_to_window(v,EDGE[0],final_x)
    until(e,lambda:v.state=='climbing')
    e.tick(.12)
    before=v.x,v.y,v.state_age,v.climb_phase
    edge_id=EDGE[0] if change=='resize' else 'editor:top:1'
    left,right=(250.,450.) if side=='left' else (600.,800.)
    shortened=(edge_id,left,right,EDGE[3])
    unchanged_wall=(SIDES[side][0],edge_id,*SIDES[side][2:])
    e.set_window_edges([shortened],climb_sides=[unchanged_wall])
    expected=min(right-SPRITE_WIDTH,max(left,final_x))
    assert v.route_final_x==expected
    assert (v.x,v.y,v.state_age,v.climb_phase)==before
    assert v.state=='climbing' and v.climb_side_id==SIDES[side][0]
    assert v.route_edge_id==edge_id
    for _ in range(1200):
        e.tick(.025)
        if v.route_stage=='finish' or v.state=='idle':
            assert left<=v.x<=right-SPRITE_WIDTH
        if v.state=='idle':break
    assert v.state=='idle'
    assert v.x==expected and v.y==EDGE[3]-SPRITE_HEIGHT
    assert v.window_edge_id==edge_id
