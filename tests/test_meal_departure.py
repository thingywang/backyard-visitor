"""A naturally ending visit must finish a committed meal, not eat its reward.

Only normal-mode, naturally arriving visitors are used. The test never edits
stay timers, food cooldowns, trust, or motion state to reach the boundary.
"""
import pytest

from backyard_visitor.model import Engine, SPRITE_WIDTH


def late_idle(seed=4):
    engine = Engine(seed=seed, visit_limit=1)
    engine.calm_mode = True
    engine.open_gate()
    assert engine.invite('amber') == 'amber'
    visitor = engine.visitors['amber']
    for _ in range(1000):
        if visitor.state == 'idle' and 0 < visitor.stay_remaining < 3:
            engine.drain_events()
            return engine, visitor
        engine.tick(.25)
        assert 'amber' in engine.visitors
    pytest.fail('Normal visitor did not reach the late idle boundary')


def committed_late_meal(seed=4):
    engine, visitor = late_idle(seed)
    engine.set_bowl(visitor.x+SPRITE_WIDTH/2, visitor.y+97)
    assert engine.feed()
    for _ in range(12):
        engine.tick(.25)
        if visitor.state == 'eating':
            assert engine.food == 0 and visitor.has_eaten
            assert 0 < visitor.stay_remaining < 3
            assert engine.cats['amber'].trust == 0
            return engine, visitor
    pytest.fail('The near bowl did not start a meal before natural expiry')


def advance(engine, seconds, step=.25):
    remaining = seconds
    while remaining > 1e-9:
        dt = min(step, remaining)
        engine.tick(dt)
        remaining -= dt


@pytest.mark.parametrize('seed', [4, 7])
@pytest.mark.parametrize('step', [.05, .25])
def test_late_food_finishes_meal_grooms_and_extends_normal_visit(seed, step):
    engine, visitor = committed_late_meal(seed)
    advance(engine, 4.25, step)
    assert engine.visitors.get('amber') is visitor
    assert visitor.state == 'grooming'
    assert engine.cats['amber'].trust == 22
    assert visitor.stay_remaining > 50
    assert engine.food == 0
    advance(engine, 5.25, step)
    assert engine.visitors.get('amber') is visitor
    assert visitor.state != 'grooming'
    kinds = [event['kind'] for event in engine.drain_events()]
    assert kinds.count('eating') == kinds.count('trust') == 1
    assert 'departure' not in kinds
    assert engine.cats['amber'].trust == 22
    assert engine.cats['amber'].meal_cooldown_remaining == pytest.approx(170.5)


@pytest.mark.parametrize('action', ['close', 'startle', 'carry'])
def test_explicit_control_still_interrupts_committed_late_meal(action):
    engine, visitor = committed_late_meal()
    if action == 'close':
        assert engine.open_gate(False)
    elif action == 'startle':
        assert engine.startle('amber')
    else:
        assert engine.carry('amber')
    assert visitor.state != 'eating'
    advance(engine, 4.25)
    assert engine.food == 0 and engine.cats['amber'].trust == 0
    assert engine.cats['amber'].meal_cooldown_remaining == pytest.approx(175.75)
    assert not any(event['kind'] == 'trust' for event in engine.drain_events())
    if action == 'carry':
        assert visitor.state == 'carried'
    else:
        assert 'amber' not in engine.visitors or visitor.route_motion in {'leaving', 'fleeing'} or visitor.state in {'leaving', 'fleeing'}


def test_natural_visit_without_food_still_expires():
    engine, visitor = late_idle()
    advance(engine, 4)
    assert 'amber' not in engine.visitors or visitor.state == 'leaving' or visitor.route_motion == 'leaving'
    assert engine.food == 0 and engine.cats['amber'].trust == 0
