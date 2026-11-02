"""The evidence script rejects broken posture contracts, not only good runs."""
import pytest
from backyard_visitor.model import Visitor
from scripts.check_ecology_stability import check_posture, directed_posture_checks, Run


def active():
    return Visitor('amber',300,608,state='teasing',posture_kind='crouch_rise',posture_source='play_crouch',
                   posture_target='standing',posture_duration=.2,posture_remaining=.1)


@pytest.mark.parametrize('field,value',[('posture_source','sleeping'),('posture_target','idle'),
    ('posture_duration',.3),('posture_remaining',.21),('state','jumping'),('state','carried'),
    ('ground_speed',1),('posture_remaining',-1),('posture_remaining',float('nan'))])
def test_script_rejects_invalid_or_unsafe_posture_contract(field,value):
    v=active();setattr(v,field,value)
    with pytest.raises(AssertionError):check_posture(v)


def test_complete_posture_must_clear_all_endpoint_and_duration_fields():
    v=active();v.posture_remaining=0
    with pytest.raises(AssertionError):check_posture(v)
    v.posture_kind=v.posture_source=v.posture_target='';v.posture_duration=0
    check_posture(v)


def test_all_three_directed_transitions_and_four_urgent_actions_are_observed():
    rows=directed_posture_checks()
    assert len(rows)==7 and all(row['passed'] for row in rows)
    assert len({tuple(row['endpoints']) for row in rows if 'endpoints' in row})==3
    assert {row['action'] for row in rows if 'action' in row}=={'carry','close','startle','fight'}
    assert all(row['frozen_frames']>0 for row in rows if 'frozen_frames' in row)


def test_run_check_rejects_stale_rise_session_and_accepts_active_contract():
    run=Run(4,1,False);e=run.engine;v=e.visitors['amber']
    e.set_window_edges([]);v.x=300;v.y=e.floor_y;v.state='idle'
    e.cursor_moved(v.x+500,v.y+50,0);assert e.start_teaser('amber')
    e.tick(.4);assert e.teaser_phase=='rise'
    run.check()
    v.posture_target='wrong'
    with pytest.raises(AssertionError,match='posture endpoints'):run.check()


def test_directed_escape_checks_exercise_repeated_pointer_and_close():
    from scripts.check_ecology_stability import directed_escape_checks
    rows=directed_escape_checks()
    assert len(rows)==4 and all(row['passed'] and row['frames']>1 for row in rows)
    assert {row['motion'] for row in rows}=={'fleeing','leaving'}
    assert all(row['trust_after']==(9 if row['motion']=='fleeing' else 10) for row in rows)


@pytest.mark.parametrize('value', [-1, 181, float('nan'), float('inf')])
def test_script_rejects_invalid_identity_meal_cooldown(value):
    from scripts.check_ecology_stability import check_reward_clocks
    run = Run(4, 1, False)
    run.engine.cats['amber'].meal_cooldown_remaining = value
    with pytest.raises(AssertionError, match='meal cooldown'):
        check_reward_clocks(run.engine)


@pytest.mark.parametrize('state,remaining,pending', [('jumping', 1, False), ('purring', 3.1, False),
    ('purring', 1, True), ('carried', 0, True), ('idle', 0, 1)])
def test_script_rejects_unsafe_gratitude(state, remaining, pending):
    from scripts.check_ecology_stability import check_reward_clocks
    run = Run(4, 1, False); v = run.engine.visitors['amber']
    v.state = state; v.gift_response_remaining = remaining; v.gift_response_pending = pending
    with pytest.raises(AssertionError):
        check_reward_clocks(run.engine)


def test_directed_rewards_wait_full_meal_interval_and_coalesce_short_feedback():
    from scripts.check_ecology_stability import directed_reward_checks
    rows = directed_reward_checks()
    assert len(rows['meals']) == len(rows['gift_responses']) == 2
    assert all(r['passed'] and r['interval_seconds'] >= 180 and r['meals'] == 2 for r in rows['meals'])
    assert all(r['passed'] and r['response_seconds'] == 3 and r['responses'] == 1 for r in rows['gift_responses'])


@pytest.mark.parametrize('kind,source,target,duration', [
    ('belly_roll','sleeping','play_crouch',.55),
    ('crouch_rise','play_crouch','standing',.20),
])
def test_daily_wake_accepts_only_its_actual_endpoint_contracts(kind,source,target,duration):
    v=Visitor('amber',300,608,state='waking',posture_kind=kind,posture_source=source,
              posture_target=target,posture_duration=duration,posture_remaining=duration/2)
    check_posture(v)
    for state in ('jumping','carried','fighting','leaving','fleeing'):
        v.state=state
        with pytest.raises(AssertionError,match='unsupported/urgent state'):
            check_posture(v)


def test_daily_wake_does_not_admit_the_other_valid_belly_contract():
    v=Visitor('amber',300,608,state='waking',posture_kind='belly_roll',posture_source='swatting',
              posture_target='resting',posture_duration=.55,posture_remaining=.2)
    with pytest.raises(AssertionError,match='daily waking posture endpoints'):
        check_posture(v)


def test_natural_sleep_to_wake_passes_full_run_invariants_for_both_phases():
    from backyard_visitor.model import Engine
    engine=Engine(seed=3,demo=True,visit_limit=1)
    engine.set_bounds(0,0,1600,1000);engine.calm_mode=True;engine.cats['mist'].trust=60
    engine.open_gate();engine.invite('mist');v=engine.visitors['mist']
    for _ in range(1200):
        if v.state=='sleeping':break
        engine.tick(.05)
    assert v.state=='sleeping'
    engine.feed()
    for _ in range(1000):
        if v.state=='waking':break
        engine.tick(.05)
    assert v.state=='waking' and v.posture_kind=='belly_roll'
    run=Run(4,1,False);run.engine=engine
    run.check()
    run.tick(.55)
    assert v.state=='waking' and v.posture_kind=='crouch_rise'
    run.check()
    assert run.stats['posture_frozen_tick_checks']>0


def test_waking_freeze_check_still_rejects_motion_during_a_posture(monkeypatch):
    run=Run(4,1,False);e=run.engine;v=e.visitors['amber']
    e.set_window_edges([]);v.x=300;v.y=e.floor_y;v.state='waking'
    v.posture_kind='belly_roll';v.posture_source='sleeping';v.posture_target='play_crouch'
    v.posture_duration=.55;v.posture_remaining=.3
    def broken_tick(dt):
        v.posture_remaining-=dt
        v.x+=1
    monkeypatch.setattr(e,'tick',broken_tick)
    with pytest.raises(AssertionError,match='posture moved before'):
        run.tick(.05)
