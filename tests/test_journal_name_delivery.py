"""Offscreen production Controller-to-handbook name delivery, without OS input."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter


@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication([])
    c = Controller(qt, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(), enable_native=False)
    qt.processEvents()
    c.timer.stop()
    yield c
    c.shutdown(); qt.processEvents()


def name_visitor(c, cid='amber', nickname='小麦'):
    c.engine.open_gate()
    assert c.engine.invite(cid) == cid
    assert c.engine.name_cat(cid, nickname) == 'unfamiliar'
    c._events()


def test_pending_journal_replays_named_notes_and_new_entries_update_live(c):
    name_visitor(c)
    c.engine.call_cat('amber'); c._events()
    before = c._notes[-1]
    assert before.endswith('小麦疑惑地看了一眼，又忙自己的事去了。')
    j = c.journal
    assert j.logs.item(0).text() == before
    c.engine.name_cat('amber', '麦麦'); c.engine.call_cat('amber'); c._events()
    assert j.logs.item(0).text().endswith('麦麦疑惑地看了一眼，又忙自己的事去了。')
    assert before in [j.logs.item(i).text() for i in range(j.logs.count())]


def test_absent_named_cat_uses_name_in_controller_only_note(c):
    name_visitor(c)
    c.engine.open_gate(False)
    for _ in range(1600):
        if 'amber' not in c.engine.visitors:
            break
        c.engine.tick(.025)
    assert 'amber' not in c.engine.visitors
    c.call_cat('amber')
    assert any(x.endswith('小麦现在不在桌面，等下一次来访再叫小麦吧。') for x in c._notes)


def test_busy_named_cat_uses_name_without_starting_a_play_session(c):
    name_visitor(c)
    assert c.engine.visitors['amber'].state == 'peeking'
    assert not c.start_play('amber')
    assert c._teaser_session is None
    assert c._notes[-1].endswith('小麦现在正忙，等小麦停下来再一起玩吧。')
