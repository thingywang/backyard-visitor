"""Scaled local feather/hint, real screen interception and unscaled play time."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QEvent, QPoint, QRect, Qt
from PyQt6.QtGui import QTransform
from PyQt6.QtWidgets import QApplication

from backyard_visitor.display_scale import SceneTransform
from backyard_visitor.play_session import TeaserSession, _hint_geometry, wand_cursor
from backyard_visitor.teaser_cursor import feather_cursor


@pytest.fixture
def qt():
    app = QApplication.instance() or QApplication([])
    yield app
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("scale", [.75, 1., 1.5])
@pytest.mark.parametrize("ratio", [1., 2.])
def test_native_cursor_size_and_hotspot_scale_once_independently_of_dpr(qt, scale, ratio):
    right = wand_cursor(ratio, facing=1, artwork_scale=scale)
    left = feather_cursor(ratio, facing=-1, artwork_scale=scale)
    assert right.pixmap().width() == 28*scale*ratio
    assert right.pixmap().deviceIndependentSize().width() == 28*scale
    assert right.pixmap().deviceIndependentSize().height() == 28*scale
    assert right.hotSpot() == left.hotSpot() == QPoint(round(14*scale), round(14*scale))
    assert left.pixmap().toImage() == right.pixmap().transformed(QTransform().scale(-1, 1)).toImage()
    assert left.pixmap().toImage() != right.pixmap().toImage()


@pytest.mark.parametrize("bad", [None, True, "large", float('nan'), float('inf')])
def test_invalid_artwork_factor_cannot_reach_native_cursor_allocation(qt, bad):
    cursor = feather_cursor(2., artwork_scale=bad)
    assert cursor.pixmap().deviceIndependentSize().width() == 28
    assert cursor.hotSpot() == QPoint(14, 14)


@pytest.fixture(params=[(kind, scale, ratio) for kind in ('native', 'widget')
                        for scale in (.75, 1., 1.5) for ratio in (1., 2.)])
def scaled_session(qt, request, monkeypatch):
    import backyard_visitor.play_session as play
    kind, scale, ratio = request.param
    if kind == 'widget':
        monkeypatch.setattr(play.QApplication, 'platformName', staticmethod(lambda: 'xcb'))
    geometry = QRect(-1440, 30, 1440, 900)
    transform = SceneTransform(scale, geometry.x(), geometry.y())
    state = dict(time=10., point=QPoint(-1000, 500))
    engine = SimpleNamespace(start_teaser=Mock(return_value=True), stop_teaser=Mock(),
        cursor_moved=Mock(), clear_cursor=Mock())
    screen = SimpleNamespace(geometry=lambda: QRect(geometry), devicePixelRatio=lambda: ratio)
    c = SimpleNamespace(engine=engine, screen=screen, artwork_scale=scale,
                        to_scene=transform.to_scene, changed=Mock(), _quitting=False,
                        _screen_ready=True, paused=False)
    s = TeaserSession(c, 'amber', clock=lambda: state['time'], wall_clock=lambda: state['time'],
                     cursor_position=lambda: QPoint(state['point']))
    assert s.start()
    yield s, c, state, geometry, scale, ratio, kind
    s.stop('test_cleanup')


def test_scaled_session_keeps_actual_screen_canvas_and_only_scales_hint_and_pointer(scaled_session):
    s, c, state, geometry, scale, ratio, kind = scaled_session
    assert s._canvas.geometry() == geometry
    assert s._canvas.cursor().pixmap().deviceIndependentSize().width() == 28*scale
    assert c.engine.cursor_moved.call_args.args == pytest.approx((*c.to_scene(-1000, 500), 0))
    if kind == 'native':
        hint = s._hint
        assert hint.geometry() == _hint_geometry(geometry, scale)
        assert geometry.contains(hint.geometry())
        assert hint.width() == round(344*scale) and hint.height() == round(56*scale)
        assert hint.windowFlags() & Qt.WindowType.WindowTransparentForInput
        assert not hint.grab().isNull()
    else:
        assert s._hint is None
        image = s._canvas.grab().toImage()
        # The fallback still paints the hint on a full native canvas; its
        # scaled box is opaque while the rest remains intentionally near-clear.
        box = _hint_geometry(s._canvas.rect(), scale)
        dpr = s._canvas.devicePixelRatioF()
        assert image.pixelColor(round(box.center().x()*dpr), round((box.y()+2*scale)*dpr)).alpha() > 100
        assert image.pixelColor(round(box.center().x()*dpr), round((box.bottom()+5)*dpr)).alpha() <= 1
    assert s.remaining == 15 and c.changed.call_count == 1


def test_scaled_targets_keep_physical_direction_then_restore_cursor_at_fifteen_seconds(scaled_session):
    s, c, state, geometry, scale, ratio, kind = scaled_session
    canvas, hint = s._canvas, s._hint
    right = canvas.cursor().pixmap().toImage()
    state['point'] = QPoint(-1030, 500)
    state['time'] += .2
    s._pulse()
    assert canvas.cursor().pixmap().toImage() != right
    assert c.engine.cursor_moved.call_args.args == pytest.approx((*c.to_scene(-1030, 500), 0))
    state['point'] = QPoint(10, 500)
    s._pulse()
    assert canvas.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert c.engine.clear_cursor.called
    state['point'] = QPoint(-1020, 500)
    s._pulse()
    assert canvas.cursor().shape() == Qt.CursorShape.BitmapCursor
    state['time'] = 25.
    s._pulse()
    assert not s.active and not canvas.isVisible() and s._canvas is None
    assert canvas.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert s._hint is None and (hint is None or not hint.isVisible())
    assert c.engine.stop_teaser.call_count == 1 and QApplication.overrideCursor() is None


def test_hint_stays_inside_small_display_at_large_scale():
    geometry = QRect(-50, -70, 120, 50)
    hint = _hint_geometry(geometry, 2.5)
    assert geometry.contains(hint)
    assert hint.width() == 60 and hint.height() == 50
