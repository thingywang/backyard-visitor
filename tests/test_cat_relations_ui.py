"""The roster exposes directed relations only between discovered neighbors."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter

@pytest.fixture
def controller(tmp_path):
    qt=QApplication.instance() or QApplication(["cat-relations-ui"])
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=3)
    c.engine.open_gate();c.engine.invite("amber")
    yield c
    c.shutdown();qt.processEvents()

def cards(c):
    c.journal.refresh()
    return {card.cat_id:card for card in c.journal.cards}

def pair(c):
    assert c.engine.cats["amber"].is_neighbor  # First arrival is formally welcomed.
    c.engine.invite("mist")
    # These tests exercise relations between two established neighbours,
    # rather than the separate guest-to-neighbour qualification process.
    c.engine.cats["mist"].neighbor_status = "neighbor"
    c.engine.relations.restore({"schema_version":1,"relations":{
        "amber":{"mist":{"initial":10,"meal_gain":0}},
        "mist":{"amber":{"initial":80,"meal_gain":0}}}})
    return cards(c)

def test_unknown_neighbors_never_appear_as_relation_rows(controller):
    c=controller;visible=cards(c)
    assert set(visible)=={"amber"}
    assert visible["amber"].relationships.isHidden()
    assert visible["amber"].relationships.text()==""
    visible=pair(c)
    assert set(visible)=={"amber","mist"}
    for own,other in (("amber","mist"),("mist","amber")):
        text=visible[own].relationships.text()
        assert c.engine.cats[other].display_name in text
        assert all(cat.display_name not in text for cid,cat in c.engine.cats.items() if cid not in visible)

def test_directional_values_and_common_meal_totals_are_distinct_from_user_trust(controller):
    c=controller;visible=pair(c)
    assert "好感 10" in visible["amber"].relationships.text()
    assert "好感 80" in visible["mist"].relationships.text()
    assert "看不顺眼" in visible["amber"].relationships.text()
    assert "十分喜爱" in visible["mist"].relationships.text()
    before={cid:c.engine.cats[cid].trust for cid in visible}
    c.engine.relations.share_meal("amber","mist")
    visible=cards(c)
    assert "好感 13（共餐累计 +3）" in visible["amber"].relationships.text()
    assert "好感 83（共餐累计 +3）" in visible["mist"].relationships.text()
    assert {cid:c.engine.cats[cid].trust for cid in visible}==before

def test_relation_names_are_plain_text_and_refresh_after_renaming(controller):
    c=controller;visible=pair(c)
    c.engine.name_cat("mist","<b>小灰</b> & 伙伴")
    visible=cards(c)
    assert "<b>小灰</b> & 伙伴" in visible["amber"].relationships.text()
    assert visible["amber"].relationships.textFormat()==Qt.TextFormat.PlainText
    c.engine.name_cat("mist","灰灰")
    visible=cards(c)
    assert "对 灰灰：" in visible["amber"].relationships.text()
    assert "<b>" not in visible["amber"].relationships.text()

def test_controller_storage_roundtrip_retains_relations_and_only_known_cards(controller):
    c=controller;pair(c);c.engine.relations.share_meal("amber","mist")
    before=c.engine.relations.export();assert c._save();c.shutdown()
    other=Controller(c.qt,state_dir=c.state_dir,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=91)
    try:
        assert other.engine.relations.export()==before
        visible=cards(other)
        assert set(visible)=={"amber","mist"}
        assert "好感 13（共餐累计 +3）" in visible["amber"].relationships.text()
        assert not other.engine.gate_open and not other.engine.visitors
    finally:other.shutdown()


def test_legacy_random_relations_are_saved_without_opening_the_gate(tmp_path):
    import json
    from PyQt6.QtTest import QTest
    qt=QApplication.instance() or QApplication(["cat-relations-migration"])
    path=tmp_path/"state.json"
    path.write_text(json.dumps({"version":1,"cats":{"amber":{"visits":2,"nickname":"旧昵称","trust":42}}}))
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=7)
    try:
        expected=c.engine.relations.export()
        assert not c.engine.gate_open and not c.engine.visitors
        QTest.qWait(800)
        saved=json.loads(path.read_text())
        assert saved["cat_relations"]==expected
        assert saved["cats"]["amber"]["nickname"]=="旧昵称"
        assert saved["cats"]["amber"]["trust"]==42
    finally:c.shutdown();qt.processEvents()



def test_second_arrival_remains_a_guest_without_handbook_controls_or_relation_rows(controller):
    c = controller
    assert c.engine.cats['amber'].is_neighbor
    assert c.engine.invite('mist') == 'mist'
    assert not c.engine.cats['mist'].is_neighbor
    visible = cards(c)
    guest = visible['mist']
    assert guest.identity.text() == '过客'
    assert guest.rename_button.isHidden() and guest.call_button.isHidden()
    assert guest.adopt.isHidden() and guest.trust.isHidden()
    assert guest.relationships.isHidden()
    assert visible['amber'].relationships.text() == ''
