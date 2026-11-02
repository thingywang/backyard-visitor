"""Raster resource and drawing contracts; these do not grade generated aesthetics."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor.gift_art import ASSETS, GiftArt, KINDS


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["backyard-gift-art-test"])


@pytest.fixture
def gifts(qt):
    return GiftArt()


def alpha_points(image):
    return [(x, y) for y in range(image.height()) for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 8]


def bounds(points):
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def test_resource_has_alpha_and_all_declared_crops_fit():
    data = json.loads((ASSETS / "gifts.json").read_text())
    image = QImage(str(ASSETS / data["file"]))
    assert not image.isNull() and image.hasAlphaChannel()
    assert (image.width(), image.height()) == (data["width"], data["height"])
    assert image.pixelColor(0, 0).alpha() == 0
    assert set(data["frames"]) == KINDS
    for frame in data["frames"].values():
        x, y, width, height = frame["rect"]
        assert 0 <= x < x+width <= image.width()
        assert 0 <= y < y+height <= image.height()
        assert alpha_points(image.copy(x, y, width, height))


def test_constructor_and_simplified_mouse_do_not_load_atlas(gifts):
    assert not gifts._loaded and not gifts._frames
    assert gifts.frame("toy_mouse", realistic_mouse=False) is None
    assert gifts.frame("mouse", realistic_mouse=False) is None
    assert gifts.frame("fur") is None
    assert gifts.frame("unknown") is None
    assert not gifts._loaded and not gifts._frames


def test_first_supported_gift_keeps_only_three_small_retina_frames(gifts, monkeypatch):
    feather = gifts.frame("feather")
    assert gifts._loaded and gifts.load_error is None
    assert set(gifts._frames) == KINDS
    assert not hasattr(gifts, "atlas")
    for frame in gifts._frames.values():
        assert (frame.width(), frame.height(), frame.devicePixelRatio()) == (76, 76, 2)
        left, top, right, bottom = bounds(alpha_points(frame.toImage()))
        assert 0 < left < right < 75 and 0 < top < bottom < 75
    monkeypatch.setattr(gifts, "_load", lambda: None)
    assert gifts.frame("feather").cacheKey() == feather.cacheKey()


def test_mouse_alias_toggle_and_mirror_preserve_cached_alpha(gifts):
    mouse = gifts.frame("toy_mouse")
    assert gifts.frame("mouse").cacheKey() == mouse.cacheKey()
    assert gifts.frame("toy_mouse", realistic_mouse=False) is None
    assert gifts.frame("toy_mouse").cacheKey() == mouse.cacheKey()
    mirror = gifts.frame("toy_mouse", facing=1)
    assert gifts.frame("mouse", facing=1).cacheKey() == mirror.cacheKey()
    left, top, right, bottom = bounds(alpha_points(mouse.toImage()))
    assert bounds(alpha_points(mirror.toImage())) == (75-right, top, 75-left, bottom)


@pytest.mark.parametrize("kind", ["feather", "bottle_cap", "toy_mouse"])
@pytest.mark.parametrize("size", [22, 38])
def test_paint_supports_mouth_and_desktop_rects_and_restores_painter(gifts, kind, size):
    image = QImage(90, 60, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setOpacity(.8)
    painter.setPen(QColor("#123456"))
    before_transform = painter.transform()
    assert gifts.draw(painter, QRectF(10, 10, size, size), kind, opacity=.5)
    assert painter.opacity() == .8
    assert painter.pen().color() == QColor("#123456")
    assert painter.transform() == before_transform
    painter.end()
    left, top, right, bottom = bounds(alpha_points(image))
    assert 10 <= left < right < 10+size and 10 <= top < bottom < 10+size
    assert max(image.pixelColor(x, y).alpha() for x, y in alpha_points(image)) <= 103


def test_non_square_destination_does_not_stretch_mouse(gifts):
    image = QImage(90, 70, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    assert gifts.paint(painter, QRectF(5, 5, 76, 38), "mouse")
    painter.end()
    left, top, right, bottom = bounds(alpha_points(image))
    assert 24 <= left < right <= 61  # Fit a centered 38px square, never fill 76px width.
    assert right-left > bottom-top


def test_fallback_reports_false_without_drawing(gifts):
    image = QImage(38, 38, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    assert not gifts.paint(painter, QRectF(0, 0, 38, 38), "toy_mouse", realistic_mouse=False)
    assert not gifts.paint(painter, QRectF(0, 0, 38, 38), "fur")
    assert not gifts.paint(painter, QRectF(0, 0, 0, 0), "feather")
    painter.end()
    assert not alpha_points(image)
    assert not gifts._loaded


def test_missing_atlas_falls_back_with_diagnostic(qt, tmp_path):
    gifts = GiftArt(tmp_path)
    assert gifts.frame("feather") is None
    assert gifts.load_error
    assert gifts._loaded and not gifts._frames


def test_fallen_fur_uses_transparent_resource_in_existing_gift_entrypoint(qt, monkeypatch):
    from backyard_visitor import art
    from backyard_visitor.fur_art import FurArt
    fur = FurArt()
    monkeypatch.setattr(art, "_fur_art", fur)
    assert not fur._loaded and fur._frame is None
    source = QImage(str(ASSETS / "fur-tuft.png"))
    assert not source.isNull() and source.hasAlphaChannel()
    assert source.pixelColor(0, 0).alpha() == 0
    image = QImage(38, 38, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setOpacity(.8)
    painter.setPen(QColor("#123456"))
    art.paint_gift(painter, QRectF(0, 0, 38, 38), "fur")
    assert painter.opacity() == .8 and painter.pen().color() == QColor("#123456")
    painter.end()
    assert fur._loaded and fur.load_error is None
    frame = fur.frame()
    assert (frame.width(), frame.height(), frame.devicePixelRatio()) == (76, 76, 2)
    assert fur.frame().cacheKey() == frame.cacheKey()
    left, top, right, bottom = bounds(alpha_points(image))
    assert 0 < left < right < 37 and 0 < top < bottom < 37


def test_missing_fallen_fur_keeps_usable_vector_fallback(qt, tmp_path, monkeypatch):
    from backyard_visitor import art
    from backyard_visitor.fur_art import FurArt
    fur = FurArt(tmp_path)
    monkeypatch.setattr(art, "_fur_art", fur)
    image = QImage(38, 38, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    art.paint_gift(painter, QRectF(0, 0, 38, 38), "fur")
    painter.end()
    assert fur.load_error and fur.frame() is None
    assert alpha_points(image)
