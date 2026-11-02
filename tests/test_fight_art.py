"""Shared smoke visuals: real coat tails, stable mask, and model-owned clock."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt
from backyard_visitor.fight_art import FightArt, cloud_path, scene_mask, tail_slots


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication(['fight-art-test'])


@pytest.fixture(scope='module')
def art(qt):
    return FightArt(CatArt().tail)


def pixels(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return rgba.constBits().asstring(rgba.sizeInBytes())


def alpha(image):
    return pixels(image)[3::4]


def render(art, rect, coats=('ginger', 'black'), age=0., dpr=1):
    canvas = QImage(round(rect.right()*dpr), round(rect.bottom()*dpr), QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent); canvas.setDevicePixelRatio(dpr)
    painter = QPainter(canvas)
    metrics = art.paint(painter, rect, coats, age)
    painter.end()
    return canvas, metrics


def path_image(path, rect, dpr=1):
    image = QImage(round(rect.right()*dpr), round(rect.bottom()*dpr), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent); image.setDevicePixelRatio(dpr)
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('white')); painter.drawPath(path)
    painter.end()
    return image


@pytest.mark.parametrize('size', [(144, 112), (180, 140), (337, 140), (300, 200)])
@pytest.mark.parametrize('dpr', [1, 2])
def test_stable_mask_covers_visible_pixels_for_swing_and_effect_phases(art, size, dpr):
    rect = QRectF(0, 0, *size)
    mask = scene_mask(rect)
    mask_alpha = alpha(path_image(mask, rect, dpr))
    for age in (0., .12, .30, .44, .62, .85, 1.2, 1.7, 2.6, 3.4, 4.7):
        image, metrics = render(art, rect, age=age, dpr=dpr)
        assert metrics['tail_count'] == 2 and metrics['cloud_count'] == 1
        assert all(not visible or covered for visible, covered in zip(alpha(image), mask_alpha))
    assert mask == scene_mask(rect)
    for x, y in ((.5, .5), (rect.width()-.5, .5), (.5, rect.height()-.5),
                 (rect.width()-.5, rect.height()-.5)):
        assert not mask.contains(QPointF(x, y))


def test_cloud_is_one_connected_component_with_hidden_tail_roots():
    rect = QRectF(0, 0, 337, 140)
    path = cloud_path(rect)
    assert len(path.toSubpathPolygons()) == 1
    for slot in tail_slots(rect, .4):
        assert path.contains(slot.root)
        assert not slot.exposed_path.isEmpty()
    assert all(path.contains(QPointF(x, rect.height()*.64)) for x in range(20, 318))


@pytest.mark.parametrize('coat', ['ginger', 'gray', 'tuxedo', 'black'])
def test_tail_variants_keep_real_source_pixels_and_small_coat_cache(art, coat):
    from backyard_visitor import black_coat
    frame = art.tail_frame(coat)
    assert frame is not None and not frame.isNull()
    assert frame.size() == art._source.size()
    assert frame.devicePixelRatio() == art._source.devicePixelRatio()
    assert art.tail_frame(coat).cacheKey() == frame.cacheKey()
    if coat == 'black':
        assert frame.toImage() == black_coat.black_frame(art._source).toImage()
        assert black_coat.load_error is None and art.load_error is None
    elif coat == 'ginger':
        assert frame.toImage() == art._source.toImage()
    else:
        assert alpha(frame.toImage()) == alpha(art._source.toImage())
        assert pixels(frame.toImage()) != pixels(art._source.toImage())
    assert len(art._variants) <= 4


def test_both_tails_are_visible_and_coat_order_changes_only_their_side(art):
    rect = QRectF(0, 0, 337, 140)
    image, metrics = render(art, rect, coats=('ginger', 'black'), age=.3, dpr=2)
    swapped, other = render(art, rect, coats=('black', 'ginger'), age=.3, dpr=2)
    assert [tail['coat'] for tail in metrics['tails']] == ['ginger', 'black']
    assert [tail['coat'] for tail in other['tails']] == ['black', 'ginger']
    original, reversed_pixels = pixels(image), pixels(swapped)
    for slot in tail_slots(rect, .3):
        region = alpha(path_image(slot.exposed_path, rect, 2))
        visible = changed = 0
        for i, included in enumerate(region):
            if not included: continue
            pixel = original[i*4:i*4+4]
            visible += pixel[3] > 0
            changed += pixel != reversed_pixels[i*4:i*4+4]
        assert visible > 80 and changed > 60  # Actual fur, not just a slot metric.
    centre = QRectF(145, 80, 45, 35).toRect()
    assert image.copy(centre.x()*2, centre.y()*2, centre.width()*2, centre.height()*2) == swapped.copy(
        centre.x()*2, centre.y()*2, centre.width()*2, centre.height()*2)


def test_state_age_alone_controls_independent_sway_and_short_geometry_marks(art):
    rect = QRectF(0, 0, 337, 140)
    first, _ = render(art, rect, age=.3)
    later, _ = render(art, rect, age=.8)
    again, _ = render(art, rect, age=.3)
    assert first == again and first != later
    deltas = [b.angle-a.angle for a, b in zip(tail_slots(rect, .3), tail_slots(rect, .8))]
    assert deltas[0] != pytest.approx(deltas[1])
    events = []
    for i in range(100):
        _, metrics = render(art, rect, age=i*.05)
        assert len(metrics['active_marks']) <= 2
        events.append(metrics['active_marks'])
    assert any('scratch' in e for e in events) and any('star' in e for e in events)
    assert any(not e for e in events)


def test_drawing_does_not_use_text_or_fonts(art):
    class GeometricPainter(QPainter):
        def setFont(self, *args):
            raise AssertionError('Font-dependent fight effects forbidden')
        def drawText(self, *args):
            raise AssertionError('Text-dependent fight effects forbidden')
    image = QImage(337, 140, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = GeometricPainter(image)
    art.paint(painter, QRectF(0, 0, 337, 140), ('gray', 'tuxedo'), .1)
    painter.end()
    assert any(alpha(image))


def test_translated_rect_translates_paths_slots_and_paint(art):
    plain = QRectF(0, 0, 180, 140); moved = QRectF(11, 9, 180, 140)
    assert cloud_path(moved).boundingRect() == cloud_path(plain).boundingRect().translated(11, 9)
    for a, b in zip(tail_slots(plain, .4), tail_slots(moved, .4)):
        assert b.root == a.root+QPointF(11, 9)
    image, _ = render(art, moved, age=.4)
    assert not image.pixelColor(0, 0).alpha()


def test_missing_tail_is_reported_without_inventing_extra_clouds(qt):
    art = FightArt(QPixmap())
    _, metrics = render(art, QRectF(0, 0, 337, 140))
    assert metrics['tail_count'] == 0 and metrics['cloud_count'] == 1
    assert metrics['load_error']
