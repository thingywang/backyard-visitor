"""Mouse gifts own a small clock even after the cats and gate are inactive."""
import math

import pytest

from backyard_visitor.model import (Engine, Gift, GIFT_SIZE, MOUSE_ESCAPE_SPEED,
                                   MOUSE_WAIT_MIN, MOUSE_WAIT_MAX)


def advance(engine, seconds, step=0.05):
    remaining = seconds
    while remaining > 1e-9:
        delta = min(step, remaining)
        engine.tick(delta)
        remaining -= delta


def mouse(engine, x=500, y=550):
    engine._drop("toy_mouse", x, y, "amber")
    return engine.gifts[-1]


def test_mouse_waits_without_opening_gate_or_moving_static_gifts():
    engine = Engine(seed=14)
    gift = mouse(engine)
    feather = Gift("feather", "feather", 600, 600, "amber")
    engine.gifts.append(feather)
    assert MOUSE_WAIT_MIN <= gift.wait_remaining <= MOUSE_WAIT_MAX
    assert not engine.gate_open and not engine.visitors
    assert engine.has_gift_activity and not engine.moving
    initial = (gift.x, gift.y)
    advance(engine, gift.wait_remaining - 0.01)
    assert gift.state == "idle" and (gift.x, gift.y) == initial
    engine.tick(0.02)
    assert gift.state == "escaping" and engine.moving
    assert gift.escape_direction in (-1, 1)
    assert math.isclose(abs(gift.x - initial[0]), 0.01 * MOUSE_ESCAPE_SPEED, abs_tol=1e-7)
    assert (feather.x, feather.y, feather.state) == (600, 600, "idle")


def test_countdown_crossing_moves_only_the_remainder_of_the_tick():
    engine = Engine(seed=7)
    gift = mouse(engine)
    engine.rng.choice = lambda values: 1
    gift.wait_remaining = 0.1
    engine.tick(0.25)
    assert gift.x == pytest.approx(500 + MOUSE_ESCAPE_SPEED * 0.15)
    assert gift.wait_remaining == 0 and gift.state == "escaping"


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("left,top", [(0, 0), (-1440, -300)])
def test_mouse_runs_continuously_until_completely_offscreen(direction, left, top):
    engine = Engine(seed=4)
    engine.set_bounds(left, top, 1440, 900)
    gift = mouse(engine, left + 650, top + 600)
    gift.wait_remaining = 0
    engine.rng.choice = lambda values: direction
    engine.drain_events()
    previous_x = gift.x
    for _ in range(200):
        engine.tick(0.02)
        assert gift.x - previous_x == pytest.approx(direction * MOUSE_ESCAPE_SPEED * 0.02)
        assert gift.y == top + 600
        fully_outside = gift.x + GIFT_SIZE <= left if direction < 0 else gift.x >= left + 1440
        assert (gift not in engine.gifts) == fully_outside
        previous_x = gift.x
        if fully_outside:
            break
    assert gift not in engine.gifts
    assert not engine.has_gift_activity and not engine.moving
    events = engine.drain_events()
    assert [event["kind"] for event in events] == ["gift_escaped"]
    assert events[0]["cat_id"] == "amber"
    assert engine.cats["amber"].trust == 0
    advance(engine, 30)
    assert not engine.drain_events()
    assert not engine.export()["gifts"]


@pytest.mark.parametrize("direction", [-1, 1])
def test_partially_visible_mouse_is_not_removed(direction):
    engine = Engine()
    gift = mouse(engine, 0 if direction < 0 else 1280 - GIFT_SIZE)
    gift.wait_remaining = 0
    engine.rng.choice = lambda values: direction
    engine.tick(0.1)
    assert gift in engine.gifts
    assert (gift.x < 0 and gift.x + GIFT_SIZE > 0) if direction < 0 else (gift.x < 1280 < gift.x + GIFT_SIZE)
    engine.tick(0.1)
    assert gift not in engine.gifts


def test_holding_pauses_wait_and_release_starts_a_fresh_delay():
    engine = Engine(seed=22)
    gift = mouse(engine)
    advance(engine, 3)
    before = (gift.x, gift.y, gift.wait_remaining)
    assert engine.set_gift_held(gift.id, True)
    assert not engine.set_gift_held(gift.id, True)
    advance(engine, 40)
    assert (gift.x, gift.y, gift.wait_remaining) == before
    assert gift.state == "held" and not engine.has_gift_activity and not engine.moving
    gift.x, gift.y = 700, 300  # The UI owns direct drag positioning.
    assert engine.set_gift_held(gift.id, False)
    delay = gift.wait_remaining
    assert MOUSE_WAIT_MIN <= delay <= MOUSE_WAIT_MAX
    assert gift.state == "idle" and gift.escape_direction == 0
    assert engine.has_gift_activity and not engine.moving
    assert not engine.set_gift_held(gift.id, False)
    assert gift.wait_remaining == delay
    advance(engine, 7)
    assert (gift.x, gift.y) == (700, 300)


def test_dragging_a_running_mouse_cancels_escape_without_jumping():
    engine = Engine(seed=22)
    gift = mouse(engine)
    gift.wait_remaining = 0
    engine.tick(0.2)
    old = (gift.x, gift.y)
    assert engine.set_gift_held(gift.id, True)
    assert (gift.x, gift.y) == old and gift.escape_direction == 0
    advance(engine, 20)
    assert (gift.x, gift.y) == old
    assert engine.set_gift_held(gift.id, False)
    advance(engine, 7.9)
    assert gift.state == "idle" and (gift.x, gift.y) == old


@pytest.mark.parametrize("action", ["collect", "discard"])
def test_collecting_or_discarding_cancels_a_running_mouse(action):
    engine = Engine(seed=9)
    gift = mouse(engine)
    gift.wait_remaining = 0
    engine.tick(0.2)
    engine.drain_events()
    operation = engine.collect_gift if action == "collect" else engine.discard_gift
    assert operation(gift.id)
    assert not engine.set_gift_held(gift.id, False)
    advance(engine, 100)
    assert not engine.has_gift_activity and not engine.moving
    assert not any(event["kind"] == "gift_escaped" for event in engine.drain_events())
    assert engine.cats["amber"].trust == (3 if action == "collect" else 0)


def test_multiple_mouse_waits_and_directions_are_independent():
    engine = Engine(seed=11)
    gifts = [mouse(engine, 500 + index * 50) for index in range(4)]
    waits = [gift.wait_remaining for gift in gifts]
    assert all(MOUSE_WAIT_MIN <= value <= MOUSE_WAIT_MAX for value in waits)
    assert len(set(waits)) == len(waits)
    directions = iter([-1, 1, -1, 1])
    engine.rng.choice = lambda values: next(directions)
    for gift in gifts:
        gift.wait_remaining = 0
    engine.tick(0.05)
    assert [gift.escape_direction for gift in gifts] == [-1, 1, -1, 1]


def test_old_mouse_save_restarts_waiting_inside_its_negative_screen():
    saved = {"version": 1, "bounds": [-1920, 30, 1920, 985],
             "settings": {"realistic_mouse": False},
             "gifts": [{"id": "old-mouse", "kind": "toy_mouse", "x": -1940, "y": 900,
                        "cat_id": "amber", "state": "escaping", "wait_remaining": -100,
                        "escape_direction": -1}]}
    engine = Engine(saved, seed=17)
    gift = engine.gifts[0]
    assert gift.x == -1920 and gift.y == 900
    assert gift.state == "idle" and gift.escape_direction == 0
    assert MOUSE_WAIT_MIN <= gift.wait_remaining <= MOUSE_WAIT_MAX
    assert not engine.gate_open and engine.has_gift_activity
    advance(engine, 7.9)
    assert gift.state == "idle" and gift.x == -1920
    advance(engine, 20)
    assert gift.state == "escaping" or gift not in engine.gifts


def test_runtime_escape_and_drag_state_are_not_persisted():
    engine = Engine(seed=17, visit_limit=1)
    gift = mouse(engine)
    gift.wait_remaining = 0
    engine.tick(0.02)
    saved = engine.export()
    assert set(saved["gifts"][0]) == {"id", "kind", "x", "y", "cat_id", "facing"}
    assert "visitor_limit" not in saved and "visit_limit" not in saved
    restored = Engine(saved, seed=8)
    assert restored.visitor_limit == 3
    assert restored.gifts[0].state == "idle"
    assert MOUSE_WAIT_MIN <= restored.gifts[0].wait_remaining <= MOUSE_WAIT_MAX
    engine.set_gift_held(gift.id, True)
    restored_held = Engine(engine.export(), seed=1)
    assert restored_held.gifts[0].state == "idle"
    assert restored_held.has_gift_activity


def test_other_gifts_stay_static_and_do_not_keep_the_clock_alive():
    engine = Engine(seed=17)
    for kind in ("feather", "bottle_cap", "fur"):
        engine._drop(kind, 100, 200, "amber")
    original = engine.export()["gifts"]
    assert not engine.has_gift_activity and not engine.moving
    for gift in engine.gifts:
        assert not engine.set_gift_held(gift.id, True)
    assert not engine.set_gift_held("missing", True)
    assert not engine.set_gift_held(engine.gifts[0].id, "yes")
    advance(engine, 100)
    assert engine.export()["gifts"] == original


def test_screen_change_rebases_mouse_and_restarts_wait_safely():
    engine = Engine(seed=25)
    gift = mouse(engine)
    gift.wait_remaining = 0
    engine.tick(0.25)
    old = (gift.x, gift.y)
    engine.set_bounds(-1280, -100, 1280, 720)
    assert (gift.x, gift.y) == (old[0] - 1280, old[1] - 100)
    assert gift.state == "idle" and MOUSE_WAIT_MIN <= gift.wait_remaining <= MOUSE_WAIT_MAX
    before = gift.wait_remaining
    engine.set_bounds(*engine.bounds)
    assert gift.wait_remaining == before


def test_gate_and_calm_mode_do_not_disable_mouse_escape():
    engine = Engine(seed=17)
    engine.calm_mode = True
    gift = mouse(engine)
    gift.wait_remaining = 0
    engine.tick(0.1)
    assert gift.state == "escaping" and engine.moving
    assert not engine.gate_open and not engine.visitors


@pytest.mark.parametrize("invalid", [None, True, False, 0, -1, 4, 1.0, "1"])
def test_visitor_limit_is_strict_integer_between_one_and_three(invalid):
    with pytest.raises(ValueError, match="visit_limit"):
        Engine(visit_limit=invalid)


def test_single_limit_blocks_manual_auto_and_fight_demo_visitors_for_forty_seconds():
    engine = Engine(seed=5, demo=True, visit_limit=1)
    assert engine.demo_scene("feeding")
    only_id = next(iter(engine.visitors))
    engine.cats[only_id].adopted = True
    assert engine.invite(next(cid for cid in engine.cats if cid != only_id)) is None
    assert not engine.demo_scene("fight")
    engine.rng.random = lambda: 0.0  # Even a guaranteed automatic second-arrival roll cannot add one.
    for _ in range(160):
        engine._visit_countdown = 0
        engine.tick(0.25)
        assert list(engine.visitors) == [only_id]
    assert engine.visitor_limit == 1


def test_fight_demo_keeps_two_participants_with_default_three_capacity():
    engine = Engine(seed=2, demo=True)
    assert engine.demo_scene("fight") and len(engine.visitors) == 2
    pair = set(engine.visitors)
    assert len(pair) == 2 and engine.invite() is not None
    assert len(engine.visitors) == 3 and pair < set(engine.visitors)
    assert engine.invite() is None
    limited = Engine(seed=2, demo=True, visit_limit=2)
    assert limited.demo_scene("fight") and len(limited.visitors) == 2
    assert limited.invite() is None


def test_mouse_demo_carries_mouse_in_from_screen_edge():
    engine = Engine(seed=51, demo=True, visit_limit=1)
    assert engine.demo_scene("mouse")
    visitor = next(iter(engine.visitors.values()))
    assert visitor.state == "peeking"
    assert visitor.carrying_gift == "toy_mouse"
    assert not engine.gifts
