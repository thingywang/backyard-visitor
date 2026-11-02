"""Check sitting-transition resources and geometry, not generated aesthetics."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from backyard_visitor import transition_art
from backyard_visitor.transition_art import TransitionArt


@pytest.fixture(scope="module", autouse=True)
def qt():
    return QApplication.instance() or QApplication(["transition-art-test"])


def test_lazy_frames_are_small_distinct_and_cached(monkeypatch):
    art = TransitionArt()
    assert not art._loaded and not art._frames
    frames = art.load_frames()
    assert len(frames) == 3 and art.load_error is None
    assert all((f.width(), f.height(), f.devicePixelRatio()) == (288, 224, 2) for f in frames)
    assert frames[0].toImage() != frames[1].toImage() != frames[2].toImage()
    assert sum(f.width()*f.height()*4 for f in frames) == 774144
    def unexpected_decode(*args):
        pytest.fail("A held transition must not reread the source atlas")
    monkeypatch.setattr(transition_art, "QImage", unexpected_decode)
    assert art.load_frames() is frames
    assert art.frame(1) is frames[-1]


def test_source_has_real_alpha_and_whole_nonoverlapping_cells():
    data = json.loads((transition_art.ASSETS / "settling.json").read_text())
    image = QImage(str(transition_art.ASSETS / data["file"]))
    assert image.hasAlphaChannel() and image.pixelColor(0, 0).alpha() == 0
    assert (image.width(), image.height()) == (data["width"], data["height"])
    for row in data["frames"]:
        x, y, w, h = row["rect"]
        assert 0 <= x < x+w <= image.width() and 0 <= y < y+h <= image.height()


def test_near_front_paw_stays_on_shared_ground_and_canvas():
    art = TransitionArt()
    front_bottoms = []
    for frame in art.load_frames():
        image = frame.toImage()
        front = [(x, y) for x in range(96, 116) for y in range(195, 224)
                 if image.pixelColor(x, y).alpha() > 64]
        assert front
        front_bottoms.append(max(y for x, y in front))
        assert 214 <= front_bottoms[-1] <= 217
        assert all(image.pixelColor(x, 223).alpha() == 0 for x in range(288))
        assert all(image.pixelColor(x, 0).alpha() == 0 for x in range(288))
    assert max(front_bottoms)-min(front_bottoms) <= 2


def test_progress_uses_only_three_actual_poses_and_holds_endpoint():
    art = TransitionArt()
    frames = art.load_frames()
    assert [art.frame(p) for p in (-1, 0, .4, .8, 1, 2)] == [
        frames[0], frames[0], frames[1], frames[2], frames[2], frames[2]]
    assert art.frame(float("nan")) is frames[0]


def test_missing_resource_returns_fallback_without_raising(tmp_path):
    art = TransitionArt(tmp_path)
    assert art.load_frames() == [] and art.load_error
    assert art.frame(1) is None


def test_invalid_last_crop_does_not_publish_partial_frames(tmp_path):
    data = json.loads((transition_art.ASSETS / "settling.json").read_text())
    data["frames"][-1]["rect"] = [0, 100000, 887, 592]
    (tmp_path / "settling.json").write_text(json.dumps(data))
    (tmp_path / "settling.png").symlink_to(transition_art.ASSETS / "settling.png")
    art = TransitionArt(tmp_path)
    assert art.load_frames() == [] and art.load_error
