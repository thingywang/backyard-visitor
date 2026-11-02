"""Photographic bowl loading and matched state geometry."""
import json
import os
import shutil
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage,QPainter
from PyQt6.QtWidgets import QApplication
from backyard_visitor import bowl_art
from backyard_visitor.bowl_art import BowlArt


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["bowl-art-test"])


def bounds(image):
    points=[(x,y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x,y).alpha()>20]
    xs,ys=zip(*points)
    return min(xs),min(ys),max(xs),max(ys)


def test_two_small_states_and_no_second_decode(qt,monkeypatch):
    art=BowlArt()
    assert not art._loaded
    empty,full=art.load_frames()
    assert art.load_error is None
    assert all((f.width(),f.height(),f.devicePixelRatio())==(124,80,2) for f in (empty,full))
    assert empty.toImage()!=full.toImage()
    def unexpected(*args):pytest.fail("Bowl atlas decoded again")
    monkeypatch.setattr(bowl_art,"QImage",unexpected)
    assert art.frame(False) is empty and art.frame(True) is full


def test_matching_exterior_geometry_without_frame_jump(qt):
    frames=BowlArt().load_frames()
    first,second=[bounds(frame.toImage()) for frame in frames]
    assert all(abs(a-b)<=1 for a,b in zip(first,second))
    assert 1 < first[0] < first[2] < 122
    assert 1 < first[1] < first[3] < 78


def test_paint_uses_existing_62_by_40_bowl_geometry(qt):
    canvas=QImage(100,70,QImage.Format.Format_ARGB32_Premultiplied);canvas.fill(Qt.GlobalColor.transparent)
    painter=QPainter(canvas);painter.setOpacity(.7)
    assert BowlArt().paint(painter,QRectF(10,10,62,40),full=True)
    assert painter.opacity()==.7
    painter.end()
    l,t,r,b=bounds(canvas)
    assert 10<l<r<72 and 10<t<b<50


@pytest.mark.parametrize("damage",["bad_crop","wrong_order","no_alpha","missing"])
def test_unavailable_group_returns_none_atomically(qt,tmp_path,damage):
    data=json.loads((bowl_art.ASSETS/"bowl.json").read_text())
    shutil.copy2(bowl_art.ASSETS/"bowl.png",tmp_path/"bowl.png")
    if damage=="bad_crop":data["frames"][1]["rect"][2]=9999
    elif damage=="wrong_order":data["frames"].reverse()
    elif damage=="no_alpha":
        source=QImage(str(tmp_path/"bowl.png")).convertToFormat(QImage.Format.Format_RGB32)
        source.save(str(tmp_path/"bowl.png"))
    elif damage=="missing":(tmp_path/"bowl.png").unlink()
    (tmp_path/"bowl.json").write_text(json.dumps(data))
    art=BowlArt(tmp_path)
    assert art.load_frames()==[] and art.frame() is None and art.load_error
