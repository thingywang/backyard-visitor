"""Walk resource/cycle/cache contracts; limb correspondence is visually inspected."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor import motion_art
from backyard_visitor.motion_art import MotionArt


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["backyard-motion-test"])


@pytest.fixture
def motion(qt):
    return MotionArt()


def bounds(image):
    points = [(x,y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x,y).alpha() > 8]
    assert points
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def test_four_individual_source_files_are_real_transparent_images():
    data = json.loads((motion_art.ASSETS / "locomotion.json").read_text())
    assert data["accepted"] is True
    assert len(data["frames"]) == 4
    assert len({row["file"] for row in data["frames"]}) == 4
    for row in data["frames"]:
        image = QImage(str(motion_art.ASSETS / row["file"]))
        assert not image.isNull() and image.hasAlphaChannel()
        assert (image.width(), image.height()) == (row["width"], row["height"])
        assert image.pixelColor(0, 0).alpha() == 0


def test_frames_decode_only_on_first_movement_and_remain_small(motion, monkeypatch):
    assert not motion._loaded and not motion._frames
    frames = motion.load_frames()
    assert len(frames) == 4 and motion.load_error is None
    assert sum(f.width()*f.height()*4 for f in frames) == 1_032_192
    for frame in frames:
        assert (frame.width(), frame.height(), frame.devicePixelRatio()) == (288, 224, 2)
    def unexpected_decode(*args):
        pytest.fail("A cached walk requested another source decode")
    monkeypatch.setattr(motion_art, "QImageReader", unexpected_decode)
    assert motion.load_frames() is frames
    for age in (0, .2, .4, .6, 1.2):
        assert motion.frame(age) is not None


def test_cycle_visits_all_four_phases_then_wraps(motion):
    assert [motion.frame_index(age) for age in (0, .2, .4, .6, .8, 1.0)] == [0, 1, 2, 3, 0, 1]
    assert len({motion.frame(age).cacheKey() for age in (0, .2, .4, .6)}) == 4
    assert motion.frame(0).cacheKey() == motion.frame(.8).cacheKey()
    assert motion.frame_index(-1) == 0
    assert motion.frame_index(float("nan")) == 0


def test_shared_scale_and_ground_anchor_keep_all_poses_inside_canvas(motion):
    bottom_rows = []
    for frame in motion.load_frames():
        left, top, right, bottom = bounds(frame.toImage())
        assert 0 < left < right < 287
        assert 0 < top < bottom < 223
        assert 214 <= bottom <= 216
        bottom_rows.append(bottom)
    assert max(bottom_rows)-min(bottom_rows) <= 1


def test_mirroring_preserves_alpha_and_is_cached_per_phase(motion):
    for age in (0, .2, .4, .6):
        frame = motion.frame(age, -1)
        mirror = motion.frame(age, 1)
        assert motion.frame(age, 1).cacheKey() == mirror.cacheKey()
        assert mirror.devicePixelRatio() == 2
        left, top, right, bottom = bounds(frame.toImage())
        assert bounds(mirror.toImage()) == (287-right, top, 287-left, bottom)
    assert len(motion._mirrors) == 4


def test_paint_uses_full_logical_canvas_and_restores_painter(motion):
    image = QImage(180, 140, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setOpacity(.8); painter.setPen(QColor("#234567"))
    transform = painter.transform()
    assert motion.draw(painter, QRectF(10, 10, 144, 112), .4, opacity=.5)
    assert painter.opacity() == .8 and painter.pen().color() == QColor("#234567")
    assert painter.transform() == transform
    painter.end()
    left, top, right, bottom = bounds(image)
    assert 10 < left < right < 154 and 10 < top < bottom < 122
    assert 116 <= bottom <= 118


def test_missing_manifest_falls_back_without_crashing(qt, tmp_path):
    motion = MotionArt(tmp_path)
    assert motion.load_frames() == []
    assert motion.frame(0) is None and motion.load_error
