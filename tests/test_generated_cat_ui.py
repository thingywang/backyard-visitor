"""Real Controller migration and roster keep generated identities private and stable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import json

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QLabel

from backyard_visitor.app import Controller
from backyard_visitor.coat_traits import describe_coat
from backyard_visitor.desktop import DesktopAdapter
from backyard_visitor.model import Engine, GENERATED_CAT_IDS
from backyard_visitor.storage import load_state, save_state


@pytest.fixture
def make(tmp_path):
    qt=QApplication.instance() or QApplication(['generated-cat-ui'])
    controllers=[]
    def create(saved=None,folder=None,seed=7):
        folder=folder or tmp_path/f'save-{len(controllers)}'
        if saved is not None:save_state(folder/'state.json',saved)
        c=Controller(qt,state_dir=folder,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=seed)
        controllers.append(c)
        return c
    yield create
    for c in controllers:c.shutdown()
    qt.processEvents()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def cards(c):
    c.journal.refresh()
    return {card.cat_id:card for card in c.journal.cards}


def assert_literal(widget,text):
    assert widget.text()==text and widget.textFormat()==Qt.TextFormat.PlainText
    widget.ensurePolished()
    reference=QLabel(text,widget.parentWidget())
    reference.setObjectName(widget.objectName());reference.setWordWrap(widget.wordWrap())
    reference.setTextFormat(Qt.TextFormat.PlainText);reference.ensurePolished()
    try:assert widget.sizeHint()==reference.sizeHint()
    finally:reference.deleteLater()


@pytest.mark.parametrize('missing',['profile','all_identity_fields'])
def test_closed_gate_migration_saves_new_profiles_even_when_six_cat_relations_already_match(make,tmp_path,missing):
    old=Engine(seed=19).export()
    old['cats']['amber'].update(nickname='旧朋友',trust=44,visits=2,discovered=True)
    old['cats']['amber']['lifecycle']['status']='neighbor'
    for cid in GENERATED_CAT_IDS:
        fields=('coat_profile',) if missing=='profile' else ('coat_profile','identity_seed','personality')
        for key in fields:del old['cats'][cid][key]
    folder=tmp_path/'existing-relations'
    c=make(old,folder=folder)
    assert c.engine.relations.export()==old['cat_relations']
    assert not c.engine.gate_open and not c.engine.visitors and not c.timer.isActive()
    assert c.save_timer.isActive()
    expected=deepcopy(c.engine.export()['cats'])
    spy=QSignalSpy(c.save_timer.timeout)
    assert spy.wait(1500), 'Migration was not atomically saved by the normal timer'
    saved=load_state(c.state_path)
    assert saved['cats']==expected and saved['cat_relations']==old['cat_relations']
    assert saved['cats']['amber']['nickname']=='旧朋友' and saved['cats']['amber']['trust']==44
    assert not c.engine.gate_open and not c.engine.visitors
    c.shutdown()
    restored=make(folder=folder,seed=991)
    assert restored.engine.export()['cats']==expected
    assert restored.engine.relations.export()==old['cat_relations']
    assert all(not restored.engine.cats[cid].discovered for cid in GENERATED_CAT_IDS)
    assert not restored.engine.gate_open and not restored.engine.visitors


@pytest.mark.parametrize('cid',GENERATED_CAT_IDS)
def test_first_actual_arrival_unlocks_generated_card_with_plain_description_and_literal_nickname(make,cid):
    c=make()
    assert cards(c)=={} and not c.engine.cats[cid].discovered
    assert c.engine.name_cat(cid,'还没见面')=='undiscovered'
    appearance=c.engine.cats[cid].coat_profile
    c.engine.open_gate();assert c.engine.invite(cid)==cid
    assert c.engine.cats[cid].is_neighbor  # The very first arrival remains formal.
    c.changed()
    visible=cards(c)
    assert set(visible)=={cid}
    card=visible[cid]
    assert_literal(card.coat_label,describe_coat(appearance))
    assert_literal(card.name,c.engine.cats[cid].display_name)
    assert card.coat_warning.isHidden()
    nickname='<b>小客</b> & 朋友'
    assert c.engine.name_cat(cid,nickname)=='unfamiliar'
    c.changed();card=cards(c)[cid]
    assert_literal(card.name,nickname)
    assert_literal(card.coat_label,describe_coat(appearance))
    assert c.engine.cats[cid].coat_profile is appearance
    assert set(cards(c))=={cid}
    assert c._save()
    saved=load_state(c.state_path)
    c.shutdown();restored=make(saved=saved,seed=99)
    restored_card=cards(restored)[cid]
    assert_literal(restored_card.name,nickname)
    assert_literal(restored_card.coat_label,describe_coat(appearance))
    assert not restored.engine.visitors


@pytest.mark.parametrize('discovered',[False,True])
def test_unreadable_coat_is_warned_while_gate_closed_and_raw_payload_survives_save(make,discovered):
    state=Engine(seed=17).export();cid=GENERATED_CAT_IDS[0]
    raw={'version':999,'map_version':7,'future_palette':{'primary':[22,31,40]},
         'future_pattern':['preserve','exactly']}
    state['cats'][cid].update(coat_profile=raw,discovered=discovered,
                             visits=2 if discovered else 0,nickname='旧称呼' if discovered else '')
    state['cats'][cid]['lifecycle']['status']='neighbor' if discovered else 'guest'
    seed=state['cats'][cid]['identity_seed']
    c=make(state)
    warning=c.engine.cats[cid].coat_warning
    assert warning and not c.engine.gate_open and not c.engine.visitors
    assert sum(warning in note for note in c._notes)==1
    visible=cards(c)
    if discovered:
        assert set(visible)=={cid}
        assert_literal(visible[cid].name,'旧称呼')
        assert_literal(visible[cid].coat_warning,warning)
        assert not visible[cid].coat_warning.isHidden()
    else:
        assert visible=={} and c.engine.discovered_cat_ids()==()
    c.changed();cards(c)
    assert sum(warning in note for note in c._notes)==1
    assert c._save()
    saved=load_state(c.state_path)
    assert saved['cats'][cid]['coat_profile']==raw
    assert saved['cats'][cid]['identity_seed']==seed
    public=json.dumps(saved,ensure_ascii=False)+'\n'.join(c._notes)
    leaked=any(cat.true_name in public for cat in c.engine.cats.values())
    assert not leaked, 'Private name leaked through UI or persisted appearance'
    c.shutdown();restored=make(saved=saved,seed=123)
    assert restored.engine.export()['cats'][cid]==saved['cats'][cid]
    assert sum(warning in note for note in restored._notes)==1
    assert restored.engine.cats[cid].render_coat=='gray'


def test_lazy_render_asset_warning_is_reported_once_without_changing_saved_identity(make):
    from backyard_visitor import procedural_coat
    error='随机毛色素材暂时无法读取，已暂用灰色外观；请保留存档。'
    procedural_coat.load_error=None
    try:
        c=make()
        before=deepcopy(c.engine.export()['cats'])
        assert not c.engine.gate_open and not c.engine.visitors
        procedural_coat.load_error=error
        assert c._events()
        assert sum(error in note for note in c._notes)==1
        for _ in range(3):
            assert not c._events()
        assert sum(error in note for note in c._notes)==1
        procedural_coat.load_error=None
        assert not c._events()
        assert c.engine.export()['cats']==before and c.engine.discovered_cat_ids()==()
        assert cards(c)=={} and not c.engine.gate_open and not c.engine.visitors
        assert c._save()
        assert load_state(c.state_path)['cats']==before
        assert sum(error in note for note in c._notes)==1
    finally:
        procedural_coat.load_error=None
