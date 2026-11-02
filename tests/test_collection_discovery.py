"""First meetings and explicitly received gifts have independent durable records."""
import json

import pytest

from backyard_visitor.model import (Engine, Gift, COLLECTIBLE_GIFTS, GIFT_DISPLAY_NAMES,
                                    MAX_COLLECTION_COUNT, MAX_VISITS)
from backyard_visitor.storage import load_state, save_state


def discover(engine, cid='amber'):
    engine.open_gate()
    assert engine.invite(cid)==cid
    return engine.cats[cid]


def test_initial_roster_is_locked_and_relationship_actions_cannot_bypass_it():
    e=Engine(seed=4)
    assert e.discovered_cat_ids()==()
    for cid,cat in e.cats.items():
        assert not e.is_discovered(cid) and not cat.discovered
        before=(cat.trust,cat.nickname,cat.adopted)
        assert e.name_cat(cid,cat.true_name)=='undiscovered'
        assert e.call_cat(cid)=='undiscovered'
        assert not e.adopt(cid)
        assert (cat.trust,cat.nickname,cat.adopted)==before
        cat.trust=100
        assert not e.adopt(cid)  # Trust alone cannot manufacture a first meeting.
    assert not e.drain_events()


def test_only_successful_arrival_unlocks_and_discovery_is_announced_once():
    e=Engine(seed=3,visit_limit=1)
    assert e.invite('amber') is None and not e.is_discovered('amber')
    discover(e)
    assert e.is_discovered('amber') and e.discovered_cat_ids()==('amber',)
    assert e.cats['amber'].visits==1
    assert sum(event['kind']=='discovered' for event in e.drain_events())==1
    assert e.invite('mist') is None and not e.is_discovered('mist')
    e.open_gate(False)
    for _ in range(1000):
        e.tick(.04)
        if not e.visitors:break
    assert not e.visitors and e.is_discovered('amber')
    discover(e)
    assert e.cats['amber'].visits==2
    assert not any(event['kind']=='discovered' for event in e.drain_events())
    assert e.name_cat('amber','小客')=='unfamiliar'
    e.cats['amber'].trust=100
    assert e.adopt('amber')


@pytest.mark.parametrize('visits',[1,37,10**100])
def test_legacy_positive_integer_visits_unlock_without_inventing_collection(visits):
    e=Engine({'version':1,'cats':{'mist':{'visits':visits,'trust':100,'adopted':True}}})
    assert e.is_discovered('mist') and e.cats['mist'].visits==min(visits,MAX_VISITS)
    assert e.resident_id=='mist' and e.collection_counts==dict.fromkeys(COLLECTIBLE_GIFTS,0)


@pytest.mark.parametrize('visits',[None,False,True,-1,1.5,'2',[],{},float('inf')])
def test_invalid_legacy_visit_count_does_not_unlock(visits):
    e=Engine({'cats':{'amber':{'visits':visits,'nickname':'旧昵称','trust':100,'adopted':True}}})
    assert not e.is_discovered('amber') and e.cats['amber'].visits==0
    assert not e.cats['amber'].adopted and e.resident_id is None
    assert e.name_cat('amber','另一个昵称')=='undiscovered'
    assert not e.cats['amber'].recognizes_name


@pytest.mark.parametrize('flag',[None,False,0,1,'true',[],{}])
def test_discovery_field_requires_a_real_boolean(flag):
    e=Engine({'cats':{'amber':{'discovered':flag}}})
    assert not e.is_discovered('amber')


def test_explicit_discovery_and_versionless_list_migration_are_supported():
    e=Engine({'cats':[{'id':'amber','discovered':True},{'id':'mist','visits':2}]})
    assert e.discovered_cat_ids()==('amber','mist')
    assert not e.is_discovered('unknown') and not e.is_discovered([])
    restored=Engine(e.export())
    assert restored.discovered_cat_ids()==e.discovered_cat_ids()
    assert e.name_cat('amber','小客')=='unfamiliar'


def test_collection_counts_only_successful_collect_and_never_decrements():
    e=Engine(seed=3)
    assert COLLECTIBLE_GIFTS==('feather','bottle_cap','toy_mouse')
    assert GIFT_DISPLAY_NAMES['toy_mouse']=='小老鼠'
    for i,kind in enumerate(COLLECTIBLE_GIFTS):
        e.gifts.append(Gift(str(i),kind,200,300,'amber'))
        assert e.collect_gift(str(i)) and e.collected_count(kind)==1
        assert not e.collect_gift(str(i)) and e.collected_count(kind)==1
        e.gifts.append(Gift('discard-'+kind,kind,200,300,'amber'))
        assert e.discard_gift('discard-'+kind) and e.collected_count(kind)==1
    e.gifts.append(Gift('fur','fur',200,300,'amber'))
    assert e.collect_gift('fur') and e.collected_count('fur')==0
    mouse=Gift('escape','toy_mouse',e.bounds[2]-1,300,'amber',state='escaping',escape_direction=1,facing=1)
    e.gifts.append(mouse);e.tick(.04)
    assert not e.gifts and e.collection_counts==dict.fromkeys(COLLECTIBLE_GIFTS,1)
    e.gifts.append(Gift('next','feather',200,300,'amber'));e.collect_gift('next')
    assert e.collected_count('feather')==2


def test_held_mouse_is_counted_once_when_collected_and_snapshot_cannot_mutate_counts():
    e=Engine();e.gifts.append(Gift('mouse','toy_mouse',200,300,'amber'))
    assert e.set_gift_held('mouse',True)
    assert e.collect_gift('mouse') and not e.collect_gift('mouse')
    assert not e.set_gift_held('mouse',False)
    snapshot=e.collection_counts;snapshot['toy_mouse']=99
    assert e.collected_count('toy_mouse')==1
    assert e.collected_count('unknown')==0 and e.collected_count([])==0


@pytest.mark.parametrize('value',[None,True,False,-10,1.5,'3',[],{},float('inf'),float('nan')])
def test_collection_restores_only_nonnegative_integer_counts(value):
    e=Engine({'gift_collection':{'feather':value,'bottle_cap':2,'alien':5,'fur':3}})
    assert e.collection_counts=={'feather':0,'bottle_cap':2,'toy_mouse':0}
    assert set(e.export()['gift_collection'])==set(COLLECTIBLE_GIFTS)


@pytest.mark.parametrize('collection',[None,[],True,'bad'])
def test_malformed_collection_container_is_empty(collection):
    e=Engine({'gift_collection':collection})
    assert e.collection_counts==dict.fromkeys(COLLECTIBLE_GIFTS,0)


def test_huge_collection_values_are_bounded_and_collection_never_overflows():
    e=Engine({'gift_collection':{'feather':10**100}})
    assert e.collected_count('feather')==MAX_COLLECTION_COUNT
    e.gifts.append(Gift('f','feather',200,300,'amber'));assert e.collect_gift('f')
    assert e.collected_count('feather')==MAX_COLLECTION_COUNT
    json.dumps(e.export(),allow_nan=False)


def test_existing_dropped_gifts_are_not_guessed_as_previously_received():
    e=Engine({'gifts':[{'id':'old','kind':'feather','x':200,'y':300,'cat_id':'amber'}]})
    assert e.collected_count('feather')==0
    assert e.collect_gift('old') and e.collected_count('feather')==1
    assert not e.is_discovered('amber')


def test_atomic_storage_roundtrip_preserves_discoveries_and_collection_without_secret(tmp_path):
    e=Engine(seed=8);discover(e,'mist')
    e.gifts.append(Gift('received','toy_mouse',200,300,'mist'));e.collect_gift('received')
    state=e.export()
    text=json.dumps(state,ensure_ascii=False)
    for cat in e.cats.values():assert cat.true_name not in text
    path=tmp_path/'state.json';save_state(path,state)
    restored=Engine(load_state(path))
    assert restored.is_discovered('mist') and restored.collected_count('toy_mouse')==1
    assert not restored.gate_open and not restored.visitors
    assert not any('true_name' in str(event) for event in e.drain_events())
