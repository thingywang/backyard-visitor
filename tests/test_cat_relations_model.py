"""Q16 behavior uses directed identity data without changing one-cat cadence."""
import random

import pytest

from backyard_visitor.model import (
    Engine, Visitor, EATING_MUZZLE_LEFT, EATING_MUZZLE_RIGHT,
    MEAL_COOLDOWN_SECONDS, STATE_VERSION,
)


def set_scores(engine, value=0, overrides=None):
    overrides = overrides or {}
    engine.relations.restore({'schema_version': 1, 'relations': {
        a: {b: {'initial': overrides.get((a, b), value), 'meal_gain': 0}
            for b in engine.cats if a != b} for a in engine.cats}})


def setup_pair(ids=('amber', 'mist'), *, calm=True):
    engine = Engine(seed=10)
    engine.open_gate()
    engine.calm_mode = calm
    engine._visit_countdown = engine._fight_countdown = 10000
    for index, cid in enumerate(ids):
        engine.cats[cid].discovered = True
        engine.visitors[cid] = Visitor(cid, 450+60*index, engine.floor_y,
                                      state='idle', next_action=10000,
                                      stay_remaining=10000)
    set_scores(engine)
    return engine, *(engine.visitors[cid] for cid in ids)


def advance(engine, seconds):
    while seconds > 1e-9:
        step = min(.25, seconds)
        engine.tick(step)
        seconds -= step


def meal(engine, visitor):
    engine.food += 1
    assert engine._begin_eating(visitor)
    assert engine.food == 0


def meal_target_x(visitor):
    return visitor.route_final_x if visitor.route_final_x is not None else visitor.target_x


def gains(engine, a='amber', b='mist'):
    return engine.relations.meal_gain(a, b), engine.relations.meal_gain(b, a)


def test_relationship_initialization_does_not_consume_engine_rng():
    engine = Engine(seed=81)
    reference = random.Random(81)
    assert engine._visit_countdown == reference.uniform(240, 360)
    assert engine._fight_countdown == reference.uniform(20, 45)
    assert engine.rng.getstate() == reference.getstate()


def test_relation_identity_roundtrip_and_legacy_backfill():
    engine = Engine(seed=3)
    set_scores(engine, 21, {('amber', 'mist'): 64})
    engine.relations.share_meal('amber', 'mist')
    state = engine.export()
    restored = Engine(state, seed=999)
    assert restored.relations.export() == engine.relations.export()
    assert restored.relations.score('amber', 'mist') == 67
    assert restored.relations.score('mist', 'amber') == 24
    assert restored.discovered_cat_ids() == ()
    assert not restored.gate_open and not restored.visitors
    legacy = dict(state)
    del legacy['cat_relations']
    supplemented = Engine(legacy, seed=999)
    assert supplemented.relations.export() == Engine(seed=999).relations.export()
    future = Engine(dict(state, version=STATE_VERSION+1), seed=999)
    assert future.relations.export() == Engine(seed=999).relations.export()


def test_empty_garden_keeps_original_weights_and_second_candidates_use_relations():
    engine = Engine(seed=3)
    engine.cats['mist'].trust = 60
    set_scores(engine, 100, {('amber', 'mist'): 0, ('ink', 'amber'): 50})
    assert engine.visit_weights() == {'amber': 1, 'mist': 4, 'ink': 1, 'onyx': 1,
                                      'generated-a': 1, 'generated-b': 1}
    engine.open_gate(); engine.invite('amber')
    assert engine.visit_weights() == pytest.approx({'mist': .8, 'ink': .6, 'onyx': 1,
                                                          'generated-a': 1, 'generated-b': 1})
    assert engine._co_visit_chance_factor() == pytest.approx(4.4/8)


@pytest.mark.parametrize('score,expected', [(0, 1), (100, 2)])
def test_relations_change_actual_second_arrival_opportunity(monkeypatch, score, expected):
    engine, first, _ = setup_pair()
    del engine.visitors['mist']
    set_scores(engine, score)
    engine._visit_countdown = 0
    monkeypatch.setattr(engine.rng, 'random', lambda: .05)
    engine.tick(.1)
    assert len(engine.visitors) == expected  # .024 versus .12 actual gate


def test_single_cat_lifecycle_is_identical_across_relationship_scores():
    engines = [Engine(seed=6, visit_limit=1), Engine(seed=6, visit_limit=1)]
    for engine, score in zip(engines, [0, 100]):
        set_scores(engine, score)
        engine.open_gate()
    for _ in range(2400):
        for engine in engines:
            engine.tick(.25)
        snapshots = [tuple((cid, v.state, v.x, v.y, v.stay_remaining)
                           for cid, v in engine.visitors.items()) for engine in engines]
        assert snapshots[0] == snapshots[1]
    assert engines[0].rng.getstate() == engines[1].rng.getstate()


def test_missing_resident_and_explicit_invites_do_not_use_relation_gate(monkeypatch):
    engine, _, _ = setup_pair()
    del engine.visitors['amber']
    engine.cats['amber'].trust = 100
    engine.cats['amber'].neighbor_status = 'neighbor'
    assert engine.adopt('amber')
    engine._visit_countdown = 0
    monkeypatch.setattr(engine.rng, 'random', lambda: .99)
    engine.tick(.1)
    assert 'amber' in engine.visitors
    manual = Engine(seed=3); set_scores(manual)
    manual.open_gate()
    assert manual.invite('onyx') == 'onyx'
    assert manual.invite('mist') == 'mist'


@pytest.mark.parametrize('score,expected', [(0, True), (100, False)])
def test_relations_change_auto_fight_threshold(monkeypatch, score, expected):
    engine, _, _ = setup_pair(calm=False)
    set_scores(engine, score)
    engine._fight_countdown = 0
    monkeypatch.setattr(engine.rng, 'random', lambda: .4)
    engine.tick(.1)
    assert bool(engine._fight_pair) is expected


@pytest.mark.parametrize('case', ['calm', 'no_mischief', 'far', 'eating'])
def test_relation_hostility_does_not_bypass_fight_eligibility(monkeypatch, case):
    engine, first, second = setup_pair(calm=False)
    if case == 'calm':
        engine.calm_mode = True
    elif case == 'no_mischief':
        engine.cats[first.cat_id].personality = 'shy'
    elif case == 'far':
        second.x = first.x+300
    else:
        meal(engine, first)
    engine._fight_countdown = 0
    monkeypatch.setattr(engine.rng, 'random', lambda: 0)
    engine.tick(.1)
    assert engine._fight_pair is None


def test_manual_fight_and_demo_keep_their_explicit_contract():
    engine, _, _ = setup_pair(calm=False)
    set_scores(engine, 100)
    assert engine.trigger_fight()
    demo = Engine(seed=3, demo=True)
    set_scores(demo, 100)
    assert demo.demo_scene('fight')
    for _ in range(2400):
        demo.tick(.05)
        if demo._fight_pair:
            break
    assert demo._fight_pair is not None


def test_shared_meal_waits_for_both_full_meals_and_grants_once():
    engine, first, second = setup_pair()
    engine.cats[first.cat_id].nickname = '阿甲'
    engine.cats[second.cat_id].nickname = '阿乙'
    meal(engine, first); advance(engine, 1)
    meal(engine, second); advance(engine, 3)
    assert first.state == 'grooming' and second.state == 'eating'
    assert gains(engine) == (0, 0)
    advance(engine, 1)
    assert gains(engine) == (3, 3)
    assert engine.cats[first.cat_id].trust == 22
    assert engine.cats[second.cat_id].trust == 18
    assert engine.cats[first.cat_id].meal_cooldown_remaining == 175
    assert engine.cats[second.cat_id].meal_cooldown_remaining == 176
    advance(engine, 8)
    assert gains(engine) == (3, 3)
    events = [event for event in engine.drain_events() if event['kind'] == 'shared_meal']
    assert len(events) == 1
    assert '阿甲' in events[0]['message'] and '阿乙' in events[0]['message']
    assert not engine._meal_tokens and not engine._shared_meal_tokens


def test_paused_ticks_and_duplicate_arrivals_cannot_finish_or_repeat_meal():
    engine, first, second = setup_pair()
    meal(engine, first); meal(engine, second)
    for _ in range(20):
        engine.tick(0)
        assert not engine._begin_eating(first)
    assert first.state_age == second.state_age == 0
    assert gains(engine) == (0, 0)
    assert engine.cats[first.cat_id].meal_cooldown_remaining == MEAL_COOLDOWN_SECONDS
    advance(engine, 4)
    assert gains(engine) == (3, 3)
    advance(engine, 4)
    assert gains(engine) == (3, 3)


def test_sequential_meals_without_overlap_do_not_count_as_shared():
    engine, first, second = setup_pair()
    meal(engine, first); advance(engine, 4)
    meal(engine, second); advance(engine, 4)
    assert gains(engine) == (0, 0)


def test_moved_bowl_starts_a_different_meal_location():
    engine, first, second = setup_pair()
    meal(engine, first); advance(engine, 1)
    engine.set_bowl(engine.bowl[0]+240, engine.bowl[1])
    meal(engine, second); advance(engine, 4)
    assert gains(engine) == (0, 0)


@pytest.mark.parametrize('interrupt', ['carry', 'startle', 'close', 'state', 'remove'])
def test_interrupted_meal_never_grants_pair_gain(interrupt):
    engine, first, second = setup_pair()
    meal(engine, first); meal(engine, second); advance(engine, 1)
    if interrupt == 'carry':
        assert engine.carry(first.cat_id)
    elif interrupt == 'startle':
        assert engine.startle(first.cat_id)
    elif interrupt == 'close':
        engine.open_gate(False)
    elif interrupt == 'state':
        engine._state(first, 'idle')
    else:
        del engine.visitors[first.cat_id]
    advance(engine, 4)
    assert gains(engine) == (0, 0)
    assert not any(event['kind'] == 'shared_meal' for event in engine.drain_events())


@pytest.mark.parametrize('interrupt', ['carry', 'startle', 'close'])
def test_completed_first_meal_does_not_ignore_urgent_interruption(interrupt):
    engine, first, second = setup_pair()
    meal(engine, first); advance(engine, 1)
    meal(engine, second); advance(engine, 3)
    assert first.state == 'grooming'
    if interrupt == 'carry':
        assert engine.carry(first.cat_id)
    elif interrupt == 'startle':
        assert engine.startle(first.cat_id)
    else:
        engine.open_gate(False)
    advance(engine, 1)
    assert gains(engine) == (0, 0)


def test_restart_discards_unfinished_shared_meal_but_keeps_identity_cooldown():
    engine, first, second = setup_pair()
    meal(engine, first); meal(engine, second); advance(engine, 2)
    state = engine.export()
    assert not any(key.startswith('_meal') or key.startswith('_shared') for key in state)
    restored = Engine(state, seed=41)
    assert not restored._meal_tokens and not restored._shared_meal_tokens
    advance(restored, 4)
    assert gains(restored) == (0, 0)
    assert restored.cats[first.cat_id].meal_cooldown_remaining == 174
    assert not restored.visitors


def test_repeated_completed_shared_meals_stop_at_thirty_per_direction():
    engine, first, second = setup_pair()
    for index in range(11):
        meal(engine, first); meal(engine, second); advance(engine, 4)
        assert gains(engine) == (min(30, 3*(index+1)),)*2
        advance(engine, MEAL_COOLDOWN_SECONDS-4)
    assert len([event for event in engine.drain_events() if event['kind'] == 'shared_meal']) == 10


def test_common_meal_does_not_publish_or_unlock_undiscovered_identity():
    engine, first, second = setup_pair()
    engine.cats[first.cat_id].discovered = False
    meal(engine, first); meal(engine, second); advance(engine, 4)
    assert gains(engine) == (3, 3)
    assert not engine.is_discovered(first.cat_id)
    assert not any(event['kind'] == 'shared_meal' for event in engine.drain_events())


@pytest.mark.parametrize('already_eating', [False, True])
def test_same_side_pair_reserves_opposite_muzzles(already_eating):
    engine, first, second = setup_pair()
    first.x = second.x = 200
    engine.food = 2
    engine._choose_action(first)
    if already_eating:
        first.x, first.y, first.facing = (meal_target_x(first), engine.bowl[1]-97,
                                        first.arrival_facing)
        assert engine._begin_eating(first)
    engine._choose_action(second)
    assert (first.facing if already_eating else first.arrival_facing) == 1
    assert second.arrival_facing == -1
    assert (first.x if already_eating else meal_target_x(first)) == pytest.approx(engine.bowl[0]-EATING_MUZZLE_RIGHT)
    assert meal_target_x(second) == pytest.approx(engine.bowl[0]-EATING_MUZZLE_LEFT)
    assert (first.x if already_eating else meal_target_x(first)) != meal_target_x(second)


def test_single_cat_food_target_is_unchanged():
    engine, first, _ = setup_pair()
    del engine.visitors['mist']
    first.x = 200
    engine.food = 1; engine._choose_action(first)
    assert first.arrival_facing == 1
    assert meal_target_x(first) == pytest.approx(engine.bowl[0]-EATING_MUZZLE_RIGHT)


@pytest.mark.parametrize('already_eating', [False, True])
def test_old_bowl_reservation_does_not_claim_new_bowl_side(already_eating):
    engine, first, second = setup_pair()
    first.x = second.x = 200
    engine.food = 2; engine._choose_action(first)
    if already_eating:
        first.x, first.y, first.facing = (meal_target_x(first), engine.bowl[1]-97,
                                        first.arrival_facing)
        assert engine._begin_eating(first)
    engine.set_bowl(engine.bowl[0]+100, engine.bowl[1])
    engine._choose_action(second)
    assert second.arrival_facing == 1
    assert meal_target_x(second) == pytest.approx(engine.bowl[0]-EATING_MUZZLE_RIGHT)


def test_screen_edge_pair_waits_when_other_side_cannot_fit():
    engine, first, second = setup_pair()
    engine.set_bowl(24, engine.bowl[1])
    engine.food = 2; engine._choose_action(first)
    assert first.arrival_facing == -1
    engine._choose_action(second)
    assert second.state == 'idle' and second.meal_bowl is None


def test_production_food_routes_reach_two_visible_sides_and_finish_together():
    engine, first, second = setup_pair()
    first.x, second.x = 400, 420
    engine.food = 2
    overlaps = []
    for _ in range(480):
        engine.tick(.05)
        if first.state == second.state == 'eating':
            overlaps.append((first.x, second.x, first.facing, second.facing))
        if first.state == second.state == 'grooming':
            break
    assert overlaps
    assert all(abs(a-b) > 100 and fa == 1 and fb == -1 for a, b, fa, fb in overlaps)
    assert first.state == second.state == 'grooming'
    assert gains(engine) == (3, 3)


def test_startled_completed_partner_descending_from_high_bowl_cancels_shared_meal():
    engine, first, second = setup_pair()
    engine.set_bowl(640, 300)
    first.y = second.y = 203
    meal(engine, first); advance(engine, 1)
    meal(engine, second); advance(engine, 3)
    assert first.state == 'grooming'
    assert engine.startle(first.cat_id)
    assert first.state == 'jumping' and first.route_motion == 'fleeing'
    advance(engine, 1)
    assert gains(engine) == (0, 0)


@pytest.mark.parametrize('facing', [-1, 1])
def test_meal_grooming_keeps_bowl_side_until_next_cat_arrives(facing):
    engine, first, second = setup_pair()
    muzzle = EATING_MUZZLE_RIGHT if facing > 0 else EATING_MUZZLE_LEFT
    first.x, first.y, first.facing = engine.bowl[0]-muzzle, engine.bowl[1]-97, facing
    second.x, second.y = first.x-50*facing, first.y
    meal(engine, first); advance(engine, 4)
    assert first.state == 'grooming' and not engine._meal_tokens
    engine.food = 1
    engine._choose_action(second)
    assert second.arrival_facing == -facing
    assert abs(meal_target_x(second)-first.x) > 100
    for _ in range(90):
        engine.tick(.05)
        if second.state == 'eating':
            break
    assert first.state == 'grooming' and second.state == 'eating'
    assert abs(first.x-second.x) > 100
    assert gains(engine) == (0, 0)  # Sequential meals are not shared meals.


@pytest.mark.parametrize('change', ['moved_bowl', 'different_x', 'different_y', 'not_after_meal'])
def test_grooming_elsewhere_does_not_reserve_current_bowl(change):
    engine, first, second = setup_pair()
    first.x, first.y, first.facing = engine.bowl[0]-EATING_MUZZLE_RIGHT, engine.bowl[1]-97, 1
    second.x, second.y = first.x-50, first.y
    meal(engine, first); advance(engine, 4)
    assert first.state == 'grooming'
    if change == 'moved_bowl':
        engine.set_bowl(engine.bowl[0]+100, engine.bowl[1])
    elif change == 'different_x':
        first.x -= 100
    elif change == 'different_y':
        first.y -= 100
    else:
        first.has_eaten = False
    engine.food = 1
    engine._choose_action(second)
    assert second.arrival_facing == 1
    assert meal_target_x(second) == pytest.approx(engine.bowl[0]-EATING_MUZZLE_RIGHT)
