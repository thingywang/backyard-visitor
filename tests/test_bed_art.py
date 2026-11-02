"""Resident-bed art keeps the floor geometry and only captures visible alpha."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QRegion
from PyQt6.QtWidgets import QApplication

from backyard_visitor.bed_art import BedArt, PIXEL_RATIO, CORE_ALPHA_MIN, _alpha_bounds, _alpha_rows
from backyard_visitor.resident_bed import BED_HEIGHT, BED_WIDTH


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication(['bed-art-test'])


@pytest.fixture
def asset(tmp_path):
    image = QImage(420, 180, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#b8ab91'))
    painter.drawEllipse(QRectF(40, 40, 340, 85))
    # A fully transparent opening must not be converted into a rectangular hit.
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawEllipse(QRectF(200, 70, 20, 15))
    painter.end()
    path = tmp_path/'bed.png'
    assert image.save(str(path))
    return path


def test_alpha_crop_uniform_fit_and_floor_anchor(qt, asset):
    original = asset.read_bytes()
    art = BedArt(asset)
    frame = art.frame()
    assert frame is not None and art.load_error is None
    assert frame.size().width() == BED_WIDTH*PIXEL_RATIO
    assert frame.size().height() == BED_HEIGHT*PIXEL_RATIO
    assert frame.devicePixelRatio() == PIXEL_RATIO
    assert QRect(40, 40, 340, 85).contains(art.core_rect)
    assert art.source_rect.contains(art.core_rect)
    assert art.source_padding > 0
    assert art.source_rect.width() > 330 and art.source_rect.height() > 80
    assert art.target_rect.width()/art.source_rect.width() == pytest.approx(
        art.target_rect.height()/art.source_rect.height())
    assert art.target_rect.center().x() == BED_WIDTH/2
    assert art.target_rect.bottom() == BED_HEIGHT-1
    assert art.target_rect.left() >= 3 and art.target_rect.top() >= 1
    assert asset.read_bytes() == original
    assert art.frame().cacheKey() == frame.cacheKey()


def test_mask_covers_every_visible_rendered_pixel_and_excludes_empty_corners(qt, asset):
    art = BedArt(asset)
    image = art.frame().toImage()
    region = art.input_region()
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha():
                assert region.contains(QPoint(x//PIXEL_RATIO, y//PIXEL_RATIO))
    for point in (QPoint(0, 0), QPoint(171, 0), QPoint(0, 43), QPoint(171, 43)):
        assert not region.contains(point)
    opening = QPoint(round(art.target_rect.center().x()),
                     round(art.target_rect.y()+(77-art.source_rect.y())*
                           art.target_rect.height()/art.source_rect.height()))
    assert not region.contains(opening)
    region |= QRegion(QRect(0, 0, BED_WIDTH, BED_HEIGHT))
    assert not art.input_region().contains(QPoint(0, 0))


@pytest.mark.parametrize('failure', ['missing', 'broken', 'opaque', 'empty', 'no-alpha'])
def test_invalid_source_reports_failure_and_paints_previous_bed(qt, tmp_path, failure):
    path = tmp_path/'bed.png'
    if failure == 'broken':
        path.write_bytes(b'broken PNG')
    elif failure in {'opaque', 'empty', 'no-alpha'}:
        fmt = QImage.Format.Format_RGB32 if failure == 'no-alpha' else QImage.Format.Format_ARGB32_Premultiplied
        image = QImage(100, 25, fmt)
        image.fill(Qt.GlobalColor.transparent if failure == 'empty' else QColor('#b8ab91'))
        assert image.save(str(path))
    art = BedArt(path)
    assert art.frame() is None and art.load_error
    canvas = QImage(BED_WIDTH, BED_HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    assert not art.paint(painter, QRectF(0, 0, BED_WIDTH, BED_HEIGHT))
    painter.end()
    assert canvas.pixelColor(86, 22).alpha() > 0
    assert art.input_region().contains(QPoint(86, 22))
    assert not art.input_region().contains(QPoint(0, 0))


def test_overlay_uses_art_mask_keeps_floor_geometry_and_hides_when_removed(qt, asset, monkeypatch):
    from backyard_visitor import bed_overlay
    art = BedArt(asset)
    monkeypatch.setattr(bed_overlay, 'BedArt', lambda: art)
    c = SimpleNamespace(settings={'overlay_topmost': True}, paused=False,
        engine=SimpleNamespace(bed=SimpleNamespace(x=500), bed_y=778),
        _screen_ready=True, _quitting=False)
    bed = bed_overlay.CatBed(c)
    try:
        bed.sync_geometry()
        assert bed.geometry() == QRect(414, 756, BED_WIDTH, BED_HEIGHT)
        assert bed.mask() == art.input_region()
        assert bed.art.load_error is None
        assert bed.isVisible()
        c.engine.bed = None
        bed.sync_geometry()
        assert not bed.isVisible() and not bed.drag_active
    finally:
        bed.close(); bed.deleteLater(); qt.processEvents()


def test_installed_photographic_bed_loads_without_fallback(qt):
    from backyard_visitor.bed_art import ASSET
    if not ASSET.exists():
        pytest.skip('Final resident-bed PNG has not been installed yet')
    art = BedArt()
    assert art.frame() is not None and art.load_error is None
    assert not art.source_rect.isEmpty() and not art.input_region().isEmpty()
    assert not art.input_region().contains(QPoint(0, 0))


def test_remote_faint_specks_do_not_shrink_or_lift_visible_body(qt, asset):
    baseline = BedArt(asset)
    assert baseline.frame() is not None
    source = QImage(str(asset))
    source.setPixelColor(0, 0, QColor(230, 20, 10, CORE_ALPHA_MIN-1))
    source.setPixelColor(source.width()-1, source.height()-1, QColor(20, 230, 10, 1))
    assert source.save(str(asset))
    source_bytes = asset.read_bytes()
    with_specks = BedArt(asset)
    assert with_specks.frame() is not None
    assert with_specks.core_rect == baseline.core_rect
    assert with_specks.source_rect == baseline.source_rect
    assert with_specks.target_rect == baseline.target_rect
    assert with_specks.frame().toImage() == baseline.frame().toImage()
    assert asset.read_bytes() == source_bytes


def test_installed_visible_bed_body_is_wide_and_supported_at_floor(qt):
    from backyard_visitor.bed_art import ASSET
    if not ASSET.exists():
        pytest.skip('Final resident-bed PNG has not been installed yet')
    art = BedArt()
    frame = art.frame()
    assert frame is not None and art.load_error is None
    # Measure rendered opaque fabric, independently of the selected source box.
    # A tiny alpha speck cannot satisfy the usable width or floor contact checks.
    body = _alpha_bounds(_alpha_rows(frame.toImage()), 128)
    assert 156 <= body.width()/PIXEL_RATIO <= BED_WIDTH-6
    floor_gap = BED_HEIGHT-(body.y()+body.height())/PIXEL_RATIO
    assert 1 <= floor_gap <= 4
    assert art.source_rect.contains(art.core_rect)
    assert art.core_rect.width() > art.source_rect.width()*.97
