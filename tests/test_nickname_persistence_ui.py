"""Literal user names survive the ordinary handbook and isolated save chain."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel

from backyard_visitor import app
from backyard_visitor.desktop import DesktopAdapter
from backyard_visitor.storage import load_state


@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication(['nickname-persistence-tests'])
    controller = app.Controller(qt, state_dir=tmp_path, hidden=True,
                                adapter=DesktopAdapter(), enable_native=False, seed=5)
    qt.processEvents()
    controller.timer.stop()
    yield controller
    controller.shutdown()
    qt.processEvents()


def assert_literal_name(card, expected):
    """Compare actual text layout to an explicitly literal reference widget."""
    actual = card.name
    actual.ensurePolished()
    reference = QLabel(expected, card)
    reference.setObjectName(actual.objectName())
    reference.setWordWrap(actual.wordWrap())
    reference.setTextFormat(Qt.TextFormat.PlainText)
    reference.ensurePolished()
    try:
        assert actual.text() == expected
        assert actual.sizeHint() == reference.sizeHint(), 'Nickname was interpreted as formatting'
    finally:
        reference.deleteLater()


def rename(c, monkeypatch, nickname):
    monkeypatch.setattr(app.QInputDialog, 'getText', lambda *args, **kwargs: (nickname, True))
    c.rename_cat('amber')
    c.journal.refresh()
    return c.journal.cards[0]


@pytest.mark.parametrize('nickname', ['<b>小麦</b>', '<i>小团</i>', '<p>豆豆</p>', '小麦 & 团子'])
def test_accepted_nickname_renders_literally_in_live_roster(c, monkeypatch, nickname):
    c.toggle_gate()
    assert c.engine.invite('amber') == 'amber'
    c.changed()
    c.journal.refresh()  # Rename an existing card as well as testing restored construction.
    card = rename(c, monkeypatch, nickname)
    assert_literal_name(card, nickname)
    c.call_cat('amber')
    assert any(nickname+'疑惑' in entry for entry in c._notes)


def click_gift(window):
    local = QPointF(19, 19)
    global_position = QPointF(window.x()+19, window.y()+19)
    for kind, buttons in ((QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                          (QMouseEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
        event = QMouseEvent(kind, local, global_position, Qt.MouseButton.LeftButton,
                            buttons, Qt.KeyboardModifier.NoModifier)
        if kind == QMouseEvent.Type.MouseButtonPress:
            window.mousePressEvent(event)
        else:
            window.mouseReleaseEvent(event)


def test_literal_name_gift_count_and_resident_bed_restore_together(c, monkeypatch):
    # Normal-mode controller. Only rare-event prerequisites are explicit fixtures;
    # this does not claim that 100 trust or the gift were naturally earned.
    assert not c.demo and not c.engine.gate_open and c.journal.cards == []
    c.toggle_gate()
    assert c.engine.invite('amber') == 'amber'
    nickname = '<b>小麦</b>'
    rename(c, monkeypatch, nickname)
    c.engine.cats['amber'].trust = 100
    c.adopt('amber')
    c.place_bed('amber')
    c.move_bed(320)
    c.engine._drop('feather', 200, 200, 'amber')
    gift_id = c.engine.gifts[-1].id
    c.changed()
    click_gift(c.gift_windows[gift_id])
    assert c.engine.collected_count('feather') == 1 and not c.engine.gifts
    c.journal.refresh()
    assert '1 件' in c.journal.gift_counts['feather'].text()
    assert c._save()
    stored = load_state(c.state_path)
    assert stored['cats']['amber']['nickname'] == nickname
    assert stored['resident_bed'] == {'cat_id': 'amber', 'x': 320.0}
    assert stored['gift_collection']['feather'] == 1
    assert all('true_name' not in record for record in stored['cats'].values())
    c.shutdown()
    restored = app.Controller(c.qt, state_dir=c.state_dir, hidden=True,
                              adapter=DesktopAdapter(), enable_native=False, seed=5)
    try:
        assert not restored.demo and not restored.engine.gate_open and not restored.engine.visitors
        assert restored.engine.discovered_cat_ids() == ('amber',)
        card = restored.journal.cards[0]
        assert_literal_name(card, nickname)
        assert restored.engine.resident_id == 'amber'
        assert restored.engine.bed.cat_id == 'amber' and restored.engine.bed.x == 320
        assert restored.bed_window.isVisible() and card.bed_button.text() == '收起猫窝'
        assert restored.engine.collected_count('feather') == 1
        assert '1 件' in restored.journal.gift_counts['feather'].text()
        restored.call_cat('amber')
        assert restored._notes[-1].endswith(f'{nickname}现在不在桌面，等下一次来访再叫{nickname}吧。')
    finally:
        restored.shutdown()
