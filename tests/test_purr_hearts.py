"""Visible affection feedback uses existing scene time, clip and cat window."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QPoint, QRect, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from backyard_visitor.affection_art import paint_purr_hearts
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter

_QT = None


def rgba(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return image.constBits().asstring(image.sizeInBytes())


def painted(age, clip=None):
    image = QImage(144, 112, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if clip is not None:
        painter.setClipRect(clip)
    paint_purr_hearts(painter, age)
    painter.end()
    return image


def occupied(image):
    return [(x, y) for y in range(image.height()) for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0]


def test_small_hearts_move_only_above_body_and_respect_clip():
    first, later = painted(.2), painted(.6)
    assert rgba(first) != rgba(later)
    for time in [0, .2, .6, 1.1, 2.39, 4.0, 1000]:
        pixels = occupied(painted(time))
        assert pixels and len(pixels) < 230
        assert all(0 <= x < 144 and 0 <= y < 48 for x, y in pixels)
    clip = QRect(0, 0, 35, 30)
    clipped = occupied(painted(.6, clip))
    assert clipped and all(clip.contains(x, y) for x, y in clipped)


def test_painter_state_restored_and_bad_time_draws_nothing():
    image = QImage(144, 112, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    p = QPainter(image)
    p.setOpacity(.4)
    p.setPen(QColor('#123456'))
    p.translate(2, 3)
    transform, pen = p.transform(), p.pen()
    paint_purr_hearts(p, .5)
    assert p.opacity() == pytest.approx(.4)
    assert p.transform() == transform and p.pen() == pen
    p.end()
    assert not occupied(painted(float('nan')))


@pytest.fixture
def controller(tmp_path):
    global _QT
    _QT = QApplication.instance() or QApplication([])
    qt = _QT
    c = Controller(qt, state_dir=tmp_path, hidden=True,
                   adapter=DesktopAdapter(), enable_native=False, seed=0,
                   visit_limit=1)
    e = c.engine
    e.calm_mode = True
    e.cats['mist'].trust = 100
    e.open_gate(); e.invite('mist')
    v = e.visitors['mist']
    v.x = v.target_x = e.bounds[0] + 220
    v.y = v.target_y = e.floor_y
    v.state = 'idle'; v.state_age = 0
    v.next_action = 100; v.stay_remaining = 1000
    v.carrying_gift = None
    c.changed()
    yield c, v
    c.shutdown(); qt.processEvents()


def actual_without_hearts(window):
    # A recording Mock would retain the active QPainter after paintEvent
    # returns, outliving QWidget.grab's temporary paint device. Use a plain
    # no-op so every painter is destroyed before that temporary is released.
    with patch('backyard_visitor.affection_art.paint_purr_hearts',
               new=lambda *args, **kwargs: None):
        return window.grab().toImage()


def test_actual_cat_window_shows_hearts_then_cleans_up_without_new_timers(controller):
    c, v = controller
    w = c.cat_windows['mist']
    timers = set(c.findChildren(QTimer)) | set(w.findChildren(QTimer))
    windows = set(QApplication.topLevelWidgets())
    assert c.engine.pet('mist')
    c.changed()
    with_hearts = w.grab().toImage()
    without_hearts = actual_without_hearts(w)
    assert rgba(with_hearts) != rgba(without_hearts)
    assert rgba(with_hearts.copy(QRect(0, 48, 144, 64))) == rgba(without_hearts.copy(QRect(0, 48, 144, 64)))
    assert set(c.findChildren(QTimer)) | set(w.findChildren(QTimer)) == timers
    assert set(QApplication.topLevelWidgets()) == windows
    assert c.engine.carry('mist')
    c.changed()
    assert rgba(w.grab().toImage()) == rgba(actual_without_hearts(w))


def test_actual_paused_overlay_repaint_does_not_advance_hearts(controller):
    c, v = controller
    c.pet('mist'); c.set_paused(True)
    w = c.cat_windows['mist']
    before, age = w.grab().toImage(), v.state_age
    QTest.qWait(80)
    w.update(); c.qt.processEvents()
    assert v.state_age == age
    assert rgba(w.grab().toImage()) == rgba(before)


def test_refused_low_trust_pet_does_not_show_hearts(controller):
    c, v = controller
    c.engine.cats['mist'].trust = 0
    assert not c.engine.pet('mist')
    c.changed()
    w = c.cat_windows['mist']
    assert v.state != 'purring'
    assert rgba(w.grab().toImage()) == rgba(actual_without_hearts(w))


def test_left_gift_bubble_stays_clear_of_hearts(controller):
    c, v = controller
    c.pet('mist')
    v.facing = -1
    v.gift_response_remaining = 2
    c.changed()
    w = c.cat_windows['mist']
    shown, plain = w.grab().toImage(), actual_without_hearts(w)
    assert rgba(shown) != rgba(plain)
    bubble = QRect(7, 2, 55, 23)
    assert rgba(shown.copy(bubble)) == rgba(plain.copy(bubble))


def test_existing_input_region_contains_hearts_without_expansion(controller):
    c, v = controller
    w = c.cat_windows['mist']
    region_before = w.mask()
    c.pet('mist')
    w.refresh_input_region()
    assert w.mask() == region_before
    shown, plain = w.grab().toImage(), actual_without_hearts(w)
    different = [(x, y) for y in range(112) for x in range(144)
                 if shown.pixelColor(x, y) != plain.pixelColor(x, y)]
    assert different and all(w.mask().contains(QPoint(x, y)) for x, y in different)
    # Native/widget masks and painter clipping both keep the original surface
    # boundary. This must not enlarge the interaction footprint to fit hearts.
    v.clip_y = v.y+30
    w.refresh_input_region()
    shown, plain = w.grab().toImage(), actual_without_hearts(w)
    different = [(x, y) for y in range(112) for x in range(144)
                 if shown.pixelColor(x, y) != plain.pixelColor(x, y)]
    assert different and all(y < 30 and w.mask().contains(QPoint(x, y)) for x, y in different)


def test_heart_comparison_does_not_outlive_grab_paint_device(controller):
    from PyQt6.QtCore import qInstallMessageHandler
    c, v = controller
    c.pet('mist')
    window = c.cat_windows['mist']
    messages = []
    previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    try:
        for _ in range(3):
            assert not actual_without_hearts(window).isNull()
    finally:
        qInstallMessageHandler(previous)
    assert not [message for message in messages
                if 'QPaintDevice' in message or 'being painted' in message]
