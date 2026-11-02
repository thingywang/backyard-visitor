"""Q32 persistence, effective exposure, bounded recruitment and guest access."""
from copy import deepcopy
import json

import pytest

from backyard_visitor.model import Engine, MAX_VISITORS
from backyard_visitor import neighbor_lifecycle as life


def arrive(engine, cid):
    engine.open_gate()
    assert engine.invite(cid) == cid
    visitor = engine.visitors[cid]
    visitor.x = 400
    visitor.y = engine.floor_y
    engine._state(visitor, 'idle')
    return visitor


def guest_engine(seed=71):
    engine = Engine(seed=seed)
    arrive(engine, 'amber')
    visitor = arrive(engine, 'mist')
    assert engine.cats['amber'].is_neighbor
    assert engine.cats['mist'].neighbor_status == 'guest'
    return engine, visitor


def complete_meal(engine, cid):
    cat = engine.cats[cid]
    cat.meal_cooldown_remaining = 0
    visitor = engine.visitors[cid]
    engine._state(visitor, 'idle')
    visitor.meal_bowl = None
    engine.food = 1
    assert engine._begin_eating(visitor)
    engine.tick(3.9)
    assert visitor.state == 'eating'
    engine.tick(.2)
    assert visitor.state == 'grooming'


def test_first_real_visit_only_is_neighbor_and_welcome_still_works():
    engine = Engine(seed=9)
    assert not any(cat.is_neighbor for cat in engine.cats.values())
    engine.open_gate()
    for _ in range(4):
        engine.tick(5)
    assert len(engine.visitors) == 1
    first = next(iter(engine.visitors))
    assert engine.cats[first].is_neighbor
    assert engine._welcome_visit_remaining is None
    other = next(cid for cid in engine.cats if cid != first)
    engine.invite(other)
    assert engine.cats[other].neighbor_status == 'guest'
    assert sum(c.is_neighbor for c in engine.cats.values()) == 1
    restored = Engine(engine.export(), seed=55)
    restored.open_gate()
    restored.invite(other)
    assert restored.cats[other].neighbor_status == 'guest'


def test_guest_cannot_name_call_or_adopt_but_completed_food_counts():
    engine, visitor = guest_engine()
    cat = engine.cats['mist']
    assert engine.name_cat(cat.id, '新名字') == 'guest'
    assert engine.call_cat(cat.id) == 'guest'
    assert not engine.adopt(cat.id)
    assert cat.nickname == ''
    complete_meal(engine, cat.id)
    assert cat.meal_count == 1 and cat.trust == 18
    assert not cat.is_neighbor
    complete_meal(engine, cat.id)
    assert cat.meal_count == 2 and cat.trust == 36 and not cat.is_neighbor
    complete_meal(engine, cat.id)
    assert cat.meal_count == 3 and cat.is_neighbor
    assert engine.name_cat(cat.id, '新名字') == 'recognized'


def test_trust_threshold_promotes_after_two_completed_nonshy_meals():
    engine, visitor = guest_engine()
    engine.cats['mist'].personality = 'graceful'
    complete_meal(engine, 'mist')
    assert not engine.cats['mist'].is_neighbor
    complete_meal(engine, 'mist')
    assert engine.cats['mist'].trust == 44
    assert engine.cats['mist'].is_neighbor


def test_interrupted_meal_is_not_counted_or_deadline_exempt():
    engine, visitor = guest_engine()
    engine.food = 1
    assert engine._begin_eating(visitor)
    engine.tick(2)
    engine._state(visitor, 'idle')
    assert engine.cats['mist'].meal_count == 0
    engine.cats['mist'].exposure_seconds = 7199
    assert engine.account_guest_exposure(1, ['mist'])
    assert engine.cats['mist'].is_departed


@pytest.mark.parametrize('dt', [0, -1, True, float('nan'), float('inf'), 10**1000])
def test_bad_effective_time_does_not_change_exposure(dt):
    engine, _ = guest_engine()
    assert not engine.account_guest_exposure(dt, ['mist'])
    assert engine.cats['mist'].exposure_seconds == 0


def test_only_awake_visible_current_guests_accumulate_and_dt_is_capped():
    engine, _ = guest_engine()
    engine.tick(5)
    assert engine.cats['mist'].exposure_seconds == 0
    assert not engine.account_guest_exposure(2, ['mist'], display_awake=False)
    assert not engine.account_guest_exposure(2, ['mist'], display_awake=None)
    assert not engine.account_guest_exposure(2, ['amber', 'ink', 'missing'])
    assert not engine.account_guest_exposure(2, 'mist')
    assert not engine.account_guest_exposure(2, [['mist']])
    assert engine.account_guest_exposure(1000, ['mist', 'mist', 'amber'])
    assert engine.cats['mist'].exposure_seconds == 5
    assert engine.cats['amber'].exposure_seconds == 0
    del engine.visitors['mist']
    assert not engine.account_guest_exposure(5, ['mist'])


def test_deadline_departure_stays_in_roster_and_cannot_return_or_unlock_name():
    engine, visitor = guest_engine()
    cat = engine.cats['mist']
    cat.exposure_seconds = 7199
    assert engine.account_guest_exposure(.5, [cat.id])
    assert not cat.is_departed
    assert engine.account_guest_exposure(.5, [cat.id])
    assert cat.is_departed and cat.id in engine.roster_cat_ids()
    assert visitor.state == 'leaving' or visitor.route_motion == 'leaving'
    del engine.visitors[cat.id]
    assert engine.invite(cat.id) is None
    assert cat.id not in engine.visit_weights()
    restored = Engine(engine.export(), seed=999)
    assert restored.cats[cat.id].is_departed
    assert restored.cats[cat.id].exposure_seconds == 7200
    assert cat.id in restored.roster_cat_ids()
    assert restored.name_cat(cat.id, '不能取名') == 'guest'


def test_meal_exemption_survives_save_and_long_effective_presence():
    engine, _ = guest_engine()
    complete_meal(engine, 'mist')
    engine.cats['mist'].exposure_seconds = 7199
    state = engine.export()
    restored = Engine(state, seed=99)
    arrive(restored, 'mist')
    assert restored.account_guest_exposure(5, ['mist'])
    assert restored.cats['mist'].meal_count == 1
    assert not restored.cats['mist'].is_departed
    assert restored.cats['mist'].exposure_seconds == 7200
    assert not restored.account_guest_exposure(5, ['mist'])


def test_carried_expired_guest_keeps_pointer_then_leaves_on_release():
    engine, visitor = guest_engine()
    assert engine.carry('mist')
    engine.cats['mist'].exposure_seconds = 7199
    engine.account_guest_exposure(1, ['mist'])
    assert visitor.state == 'carried'
    assert engine.release('mist', 400, engine.floor_y)
    assert visitor.state == 'leaving' or visitor.route_motion == 'leaving'


def test_legacy_met_named_and_resident_cats_keep_identity_and_old_relations():
    original = Engine(seed=21)
    saved = original.export()
    saved.pop('neighbor_lifecycle')
    for i, (cid, row) in enumerate(saved['cats'].items()):
        row.pop('lifecycle')
        row['discovered'] = True
        row['visits'] = i+1
        row['trust'] = 100 if cid == 'onyx' else 25
        row['nickname'] = f'老朋友{i}'
        row['adopted'] = cid == 'onyx'
    restored = Engine(saved, seed=88)
    assert len(restored.cats) == 8  # Six established neighbours plus two waiting guests.
    assert sum(cat.is_neighbor for cat in restored.cats.values()) == 6
    assert restored.resident_id == 'onyx'
    assert restored.roster_cat_ids()[0] == 'onyx'
    for cid, row in saved['cats'].items():
        cat = restored.cats[cid]
        assert (cat.trust, cat.nickname, cat.visits) == (row['trust'], row['nickname'], row['visits'])
        for other, direction in saved['cat_relations']['relations'][cid].items():
            assert restored.relations.export()['relations'][cid][other] == direction
    named_only = Engine({'cats': {'mist': {'nickname': '旧名字'}}}, seed=1)
    assert named_only.cats['mist'].is_neighbor
    assert not named_only.cats['mist'].discovered


def test_pool_is_bounded_records_are_retained_and_generated_identity_does_not_drift():
    engine, _ = guest_engine()
    old_relations = engine.relations.export()['relations']
    original_ids = tuple(engine.cats)
    for cid in original_ids:
        if not engine.cats[cid].is_neighbor:
            engine.cats[cid].neighbor_status = 'departed'
    life.replenish(engine)
    added = set(engine.cats)-set(original_ids)
    assert len(added) == 2
    assert sum(c.neighbor_status == 'guest' for c in engine.cats.values()) == 2
    before = engine.export()
    for _ in range(30):
        engine.tick(.01)
    assert set(engine.cats) == set(before['cats'])
    restored = Engine(before, seed=888)
    for cid in added:
        cat, original = restored.cats[cid], engine.cats[cid]
        assert (cat.identity_seed, cat.personality, cat.render_coat) == (original.identity_seed, original.personality, original.render_coat)
        assert cat.true_name == original.true_name
    for cid, targets in old_relations.items():
        for other, direction in targets.items():
            assert restored.relations.export()['relations'][cid][other] == direction
    retired_id = sorted(added)[0]
    retired_seed = engine.cats[retired_id].identity_seed
    engine.cats[retired_id].neighbor_status = 'departed'
    engine.cats[retired_id].discovered = True
    life.replenish(engine)
    newly_added = set(engine.cats)-set(before['cats'])
    assert len(newly_added) == 1 and retired_id not in newly_added
    assert engine.cats[next(iter(newly_added))].identity_seed != retired_seed
    assert retired_id in engine.roster_cat_ids()
    public = json.dumps(engine.export(), ensure_ascii=False)
    assert not any(c.true_name in public for c in engine.cats.values()), 'Private identity leaked'


def test_seven_neighbor_limit_waiting_guest_and_no_more_pool_when_full():
    engine = Engine(seed=4)
    for cat in engine.cats.values():
        cat.neighbor_status = 'neighbor'
        cat.discovered = True
    life.replenish(engine)
    guests = [cat for cat in engine.cats.values() if not cat.is_neighbor]
    assert len(guests) == 2
    for cat in guests:
        cat.discovered = True
        cat.meal_count = 3
    life.promote(engine)
    assert sum(c.is_neighbor for c in engine.cats.values()) == 7
    waiting = next(c for c in guests if not c.is_neighbor)
    assert engine.guest_waiting_for_slot(waiting.id)
    count = len(engine.cats)
    life.replenish(engine)
    assert len(engine.cats) == count
    restored = Engine(engine.export(), seed=200)
    assert sum(c.is_neighbor for c in restored.cats.values()) == 7
    assert restored.guest_waiting_for_slot(waiting.id)


def test_recent_arrivals_sort_after_resident_and_order_survives_restart():
    engine, _ = guest_engine()
    arrive(engine, 'ink')
    assert engine.roster_cat_ids()[:3] == ('ink', 'mist', 'amber')
    engine.cats['amber'].trust = 100
    assert engine.adopt('amber')
    assert engine.roster_cat_ids()[:3] == ('amber', 'ink', 'mist')
    restored = Engine(engine.export(), seed=8)
    assert restored.roster_cat_ids() == engine.roster_cat_ids()


def test_three_default_visitors_explicit_smaller_limits_and_pair_fight():
    assert MAX_VISITORS == 3
    engine, first = guest_engine()
    third = arrive(engine, 'ink')
    assert len(engine.visitors) == 3
    assert engine.invite('onyx') is None
    third.x = 900
    assert engine.trigger_fight()
    assert engine._fight_pair == ('amber', 'mist')
    assert third.state == 'idle'
    engine.intervene('amber')
    assert third.state == 'idle'
    for limit in (1, 2):
        smaller = Engine(seed=1, visit_limit=limit)
        smaller.open_gate()
        for cid in tuple(smaller.cats)[:limit]:
            assert smaller.invite(cid) == cid
        assert smaller.invite('onyx') is None
    for bad in (0, 4, True, 1.5):
        with pytest.raises(ValueError):
            Engine(visit_limit=bad)


@pytest.mark.parametrize('bad', [None, [], 'wrong', {'status': []}, {'status': 'guest', 'meal_count': True, 'exposure_seconds': float('nan'), 'last_seen_order': -1}])
def test_malformed_lifecycle_fields_fallback_without_crash(bad):
    engine, _ = guest_engine()
    saved = engine.export()
    saved['cats']['mist']['lifecycle'] = bad
    restored = Engine(saved, seed=3)
    assert restored.cats['mist'].meal_count == 0
    assert restored.cats['mist'].exposure_seconds == 0


def test_dynamic_identity_with_missing_seed_has_stable_recovery():
    engine, _ = guest_engine()
    for cat in engine.cats.values():
        if not cat.is_neighbor:
            cat.neighbor_status = 'departed'
    life.replenish(engine)
    state = engine.export()
    cid = next(cid for cid in state['cats'] if cid.startswith('generated-0'))
    state['cats'][cid].pop('identity_seed')
    state['cats'][cid].pop('coat_profile')
    first, second = Engine(deepcopy(state), seed=1), Engine(deepcopy(state), seed=500)
    assert first.cats[cid].identity_record() == second.cats[cid].identity_record()
    assert first.cats[cid].coat_warning


def test_exposure_finite_extreme_dt_is_clamped_to_five_seconds():
    engine, _ = guest_engine()
    assert engine.account_guest_exposure(1e308, ['mist'])
    assert engine.cats['mist'].exposure_seconds == 5


def test_saved_deadline_cannot_reinvite_an_unfed_guest_even_if_status_is_stale():
    engine, _ = guest_engine()
    saved = engine.export()
    saved['cats']['mist']['lifecycle']['exposure_seconds'] = 7200
    restored = Engine(saved, seed=8)
    assert restored.cats['mist'].is_departed
    restored.open_gate()
    assert restored.invite('mist') is None


def three_meals():
    engine, _ = guest_engine()
    arrive(engine, 'ink')
    # Fix only pair scores; the production scheduler and meal completion run.
    state = engine.relations.export()
    for targets in state['relations'].values():
        for row in targets.values():
            row['initial'] = 20
    engine.relations.restore(state)
    engine.food = 3
    for cid in ('amber', 'mist', 'ink'):
        assert engine._begin_eating(engine.visitors[cid])
    return engine


def test_three_shared_meals_complete_all_three_pairs_once():
    engine = three_meals()
    engine.tick(4.1)
    for a in ('amber', 'mist', 'ink'):
        assert engine.cats[a].meal_count == 1
        for b in ('amber', 'mist', 'ink'):
            if a != b:
                assert engine.relations.meal_gain(a, b) == 3
    assert not engine._shared_meal_tokens and not engine._meal_tokens
    assert len([event for event in engine.drain_events() if event['kind'] == 'shared_meal']) == 3
    engine.tick(5)
    assert engine.relations.meal_gain('amber', 'ink') == 3


def test_interrupted_third_meal_does_not_erase_other_pair_reward():
    engine = three_meals()
    engine.tick(1)
    engine._state(engine.visitors['ink'], 'idle')
    engine.tick(3.1)
    assert engine.cats['ink'].meal_count == 0
    assert engine.relations.meal_gain('amber', 'mist') == 3
    assert engine.relations.meal_gain('mist', 'amber') == 3
    for cid in ('amber', 'mist'):
        assert engine.relations.meal_gain(cid, 'ink') == 0
        assert engine.relations.meal_gain('ink', cid) == 0


def test_pending_dynamic_guests_do_not_dilute_existing_high_trust_cadence():
    engine = Engine(seed=2)
    for cat in engine.cats.values():
        cat.neighbor_status = 'neighbor'
        cat.trust = 100
    life.replenish(engine)
    assert len(engine.cats) == 8
    for _ in range(10):
        assert 10 <= engine._visit_delay() <= 20
