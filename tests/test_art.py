"""Actual raster geometry and slow-blink behavior at the window clipping boundary."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor import art


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["backyard-art-test"])


@pytest.fixture
def cats(qt):
    return art.CatArt(prefer_compiled=False)


def opaque_bounds(image):
    points = [(x, y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x, y).alpha() > 8]
    assert points, "Expected visible cat pixels"
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def test_new_interactions_load_once_without_replacing_walking_or_edge_tail(cats):
    cats.frame("walking", 0, -1)
    initial_count = len(cats.frames)
    walking = cats.states["walking"][:]
    tail_key = cats.tail.cacheKey()
    cats.frame("walking", 1, -1)
    assert not cats._interactions_loaded
    cats.frame("window_peeking", 0, -1)
    assert cats._interactions_loaded
    assert len(cats.frames) == initial_count + 4
    cats.frame("purring", 3, -1)
    cats.frame("window_peeking", 4, 1)
    assert len(cats.frames) == initial_count + 4
    assert cats.states["walking"] == walking
    assert cats.tail.cacheKey() == tail_key


@pytest.mark.parametrize("age", [0, 3.8])
@pytest.mark.parametrize("facing", [-1, 1])
def test_window_head_and_paws_survive_final_window_edge_clip(cats, age, facing):
    # Model ends at window_top - 34; everything below local y=34 is obscured.
    image = QImage(144, 112, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    cats.paint(painter, QRectF(0, 0, 144, 112), "window_peeking", age, facing)
    painter.end()
    left, top, right, bottom = opaque_bounds(image)
    assert 25 <= right-left+1 <= 38  # Preserve head/paw anatomy, no horizontal stretch.
    assert 0 <= top <= 2
    assert 30 <= bottom <= 33  # Paw line remains visible, not shifted upward by idle offset.
    assert all(image.pixelColor(x, y).alpha() <= 8
               for y in range(34, 112) for x in range(144))
    # At the final clipping position no visible anatomy gets lost.
    assert opaque_bounds(image.copy(0, 0, 144, 34)) == (left, top, right, bottom)


@pytest.mark.parametrize("state,closed,reopened,period", [
    ("purring", 2.8, 3.8, 4.4),
    ("window_peeking", 3.8, 4.2, 5.0),
])
def test_slow_blink_holds_open_and_closed_poses(cats, state, closed, reopened, period):
    opened = cats.frame(state, 0, -1).cacheKey()
    assert cats.frame(state, 1, -1).cacheKey() == opened
    assert cats.frame(state, closed, -1).cacheKey() != opened
    assert cats.frame(state, closed + 0.05, -1).cacheKey() == cats.frame(state, closed, -1).cacheKey()
    assert cats.frame(state, reopened, -1).cacheKey() == opened
    assert cats.frame(state, period, -1).cacheKey() == opened


def test_new_poses_keep_retina_alpha_coat_and_facing_cache(cats):
    original = cats.frame("window_peeking", 0, -1)
    gray = cats.frame("window_peeking", 0, -1, "gray")
    dark = cats.frame("window_peeking", 0, -1, "tuxedo")
    mirrored = cats.frame("window_peeking", 0, 1)
    assert cats.frame("window_peeking", 1, -1, "gray").cacheKey() == gray.cacheKey()
    for frame in (original, gray, dark, mirrored):
        assert frame.devicePixelRatio() == 2
        assert (frame.width(), frame.height()) == (288, 224)
    bounds = opaque_bounds(original.toImage())
    assert opaque_bounds(gray.toImage()) == bounds
    assert opaque_bounds(dark.toImage()) == bounds
    left, top, right, bottom = bounds
    assert opaque_bounds(mirrored.toImage()) == (287-right, top, 287-left, bottom)


def test_missing_optional_atlas_keeps_original_idle_fallback(cats, monkeypatch, tmp_path):
    idle = cats.frame("idle", 0, -1).cacheKey()
    monkeypatch.setattr(art, "ASSETS", tmp_path)
    assert cats.frame("purring", 0, -1).cacheKey() == idle
    assert "purring" not in cats._blink_windows


def test_gray_conversion_retains_alpha_without_opaque_rectangle(cats):
    original = cats.frame("walking", 0, -1).toImage()
    gray = cats.frame("walking", 0, -1, "gray").toImage()
    for y in range(0, original.height(), 3):
        for x in range(0, original.width(), 3):
            color = gray.pixelColor(x, y)
            assert color.alpha() == original.pixelColor(x, y).alpha()
            if color.alpha() > 20:
                assert max(color.red(), color.green(), color.blue()) - min(color.red(), color.green(), color.blue()) <= 1


def test_variant_cache_is_bounded_and_recent_pose_is_reused(cats):
    cats.cache_limit = 4
    remembered = cats.frame("walking", 0, -1, "gray")
    cats.frame("walking", .2, -1, "gray")
    cats.frame("idle", 0, -1, "gray")
    cats.frame("sleeping", 0, -1, "gray")
    assert cats.frame("walking", 0, -1, "gray").cacheKey() == remembered.cacheKey()
    cats.frame("carried", 0, -1, "gray")
    assert len(cats.cache) == 4
    assert cats.frame("walking", 0, -1, "gray").cacheKey() == remembered.cacheKey()


def test_moving_states_use_four_alternating_phases_with_coat_cache(cats):
    frames = [cats.frame("walking", t, -1, "gray").cacheKey() for t in (0, .2, .4, .6)]
    assert len(set(frames)) == 4
    assert cats.frame("walking", .8, -1, "gray").cacheKey() == frames[0]
    assert cats.frame("entering", .2, -1, "gray").cacheKey() == frames[1]
    assert cats.frame("leaving", .4, -1, "gray").cacheKey() == frames[2]
    assert cats.frame("fleeing", .12, -1, "gray").cacheKey() == cats.frame("running", .12, -1, "gray").cacheKey()
    assert cats.frame("fleeing", .12, -1, "gray").cacheKey() not in frames
    assert len(cats.states["jumping"]) == 1
