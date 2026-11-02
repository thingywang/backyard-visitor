"""Automatic resident return respects normal opportunity, gate and capacity."""
import pytest
from backyard_visitor.model import Engine
from backyard_visitor.storage import load_state, save_state


def resident_engine(seed=231, limit=2):
    engine = Engine({'version': 1, 'calm_mode': True,
        'cats': {'amber': {'trust': 100, 'discovered': True, 'visits': 1}}}, seed=seed, visit_limit=limit)
    assert engine.adopt('amber')
    return engine


def advance(engine, seconds):
    remaining = seconds
    while remaining > 1e-9:
        dt = min(.25, remaining)
        engine.tick(dt)
        remaining -= dt


def until(engine, condition, timeout):
    for _ in range(round(timeout/.25)):
        if condition():
            return
        engine.tick(.25)
        assert len(engine.visitors) <= engine.visitor_limit
    assert condition(), 'Expected resident lifecycle observation did not occur'


def test_seed231_first_automatic_arrival_is_missing_resident():
    engine = resident_engine()
    engine.open_gate()
    until(engine, lambda: bool(engine.visitors), 35)
    assert list(engine.visitors) == ['amber']
    visitor = engine.visitors['amber']
    assert visitor.state == 'peeking'
    assert visitor.x < engine.bounds[0] or visitor.x+144 > engine.bounds[0]+engine.bounds[2]
    assert engine._time == pytest.approx(27.0)


def test_missing_resident_not_throttled_as_neighbour_when_one_guest_is_present():
    engine = resident_engine(seed=0)
    engine.open_gate()
    assert engine.invite('ink') == 'ink'
    advance(engine, 31)
    assert set(engine.visitors) == {'ink', 'amber'}
    assert engine.visitors['ink'].state not in {'leaving', 'fleeing'}


def test_single_cat_capacity_keeps_guest_then_waits_for_normal_opportunity():
    engine = resident_engine(seed=0, limit=1)
    engine.open_gate()
    assert engine.invite('ink') == 'ink'
    advance(engine, 35)
    assert list(engine.visitors) == ['ink']
    until(engine, lambda: not engine.visitors, 220)
    empty_at = engine._time
    wait = engine._visit_countdown
    assert wait > 0
    advance(engine, max(0, wait-.5))
    assert not engine.visitors
    until(engine, lambda: 'amber' in engine.visitors, 2)
    assert engine._time-empty_at >= wait-.25
    assert engine.visitors['amber'].state == 'peeking'


def test_gate_closed_suppresses_return_and_reopen_still_enters_from_edge():
    engine = resident_engine()
    advance(engine, 90)
    assert not engine.visitors
    engine.open_gate()
    until(engine, lambda: 'amber' in engine.visitors, 35)
    engine.open_gate(False)
    until(engine, lambda: not engine.visitors, 70)
    advance(engine, 100)
    assert not engine.visitors and engine.resident_id == 'amber'
    engine.open_gate()
    until(engine, lambda: 'amber' in engine.visitors, 35)
    assert engine.visitors['amber'].state == 'peeking'


def test_actual_save_load_retains_adoption_but_requires_open_gate(tmp_path):
    engine = resident_engine()
    path = tmp_path/'state.json'
    save_state(path, engine.export())
    restored = Engine(load_state(path), seed=231)
    assert restored.resident_id == 'amber'
    assert not restored.gate_open and not restored.visitors
    advance(restored, 60)
    assert not restored.visitors
    restored.open_gate()
    until(restored, lambda: bool(restored.visitors), 35)
    assert list(restored.visitors) == ['amber']


@pytest.mark.parametrize('chance,expected_calls', [(.049, 1), (.05, 0), (.99, 0)])
def test_present_resident_keeps_five_percent_ceiling_for_familiar_neighbours(monkeypatch, chance, expected_calls):
    engine = resident_engine(seed=2)
    engine.relations.restore({'schema_version': 1, 'relations': {
        a: {b: {'initial': 100, 'meal_gain': 0} for b in engine.cats if b != a}
        for a in engine.cats}})
    engine.open_gate()
    engine.invite('amber')
    wait = engine._visit_countdown
    calls = []
    monkeypatch.setattr(engine, 'invite', lambda cat_id=None: calls.append(cat_id))
    monkeypatch.setattr(engine.rng, 'random', lambda: chance)
    advance(engine, wait+.1)
    assert calls == [None]*expected_calls


def test_manual_invitation_and_weighted_pool_are_not_overridden():
    engine = resident_engine()
    base_weights = {cid: 100. if cid == 'amber' else .2 for cid in engine.cats}
    assert engine.visit_weights() == base_weights
    engine.open_gate()
    assert engine.invite('ink') == 'ink'
    assert set(engine.visitors) == {'ink'}
    assert engine.visit_weights() == pytest.approx({
        cid: base*engine.relations.co_visit_factor(cid, 'ink')
        for cid, base in base_weights.items() if cid != 'ink'})


def test_sending_resident_away_does_not_shorten_existing_return_wait():
    engine = resident_engine(seed=1, limit=1)
    engine.open_gate()
    until(engine, lambda: 'amber' in engine.visitors, 35)
    visitor = engine.visitors['amber']
    until(engine, lambda: visitor.state not in {'peeking', 'entering'}, 40)
    assert engine.carry('amber')
    assert engine.release('amber', engine.bounds[0], visitor.y)
    until(engine, lambda: not engine.visitors, 70)
    empty_at, wait = engine._time, engine._visit_countdown
    assert wait >= 35
    advance(engine, wait-.5)
    assert not engine.visitors
    until(engine, lambda: 'amber' in engine.visitors, 2)
    assert engine._time-empty_at >= wait-.25
    assert engine.visitors['amber'].state == 'peeking'
