"""Public Engine actions cancel obsolete turns when play starts or waits."""
import pytest
from backyard_visitor.model import Engine


def natural(seed, condition):
    e=Engine(seed=seed,visit_limit=1)
    e.open_gate();assert e.invite('amber')
    v=e.visitors['amber']
    for _ in range(15000):
        e.tick(.01)
        if condition(e,v):return e,v
        assert 'amber' in e.visitors, 'Natural visitor left before the requested phase'
    pytest.fail('Natural fixture did not reach phase')


@pytest.mark.parametrize('seed',[0,3,29])
def test_start_play_cancels_natural_walk_or_run_turn_without_finishing_old_facing(seed):
    e,v=natural(seed,lambda e,v:v.turn_remaining>0)
    assert v.state in {'walking','running'} and v.turn_target_facing != v.facing
    pose=(v.x,v.y,v.facing,v.gait_phase,v.run_phase)
    assert e.start_teaser(v.cat_id)
    assert (v.x,v.y,v.facing,v.gait_phase,v.run_phase)==pose
    assert v.turn_remaining==0 and v.turn_target_facing is None and v.ground_speed==0
    for _ in range(120):
        e.tick(.01)
        assert e.teaser_phase=='watch' and v.turn_remaining==0
    assert e._teaser.remaining==pytest.approx(13.8)
    assert (v.x,v.y,v.facing,v.gait_phase,v.run_phase)==pose


@pytest.mark.parametrize('seed',[0,3])
@pytest.mark.parametrize('bounds',[None,(-900,-100,900,700)],ids=['same-bounds','changed-bounds'])
def test_lost_cursor_during_real_chase_turn_cancels_turn_without_resetting_session(seed,bounds):
    e,v=natural(seed,lambda e,v:v.state=='idle')
    left,_,width,_=e.bounds
    cursor_x=left+80 if v.facing>0 else left+width-80
    e.cursor_moved(cursor_x,e.floor_y+40,0)
    assert e.start_teaser(v.cat_id)
    for _ in range(300):
        e.tick(.01)
        if v.turn_remaining>0:break
    assert e.teaser_phase=='chase' and 0<v.turn_remaining<=.1
    remaining=e._teaser.remaining
    e.set_bounds(*(bounds or e.bounds))  # Public geometry update clears stale cursor.
    assert e._cursor is None
    pose=(v.x,v.y,v.facing,v.gait_phase,v.run_phase)
    for _ in range(120):
        e.tick(.01)
        assert e.teaser_phase=='watch'
        assert v.turn_remaining==0 and v.turn_target_facing is None and v.ground_speed==0
        assert (v.x,v.y,v.facing,v.gait_phase,v.run_phase)==pose
    assert e._teaser.remaining==pytest.approx(remaining-1.2)
    # Returning pointer gets a fresh chase decision; waiting did not complete
    # the obsolete turn or prolong the fixed fifteen-second session.
    left,_,width,_=e.bounds
    e.cursor_moved(left+80 if v.facing>0 else left+width-80,e.floor_y+40,0)
    for _ in range(200):
        e.tick(.01)
        if v.facing!=pose[2]:break
    assert v.facing==-pose[2] and e._teaser.remaining<remaining-1.2
    for _ in range(1600):
        e.tick(.01)
        if e.active_teaser_id is None:break
    assert e.active_teaser_id is None
    assert sum(event['kind']=='play_finished' for event in e.drain_events())==1
