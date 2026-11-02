"""Actual resource and atomic loader contracts, not generated pose aesthetics."""
import json
import os
import shutil
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

import pytest
from PyQt6.QtCore import QRectF,Qt
from PyQt6.QtGui import QImage,QPainter
from PyQt6.QtWidgets import QApplication
from backyard_visitor import activity_art
from backyard_visitor.activity_art import ActivityArt,STATES


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["activity-art-test"])


def bounds(image):
    points=[(x,y) for y in range(image.height()) for x in range(image.width())
            if image.pixelColor(x,y).alpha()>20]
    xs,ys=zip(*points)
    return min(xs),min(ys),max(xs),max(ys)


def test_four_named_real_alpha_poses_load_only_once(qt,monkeypatch):
    art=ActivityArt()
    assert not art._loaded and art._frames=={}
    frames=art.load_frames()
    assert tuple(frames)==STATES and art.load_error is None
    assert all((f.width(),f.height(),f.devicePixelRatio())==(288,224,2) for f in frames.values())
    assert len({f.toImage().bits().asstring(288*224*4) for f in frames.values()})==4
    def unexpected(*args):pytest.fail("Activity source decoded again")
    monkeypatch.setattr(activity_art,"QImage",unexpected)
    assert art.load_frames() is frames
    assert art.frame("belly") is frames["belly"]


def test_all_poses_keep_shared_ground_and_full_silhouette(qt):
    frames=ActivityArt().load_frames()
    for frame in frames.values():
        l,t,r,b=bounds(frame.toImage())
        assert 0<l<r<287 and 0<t<b<223
        assert 214<=b<=216
    # The lifted arm must survive source crop and normalization.
    assert bounds(frames["swatting"].toImage())[1] < bounds(frames["belly"].toImage())[1]-20
    assert bounds(frames["resting"].toImage())[1] > bounds(frames["play_crouch"].toImage())[1]


def test_mirrors_have_exact_reversed_alpha(qt):
    art=ActivityArt()
    for state in STATES:
        left,right=art.frame(state),art.frame(state,1)
        x,y,r,b=bounds(left.toImage())
        assert bounds(right.toImage())==(287-r,y,287-x,b)
        assert right.devicePixelRatio()==2
        assert art.frame(state,1).cacheKey()==right.cacheKey()
    assert art.frame("unknown") is None


def test_paint_restores_state_and_does_not_invent_missing_pose(qt):
    art=ActivityArt()
    image=QImage(180,140,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.setOpacity(.6);before=painter.transform()
    assert art.paint(painter,QRectF(12,10,144,112),"swatting")
    assert painter.opacity()==.6 and painter.transform()==before
    assert not art.paint(painter,QRectF(12,10,144,112),"unknown")
    painter.end()
    assert bounds(image)[2]<156


@pytest.mark.parametrize("damage",["bad_last_crop","wrong_states","missing_anchor","unsafe_path","bad_scale","no_alpha"])
def test_invalid_group_is_atomic(qt,tmp_path,damage):
    data=json.loads((activity_art.ASSETS/"activity.json").read_text())
    shutil.copy2(activity_art.ASSETS/"activity.png",tmp_path/"activity.png")
    if damage=="bad_last_crop":data["frames"][-1]["rect"][3]=9999
    elif damage=="wrong_states":data["states"]["belly"]=[1]
    elif damage=="missing_anchor":del data["frames"][-1]["foot_anchor"]
    elif damage=="unsafe_path":data["file"]="../activity.png"
    elif damage=="bad_scale":data["render_scale"]=float("inf")
    elif damage=="no_alpha":
        image=QImage(str(tmp_path/"activity.png")).convertToFormat(QImage.Format.Format_RGB32)
        image.save(str(tmp_path/"activity.png"))
    (tmp_path/"activity.json").write_text(json.dumps(data))
    art=ActivityArt(tmp_path)
    assert art.load_frames()=={} and art.load_error
    assert art.frame("belly") is None


def test_missing_resource_is_graceful(qt,tmp_path):
    art=ActivityArt(tmp_path)
    assert art.load_frames()=={} and art.load_error
