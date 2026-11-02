"""Real Engine visits exercise accepted ownership, travel, naps and furniture edits."""
from dataclasses import asdict
import math
import pytest
from backyard_visitor.model import Engine
from backyard_visitor.resident_bed import (
    BED_WIDTH, BED_HEIGHT, BED_SLEEP_FRACTIONS, BED_CREDIT_LIMIT, at_bed,
)


def advance(engine, seconds, step=.05):
    while seconds > 1e-9:
        dt = min(seconds, step)
        engine.tick(dt)
        seconds -= dt


def until(engine, predicate, seconds=360, step=.05):
    for _ in range(round(seconds/step)):
        if predicate():
            return
        engine.tick(step)
    assert predicate(), "Natural resident sequence did not reach expected state"


def resident(cat_id='mist', seed=3, x=800, calm=True):
    engine = Engine(seed=seed, visit_limit=1)
    engine.calm_mode = calm
    engine.open_gate()
    assert engine.invite(cat_id) == cat_id
    engine.cats[cat_id].trust = 100
    assert engine.adopt(cat_id)
    assert engine.place_bed(cat_id, x)
    return engine, engine.visitors[cat_id]


def napping(cat_id='mist'):
    engine, visitor = resident(cat_id)
    until(engine, lambda: visitor.bed_nap and visitor.state == 'sleeping')
    return engine, visitor


def test_bed_requires_actual_acceptance_not_invitation_or_high_trust():
    engine = Engine(seed=1)
    engine.cats['mist'].trust = 100
    assert not engine.place_bed('mist')
    engine.open_gate(); engine.invite('mist')
    assert not engine.place_bed('mist')
    assert engine.adopt('mist')
    assert engine.place_bed('mist')
    assert not engine.place_bed('amber')
    assert not engine.place_bed(['mist'])
    assert engine.bed.cat_id == engine.resident_id == 'mist'


@pytest.mark.parametrize('value', [True, False, math.nan, math.inf, -math.inf, '800', None, [], {}])
def test_invalid_move_does_not_displace_furniture_or_cat(value):
    engine, visitor = resident()
    before = asdict(visitor), asdict(engine.bed)
    assert not engine.move_bed(value)
    assert (asdict(visitor), asdict(engine.bed)) == before


def test_natural_arrival_walks_to_bed_then_lowers_and_lies_with_fixed_feet():
    engine, visitor = resident()
    states, poses = set(), []
    previous = visitor.x, visitor.y
    until(engine, lambda: visitor.state != 'peeking')
    for _ in range(9000):
        states.add(visitor.state)
        if visitor.state == 'bed_laying':
            poses.append(visitor.posture_kind)
            assert at_bed(engine, visitor)
            before = visitor.x, visitor.y, visitor.gait_phase, visitor.run_phase
            engine.tick(.05)
            assert (visitor.x, visitor.y, visitor.gait_phase, visitor.run_phase) == before
        else:
            engine.tick(.05)
        assert math.hypot(visitor.x-previous[0], visitor.y-previous[1]) < 25
        previous = visitor.x, visitor.y
        if visitor.bed_nap:
            break
    assert {'entering', 'walking', 'bed_laying'} <= states
    assert 'crouch_lower' in poses and 'belly_lie' in poses
    assert visitor.bed_nap and visitor.state == 'sleeping'
    assert at_bed(engine, visitor)
    assert engine.bed.nap_elapsed == 0


@pytest.mark.parametrize('cat_id', ['amber', 'ink', 'mist'])
@pytest.mark.parametrize('seed', [3, 11])
@pytest.mark.parametrize('step', [.5, 1/24])
def test_natural_half_hour_targets_time_share_not_per_tick_probability(cat_id, seed, step):
    engine, visitor = resident(cat_id, seed, calm=False)
    advance(engine, 1800, step)
    bed = engine.bed
    observed = bed.sleep_elapsed/bed.active_elapsed
    target = BED_SLEEP_FRACTIONS[engine.cats[cat_id].personality]
    assert observed == pytest.approx(target, abs=.045)
    assert bed.active_elapsed > 1700 and bed.sleep_elapsed > 400
    assert abs(bed.credit) <= BED_CREDIT_LIMIT
    assert visitor.state != 'bed_laying' or visitor.posture_remaining > 0


def test_zero_ticks_pause_budget_sleep_clock_and_pose():
    engine, visitor = napping()
    before = asdict(engine.bed), asdict(visitor)
    for _ in range(30):
        engine.tick(0)
    assert (asdict(engine.bed), asdict(visitor)) == before


def test_new_session_preserves_bed_identity_position_not_runtime_or_gate():
    engine, visitor = napping()
    engine.name_cat(visitor.cat_id, '小团')
    saved = engine.export()
    assert saved['resident_bed'] == {'cat_id': 'mist', 'x': engine.bed.x}
    restored = Engine(saved, seed=2)
    assert restored.bed.cat_id == 'mist' and restored.bed.x == engine.bed.x
    assert restored.bed.sleep_fraction == .60
    assert restored.bed.credit == restored.bed.active_elapsed == restored.bed.sleep_elapsed == 0
    assert not restored.gate_open and not restored.visitors
    assert restored.cats['mist'].nickname == '小团'


@pytest.mark.parametrize('row', [None, [], {}, {'cat_id':'amber','x':200}, {'cat_id':'missing','x':200},
                                {'cat_id':[],'x':200}, {'cat_id':'mist','x':True}, {'cat_id':'mist','x':math.inf}])
def test_invalid_saved_bed_never_unlocks_or_reassigns_it(row):
    engine, _ = resident()
    saved = engine.export(); saved['resident_bed'] = row
    assert Engine(saved).bed is None


def test_legacy_without_bed_and_unadopted_save_remain_without_bed():
    engine, _ = resident(); saved = engine.export()
    del saved['resident_bed']
    assert Engine(saved).bed is None
    saved = engine.export(); saved['cats']['mist']['adopted'] = False
    assert Engine(saved).bed is None


def test_negative_screen_rebases_and_clamps_bed_without_moving_relative_cat():
    engine, visitor = napping()
    before = visitor.x, visitor.y
    engine.set_bounds(-1920, -100, 1920, 1080)
    assert engine.bed_y == -100+1080-BED_HEIGHT/2
    assert -1920+BED_WIDTH/2 <= engine.bed.x <= -BED_WIDTH/2
    assert visitor.x == pytest.approx(before[0]-1920)
    assert visitor.y == engine.floor_y
    assert not visitor.bed_nap
    advance(engine, 1)
    assert visitor.state != 'bed_laying'


def test_bed_move_during_sleep_wakes_in_place_without_teleport():
    engine, visitor = napping()
    before = visitor.x, visitor.y
    assert engine.move_bed(200)
    assert (visitor.x, visitor.y) == before
    assert visitor.state == 'waking' and visitor.posture_kind == 'belly_roll'
    assert not visitor.bed_nap
    until(engine, lambda: visitor.bed_nap, seconds=240)
    assert at_bed(engine, visitor) and visitor.x == pytest.approx(200-72)


@pytest.mark.parametrize('action', ['move','remove'])
@pytest.mark.parametrize('kind', ['crouch_lower','belly_lie'])
def test_furniture_edit_mid_lie_finishes_at_old_spot_then_wakes(action, kind):
    engine, visitor = resident()
    until(engine, lambda: visitor.state == 'bed_laying' and visitor.posture_kind == kind)
    before = visitor.x, visitor.y
    if action == 'move':
        assert engine.move_bed(200)
    else:
        assert engine.remove_bed()
    assert (visitor.x, visitor.y) == before
    assert visitor.posture_kind == kind
    until(engine, lambda: visitor.state == 'waking', seconds=2)
    assert (visitor.x, visitor.y) == before
    assert not visitor.bed_nap


def test_move_mid_route_rejects_old_arrival_instead_of_sleeping_in_empty_bed():
    engine, visitor = resident(x=1100)
    until(engine, lambda: visitor.bed_route_revision is not None and visitor.moving)
    original = visitor.x, visitor.y
    assert engine.move_bed(200)
    assert (visitor.x, visitor.y) == original
    assert visitor.bed_route_revision is None and visitor.arrival_state != 'bed'
    until(engine, lambda: visitor.bed_nap, seconds=360)
    assert at_bed(engine, visitor) and visitor.x == pytest.approx(128)


@pytest.mark.parametrize('stage', ['bed_laying', 'sleeping'])
@pytest.mark.parametrize('action', ['carry','close'])
def test_urgent_actions_cancel_bed_intent_and_pose(stage, action):
    engine, visitor = resident()
    until(engine, lambda: visitor.state == stage and (stage!='sleeping' or visitor.bed_nap))
    if action == 'carry':
        assert engine.carry(visitor.cat_id)
        assert visitor.state == 'carried'
    else:
        engine.open_gate(False)
        assert visitor.state in {'leaving','jumping','walking'}
    assert not visitor.bed_nap and visitor.bed_route_revision is None
    assert not visitor.posture_remaining
    if action == 'close':
        until(engine, lambda: not engine.visitors, seconds=30)
        budget = asdict(engine.bed)
        advance(engine, 20)
        assert asdict(engine.bed) == budget


def test_affection_keeps_nap_pose_and_accumulates_same_budget():
    engine, visitor = napping()
    before = engine.bed.nap_elapsed, engine.bed.sleep_elapsed
    assert engine.pet(visitor.cat_id)
    assert visitor.bed_nap and visitor.sleeping_purr
    advance(engine, 4.05)
    assert visitor.state == 'sleeping' and visitor.bed_nap and visitor.on_back
    assert engine.bed.nap_elapsed == pytest.approx(before[0]+4.05)
    assert engine.bed.sleep_elapsed == pytest.approx(before[1]+4.05)


def test_explicit_play_interrupts_bed_nap_and_keeps_fifteen_second_limit():
    engine, visitor = napping()
    assert engine.start_teaser(visitor.cat_id)
    assert not visitor.bed_nap and visitor.posture_kind == 'belly_roll'
    advance(engine, 15.05)
    assert engine.active_teaser_id is None
    assert visitor.state in {'resting','walking','jumping'}


def test_forced_holding_cannot_build_unbounded_sleep_debt():
    engine, visitor = napping()
    assert engine.carry(visitor.cat_id)
    advance(engine, 1200, .5)
    assert engine.bed.credit == BED_CREDIT_LIMIT
    assert visitor.state == 'carried'


def window_route():
    engine = Engine(seed=1, visit_limit=1)
    engine.calm_mode = True
    engine.open_gate(); engine.invite('ink')
    engine.cats['ink'].trust = 100; assert engine.adopt('ink')
    engine.set_window_edges([('low', 150, 1100, 450)])
    visitor = engine.visitors['ink']
    until(engine, lambda: visitor.window_edge_id == 'low' and visitor.state == 'idle')
    assert engine.place_bed('ink', 1100)
    until(engine, lambda: visitor.bed_route_revision is not None)
    assert visitor.state == 'jumping' and visitor.y < engine.floor_y
    return engine, visitor


def test_natural_window_resident_jumps_short_then_walks_to_floor_bed():
    engine, visitor = window_route()
    assert abs(visitor.target_x-visitor.jump_start_x) <= 64
    saw_finish = False
    for _ in range(1200):
        engine.tick(.05)
        if visitor.route_stage == 'finish':
            saw_finish = True
            assert visitor.y == engine.floor_y and visitor.state == 'walking'
        if visitor.bed_nap:
            break
    assert saw_finish and visitor.bed_nap and at_bed(engine, visitor)
    assert visitor.window_edge_id is None and visitor.window_target_id is None


@pytest.mark.parametrize('action', ['move_bed','remove_support'])
def test_bed_or_window_edit_during_return_jump_keeps_actual_arc_position(action):
    engine, visitor = window_route()
    advance(engine, .35)
    before = visitor.x, visitor.y
    if action == 'move_bed':
        assert engine.move_bed(200)
    else:
        engine.set_window_edges([])
    assert (visitor.x, visitor.y) == before
    until(engine, lambda: visitor.bed_nap, seconds=360)
    assert at_bed(engine, visitor)
    assert visitor.x == pytest.approx(engine.bed.x-72)


@pytest.mark.parametrize('kind', ['crouch_lower','belly_lie'])
def test_screen_change_mid_lie_does_not_leave_an_orphan_posture(kind):
    engine, visitor = resident()
    until(engine, lambda: visitor.state == 'bed_laying' and visitor.posture_kind == kind)
    engine.set_bounds(-1280, -200, 1280, 720)
    before = visitor.x, visitor.y
    engine.tick(.05)
    assert (visitor.x, visitor.y) == before
    assert visitor.state != 'bed_laying'
    until(engine, lambda: visitor.bed_nap, seconds=360)
    assert at_bed(engine, visitor)
