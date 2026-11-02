"""Real pose endpoints, source/cache parity, interruption precedence and redraw."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from unittest.mock import patch
import pytest
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication
from backyard_visitor.art import CatArt

@pytest.fixture(scope='module',autouse=True)
def qt():
    app=QApplication.instance() or QApplication([])
    yield app

def pixels(pixmap):
    image=pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return image.constBits().asstring(image.sizeInBytes())

@pytest.mark.parametrize('source,target',[('sleeping','resting'),('swatting','resting'),('sleeping','play_crouch')])
def test_roll_keeps_existing_endpoints_and_real_intermediates(source,target):
    art=CatArt()
    name=art.posture_state('belly_roll',source,target)
    assert name is not None
    seq=art.states[name]
    assert len(seq)==5
    assert seq[0]==art.states[source][0] and seq[-1]==art.states[target][0]
    rendered=[pixels(art.frame(name,0,-1,pose_progress=p)) for p in (0,.21,.41,.61,.99)]
    assert len(set(rendered))==5
    assert rendered[0]==pixels(art.frame(source,0,-1))
    assert rendered[-1]==pixels(art.frame(target,0,-1))
    assert rendered[2]==pixels(art.frame(name,999,-1,pose_progress=.41))

def test_postures_load_once_without_invalid_state_cache_growth():
    art=CatArt();before=len(art.frames)
    assert art.posture_state('unknown','sleeping','resting') is None
    assert len(art.frames)==before
    name=art.posture_state('crouch_rise','play_crouch','standing')
    n=len(art.frames)
    assert len(art.states[name])==5
    for _ in range(8):
        assert art.posture_state('crouch_rise','play_crouch','standing')==name
    assert len(art.frames)==n

@pytest.mark.parametrize('kind,source,target',[('crouch_rise','play_crouch','standing'),('belly_roll','swatting','resting')])
def test_source_and_compiled_mirror_coat_paths_agree(kind,source,target):
    a,b=CatArt(),CatArt(prefer_compiled=False)
    assert a._compiled is not None
    for art in (a,b):art.frame('walking',0,1);art.frame('running',0,-1)
    sa,sb=a.posture_state(kind,source,target),b.posture_state(kind,source,target)
    for p in (0,.25,.5,.75,.99):
        for face in (-1,1):
            for coat in ('ginger','gray','tuxedo'):
                assert pixels(a.frame(sa,0,face,coat,pose_progress=p))==pixels(b.frame(sb,0,face,coat,pose_progress=p))

def draw(art,**kwargs):
    image=QImage(288,224,QImage.Format.Format_ARGB32_Premultiplied);image.setDevicePixelRatio(2);image.fill(Qt.GlobalColor.transparent)
    p=QPainter(image)
    try:art.paint(p,QRectF(0,0,144,112),**kwargs)
    finally:p.end()
    return image

def test_landing_and_airborne_pose_override_stale_posture_fields():
    art=CatArt()
    extra=dict(posture_kind='belly_roll',posture_source='swatting',posture_target='resting',posture_progress=.5)
    assert draw(art,state='resting',land_progress=.4,**extra)==draw(art,state='resting',land_progress=.4)
    assert draw(art,state='jumping',**extra)==draw(art,state='jumping')
    assert draw(art,state='jumping',takeoff_progress=.5,**extra)==draw(art,state='jumping',takeoff_progress=.5)

def test_rejected_posture_asset_keeps_prior_state_rendering():
    from backyard_visitor.posture_art import PostureArt
    with patch.object(PostureArt,'load_frames',return_value={}):
        art=CatArt()
        original=draw(art,state='resting')
        actual=draw(art,state='resting',posture_kind='belly_roll',posture_source='swatting',posture_target='resting',posture_progress=.5)
        assert actual==original
        assert not art._posture_groups
