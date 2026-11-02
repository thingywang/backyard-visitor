"""Low-headroom perches stay physical, continuous, transient and interruptible."""
from dataclasses import asdict
import math
import pytest
from backyard_visitor.desktop import DesktopTarget, Rect, visible_window_edges, visible_window_supports
from backyard_visitor.model import Engine, SPRITE_HEIGHT, SPRITE_WIDTH, REAR_BLEND_SECONDS

FULL=Rect(0,0,1280,720)
AVAILABLE=Rect(0,32,1280,688)
MAXIMIZED=DesktopTarget('editor','editor',AVAILABLE)


def tick_until(e, predicate, seconds=20, step=.025):
    for _ in range(math.ceil(seconds/step)):
        if predicate():return
        e.tick(step)
    assert predicate()


def floor_visitor(bounds=(0,32,1280,688)):
    e=Engine(seed=31,visit_limit=1);e.set_bounds(*bounds);e.calm_mode=True
    e.open_gate();e.invite('amber');e.cats['amber'].trust=100
    # Public entry proceeds on the real floor before adding native supports.
    tick_until(e,lambda:e.visitors['amber'].state=='idle')
    v=e.visitors['amber'];assert v.y==e.floor_y
    return e,v


def top_visitor():
    e=Engine(seed=3,visit_limit=1);e.set_bounds(0,32,1280,688);e.calm_mode=True
    edges,low=visible_window_supports([MAXIMIZED],AVAILABLE,screen_rect=FULL)
    e.set_window_edges(edges,low_headroom_ids=low,screen_top=FULL.top)
    e.open_gate();e.invite('amber');v=e.visitors['amber']
    assert v.state=='top_entering'
    return e,v


def rear_visitor():
    e,v=top_visitor()
    tick_until(e,lambda:v.state=='window_rear' and v.rear_blend==1 and v.landing_remaining<=0)
    e.cats[v.cat_id].trust=100
    v.next_action=1000
    return e,v


def test_low_headroom_is_opt_in_and_not_a_fullscreen_flag():
    assert visible_window_edges([MAXIMIZED],AVAILABLE)==[]
    edges,low=visible_window_supports([MAXIMIZED],AVAILABLE,screen_rect=FULL)
    assert edges==[('editor:top:0',0.,1280.,32.)]
    assert low=={'editor:top:0'}
    # A full-display rectangle is geometrically different from available-area
    # maximization. No native full-screen assertion is derived from either.
    assert visible_window_supports([DesktopTarget('full','full',FULL)],AVAILABLE,screen_rect=FULL)==([],set())


@pytest.mark.parametrize('rect',[Rect(0,32,600,688),Rect(0,32,1280,200),Rect(-600,32,1880,688),
                                 Rect(20,32,1260,688),Rect(0,60,1280,660)])
def test_partial_or_cross_screen_windows_do_not_gain_special_support(rect):
    assert visible_window_supports([DesktopTarget('other','other',rect)],AVAILABLE,screen_rect=FULL)==([],set())


@pytest.mark.parametrize('x,y',[(0,32),(-1280,-968),(1600,200)])
def test_nonzero_and_negative_screen_origins(x,y):
    available=Rect(x,y,1280,688);full=Rect(x,y-32,1280,720)
    edges,low=visible_window_supports([DesktopTarget('own','own',available)],available,screen_rect=full)
    assert edges==[('own:top:0',float(x),float(x+1280),float(y))]
    e=Engine();e.set_bounds(x,y,1280,688);e.set_window_edges(edges,low_headroom_ids=low)
    assert e.low_headroom_edges==low


def test_foreground_window_covering_only_tail_still_splits_support():
    blocker=DesktopTarget('dialog','dialog',Rect(300,45,100,35))
    edges,low=visible_window_supports([blocker,MAXIMIZED],AVAILABLE,screen_rect=FULL)
    assert edges==[('editor:top:0',0.,300.,32.),('editor:top:1',400.,1280.,32.)]
    assert low=={row[0] for row in edges}


def test_window_below_tail_does_not_occlude_rear_support():
    blocker=DesktopTarget('dialog','dialog',Rect(300,89,100,35))
    edges,low=visible_window_supports([blocker,MAXIMIZED],AVAILABLE,screen_rect=FULL)
    assert edges==[('editor:top:0',0.,1280.,32.)]
    assert low=={'editor:top:0'}


def test_validation_metadata_is_transient_scoped_and_not_blanket_headroom_bypass():
    e=Engine();e.set_bounds(0,32,1280,688)
    edges=[('good',0,1280,32),('not_near_top',0,1280,90),('ordinary',0,1280,300)]
    e.set_window_edges(edges,low_headroom_ids={'good','not_near_top','ordinary','missing'})
    assert e.low_headroom_edges=={'good'}
    assert set(e.window_edges)=={'good','ordinary'}
    e.set_window_edges(edges)
    assert e.low_headroom_edges==set() and set(e.window_edges)=={'ordinary'}


def test_floor_cannot_make_a_giant_jump_to_low_headroom_support():
    e,v=floor_visitor();e.set_window_edges([('edge',0,1280,32)],low_headroom_ids={'edge'},screen_top=0)
    before=asdict(v)
    assert not e._move_to_window(v,'edge',v.x+500)
    assert asdict(v)==before and 'edge' not in e._reachable_window_edges(v)


def test_upper_entry_starts_fully_hidden_then_descends_to_rear_without_finish_walk():
    e,v=top_visitor();before=v.x,v.y
    assert v.y+168<FULL.top and v.rear_blend==1
    samples=[]
    for _ in range(500):
        old=(v.x,v.y);e.tick(.025);samples.append((v.state,v.x,v.y,v.route_stage))
        assert math.hypot(v.x-old[0],v.y-old[1])<6
        assert v.y>=old[1]
        if v.state=='window_rear':break
    assert v.state=='window_rear' and v.y==32-SPRITE_HEIGHT
    assert all(stage!='finish' for state,x,y,stage in samples)
    assert v.x==before[0] and v.y-before[1]<120
    assert v.clip_y is None
    tick_until(e,lambda:v.landing_remaining==0)
    assert not e.moving


def test_rear_blend_freezes_without_effective_ticks_and_decays_during_exit():
    e,v=rear_visitor();before=asdict(v);e.tick(0);assert asdict(v)==before
    e.leave_rear_perch(v);assert v.rear_blend==1 and v.y<e.bounds[1]
    e.tick(REAR_BLEND_SECONDS/2);assert 0<v.rear_blend<1
    tick_until(e,lambda:v.state=='idle' and v.y==e.floor_y)
    assert v.rear_blend==0 and v.window_edge_id is None


def test_remote_tail_occlusion_renumbers_without_restarting_rear():
    e,v=rear_visitor()
    # Place the foreground blocker remotely left of this cat's whole body.
    blocker=DesktopTarget('dialog','dialog',Rect(5,45,100,35))
    assert v.x>105
    before=asdict(v);edges,low=visible_window_supports([blocker,MAXIMIZED],AVAILABLE,screen_rect=FULL)
    e.set_window_edges(edges,low_headroom_ids=low)
    after=asdict(v)
    assert v.window_edge_id=='editor:top:1'
    assert {k:a for k,a in before.items() if k!='window_edge_id'}=={k:a for k,a in after.items() if k!='window_edge_id'}


@pytest.mark.parametrize('change',['close','covers_tail','restore','disable_special'])
def test_support_loss_preserves_origin_then_exits(change):
    e,v=rear_visitor();before=v.x,v.y
    if change=='covers_tail':
        blocker=DesktopTarget('dialog','dialog',Rect(v.x-5,45,SPRITE_WIDTH+10,35))
        edges,low=visible_window_supports([blocker,MAXIMIZED],AVAILABLE,screen_rect=FULL)
    elif change=='restore':edges,low=[('editor:top:0',200.,1100.,400.)],set()
    elif change=='disable_special':edges,low=[('editor:top:0',0.,1280.,32.)],set()
    else:edges,low=[],set()
    e.set_window_edges(edges,low_headroom_ids=low)
    assert (v.x,v.y)==before and v.state=='jumping'
    assert v.jump_start_y==before[1]
    tick_until(e,lambda:v.state in {'idle','window_rear'} and v.window_edge_id not in {'editor:top:0'} if change=='covers_tail' else v.state=='idle')
    assert v.y>=e.bounds[1] or v.state=='window_rear'


def test_screen_rebase_does_not_clamp_away_rear_origin():
    e,v=rear_visitor();old=v.x,v.y;e.set_bounds(-1280,100,1280,620)
    assert v.x==pytest.approx(old[0]-1280) and v.y==pytest.approx(old[1]+68)
    assert v.state=='jumping' and v.jump_start_y==v.y
    tick_until(e,lambda:v.state=='idle')
    assert v.y==e.floor_y


@pytest.mark.parametrize('action',['feed','pet','call','play','startle','close_gate','gift','true_name','gift_response'])
def test_interactions_exit_special_support_without_hidden_feedback_or_drop(action):
    e,v=rear_visitor();before=v.x,v.y
    if action=='feed':assert e.feed()
    elif action=='pet':assert e.pet(v.cat_id)
    elif action=='call':e.cats[v.cat_id].nickname='访客';assert e.call_cat(v.cat_id)=='recognized'
    elif action=='play':assert e.start_teaser(v.cat_id)
    elif action=='startle':e.cats[v.cat_id].trust=10;assert e.startle(v.cat_id)
    elif action=='close_gate':e.open_gate(False)
    elif action=='gift':e._drop_from_mouth(v,'feather');assert not e.gifts;e._advance_demo_gift(v)
    elif action=='true_name':v.true_name_pending=True;assert not e._deliver_true_name(v)
    else:v.gift_response_pending=True;assert not e._deliver_gift_response(v)
    assert (v.x,v.y)==before and v.state=='jumping'
    assert v.jump_start_y==before[1]
    assert v.true_name_remaining==v.gift_response_remaining==0
    assert not e.gifts
    tick_until(e,lambda:v.cat_id not in e.visitors or (v.y>=e.bounds[1] and v.state!='jumping'))
    assert v.y>=e.bounds[1]


def test_carry_and_release_preserve_grab_origin_and_discard_special_support():
    e,v=rear_visitor();before=v.x,v.y
    assert e.carry(v.cat_id)
    assert (v.x,v.y)==before and v.state=='carried' and v.window_edge_id is None
    assert v.rear_blend==1
    e.tick(1)
    assert (v.x,v.y)==before and v.rear_blend==1
    assert e.release(v.cat_id,v.x,200)
    tick_until(e,lambda:v.state=='idle')
    assert v.y==e.floor_y and v.rear_blend==0


def test_special_support_does_not_allow_side_on_sleep_or_purr_above_screen():
    for state in ('sleeping','purring','playing','resting'):
        e,v=rear_visitor();before=v.x,v.y;e._state(v,state)
        assert (v.x,v.y)==before and v.state=='jumping'
        tick_until(e,lambda:v.state!='jumping')
        assert v.y==e.floor_y


@pytest.mark.parametrize('phase',['approach','charge','air'])
def test_special_target_vanishes_mid_route_without_teleport_or_hidden_idle(phase):
    e,v=floor_visitor()
    # The split target has a real reachable launch position on the floor.
    e.set_window_edges([('source',0.,1280.,200.)]);assert e._move_to_window(v,'source')
    tick_until(e,lambda:v.state=='idle' and v.window_edge_id=='source')
    x=min(1000.,v.x+250)
    e.set_window_edges([('source',0.,1280.,200.),('edge',x,1280.,32.)],low_headroom_ids={'edge'})
    assert e._move_to_window(v,'edge',x)
    if phase=='approach':
        assert v.route_stage=='approach'
    elif phase=='charge':
        tick_until(e,lambda:v.state=='jumping' and v.takeoff_remaining>0)
    else:
        tick_until(e,lambda:v.state=='jumping' and v.takeoff_remaining==0 and v.y<32)
    before=v.x,v.y;e.set_window_edges([])
    assert (v.x,v.y)==before
    tick_until(e,lambda:v.state=='idle')
    assert v.y==e.floor_y and v.window_edge_id is None and v.rear_blend==0


def test_normal_maximized_restore_cycle_uses_actual_continuous_routes():
    e,v=floor_visitor();e.set_window_edges([('same:top:0',0.,1280.,200.)])
    assert e._move_to_window(v,'same:top:0')
    tick_until(e,lambda:v.state=='idle' and v.window_edge_id is not None)
    for top,low,state in [(32,{'same:top:0'},'window_rear'),(200,set(),'idle')]:
        before=v.x,v.y;e.set_window_edges([('same:top:0',0.,1280.,top)],low_headroom_ids=low)
        assert (v.x,v.y)==before and v.state=='jumping'
        old=v.x,v.y
        for _ in range(500):
            e.tick(.025)
            assert math.hypot(v.x-old[0],v.y-old[1])<15
            old=v.x,v.y
            if v.state==state:break
        assert v.state==state and v.y==top-SPRITE_HEIGHT
        assert v.clip_y is None


def test_identical_native_polls_do_not_restart_low_headroom_jump_or_blend():
    e,v=top_visitor();edge=[('editor:top:0',0.,1280.,32.)]
    for _ in range(240):
        before=asdict(v);e.set_window_edges(edge,low_headroom_ids={'editor:top:0'},screen_top=0);assert asdict(v)==before
        e.tick(.025)
        if v.state=='window_rear' and v.rear_blend==1:break
    assert v.state=='window_rear' and v.rear_blend==1


def test_no_native_geometry_or_rear_runtime_is_persisted():
    e,v=rear_visitor();saved=e.export()
    assert 'window_edges' not in saved and 'low_headroom_edges' not in saved and 'visitors' not in saved
    restored=Engine(state=saved)
    assert restored.low_headroom_edges==set() and not restored.window_edges and not restored.visitors


def test_maximization_during_affection_keeps_feedback_until_safe_descent():
    e,v=floor_visitor();e.set_window_edges([('same',0,1280,200)])
    e._move_to_window(v,'same');tick_until(e,lambda:v.state=='idle' and v.window_edge_id=='same')
    assert e._begin_window_peek(v);e.tick(.3);assert e.pet(v.cat_id)
    assert v.peek_after=='purring'
    before=v.x,v.y;e.set_window_edges([('same',0,1280,32)],low_headroom_ids={'same'})
    assert (v.x,v.y)==before
    tick_until(e,lambda:v.state=='purring')
    assert v.y==e.floor_y and v.window_edge_id is None


@pytest.mark.parametrize('gesture',['still','left_edge','rebased'])
def test_release_actual_rear_carry_keeps_origin_before_continuous_descent(gesture):
    e,v=rear_visitor();assert e.carry(v.cat_id)
    if gesture=='left_edge':v.x=5.
    elif gesture=='rebased':e.set_bounds(-1280,100,1280,620)
    before=v.x,v.y
    assert e.release(v.cat_id,v.x,v.y)
    assert (v.x,v.y)==before and v.jump_start_y==before[1]
    assert v.state=='jumping'
    if gesture=='left_edge':assert v.route_motion=='leaving'
    tick_until(e,lambda:v.cat_id not in e.visitors or (v.state=='idle' and v.y==e.floor_y))
    assert v.rear_blend==0


def test_ordinary_carry_retains_original_release_clamp():
    e,v=floor_visitor();assert e.carry(v.cat_id)
    v.y=-88.;assert v.rear_blend==0
    assert e.release(v.cat_id,v.x,v.y)
    assert v.y==e.bounds[1] and v.jump_start_y==e.bounds[1]
