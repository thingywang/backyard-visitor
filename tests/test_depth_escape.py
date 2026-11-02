"""Panic keeps the current depth plane and runs straight out of one screen."""
from dataclasses import replace
import pytest
from backyard_visitor.depth_visit import DepthRoute,TurningDepthVisit
from backyard_visitor.model import Engine,Visitor

MASK=(((.84,.54),(.99,.54),(.99,.96),(.84,.96)),)
SCREEN=(0,0,1440,900)


def engine_at(phase='rear_traverse',offset=(0,0),start=.52):
    e=Engine(seed=7,visit_limit=1);sx,sy=offset
    e.set_bounds(sx,sy,1440,850);e.open_gate();e._visit_countdown=10000
    v=Visitor('amber',sx+1440*start-72,e.floor_y,state='idle',next_action=10000,stay_remaining=10000)
    e.visitors['amber']=v;e.cats['amber'].trust=10;e.cats['amber'].discovered=True
    screen=(sx,sy,1440,900)
    assert e.begin_depth_visit('amber',DepthRoute(polygons=MASK),screen_rect=screen)
    for _ in range(2500):
        if v.depth_pose['phase']==phase:break
        e.tick(.02)
    else:pytest.fail('Phase missing')
    return e,v,screen


def values(v):return v.x,v.y,v.depth_scale,v.depth_layer


def finish(e,v,screen):
    scale=v.depth_scale;direction=v.depth_pose['escape_direction'];rows=0
    while v.cat_id in e.visitors and rows<600:
        assert v.depth_pose['phase']=='escape' and v.depth_pose['pose']=='running'
        e.tick(.02);rows+=1
    assert v.cat_id not in e.visitors
    foot=v.x+72
    assert foot-72*scale >= screen[0]+screen[2] if direction>0 else foot+72*scale <= screen[0]
    assert len([event for event in e.drain_events() if event['kind']=='departure'])==1
    return rows*.02


@pytest.mark.parametrize('phase',['front_approach','turn','recede','rear_traverse','hold','rear_return','advance','front_return'])
def test_every_stage_immediately_runs_on_the_same_depth_plane(phase):
    e,v,screen=engine_at(phase)
    original=values(v);run=v.run_phase
    assert e.startle('amber')
    assert values(v)==original
    snap=v.depth_pose
    assert snap['escaping'] and snap['pose']=='running' and snap['phase']=='escape'
    assert snap['turn_progress'] is None and not snap['queued_return']
    e.tick(.1)
    assert abs(v.x-original[0])==pytest.approx(21)
    assert values(v)[1:]==original[1:]
    assert v.run_phase==pytest.approx((run+21/(100*original[2]))%1)
    assert finish(e,v,screen)<4


@pytest.mark.parametrize('offset',[(-1920,80),(0,0),(1440,-900)])
@pytest.mark.parametrize('start,direction',[(.25,-1),(.75,1)])
def test_nearest_edge_and_scaled_canvas_exit_on_translated_screen(offset,start,direction):
    e,v,screen=engine_at('front_approach',offset,start)
    assert e.startle('amber');assert v.depth_pose['escape_direction']==direction
    finish(e,v,screen)
    assert v.depth_trip is v.depth_pose is v.depth_screen is v.depth_viewport is None


def test_pause_dt_splitting_and_terminal_large_step():
    r=DepthRoute(polygons=MASK)
    a,b=TurningDepthVisit(r),TurningDepthVisit(r)
    a.step(5);b.step(5)
    assert a.begin_escape(SCREEN,SCREEN,run_phase=.3)
    assert b.begin_escape(SCREEN,SCREEN,run_phase=.3)
    a.step(.7)
    for _ in range(7):b.step(.1)
    for key in ('foot','scale','run_phase','elapsed'):
        assert a.snapshot()[key]==pytest.approx(b.snapshot()[key])
    a.set_paused(True);before=a.snapshot();a.step(100);assert a.snapshot()==before
    a.set_paused(False);done=a.step(100)
    assert done['done'] and done['phase']=='escape'
    end=a.snapshot();a.step(100);assert a.snapshot()==end
    assert done['foot'][0]*1440-72*done['scale']==pytest.approx(1441)


def test_calls_close_food_and_repeated_startle_cannot_downgrade_escape():
    e,v,screen=engine_at('hold');assert e.startle('amber')
    e.cats['amber'].nickname='测试访客'
    before=v.depth_trip.snapshot();trust=e.cats['amber'].trust
    assert not e.startle('amber');assert e.cats['amber'].trust==trust
    assert not e.pet('amber');assert not e.carry('amber');assert not e.release('amber',200,200)
    assert not e.request_depth_return('amber','desktop_obscured')
    assert not e.start_teaser('amber')
    e.call_cat('amber');e.feed();e.open_gate(False);e._leave(v)
    assert v.depth_trip.snapshot()==before and v.depth_pending_action=='fleeing'
    finish(e,v,screen)


def test_resize_migration_keeps_direction_y_scale_and_retargets_new_edge():
    e,v,screen=engine_at('rear_traverse');e.startle('amber');e.tick(.1)
    old=values(v);phase=v.run_phase;elapsed=v.depth_trip.elapsed
    e.set_bounds(-1920,80,1200,780)
    assert values(v)==old
    new=(-1920,80,1200,900)
    e.set_depth_screen_rect(new)
    assert v.x==pytest.approx(old[0]-1920) and v.y==pytest.approx(old[1]+80)
    assert v.depth_scale==old[2] and v.depth_layer==old[3] and v.run_phase==phase
    assert v.depth_trip.elapsed==elapsed and v.depth_pose['escape_direction']==1
    snapshot=v.depth_trip.snapshot();e.set_depth_screen_rect(new);assert v.depth_trip.snapshot()==snapshot
    # New screen is 1200px, while the route mapping intentionally stays1440px.
    assert v.depth_screen[2]==1440 and v.depth_viewport[2]==1200
    finish(e,v,new)


def test_resize_before_startle_uses_latest_real_viewport_not_locked_mapping():
    e,v,_=engine_at('recede')
    e.set_bounds(0,0,1100,850);e.set_depth_screen_rect((0,0,1100,900))
    assert v.depth_screen[2]==1440
    e.startle('amber')
    finish(e,v,(0,0,1100,900))


def test_shrink_already_fully_outside_removes_without_reposition_or_turn():
    e,v,_=engine_at('rear_traverse');e.startle('amber')
    before=values(v)
    e.set_bounds(0,0,500,850);e.set_depth_screen_rect((0,0,500,900))
    assert values(v)==before and v.depth_pose['done']
    e.tick(.01)
    assert not e.visitors and (v.x,v.y)==before[:2]


def test_front_left_escape_is_still_running_when_recalled_by_screen_guard():
    e,v,screen=engine_at('front_approach',start=.25);e.startle('amber')
    assert v.depth_pose['escape_direction']==-1
    e.set_bounds(0,20,1440,830);e.set_depth_screen_rect(screen)
    assert not e.request_depth_return('amber','screen_changed')
    assert v.depth_pose['pose']=='running'
    finish(e,v,screen)
