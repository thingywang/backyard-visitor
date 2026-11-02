"""User gestures, live discovery, collection and pending-click cancellation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import Mock
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QLabel
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter

@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication([])
    c = Controller(qt, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(), enable_native=False)
    qt.processEvents()  # Settle native/offscreen startup geometry before any user gesture.
    yield c
    c.shutdown(); qt.processEvents()

def mouse(w, kind, button=Qt.MouseButton.LeftButton, delta=0):
    local = QPointF(w.width()/2, w.height()/2)
    global_pos = QPointF(w.x()+local.x()+delta, w.y()+local.y())
    return QMouseEvent(kind, local, global_pos, button,
        Qt.MouseButton.NoButton if kind == QMouseEvent.Type.MouseButtonRelease else button,
        Qt.KeyboardModifier.NoModifier)

def click(w, button=Qt.MouseButton.LeftButton):
    w.mousePressEvent(mouse(w, QMouseEvent.Type.MouseButtonPress, button))
    w.mouseReleaseEvent(mouse(w, QMouseEvent.Type.MouseButtonRelease, button))

def flush_food(c):
    # Shorten only this already scheduled test timer, not the global Qt setting.
    if c.bowl.feed_click.isActive():
        timeout=QSignalSpy(c.bowl.feed_click.timeout)
        c.bowl.feed_click.setInterval(5)
        assert timeout.wait(500), "Scheduled food gesture did not complete"
    else:
        QTest.qWait(15)

def test_single_double_right_food_are_one_portion_each(c):
    click(c.bowl); flush_food(c)
    assert c.engine.food == 1
    click(c.bowl)  # native first press/release of a double-click
    c.bowl.mouseDoubleClickEvent(mouse(c.bowl, QMouseEvent.Type.MouseButtonDblClick))
    c.bowl.mouseReleaseEvent(mouse(c.bowl, QMouseEvent.Type.MouseButtonRelease))
    flush_food(c)
    assert c.engine.food == 2
    click(c.bowl, Qt.MouseButton.RightButton)
    assert c.engine.food == 3
    click(c.bowl, Qt.MouseButton.RightButton)
    assert c.engine.food == 3

def test_double_click_without_prior_pending_event_still_feeds_once(c):
    c.bowl.mouseDoubleClickEvent(mouse(c.bowl, QMouseEvent.Type.MouseButtonDblClick))
    c.bowl.mouseReleaseEvent(mouse(c.bowl, QMouseEvent.Type.MouseButtonRelease))
    flush_food(c)
    assert c.engine.food == 1

@pytest.mark.parametrize('cancel', ['screen', 'shutdown', 'drag'])
def test_deferred_single_click_is_cancelled_when_context_changes(c, cancel):
    click(c.bowl)
    if cancel == 'screen': c._cancel_screen_drags()
    elif cancel == 'shutdown': c.shutdown()
    else:
        c.bowl.mousePressEvent(mouse(c.bowl, QMouseEvent.Type.MouseButtonPress))
        c.bowl.mouseMoveEvent(mouse(c.bowl, QMouseEvent.Type.MouseMove, delta=30))
        c.bowl.mouseReleaseEvent(mouse(c.bowl, QMouseEvent.Type.MouseButtonRelease, delta=30))
    assert not c.bowl.feed_click.isActive()
    flush_food(c)
    assert c.engine.food == 0

def test_single_cat_click_pets_but_does_not_call_and_carry_release_does_not_pet(c):
    c.engine.open_gate(); c.engine.invite('amber'); c.changed()
    w = c.cat_windows['amber']; v = w.visitor(); v.state = 'idle'
    c.engine.cats['amber'].trust = 60
    c.engine.call_cat = Mock()
    click(w)
    assert c.engine.cats['amber'].trust == 62
    c.engine.call_cat.assert_not_called()
    assert v.state == 'purring' and not w.hold.isActive()
    c.engine.pet = Mock()
    w.mousePressEvent(mouse(w, QMouseEvent.Type.MouseButtonPress)); w.begin_carry()
    assert w.carrying
    w.mouseReleaseEvent(mouse(w, QMouseEvent.Type.MouseButtonRelease))
    c.engine.pet.assert_not_called()

def test_cancelled_cat_press_does_not_pet_on_late_release(c):
    c.engine.open_gate(); c.engine.invite('amber'); c.changed()
    w = c.cat_windows['amber']
    w.mousePressEvent(mouse(w, QMouseEvent.Type.MouseButtonPress))
    c._cancel_screen_drags(); c.engine.pet = Mock()
    w.mouseReleaseEvent(mouse(w, QMouseEvent.Type.MouseButtonRelease))
    c.engine.pet.assert_not_called()

def test_unseen_cat_has_no_card_or_name_and_first_visit_adds_live_card(c):
    j = c.journal
    assert j.cards == []
    texts = {w.text() for w in j.findChildren(QLabel)}
    assert all(cat.display_name not in texts for cat in c.engine.cats.values())
    c.engine.open_gate(); c.engine.invite('amber'); c.changed(); j.refresh()
    assert [card.cat_id for card in j.cards] == ['amber']
    assert j.unknown_neighbors.isHidden()
    c.engine.visitors.clear(); c.changed(); j.refresh()
    assert [card.cat_id for card in j.cards] == ['amber']

def test_collection_is_live_and_discard_does_not_increment(c):
    j = c.journal; j.show_page(4)
    assert '0 件' in j.gift_counts['feather'].text()
    c.engine._drop('feather', 200, 200, 'amber')
    gift = c.engine.gifts[-1]
    assert c.engine.collect_gift(gift.id)
    assert not c.engine.collect_gift(gift.id)
    c.changed(); j.refresh()
    assert '1 件' in j.gift_counts['feather'].text()
    c.engine._drop('feather', 250, 200, 'amber')
    c.engine.discard_gift(c.engine.gifts[-1].id); c.changed(); j.refresh()
    assert '1 件' in j.gift_counts['feather'].text()
