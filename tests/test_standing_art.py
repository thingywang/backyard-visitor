"""Resource/cache contracts for the genuine standing pose; aesthetic QA is manual."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from backyard_visitor import standing_art
from backyard_visitor.standing_art import StandingArt


@pytest.fixture(scope="module", autouse=True)
def qt():
    return QApplication.instance() or QApplication(["standing-test"])


def test_frame_is_lazy_small_cached_and_ground_aligned(monkeypatch):
    art = StandingArt()
    assert not art._loaded and art._frame is None
    frame = art.frame()
    assert frame is not None and art.load_error is None
    assert (frame.width(), frame.height(), frame.devicePixelRatio()) == (288, 224, 2)
    image = frame.toImage()
    points = [(x, y) for y in range(224) for x in range(288)
              if image.pixelColor(x, y).alpha() > 8]
    assert 214 <= max(y for x, y in points) <= 216
    assert 0 < min(x for x, y in points) < max(x for x, y in points) < 287
    def unexpected_decode(*args):
        pytest.fail("Cached standing pose must not decode again")
    monkeypatch.setattr(standing_art, "QImageReader", unexpected_decode)
    assert art.frame() is frame


def test_source_is_transparent_and_manifest_shares_walking_scale():
    data = json.loads((standing_art.ASSETS / "standing.json").read_text())
    walking = json.loads((standing_art.ASSETS / "locomotion.json").read_text())
    image = QImage(str(standing_art.ASSETS / data["file"]))
    assert image.hasAlphaChannel() and image.pixelColor(0, 0).alpha() == 0
    assert (image.width(), image.height()) == (data["width"], data["height"])
    assert data["render_scale"] == walking["render_scale"]
    assert data["render_anchor"] == walking["render_anchor"]
    assert data["native_facing"] == walking["native_facing"]


def test_missing_source_fails_once_without_ui_exception(tmp_path):
    art = StandingArt(tmp_path)
    assert art.frame() is None and art.load_error
    assert art.frame() is None


@pytest.mark.parametrize("change", [
    {"file": "../outside.png"}, {"render_scale": float("nan")},
    {"rect": [0, 0, 200, 100]}, {"width": 100000}, {"pixel_ratio": 1},
])
def test_invalid_metadata_is_rejected(tmp_path, change):
    data = json.loads((standing_art.ASSETS / "standing.json").read_text())
    data.update(change)
    (tmp_path / "standing.json").write_text(json.dumps(data))
    art = StandingArt(tmp_path)
    assert art.frame() is None and art.load_error
