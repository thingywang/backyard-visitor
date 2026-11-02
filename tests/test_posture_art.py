"""Posture resources and loader contracts; visual acceptance uses endpoint previews."""
import json
import os
import shutil
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication
from backyard_visitor import posture_art
from backyard_visitor.posture_art import PostureArt,GROUPS


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["posture-art-test"])


def bounds(image):
    pts=[(x,y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x,y).alpha()>20]
    xs,ys=zip(*pts)
    return min(xs),min(ys),max(xs),max(ys)


def test_two_lazy_groups_have_three_distinct_intermediates(qt,monkeypatch):
    art=PostureArt()
    assert not art._loaded and not art._frames
    groups=art.load_frames()
    assert tuple(groups)==GROUPS and art.load_error is None
    assert all(len(frames)==3 for frames in groups.values())
    all_frames=[f for frames in groups.values() for f in frames]
    assert len({f.toImage().bits().asstring(288*224*4) for f in all_frames})==6
    assert all((f.width(),f.height(),f.devicePixelRatio())==(288,224,2) for f in all_frames)
    def unexpected(*args):pytest.fail("Posture source decoded again")
    monkeypatch.setattr(posture_art,"QImage",unexpected)
    assert art.load_frames() is groups


def test_all_poses_keep_ground_and_clear_canvas_margins(qt):
    groups=PostureArt().load_frames()
    for frames in groups.values():
        for frame in frames:
            l,t,r,b=bounds(frame.toImage())
            assert 0<l<r<287 and 0<t<b<223
            assert 214<=b<=216
    tops=[bounds(frame.toImage())[1] for frame in groups["crouch_rise"]]
    assert tops[0]>tops[1]>tops[2]


def test_intermediates_do_not_replace_existing_endpoints(qt):
    from backyard_visitor.activity_art import ActivityArt
    from backyard_visitor.standing_art import StandingArt
    endpoints=[f.toImage() for f in ActivityArt().load_frames().values()]+[StandingArt().frame().toImage()]
    for frames in PostureArt().load_frames().values():
        assert all(frame.toImage() not in endpoints for frame in frames)


@pytest.mark.parametrize("damage",["late_crop","missing_group","wrong_count","unsafe_path","missing_source","no_alpha"])
def test_bad_second_group_cannot_publish_partial_first(qt,tmp_path,damage):
    data=json.loads((posture_art.ASSETS/"posture.json").read_text())
    for file in ("posture.png","posture-belly.png"):shutil.copy2(posture_art.ASSETS/file,tmp_path/file)
    group=data["groups"]["belly_roll"]
    if damage=="late_crop":group["frames"][-1]["rect"][3]=9999
    elif damage=="missing_group":del data["groups"]["belly_roll"]
    elif damage=="wrong_count":group["frames"].pop()
    elif damage=="unsafe_path":group["file"]="../posture-belly.png"
    elif damage=="missing_source":(tmp_path/"posture-belly.png").unlink()
    elif damage=="no_alpha":
        image=QImage(str(tmp_path/"posture-belly.png")).convertToFormat(QImage.Format.Format_RGB32)
        image.save(str(tmp_path/"posture-belly.png"))
    (tmp_path/"posture.json").write_text(json.dumps(data))
    art=PostureArt(tmp_path)
    assert art.load_frames()=={} and art.load_error


def test_missing_manifest_is_safe(qt,tmp_path):
    art=PostureArt(tmp_path)
    assert art.load_frames()=={} and art.load_error
