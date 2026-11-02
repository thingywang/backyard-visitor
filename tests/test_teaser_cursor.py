"""Directional toy pointer and cursor restoration, without OS input injection."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QEvent, QPoint, QRect, Qt
from PyQt6.QtGui import QTransform
from PyQt6.QtWidgets import QApplication

from backyard_visitor.gift_art import LOGICAL_SIZE as GIFT_SIZE
from backyard_visitor.play_session import TeaserSession, _TeaserCanvas, _WidgetTeaserCanvas
from backyard_visitor.teaser_cursor import HOTSPOT, LOGICAL_SIZE, feather_cursor


@pytest.fixture
def qt():
    app = QApplication.instance() or QApplication(['feather-cursor-tests'])
    yield app
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize('ratio', [1, 1.5, 2, 3])
def test_small_feather_has_mirrored_pixels_and_fixed_hotspot(qt, ratio):
    left = feather_cursor(ratio, facing=-1)
    right = feather_cursor(ratio, facing=1)
    assert left.hotSpot() == right.hotSpot() == QPoint(*HOTSPOT)
    assert left.shape() == right.shape() == Qt.CursorShape.BitmapCursor
    assert right.pixmap().deviceIndependentSize().width() == LOGICAL_SIZE < GIFT_SIZE
    assert right.pixmap().deviceIndependentSize().height() == LOGICAL_SIZE < GIFT_SIZE
    assert left.pixmap().toImage() == right.pixmap().transformed(QTransform().scale(-1, 1)).toImage()
    assert left.pixmap().toImage() != right.pixmap().toImage()
    pixels = right.pixmap().toImage()
    assert pixels.pixelColor(0, 0).alpha() == 0
    assert any(pixels.pixelColor(x, y).alpha() for x in range(pixels.width()) for y in range(pixels.height()))


@pytest.fixture(params=['native', 'widget'])
def directional_session(qt, monkeypatch, request):
    import backyard_visitor.play_session as play
    if request.param == 'widget':
        monkeypatch.setattr(play.QApplication, 'platformName', staticmethod(lambda: 'xcb'))
    values = dict(x=-300, y=240, time=10.)
    engine = SimpleNamespace(start_teaser=Mock(return_value=True), stop_teaser=Mock(), cursor_moved=Mock(), clear_cursor=Mock())
    rect = QRect(-900, -120, 900, 700)
    screen = SimpleNamespace(geometry=lambda: QRect(rect), devicePixelRatio=lambda: 2.)
    c = SimpleNamespace(engine=engine, screen=screen, paused=False, _screen_ready=True, _quitting=False, changed=Mock())
    s = TeaserSession(c, 'amber', clock=lambda: values['time'], wall_clock=lambda: values['time'],
                     cursor_position=lambda: QPoint(values['x'], values['y']))
    assert s.start()
    assert isinstance(s._canvas, _TeaserCanvas if request.param == 'native' else _WidgetTeaserCanvas)
    yield s, c, values, rect
    s.stop('test_cleanup')


def test_motion_switches_direction_and_stationary_or_vertical_motion_keeps_it(directional_session):
    s, c, v, rect = directional_session
    canvas = s._canvas
    right = canvas.cursor().pixmap().toImage()
    hotspot = canvas.cursor().hotSpot()
    v['x'] -= 40; s._pulse()
    left = canvas.cursor().pixmap().toImage()
    assert left != right and canvas.cursor().hotSpot() == hotspot
    s._pulse()
    assert canvas.cursor().pixmap().toImage() == left
    v['y'] += 50; s._pulse()
    assert canvas.cursor().pixmap().toImage() == left
    v['x'] += 1; s._pulse()
    assert canvas.cursor().pixmap().toImage() == right
    assert c.changed.call_count == 1
    assert s.remaining == 15
    assert QApplication.overrideCursor() is None


def test_outside_screen_has_original_cursor_and_reentry_keeps_prior_direction(directional_session):
    s, c, v, rect = directional_session
    v['x'] -= 10; s._pulse()
    left = s._canvas.cursor().pixmap().toImage()
    v['x'] = 20; s._pulse()
    assert s._canvas.cursor().shape() == Qt.CursorShape.ArrowCursor
    v['x'] = -10; s._pulse()
    assert s._canvas.cursor().pixmap().toImage() == left
    v['x'] += 1; s._pulse()
    assert s._canvas.cursor().pixmap().toImage() != left


@pytest.mark.parametrize('boundary', ['deadline', 'cancel', 'pause', 'quit', 'screen'])
def test_changed_pointer_is_removed_on_each_lifecycle_exit(directional_session, boundary):
    s, c, v, rect = directional_session
    canvas = s._canvas
    v['x'] -= 30; s._pulse()
    assert canvas.cursor().shape() == Qt.CursorShape.BitmapCursor
    if boundary == 'deadline': v['time'] += 15
    elif boundary == 'cancel': s.stop('cancelled')
    elif boundary == 'pause': c.paused = True
    elif boundary == 'quit': c._quitting = True
    elif boundary == 'screen': rect.translate(100, 100)
    s._pulse()
    assert not s.active and s._canvas is None and not canvas.isVisible()
    assert canvas.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert QApplication.overrideCursor() is None
    assert c.engine.stop_teaser.call_count == 1


def test_direction_changes_reuse_prebuilt_cursors(directional_session, monkeypatch):
    import backyard_visitor.play_session as play
    s, c, v, rect = directional_session
    def fail(*args, **kwargs):
        raise AssertionError('cursor allocation during motion')
    monkeypatch.setattr(play, 'wand_cursor', fail)
    for offset in [-20, 10, -5, 0, 5]:
        v['x'] += offset
        s._pulse()
    assert s.active
    assert c.changed.call_count == 1
