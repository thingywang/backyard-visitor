"""A one-time first meeting is prompt; ordinary neighbour cadence stays quiet."""
import json

import pytest

from backyard_visitor.model import Engine, SPRITE_HEIGHT, SPRITE_WIDTH, WELCOME_VISIT_DELAY
from backyard_visitor.storage import load_state, save_state


def advance(engine, seconds):
    remaining = seconds
    while remaining > 1e-9:
        dt = min(.05, remaining)
        engine.tick(dt)
        remaining -= dt


def seen_state():
    return {'version': 1, 'cats': {'amber': {'discovered': True, 'visits': 1}}}


@pytest.mark.parametrize('seed', range(24))
@pytest.mark.parametrize('bounds', [(0, 0, 1280, 720), (-900, 25, 720, 1240), (0, 0, 144, 112)])
def test_fresh_garden_has_a_continuous_fully_visible_cat_before_thirty_seconds(seed, bounds):
    e = Engine(seed=seed)
    e.set_bounds(*bounds)
    e.open_gate()
    advance(e, 19.9)
    assert not e.visitors
    elapsed = 19.9
    first = None
    while elapsed < 29.5:
        advance(e, .05); elapsed += .05
        if e.visitors:
            v = next(iter(e.visitors.values()))
            if first is None:
                first = (elapsed, v.x, v.y, v.state)
            left, top, width, height = e.bounds
            if (left <= v.x <= left+width-SPRITE_WIDTH
                    and top <= v.y <= top+height-SPRITE_HEIGHT):
                break
    assert first is not None and first[0] <= 20.1
    # The real model starts partially beyond an edge, then walks in. Checking
    # the full canvas inside is stronger than merely seeing a Visitor object.
    assert first[3] == 'peeking'
    assert first[1] < bounds[0] or first[1]+SPRITE_WIDTH > bounds[0]+bounds[2]
    assert elapsed < 30
    assert left <= v.x <= left+width-SPRITE_WIDTH
    assert top <= v.y <= top+height-SPRITE_HEIGHT
    assert e.export()['welcome_visit_remaining'] is None
    assert e._visit_countdown > 220  # No second fast opportunity.


def test_closed_gate_does_not_consume_welcome_or_summon_a_cat():
    e = Engine(seed=1)
    advance(e, 100)
    assert not e.visitors and e.export()['welcome_visit_remaining'] == 20
    assert e.invite('amber') is None
    e.open_gate(); advance(e, 7)
    assert e.export()['welcome_visit_remaining'] == pytest.approx(13)
    e.open_gate(False); advance(e, 100)
    assert not e.visitors and e.export()['welcome_visit_remaining'] == pytest.approx(13)
    e.open_gate(); advance(e, 12.9)
    assert not e.visitors
    advance(e, .2)
    assert len(e.visitors) == 1


def test_idempotent_open_pause_and_restart_preserve_remaining_welcome(tmp_path):
    e = Engine(seed=8); e.open_gate(); advance(e, 8.5)
    remaining = e.export()['welcome_visit_remaining']
    assert remaining == pytest.approx(11.5)
    assert not e.open_gate()
    e.tick(0)
    assert e.export()['welcome_visit_remaining'] == remaining
    path = tmp_path/'state.json'; save_state(path, e.export())
    loaded = load_state(path)
    e = Engine(loaded, seed=9)
    assert not e.gate_open and not e.visitors
    assert e.export()['welcome_visit_remaining'] == pytest.approx(remaining)
    advance(e, 60)
    e.open_gate(); advance(e, 11.4)
    assert not e.visitors
    advance(e, .2)
    assert len(e.visitors) == 1
    resumed = Engine(e.export(), seed=10)
    assert resumed.export()['welcome_visit_remaining'] is None
    resumed.open_gate(); advance(resumed, 150)
    assert not resumed.visitors


def test_successful_manual_invite_consumes_welcome_but_failure_does_not():
    e = Engine(seed=7); e.open_gate(); advance(e, 3)
    remaining = e.export()['welcome_visit_remaining']
    assert e.invite('missing') is None
    assert e.export()['welcome_visit_remaining'] == remaining
    assert e.invite('mist') == 'mist'
    assert e.export()['welcome_visit_remaining'] is None
    assert e._visit_countdown >= 240
    advance(e, 35)
    assert set(e.visitors) == {'mist'}


@pytest.mark.parametrize('food', [0, 3])
def test_first_cat_leaves_then_ordinary_cadence_has_no_new_thirty_second_guarantee(food):
    e = Engine({'food': food}, seed=19); e.open_gate(); advance(e, 20.1)
    assert len(e.visitors) == 1
    e.open_gate(False)
    for _ in range(1000):
        if not e.visitors: break
        e.tick(.05)
    assert not e.visitors
    e.open_gate(); advance(e, 100)
    assert not e.visitors
    assert e.export()['welcome_visit_remaining'] is None


@pytest.mark.parametrize('state', [seen_state(), {'cats': {'mist': {'visits': 2}}},
                                  {'welcome_visit_remaining': None}])
def test_completed_or_preexisting_meetings_cannot_receive_a_new_welcome(state):
    e = Engine(state, seed=4)
    assert e.export()['welcome_visit_remaining'] is None
    e.open_gate(); advance(e, 150)
    assert not e.visitors


def test_discovered_cat_overrides_a_stale_pending_save():
    state = seen_state(); state['welcome_visit_remaining'] = 0
    e = Engine(state, seed=4); e.open_gate(); advance(e, 20.1)
    assert not e.visitors and e.export()['welcome_visit_remaining'] is None


@pytest.mark.parametrize('bad', [True, False, -1, 21, '2', [], {}, float('inf'), float('nan'), 10**1000])
def test_invalid_welcome_duration_cannot_force_an_instant_or_unbounded_wait(bad):
    e = Engine({'welcome_visit_remaining': bad}, seed=1)
    assert e.export()['welcome_visit_remaining'] == WELCOME_VISIT_DELAY
    e.open_gate(); advance(e, 19.9)
    assert not e.visitors
    advance(e, .2)
    assert len(e.visitors) == 1
    json.dumps(e.export(), allow_nan=False)


def test_unseen_legacy_save_gets_one_meeting_without_inventing_discovery():
    e = Engine({'version': 1, 'food': 1}, seed=2)
    assert e.discovered_cat_ids() == ()
    e.open_gate(); advance(e, 20.1)
    assert len(e.visitors) == len(e.discovered_cat_ids()) == 1
    assert e.export()['food'] == 1  # A meeting alone does not grant or consume a meal.


def test_demo_keeps_its_existing_prompt_timing_in_isolated_progress():
    e = Engine(seed=4, demo=True)
    assert e.export()['welcome_visit_remaining'] is None
    e.open_gate(); advance(e, 5)
    assert e.visitors
