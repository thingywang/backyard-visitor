"""Jump resource/anchor contracts; anatomy and continuity are inspected manually."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from backyard_visitor import jump_art
from backyard_visitor.jump_art import JumpArt, FRAME_NAMES


@pytest.fixture(scope="module", autouse=True)
def qt():
    return QApplication.instance() or QApplication(["jump-art-test"])


def test_three_distinct_frames_and_mouths_load_once(monkeypatch):
    art = JumpArt()
    assert not art._loaded and not art._frames and not art.mouth_anchors
    frames = art.load_frames()
    assert len(frames) == len(art.mouth_anchors) == 3 and art.load_error is None
    assert all((f.width(), f.height(), f.devicePixelRatio()) == (288, 224, 2) for f in frames)
    assert frames[0].toImage() != frames[1].toImage() != frames[2].toImage()
    def unexpected_decode(*args):
        pytest.fail("A held pose must not decode the jump atlas again")
    monkeypatch.setattr(jump_art, "QImage", unexpected_decode)
    assert art.load_frames() is frames


def test_real_alpha_explicit_crops_and_order():
    data = json.loads((jump_art.ASSETS / "jump_transitions.json").read_text())
    im = QImage(str(jump_art.ASSETS / data["file"]))
    assert im.hasAlphaChannel() and im.pixelColor(0, 0).alpha() == 0
    assert (im.width(), im.height()) == (data["width"], data["height"])
    assert tuple(row["name"] for row in data["frames"]) == FRAME_NAMES
    assert [row["rect"][3] for row in data["frames"]] == [750, 420, 604]


def test_forepaw_ground_is_stable_and_all_output_edges_are_clear():
    bottoms = []
    for frame in JumpArt().load_frames():
        im = frame.toImage()
        front = [(x,y) for x in range(95,116) for y in range(195,224)
                 if im.pixelColor(x,y).alpha() > 64]
        assert front
        bottoms.append(max(y for x,y in front))
        assert 214 <= bottoms[-1] <= 217
        assert all(im.pixelColor(x,223).alpha() == 0 for x in range(288))
        assert all(im.pixelColor(0,y).alpha() == 0 and im.pixelColor(287,y).alpha() == 0 for y in range(224))
    assert max(bottoms)-min(bottoms) <= 2


def test_mouth_landmarks_follow_the_same_canvas_transform():
    data = json.loads((jump_art.ASSETS / "jump_transitions.json").read_text())
    art = JumpArt(); art.load_frames()
    for info, actual in zip(data["frames"], art.mouth_anchors):
        expected = tuple(data["render_anchor"][axis]
                         +(info["mouth_anchor"][axis]-info["foot_anchor"][axis])*data["render_scale"]/2
                         for axis in (0,1))
        assert actual == pytest.approx(expected)
    # The deep landing must move a held gift lower with the actual head.
    assert art.mouth_anchors[1][1] > art.mouth_anchors[0][1]+10
    assert art.mouth_anchors[1][1] > art.mouth_anchors[2][1]+10


@pytest.mark.parametrize("mutation", ["mouth_missing", "bad_last_crop", "order", "bad_scale"])
def test_any_invalid_frame_or_landmark_causes_atomic_fallback(tmp_path, mutation):
    data = json.loads((jump_art.ASSETS / "jump_transitions.json").read_text())
    if mutation == "mouth_missing": del data["frames"][-1]["mouth_anchor"]
    elif mutation == "bad_last_crop": data["frames"][-1]["rect"][1] = 100000
    elif mutation == "order": data["frames"].reverse()
    else: data["render_scale"] = float("nan")
    (tmp_path / "jump_transitions.json").write_text(json.dumps(data))
    (tmp_path / "jump_transitions.png").symlink_to(jump_art.ASSETS / "jump_transitions.png")
    art = JumpArt(tmp_path)
    assert art.load_frames() == [] and art.mouth_anchors == [] and art.load_error


def test_missing_atlas_is_safe_fallback(tmp_path):
    data = json.loads((jump_art.ASSETS / "jump_transitions.json").read_text())
    (tmp_path / "jump_transitions.json").write_text(json.dumps(data))
    art = JumpArt(tmp_path)
    assert art.load_frames() == [] and art.mouth_anchors == [] and art.load_error
