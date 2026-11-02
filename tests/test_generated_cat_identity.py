"""Random coats belong to a saved neighbour, never a frame or individual visit."""
from copy import deepcopy
import json
import random

import pytest

from backyard_visitor import model
from backyard_visitor.cat_relations import CatRelations
from backyard_visitor.coat_traits import CoatSpec, describe_coat, generate_coat
from backyard_visitor.model import Engine, Gift, GENERATED_CAT_IDS, MAX_VISITORS
from backyard_visitor.storage import load_state, save_state

LEGACY = {'amber': ('ginger', 'mischievous'), 'mist': ('gray', 'shy'),
          'ink': ('tuxedo', 'graceful'), 'onyx': ('black', 'graceful')}


def identities(engine):
    return {cid: (cat.identity_seed, cat.personality, cat.coat_profile)
            for cid, cat in engine.cats.items() if cid in GENERATED_CAT_IDS}


def assert_private(engine, public):
    # Assert a boolean so a diagnostic cannot print a private name on failure.
    leaked = any(cat.true_name in public for cat in engine.cats.values())
    assert not leaked, 'Private name appeared in public data'


def test_two_generated_slots_keep_all_original_neighbours_and_start_locked():
    engine = Engine(seed=42)
    assert tuple(engine.cats) == (*LEGACY, *GENERATED_CAT_IDS)
    assert MAX_VISITORS == engine.visitor_limit == 3
    for cid, expected in LEGACY.items():
        cat = engine.cats[cid]
        assert (cat.coat, cat.personality) == expected
        assert cat.coat_profile is None and cat.identity_seed is None
        assert cat.render_coat == expected[0]
    assert engine.cats['onyx'].display_name == '黑色访客'
    for cid in GENERATED_CAT_IDS:
        cat = engine.cats[cid]
        assert isinstance(cat.render_coat, CoatSpec)
        assert cat.render_coat is cat.coat_profile
        assert isinstance(hash(cat.render_coat),int)
        assert cat.coat_description == describe_coat(cat.coat_profile)
        assert cat.display_name == cat.coat_description + '访客'
        assert not cat.discovered and cat.trust == cat.visits == 0
        assert engine.name_cat(cid, '新邻居') == 'undiscovered'
        assert engine.call_cat(cid) == 'undiscovered' and not engine.adopt(cid)
    assert engine.discovered_cat_ids() == () and engine.drain_events() == []


def test_identity_generation_is_separate_from_lifecycle_rng_and_old_pair_draws():
    engine = Engine(seed=81)
    reference = random.Random(81)
    assert engine._visit_countdown == reference.uniform(240,360)
    assert engine._fight_countdown == reference.uniform(20,45)
    assert engine.rng.getstate() == reference.getstate()
    original = CatRelations(LEGACY, seed=81).export()['relations']
    current = engine.relations.export()['relations']
    assert {a: {b: current[a][b] for b in original[a]} for a in LEGACY} == original
    assert identities(Engine(seed=81)) == identities(engine)
    assert identities(Engine(seed=82)) != identities(engine)


def test_restart_preserves_complete_identity_and_never_exports_private_names(tmp_path):
    engine = Engine(seed=4)
    engine.open_gate();engine.invite('generated-a')
    assert engine.name_cat('generated-a','新的小客') == 'unfamiliar'
    cat = engine.cats['generated-a'];cat.trust = 100
    assert engine.adopt(cat.id) and engine.place_bed(cat.id,500)
    engine.gifts.append(Gift('keepsake','feather',200,300,cat.id))
    state = engine.export()
    assert_private(engine,json.dumps(state,ensure_ascii=False))
    assert_private(engine,repr(engine.cats))
    assert_private(engine,json.dumps(engine.drain_events(),ensure_ascii=False))
    path = tmp_path/'state.json';save_state(path,state)
    restored = Engine(load_state(path),seed=999)
    assert identities(restored) == identities(engine)
    for cid in GENERATED_CAT_IDS:
        same_private_name = restored.cats[cid].true_name == engine.cats[cid].true_name
        assert same_private_name
    assert restored.export()['cats'] == state['cats']
    assert restored.relations.export() == state['cat_relations']
    assert restored.cats[cat.id].display_name == '新的小客'
    assert restored.resident_id == cat.id and restored.bed.cat_id == cat.id
    assert restored.gifts[0].cat_id == cat.id
    assert not restored.gate_open and not restored.visitors


def test_legacy_save_keeps_old_looks_progress_and_every_existing_relationship():
    original = Engine(seed=7)
    for index,cid in enumerate(LEGACY):
        cat = original.cats[cid]
        cat.discovered = True;cat.visits = index+1
        cat.neighbor_status = "neighbor"
        cat.trust = 100 if cid == 'onyx' else 30+index
        cat.nickname = f'旧朋友{index}'
    assert original.adopt('onyx') and original.place_bed('onyx',700)
    old = original.export()
    old['cats'] = {cid: old['cats'][cid] for cid in LEGACY}
    old['cat_relations']['relations'] = {
        a: {b: row for b,row in targets.items() if b in LEGACY}
        for a,targets in old['cat_relations']['relations'].items() if a in LEGACY}
    restored = Engine(old,seed=999)
    saved = restored.export()
    assert {cid:saved['cats'][cid] for cid in LEGACY} == old['cats']
    assert saved['resident_bed'] == old['resident_bed'] and restored.resident_id == 'onyx'
    for source,targets in old['cat_relations']['relations'].items():
        for target,relation in targets.items():
            assert saved['cat_relations']['relations'][source][target] == relation
    assert all(not restored.cats[cid].discovered for cid in GENERATED_CAT_IDS)
    second = Engine(saved,seed=113)
    assert identities(second) == identities(restored)
    assert second.relations.export() == restored.relations.export()


@pytest.mark.parametrize('cid',GENERATED_CAT_IDS)
def test_visits_unlock_only_arriving_neighbour_and_never_reroll_appearance(cid):
    engine = Engine(seed=16)
    before = identities(engine)
    assert engine.invite(cid) is None
    engine.open_gate();assert engine.invite(cid) == cid
    assert engine.discovered_cat_ids() == (cid,)
    v = engine.visitors[cid]
    assert v.state == 'peeking' and (v.x < 0 or v.x+144 > engine.bounds[2])
    engine.open_gate(False)
    for _ in range(1600):
        engine.tick(.05)
        if not engine.visitors:break
    assert not engine.visitors
    engine.open_gate();assert engine.invite(cid) == cid
    assert engine.cats[cid].visits == 2 and identities(engine) == before
    assert engine.invite('amber') == 'amber'
    assert engine.invite('ink') == 'ink'
    other = next(value for value in GENERATED_CAT_IDS if value != cid)
    assert engine.invite(other) is None and not engine.cats[other].discovered
    assert len(engine.visitors) == MAX_VISITORS


def test_generated_neighbours_participate_naturally_without_capacity_increase():
    observed = set()
    for seed in range(40):
        engine = Engine(seed=seed)
        assert all(engine.visit_weights()[cid] > 0 for cid in GENERATED_CAT_IDS)
        engine.open_gate()
        for _ in range(81):engine.tick(.25)
        assert len(engine.visitors) == 1
        observed.update(engine.visitors)
        assert engine.export()['welcome_visit_remaining'] is None
    assert set(GENERATED_CAT_IDS) <= observed


def test_new_directional_relations_roundtrip_and_keep_lifetime_meal_cap():
    engine = Engine(seed=4)
    a,b = GENERATED_CAT_IDS
    assert len(engine.relations.export()['relations']) == 6
    engine.relations.restore({'schema_version':1,'relations':{
        a:{b:{'initial':10,'meal_gain':0}},b:{a:{'initial':20,'meal_gain':0}}}})
    for _ in range(30):engine.relations.share_meal(a,b)
    assert engine.relations.score(a,b) == 40 and engine.relations.score(b,a) == 50
    assert engine.relations.meal_gain(a,b) == engine.relations.meal_gain(b,a) == 30
    assert Engine(engine.export(),seed=91).relations.export() == engine.relations.export()


@pytest.mark.parametrize('raw',[None,[],{}, {'version':999},
    dict(generate_coat(42).to_dict(),version=999),
    dict(generate_coat(42).to_dict(),unexpected=True)])
def test_unreadable_profile_preserves_raw_and_stable_identity_without_regeneration(raw,monkeypatch):
    state = Engine(seed=21).export()
    cid = GENERATED_CAT_IDS[0]
    state['cats'][cid]['coat_profile'] = deepcopy(raw)
    def unexpected_generation(*args):
        pytest.fail('An existing damaged or future coat must not be rerolled')
    monkeypatch.setattr(model,'generate_coat',unexpected_generation)
    engine = Engine(state,seed=999)
    cat = engine.cats[cid]
    assert cat.coat_profile is None and cat.render_coat == 'gray' and cat.coat_warning
    assert cat.identity_seed == state['cats'][cid]['identity_seed']
    saved = engine.export()
    assert saved['cats'][cid]['coat_profile'] == raw
    assert len([event for event in engine.drain_events() if event['kind']=='coat_warning']) == 1
    assert engine.drain_events() == []
    second = Engine(saved,seed=113)
    assert second.cats[cid].identity_seed == cat.identity_seed
    assert second.export()['cats'][cid] == saved['cats'][cid]
    assert second.cats[cid].coat_warning and second.cats[cid].render_coat == 'gray'


@pytest.mark.parametrize('bad_seed',[None,True,-1,1.5,'42',2**32,1e308,10**400])
def test_bad_identity_seed_with_future_profile_recovers_deterministically(bad_seed):
    state = Engine(seed=8).export();cid = GENERATED_CAT_IDS[1]
    state['cats'][cid]['identity_seed'] = bad_seed
    state['cats'][cid]['coat_profile'] = {'version':999,'future_appearance':'retained'}
    first,second = Engine(state,seed=1),Engine(state,seed=111)
    a,b = first.cats[cid],second.cats[cid]
    assert type(a.identity_seed) is int and 0 <= a.identity_seed <= 0xFFFFFFFF
    assert a.identity_seed == b.identity_seed and a.personality == b.personality
    assert a.coat_warning and a.render_coat == b.render_coat == 'gray'
    saved = first.export();restored = Engine(saved,seed=991)
    assert saved['cats'][cid] == restored.export()['cats'][cid]
    assert saved['cats'][cid]['coat_profile'] == state['cats'][cid]['coat_profile']


def test_missing_identity_seed_with_future_profile_is_not_randomized_on_restart():
    state = Engine(seed=8).export();cid = GENERATED_CAT_IDS[0]
    del state['cats'][cid]['identity_seed']
    state['cats'][cid]['coat_profile'] = {'version':99,'preserve':'future-coat'}
    assert Engine(state,seed=1).export()['cats'][cid] == Engine(state,seed=55).export()['cats'][cid]


def test_valid_seed_recovers_missing_profile_once_and_ignores_legacy_repaint_fields():
    state = Engine(seed=12).export();cid = GENERATED_CAT_IDS[0]
    original = state['cats'][cid]['coat_profile']
    del state['cats'][cid]['coat_profile']
    state['cats']['onyx'].update(identity_seed=10,coat_profile=generate_coat(10).to_dict())
    engine = Engine(state,seed=99)
    assert engine.export()['cats'][cid]['coat_profile'] == original
    assert engine.cats['onyx'].render_coat == 'black' and engine.cats['onyx'].coat_profile is None
    assert 'coat_profile' not in engine.export()['cats']['onyx']
