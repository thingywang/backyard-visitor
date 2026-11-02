"""Pure relation math and save recovery; no engine, profiles, UI or timers."""
import copy
import json
import random

import pytest

from backyard_visitor.cat_relations import CatRelations


def state(a=10, b=70, ag=0, bg=0):
    return {'schema_version': 1, 'relations': {
        'a': {'b': {'initial': a, 'meal_gain': ag}},
        'b': {'a': {'initial': b, 'meal_gain': bg}},
    }}


def test_seeded_directions_stable_across_id_order_without_global_rng_changes():
    before = random.getstate()
    first = CatRelations(['d', 'b', 'a', 'c'], seed=17)
    second = CatRelations(['a', 'b', 'c', 'd'], seed=17)
    assert first.export() == second.export()
    assert random.getstate() == before
    assert first.export() != CatRelations(['a', 'b', 'c', 'd'], seed=18).export()
    assert len([v for targets in first.export()['relations'].values() for v in targets]) == 12
    assert all(type(first.score(a, b)) is int and 0 <= first.score(a, b) <= 100
               for a in first.cat_ids for b in first.cat_ids if a != b)
    assert any(first.score(a, b) != first.score(b, a)
               for a in first.cat_ids for b in first.cat_ids if a != b)


def test_directions_and_pair_use_lower_score_not_an_average():
    relations = CatRelations(['a', 'b'], seed=4, state=state(12, 88, 6, 3))
    assert relations.score('a', 'b') == 18
    assert relations.score('b', 'a') == 91
    assert relations.meal_gain('a', 'b') == 6
    assert relations.pair_score('a', 'b') == relations.pair_score('b', 'a') == 18
    assert relations.co_visit_factor('a', 'b') == pytest.approx(.344)
    assert relations.fight_chance('a', 'b') == pytest.approx(.4844)


def test_pair_factors_are_monotonic_bounded_and_never_disable_conflict():
    factors, fights = [], []
    for score in range(101):
        relations = CatRelations(['a', 'b'], state=state(score, 100))
        factors.append(relations.co_visit_factor('a', 'b'))
        fights.append(relations.fight_chance('a', 'b'))
        assert .2 <= factors[-1] <= 1
        assert 0 < fights[-1] < 1
    assert factors[0] == pytest.approx(.2) and factors[-1] == pytest.approx(1)
    assert fights[0] == pytest.approx(.56) and fights[-1] == pytest.approx(.14)
    assert all(a < b for a, b in zip(factors, factors[1:]))
    assert all(a > b for a, b in zip(fights, fights[1:]))


def test_shared_meals_add_three_per_direction_then_stop_at_lifetime_thirty():
    relations = CatRelations(['a', 'b', 'c'], seed=3, state=state(10, 20))
    unaffected = {direction: relations.score(*direction) for direction in (('a', 'c'), ('c', 'a'), ('b', 'c'), ('c', 'b'))}
    for n in range(1, 11):
        assert relations.share_meal('a', 'b') == {'a': 3, 'b': 3}
        assert relations.meal_gain('a', 'b') == relations.meal_gain('b', 'a') == 3*n
    for _ in range(12):
        assert relations.share_meal('a', 'b') == {}
    assert relations.score('a', 'b') == 40 and relations.score('b', 'a') == 50
    assert all(relations.score(*pair) == value for pair, value in unaffected.items())


def test_near_hundred_returns_only_actual_partial_gains():
    relations = CatRelations(['a', 'b'], state=state(99, 96))
    assert relations.share_meal('a', 'b') == {'a': 1, 'b': 3}
    assert relations.share_meal('b', 'a') == {'b': 1}
    assert relations.share_meal('a', 'b') == {}
    assert relations.score('a', 'b') == relations.score('b', 'a') == 100
    assert relations.meal_gain('a', 'b') == 1 and relations.meal_gain('b', 'a') == 4


def test_json_roundtrip_does_not_replay_gains_or_reset_thirty_point_budget():
    relations = CatRelations(['a', 'b'], state=state(5, 10))
    for i in range(10):
        assert relations.share_meal('a', 'b') == {'a': 3, 'b': 3}
        saved = json.loads(json.dumps(relations.export(), allow_nan=False))
        relations = CatRelations(['b', 'a'], seed=100+i, state=saved)
        assert relations.export() == saved
        assert relations.meal_gain('a', 'b') == 3*(i+1)
    assert relations.share_meal('a', 'b') == {}
    assert relations.score('a', 'b') == 35 and relations.score('b', 'a') == 40


def test_legacy_state_initializes_once_then_new_ids_preserve_existing_directions():
    first = CatRelations(['a', 'b'], seed=88)
    saved = first.export()
    recovered = CatRelations(['a', 'b', 'c'], seed=89, state=saved)
    assert recovered.score('a', 'b') == first.score('a', 'b')
    assert recovered.score('b', 'a') == first.score('b', 'a')
    assert recovered.meal_gain('a', 'b') == 0
    assert 0 <= recovered.score('a', 'c') <= 100
    assert CatRelations(['a', 'b'], seed=999, state=saved).export() == saved


@pytest.mark.parametrize('bad', [None, [], 'state', {},
    {'schema_version': True, 'relations': {}},
    {'schema_version': 1.0, 'relations': {}},
    {'schema_version': 2, 'relations': {}},
    {'schema_version': 1, 'relations': []}])
def test_malformed_root_keeps_fresh_seeded_initial_values(bad):
    assert CatRelations(['a', 'b'], seed=2, state=bad).export() == CatRelations(['a', 'b'], seed=2).export()


@pytest.mark.parametrize('initial,gain', [
    (True, 0), (False, 0), ('12', 0), (None, 0), (-1, 0), (101, 0),
    (float('nan'), 0), (float('inf'), 0), (-float('inf'), 0), (10**1000, 0),
    (20, True), (20, '3'), (20, None), (20, -1), (20, 31), (99, 2),
    (20, float('nan')), (20, float('inf')), (20, 10**1000),
])
def test_invalid_direction_is_atomic_and_does_not_discard_other_valid_direction(initial, gain):
    baseline = CatRelations(['a', 'b'], seed=8)
    relations = CatRelations(['a', 'b'], seed=8, state=state(initial, 42, gain, 9))
    assert relations.score('a', 'b') == baseline.score('a', 'b')
    assert relations.meal_gain('a', 'b') == 0
    assert relations.score('b', 'a') == 51 and relations.meal_gain('b', 'a') == 9


def test_fractional_finite_save_values_obey_same_caps_without_rounding():
    relations = CatRelations(['a', 'b'], state=state(99.5, 10.25, 0, 28.5))
    assert relations.share_meal('a', 'b') == {'a': .5, 'b': 1.5}
    assert relations.score('a', 'b') == 100
    assert relations.score('b', 'a') == 40.25
    assert relations.share_meal('a', 'b') == {}


def test_unknown_self_missing_and_malformed_rows_are_ignored():
    saved = state()
    saved['relations']['a']['a'] = {'initial': 100, 'meal_gain': 0}
    saved['relations']['a']['unknown'] = {'initial': 100, 'meal_gain': 0}
    saved['relations']['unknown'] = {'a': {'initial': 100, 'meal_gain': 0}}
    saved['relations']['b']['a'] = {'initial': 100}
    saved['relations']['c'] = []
    relations = CatRelations(['a', 'b', 'c'], seed=23, state=saved)
    baseline = CatRelations(['a', 'b', 'c'], seed=23)
    assert relations.score('a', 'b') == 10
    assert relations.score('b', 'a') == baseline.score('b', 'a')
    assert relations.score('c', 'a') == baseline.score('c', 'a')
    assert set(relations.export()['relations']) == {'a', 'b', 'c'}
    assert all(set(targets) == set(relations.cat_ids)-{source}
               for source, targets in relations.export()['relations'].items())


@pytest.mark.parametrize('a,b', [('a', 'a'), ('a', 'unknown'), ('unknown', 'b'), (None, 'a'), ([], 'a')])
def test_invalid_pair_queries_raise_but_meal_is_safe_noop(a, b):
    relations = CatRelations(['a', 'b'], seed=10)
    before = relations.export()
    for query in (relations.score, relations.meal_gain, relations.pair_score,
                  relations.co_visit_factor, relations.fight_chance):
        with pytest.raises(ValueError):
            query(a, b)
    assert relations.share_meal(a, b) == {}
    assert relations.export() == before


def test_export_and_input_state_do_not_alias_mutable_internal_data():
    saved = state()
    original = copy.deepcopy(saved)
    saved['relations']['a']['b']['unrelated'] = 'discarded'
    relations = CatRelations(['a', 'b'], state=saved)
    exported = relations.export()
    assert exported == original
    saved['relations']['a']['b']['initial'] = 100
    exported['relations']['b']['a']['meal_gain'] = 30
    assert relations.score('a', 'b') == 10 and relations.meal_gain('b', 'a') == 0
    assert set(exported) == {'schema_version', 'relations'}
    assert all(set(row) == {'initial', 'meal_gain'}
               for targets in relations.export()['relations'].values() for row in targets.values())


def test_empty_singleton_and_duplicate_id_collections_are_supported():
    assert CatRelations([]).export()['relations'] == {}
    assert CatRelations(['a', 'a']).export()['relations'] == {'a': {}}
    with pytest.raises(TypeError):
        CatRelations('ab')
    for invalid in ([''], [None], [True], ['a', 3]):
        with pytest.raises(ValueError):
            CatRelations(invalid)


def test_public_restore_is_repeatable_without_rewards_and_keeps_invalid_direction():
    relations = CatRelations(['a', 'b'], seed=51)
    untouched = relations.score('b', 'a')
    saved = state(20, True, 9, 0)
    assert relations.restore(saved) is None
    assert relations.score('a', 'b') == 29 and relations.meal_gain('a', 'b') == 9
    assert relations.score('b', 'a') == untouched and relations.meal_gain('b', 'a') == 0
    once = relations.export()
    assert relations.restore(saved) is None
    assert relations.export() == once
    assert relations.restore({'schema_version': True, 'relations': {}}) is None
    assert relations.export() == once
