"""The fourth coat is a new neighbour, not a replacement or extra live slot."""
import json

from backyard_visitor.model import Engine, Gift, MAX_VISITORS, GENERATED_CAT_IDS
from backyard_visitor.storage import load_state, save_state


OLD_PROFILES = {
    'amber': ('ginger', 'mischievous'),
    'mist': ('gray', 'shy'),
    'ink': ('tuxedo', 'graceful'),
}
BLACK_ID = 'onyx'


def test_new_black_profile_keeps_existing_neighbours_and_starts_undiscovered():
    engine = Engine(seed=2)
    assert tuple(engine.cats) == (*OLD_PROFILES, BLACK_ID, *GENERATED_CAT_IDS)
    assert {cid: (engine.cats[cid].coat, engine.cats[cid].personality)
            for cid in OLD_PROFILES} == OLD_PROFILES
    cat = engine.cats[BLACK_ID]
    assert (cat.id, cat.coat, cat.personality, cat.display_name) == (
        BLACK_ID, 'black', 'graceful', '黑色访客')
    assert engine.discovered_cat_ids() == ()
    assert cat.trust == cat.visits == 0 and not cat.adopted
    assert engine.name_cat(BLACK_ID, '新邻居') == 'undiscovered'
    assert engine.call_cat(BLACK_ID) == 'undiscovered'
    assert not engine.adopt(BLACK_ID)
    assert not engine.drain_events()


def test_black_first_arrival_uses_edge_entry_and_only_unlocks_after_invitation():
    engine = Engine(seed=2)
    assert engine.invite(BLACK_ID) is None
    assert not engine.is_discovered(BLACK_ID)
    engine.open_gate()
    assert engine.invite(BLACK_ID) == BLACK_ID
    visitor = engine.visitors[BLACK_ID]
    assert visitor.state == 'peeking'
    assert visitor.x < 0 or visitor.x + 144 > engine.bounds[2]
    assert engine.discovered_cat_ids() == (BLACK_ID,)
    assert engine.cats[BLACK_ID].visits == 1
    assert sum(e['kind'] == 'discovered' for e in engine.drain_events()) == 1
    assert engine.invite(BLACK_ID) is None
    assert engine.cats[BLACK_ID].visits == 1


def test_black_neighbour_does_not_increase_simultaneous_visitor_capacity():
    engine = Engine(seed=4)
    assert MAX_VISITORS == engine.visitor_limit == 3
    engine.open_gate()
    assert engine.invite('amber') == 'amber'
    assert engine.invite('mist') == 'mist'
    assert engine.invite('ink') == 'ink'
    assert engine.invite(BLACK_ID) is None
    assert not engine.is_discovered(BLACK_ID)
    assert len(engine.visitors) == 3


def test_three_cat_save_keeps_relationships_bed_food_and_gifts_when_black_is_added(tmp_path):
    # Build a valid old-schema save and explicitly remove the new profile.
    old = Engine(seed=8)
    for index, cid in enumerate(OLD_PROFILES):
        cat = old.cats[cid]
        cat.discovered = True
        cat.neighbor_status = "neighbor"
        cat.visits = index + 3
        cat.trust = 100 if cid == 'ink' else 20 + index
        cat.nickname = f'旧邻居{index}'
        cat.meal_cooldown_remaining = 30 + index
    assert old.adopt('ink') and old.place_bed('ink', 420)
    old.feed()
    old.gifts.append(Gift('legacy-feather', 'feather', 200, 300, 'amber'))
    old._collection_counts['feather'] = 7
    state = old.export()
    state['cats'] = {cid: state['cats'][cid] for cid in OLD_PROFILES}
    path = tmp_path / 'old-state.json'
    save_state(path, state)
    restored = Engine(load_state(path), seed=12)
    current = restored.export()
    assert {cid: current['cats'][cid] for cid in OLD_PROFILES} == state['cats']
    for field in ('resident_bed', 'food', 'bowl', 'gifts', 'gift_collection'):
        assert current[field] == state[field]
    assert restored.resident_id == 'ink'
    assert restored.discovered_cat_ids() == tuple(OLD_PROFILES)
    black = restored.cats[BLACK_ID]
    assert black.coat == 'black' and black.nickname == ''
    assert black.trust == black.visits == 0 and not black.discovered and not black.adopted
    assert not restored.gate_open and not restored.visitors


def test_black_adoption_nickname_and_bed_survive_restart_without_exporting_hidden_name(tmp_path):
    engine = Engine(seed=3)
    engine.open_gate()
    assert engine.invite(BLACK_ID) == BLACK_ID
    assert engine.name_cat(BLACK_ID, '桌边新邻居') == 'unfamiliar'
    engine.cats[BLACK_ID].trust = 100  # Explicit relationship fixture, not natural growth.
    assert engine.adopt(BLACK_ID) and engine.place_bed(BLACK_ID, 600)
    assert all(weight == .2*engine.relations.co_visit_factor(cid, BLACK_ID)
               for cid, weight in engine.visit_weights().items())
    state = engine.export()
    payload = json.dumps(state, ensure_ascii=False)
    assert 'true_name' not in payload
    # The fixed four coats still need no serialized skin; generated neighbours
    # now persist their own complete appearance without replacing the black cat.
    assert all('coat_profile' not in state['cats'][cid]
               for cid in (*OLD_PROFILES, BLACK_ID))
    assert all('coat_profile' in state['cats'][cid] for cid in GENERATED_CAT_IDS)
    # Do not display any secret value even if an assertion fails.
    secrets_absent = all(cat.true_name not in payload for cat in engine.cats.values())
    assert secrets_absent
    path = tmp_path / 'state.json'
    save_state(path, state)
    restored = Engine(load_state(path), seed=8)
    assert restored.resident_id == BLACK_ID
    assert restored.cats[BLACK_ID].coat == 'black'
    assert restored.cats[BLACK_ID].display_name == '桌边新邻居'
    assert restored.cats[BLACK_ID].recognizes_name
    assert restored.bed.cat_id == BLACK_ID and restored.bed.x == 600
    assert restored.bed.sleep_fraction == .45  # Reuses the graceful temperament.
    assert restored.visit_weights()[BLACK_ID] == 100
    assert not restored.gate_open and not restored.visitors


def test_black_trust_enters_existing_weighting_and_hidden_name_is_stable():
    engine = Engine(seed=6)
    weight = engine.visit_weights()[BLACK_ID]
    engine.cats[BLACK_ID].trust = 80
    assert engine.visit_weights()[BLACK_ID] > weight
    same_secret = engine.cats[BLACK_ID].true_name == Engine(seed=42).cats[BLACK_ID].true_name
    assert same_secret
    assert not bool(engine.cats[BLACK_ID].true_name in repr(engine.cats[BLACK_ID]))
