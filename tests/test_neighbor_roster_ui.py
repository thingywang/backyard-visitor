"""Seen passers-by remain in the roster without neighbour-only controls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from backyard_visitor.app import Controller
from backyard_visitor.coat_traits import CoatSpec
from backyard_visitor.desktop import DesktopAdapter


@pytest.fixture
def ui(tmp_path, monkeypatch):
    from backyard_visitor import portrait_art
    qt = QApplication.instance() or QApplication(['neighbor-roster-ui'])
    calls = []
    def portrait(cat):
        calls.append((cat.id, cat.render_coat))
        pix = QPixmap(288, 336)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.fillRect(70, 10, 148, 316, QColor('#52614a'))
        painter.end()
        pix.setDevicePixelRatio(2)
        return pix
    monkeypatch.setattr(portrait_art, 'portrait_frame', portrait)
    c = Controller(qt, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(),
                   enable_native=False, seed=7)
    yield c, calls
    c.shutdown()
    qt.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def seen(c, cid, status, order):
    cat = c.engine.cats[cid]
    cat.discovered = True
    cat.visits = max(1, cat.visits)
    cat.neighbor_status = status
    cat.last_seen_order = order
    return cat


def roster(c):
    journal = c.journal
    journal.refresh()
    journal.show_page(1)
    journal.show()
    QApplication.processEvents()
    return {card.cat_id: card for card in journal.cards}


def visible_text(card):
    return '\n'.join(label.text() for label in card.findChildren(QLabel)
                     if label.isVisibleTo(card))


def test_unseen_stays_locked_but_departed_guest_keeps_portrait_and_record(ui):
    c, calls = ui
    assert roster(c) == {}
    assert calls == []
    cat = seen(c, 'generated-a', 'guest', 5)
    first = roster(c)[cat.id]
    assert first.identity.text() == '过客'
    assert not first.portrait.pixmap().isNull()
    cat.neighbor_status = 'departed'
    cards = roster(c)
    assert list(cards) == [cat.id]
    assert cards[cat.id].identity.text() == '已离开的过客'
    assert '留下这次相遇' in cards[cat.id].guest_hint.text()
    assert len(calls) == 1  # Status refresh does not reload the same photo.
    assert c.journal.unknown_neighbors.isHidden()


@pytest.mark.parametrize('status', ['guest', 'departed'])
def test_non_neighbours_have_no_handbook_actions_nickname_or_affection(ui, status):
    c, _ = ui
    cat = seen(c, 'generated-a', status, 1)
    cat.trust = 100
    cat.nickname = '不应暴露的旧昵称'
    card = roster(c)[cat.id]
    assert not any(button.isVisibleTo(card) for button in card.findChildren(QPushButton))
    assert not card.trust.isVisibleTo(card)
    assert not card.status.isVisibleTo(card)
    assert not card.relationships.isVisibleTo(card)
    text = visible_text(card)
    assert '信任' not in text and '好感' not in text and cat.nickname not in text
    assert card.name.textFormat() == Qt.TextFormat.PlainText
    if status == 'guest':
        assert '食物' in card.guest_hint.text() and '正式邻居' in card.guest_hint.text()


def test_promotion_refresh_exposes_existing_neighbour_actions_and_literal_name(ui):
    c, _ = ui
    cat = seen(c, 'generated-a', 'guest', 1)
    card = roster(c)[cat.id]
    assert card.interactions.isHidden()
    cat.neighbor_status = 'neighbor'
    cat.trust = 100
    cat.nickname = '<b>小客</b> & 朋友'
    c.rename_cat = Mock()
    c.call_cat = Mock()
    c.adopt = Mock()
    card = roster(c)[cat.id]
    assert card.identity.text() == '正式邻居'
    assert card.name.text() == cat.nickname and card.name.textFormat() == Qt.TextFormat.PlainText
    assert card.guest_hint.isHidden()
    assert card.trust.isVisibleTo(card) and card.trust.value() == 100
    assert card.rename_button.isVisibleTo(card) and card.call_button.isVisibleTo(card)
    assert card.adopt.isVisibleTo(card) and card.adopt.isEnabled()
    card.rename_button.click(); card.call_button.click(); card.adopt.click()
    c.rename_cat.assert_called_once_with(cat.id)
    c.call_cat.assert_called_once_with(cat.id)
    c.adopt.assert_called_once_with(cat.id)


def test_roster_order_pins_resident_then_uses_latest_encounter_and_keeps_departed(ui):
    c, _ = ui
    resident = seen(c, 'amber', 'neighbor', 1)
    resident.adopted = True; resident.trust = 100
    seen(c, 'mist', 'neighbor', 20)
    seen(c, 'generated-a', 'guest', 30)
    seen(c, 'generated-b', 'departed', 25)
    assert list(roster(c)) == ['amber', 'generated-a', 'generated-b', 'mist']
    assert c.journal.cards[0].identity.text() == '常住伙伴'
    c.engine.cats['mist'].last_seen_order = 40
    assert list(roster(c)) == ['amber', 'mist', 'generated-a', 'generated-b']
    assert not any(card.cat_id in ('ink', 'onyx') for card in c.journal.cards)


def test_relationship_rows_exclude_non_neighbours_without_losing_known_neighbour_data(ui):
    c, _ = ui
    a = seen(c, 'amber', 'neighbor', 1)
    b = seen(c, 'mist', 'neighbor', 2)
    seen(c, 'generated-a', 'guest', 3)
    seen(c, 'generated-b', 'departed', 4)
    a.nickname, b.nickname = '橙橙', '灰灰'
    c.engine.relations.restore({'schema_version': 1, 'relations': {
        a.id: {b.id: {'initial': 9, 'meal_gain': 0}}}})
    card = roster(c)[a.id]
    assert card.relationships.isVisibleTo(card)
    assert '对 灰灰：不共戴天' in card.relationships.text()
    assert '/100' not in card.relationships.text()
    assert len(card.relationships.text().splitlines()) == 2


def test_portrait_stays_full_height_crisp_and_uses_current_coat_profile(ui, monkeypatch):
    c, calls = ui
    cat = seen(c, 'generated-a', 'guest', 1)
    card = roster(c)[cat.id]
    image = card.portrait.pixmap()
    assert (card.portrait.width(), card.portrait.height()) == (120, 140)
    # Qt's QLabel getter may return a pixmap normalized to this screen's DPR;
    # check its logical geometry and also capture the high-resolution input.
    assert image.width() / image.devicePixelRatio() == 120
    assert image.height() / image.devicePixelRatio() == 140
    inputs = []
    original_set = QLabel.setPixmap
    def capture(label, pixmap):
        if label is card.portrait:
            inputs.append((pixmap.width(), pixmap.height(), pixmap.devicePixelRatio()))
        return original_set(label, pixmap)
    monkeypatch.setattr(QLabel, 'setPixmap', capture)
    assert calls[-1] == (cat.id, cat.render_coat)
    assert '全身照' in card.portrait.accessibleName()
    old = cat.coat_profile
    cat.coat_profile = CoatSpec(912, pigment='orange')
    roster(c)
    assert calls[-1] == (cat.id, cat.coat_profile) and len(calls) == 2
    assert inputs == [(240, 280, 2)]
    cat.coat_profile = old


def test_missing_portrait_shows_placeholder_without_side_pose_fallback(ui, monkeypatch):
    from backyard_visitor import portrait_art
    c, _ = ui
    monkeypatch.setattr(portrait_art, 'portrait_frame', lambda cat: QPixmap())
    cat = seen(c, 'generated-a', 'guest', 1)
    card = roster(c)[cat.id]
    assert card.portrait.pixmap().isNull()
    assert card.portrait.text() == '照片暂不可用'
    assert card.identity.text() == '过客' and card.interactions.isHidden()



def test_qualified_guest_waiting_for_slot_still_has_no_neighbour_controls(ui, monkeypatch):
    c, _ = ui
    cat = seen(c, 'generated-a', 'guest', 1)
    monkeypatch.setattr(c.engine, 'guest_waiting_for_slot', lambda cid: cid == cat.id)
    card = roster(c)[cat.id]
    assert card.identity.text() == '过客 · 等待邻居名额'
    assert '正式邻居名额' in card.guest_hint.text()
    assert card.rename_button.isHidden() and card.adopt.isHidden()
    assert card.trust.isHidden() and card.relationships.isHidden()
