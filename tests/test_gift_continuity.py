"""Visible continuity across mouth release, escape, dragging and restart."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter
from backyard_visitor.gift_art import GiftArt
from backyard_visitor.gift_geometry import carried_gift_rect, GIFT_SIZE
from backyard_visitor.model import Engine, Visitor


@pytest.mark.parametrize('facing', [-1, 1])
@pytest.mark.parametrize('kind', ['toy_mouse', 'feather', 'bottle_cap'])
def test_delivery_preserves_sprite_size_and_orientation(facing, kind):
    qt = QApplication.instance() or QApplication([])
    engine = Engine(seed=4)
    v = Visitor('amber', 200, 200, state='entering', facing=facing,
                target_x=200, target_y=200, carrying_gift=kind,
                next_action=1000, stay_remaining=1000)
    engine.visitors[v.cat_id] = v
    carry = carried_gift_rect(facing)
    engine.tick(.001)
    gift = engine.gifts[0]
    assert gift.facing == facing
    assert carry[2:] == (GIFT_SIZE, GIFT_SIZE)
    assert gift.x-v.x == carry[0]
    assert gift.y-v.y-carry[1] == GIFT_SIZE/2
    # The same local sprite is painted on both sides of the handoff.
    art = GiftArt()
    assert art.frame(kind, facing=facing).toImage() == art.frame(kind, facing=gift.facing).toImage()


@pytest.mark.parametrize('facing', [-1, 1])
def test_direction_survives_hold_release_and_restart(facing):
    engine = Engine(seed=4)
    engine._drop('toy_mouse', 300, 250, 'amber', facing=facing)
    gift = engine.gifts[0]
    for held in (True, False):
        assert engine.set_gift_held(gift.id, held)
        assert gift.facing == facing
    restored = Engine(engine.export(), seed=4).gifts[0]
    assert restored.facing == facing and restored.state == 'idle'
    gift.wait_remaining = 0
    engine.tick(.01)
    assert gift.facing == gift.escape_direction
    engine.set_gift_held(gift.id, True)
    assert gift.facing in (-1, 1) and gift.escape_direction == 0


@pytest.mark.parametrize('raw', [None, True, 0, 2, '1', 1.0, [], {}])
def test_invalid_or_missing_saved_direction_uses_legacy_default(raw):
    engine = Engine(seed=4)
    engine._drop('toy_mouse', 300, 250, 'amber')
    saved = engine.export()
    if raw is None:
        saved['gifts'][0].pop('facing')
    else:
        saved['gifts'][0]['facing'] = raw
    assert Engine(saved, seed=4).gifts[0].facing == -1


@pytest.mark.parametrize('realistic', [False, True])
def test_controller_renders_both_mouse_directions(realistic, tmp_path):
    qt = QApplication.instance() or QApplication([])
    controller = Controller(qt, state_dir=tmp_path, hidden=True,
                            adapter=DesktopAdapter(), enable_native=False)
    try:
        controller.settings['realistic_mouse'] = realistic
        images = []
        for facing in (-1, 1):
            image = QImage(76, 76, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            p = QPainter(image)
            controller.paint_gift(p, QRectF(0, 0, 76, 76), 'toy_mouse', facing=facing)
            p.end()
            images.append(image)
        assert images[0] != images[1]
    finally:
        controller.shutdown()
        qt.processEvents()


def test_paused_mouse_pixels_do_not_follow_wall_clock(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    controller = Controller(qt, state_dir=tmp_path, hidden=True,
                            adapter=DesktopAdapter(), enable_native=False)
    try:
        controller.engine._drop('toy_mouse', 240, 300, 'amber', facing=1)
        gift = controller.engine.gifts[0]
        gift.wait_remaining = 0
        controller.engine.tick(.04)
        controller.changed()
        controller.set_paused(True)
        window = controller.gift_windows[gift.id]
        monkeypatch.setattr('backyard_visitor.overlays.time.monotonic', lambda: 0)
        before = window.grab().toImage()
        monkeypatch.setattr('backyard_visitor.overlays.time.monotonic', lambda: .04)
        after = window.grab().toImage()
        assert before == after
        controller.set_paused(False)
        controller.engine.tick(.04)
        controller.changed()
        assert gift.state == 'escaping'
        assert window.grab().toImage() != before
    finally:
        controller.shutdown()
        qt.processEvents()
