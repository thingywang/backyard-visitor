"""Resource/geometry tests; biomechanical appearance requires contact-sheet review."""
import json
import os
import shutil
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor import run_art
from backyard_visitor.run_art import RunArt


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["run-art-test"])


def bounds(image):
    points = [(x,y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x,y).alpha() > 20]
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def test_four_real_alpha_poses_load_lazily_and_cache(qt, monkeypatch):
    art = RunArt()
    assert not art._loaded and not art._frames and not art.mouth_anchors
    frames = art.load_frames()
    assert len(frames) == 4 and len(art.mouth_anchors) == 4 and art.load_error is None
    assert all((f.width(), f.height(), f.devicePixelRatio()) == (288,224,2) for f in frames)
    assert len({f.toImage().bits().asstring(f.width()*f.height()*4) for f in frames}) == 4
    def unexpected(*args):
        pytest.fail("Run source decoded again")
    monkeypatch.setattr(run_art, "QImage", unexpected)
    assert art.load_frames() is frames
    assert art.frame(.1) is frames[0]


def test_contact_ground_and_airborne_clearance_survive_normalization(qt):
    art = RunArt()
    all_bounds = [bounds(frame.toImage()) for frame in art.load_frames()]
    assert all(0 < left < right < 287 and 0 < top < bottom < 223
               for left, top, right, bottom in all_bounds)
    assert 214 <= all_bounds[0][3] <= 216
    assert 214 <= all_bounds[2][3] <= 216
    assert all_bounds[1][3] <= 198
    assert all_bounds[3][3] <= 206


def test_phase_override_wrap_and_mirroring(qt):
    art = RunArt()
    assert [art.frame_index(t) for t in (0,.12,.24,.36,.48)] == [0,1,2,3,0]
    assert [art.frame_index(123,gait_phase=p) for p in (0,.25,.5,.75,1)] == [0,1,2,3,0]
    assert art.frame_index(float("nan")) == art.frame_index(-1) == 0
    for i in range(4):
        left = art.frame(gait_phase=i/4)
        right = art.frame(facing=1,gait_phase=i/4)
        x,y,r,b = bounds(left.toImage())
        assert bounds(right.toImage()) == (287-r,y,287-x,b)
        assert right.devicePixelRatio() == 2
        assert right.cacheKey() == art.frame(facing=1,gait_phase=i/4).cacheKey()


def test_mouth_landmarks_match_manifest_transform(qt):
    art = RunArt()
    art.load_frames()
    data = json.loads((run_art.ASSETS/"run.json").read_text())
    for row, actual in zip(data["frames"], art.mouth_anchors):
        expected = tuple(data["render_anchor"][i]+(row["mouth_anchor"][i]-row["foot_anchor"][i])*data["render_scale"]/2 for i in range(2))
        assert actual == pytest.approx(expected)
        assert 20 < actual[0] < 35 and 60 < actual[1] < 95


def test_paint_restores_painter(qt):
    image=QImage(170,130,QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.setOpacity(.8);painter.setPen(QColor("red"))
    assert RunArt().paint(painter,QRectF(10,10,144,112),gait_phase=.25,opacity=.5)
    assert painter.opacity() == .8 and painter.pen().color() == QColor("red")
    painter.end()
    assert bounds(image)[2] < 154


@pytest.mark.parametrize("damage",["missing_mouth","bad_crop","wrong_order","unsafe_path","nan_scale"])
def test_bad_manifest_falls_back_atomically(qt,tmp_path,damage):
    data=json.loads((run_art.ASSETS/"run.json").read_text())
    shutil.copy2(run_art.ASSETS/"run.png",tmp_path/"run.png")
    if damage=="missing_mouth":del data["frames"][-1]["mouth_anchor"]
    elif damage=="bad_crop":data["frames"][-1]["rect"][3]=9999
    elif damage=="wrong_order":data["frames"].reverse()
    elif damage=="unsafe_path":data["file"]="../run.png"
    elif damage=="nan_scale":data["render_scale"]=float("nan")
    (tmp_path/"run.json").write_text(json.dumps(data))
    art=RunArt(tmp_path)
    assert art.load_frames()==[] and art.mouth_anchors==[] and art.load_error
    assert art.frame() is None


def test_missing_source_falls_back(qt,tmp_path):
    art=RunArt(tmp_path)
    assert art.load_frames()==[] and art.load_error
