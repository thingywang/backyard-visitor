"""Two fixed appearances traverse every production coat-rendering entry point.

These are routing/geometry checks, not a claim that every pose has passed visual
art review. All comparisons use the authored plain-black silhouette, because
random pigment changes colour while that material supplies the body and alpha.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor import black_coat, procedural_coat
from backyard_visitor.art import CatArt
from backyard_visitor.coat_traits import CoatSpec
from backyard_visitor.depth_art import COUNTS, DepthArt
from backyard_visitor.fight_art import FightArt
from backyard_visitor.parkour_art import KINDS, ParkourArt
from backyard_visitor.rear_art import RearArt
from backyard_visitor.tree_art import paint_tree_visit

SPECS=(CoatSpec(seed=27,pigment='dark',dark_pattern='solid',
                white_mode='spotting',white_coverage=.62),
       CoatSpec(seed=81,pigment='tortie',dark_pattern='solid',orange_pattern='mackerel',
                white_mode='spotting',white_coverage=.46))


@pytest.fixture(scope='module',autouse=True)
def qt():
    app=QApplication.instance() or QApplication(['generated-coat-routes'])
    procedural_coat.clear_procedural_cache()
    yield app
    procedural_coat.clear_procedural_cache()


@pytest.fixture(params=SPECS,ids=['cow','calico'])
def spec(request):
    return request.param


def rgba(frame):
    image=frame.toImage() if hasattr(frame,'toImage') else frame
    image=image.convertToFormat(QImage.Format.Format_RGBA8888)
    return image.constBits().asstring(image.sizeInBytes())


def assert_no_error():
    assert procedural_coat.load_error is None, procedural_coat.load_error
    assert procedural_coat.pending_error is None, procedural_coat.pending_error
    assert black_coat.load_error is None, black_coat.load_error


def assert_geometry(actual,neutral,route):
    assert actual is not None and not actual.isNull(), route
    assert neutral is not None and not neutral.isNull(), route
    assert actual.size()==neutral.size(), route
    assert actual.devicePixelRatio()==neutral.devicePixelRatio(), route
    assert actual.deviceIndependentSize()==neutral.deviceIndependentSize(), route
    actual_alpha,neutral_alpha=rgba(actual)[3::4],rgba(neutral)[3::4]
    assert any(actual_alpha), route
    assert actual_alpha==neutral_alpha, route
    assert_no_error()


def paint(draw,width=144,height=112):
    image=QImage(width*2,height*2,QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent);image.setDevicePixelRatio(2)
    painter=QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:result=draw(painter)
    finally:painter.end()
    assert any(rgba(image)[3::4])
    assert image.devicePixelRatio()==2
    return image,result


def test_all_cat_art_states_load_lazily_then_keep_canvas_alpha_flip_and_cache(spec):
    art=CatArt()
    # Exercise the same public state requests that load optional production
    # groups, then enumerate the resulting state table rather than one pose list.
    lazy=('walking','running','idle','standing','takeoff','landing','sleeping',
          'window_peeking','purring')
    for state in lazy:assert art.frame(state,0,-1,'black') is not None
    posture_requests=(('crouch_rise','play_crouch','standing'),
        ('belly_roll','sleeping','resting'),('belly_roll','sleeping','play_crouch'),
        ('belly_roll','swatting','resting'),('belly_roll','swatting','play_crouch'),
        ('crouch_lower','standing','play_crouch'),('belly_lie','play_crouch','sleeping'))
    for request in posture_requests:assert art.posture_state(*request)
    declared=set(art.states)
    assert {'walking','running','standing','idle','takeoff','landing','sleeping',
            'belly','resting','play_crouch','swatting','eating','grooming','carried',
            'window_peeking','purring'} <= declared
    aliases={'entering','leaving','fleeing','playing','fighting','jumping'}
    equal_spec=CoatSpec.from_dict(spec.to_dict())
    for state in sorted(declared|aliases):
        options=dict(gait_phase=.37,run_phase=.37,pose_progress=.54)
        left=art.frame(state,.35,-1,spec,**options)
        neutral=art.frame(state,.35,-1,'black',**options)
        assert_geometry(left,neutral,state)
        assert (left.width(),left.height(),left.devicePixelRatio())==(288,224,2)
        right=art.frame(state,.35,1,spec,**options)
        assert rgba(right)==rgba(left.toImage().mirrored(True,False)), state
        assert art.frame(state,.35,-1,equal_spec,**options).cacheKey()==left.cacheKey(), state
    assert all(getattr(art,name) for name in ('_motion_loaded','_running_loaded',
        '_activity_loaded','_standing_loaded','_transitions_loaded','_jumps_loaded',
        '_interactions_loaded','_postures_loaded'))


def test_peeking_tail_and_shared_fight_keep_the_same_generated_tail(spec):
    art=CatArt();fight=FightArt(art.tail)
    tail=fight.tail_frame(spec)
    assert_geometry(tail,fight.tail_frame('black'),'fight-tail')
    assert fight.tail_frame(CoatSpec.from_dict(spec.to_dict())).cacheKey()==tail.cacheKey()
    original=art.tail
    for facing in (-1,1):
        actual,_=paint(lambda p:art.paint(p,QRectF(0,0,144,112),'peeking',.25,facing,spec))
        # Reuse the FightArt tail through CatArt's unchanged rotation/placement.
        # This catches an edge-peek path accidentally leaving the original coat.
        art.tail=tail
        try:expected,_=paint(lambda p:art.paint(p,QRectF(0,0,144,112),'peeking',.25,facing,'ginger'))
        finally:art.tail=original
        assert rgba(actual)==rgba(expected)
        assert_no_error()
    image,metrics=paint(lambda p:fight.paint(p,QRectF(0,0,180,140),(spec,'black'),.3),180,140)
    neutral,_=paint(lambda p:fight.paint(p,QRectF(0,0,180,140),('black','black'),.3),180,140)
    assert metrics['cloud_count']==1 and metrics['tail_count']==2
    assert all(row['painted'] for row in metrics['tails'])
    assert metrics['load_error'] is None and fight.load_error is None
    assert_geometry(image,neutral,'shared-fight')


def test_rear_pose_and_rotating_input_region_follow_black_material(spec):
    art=RearArt()
    frame=art.frame(spec);neutral=art.frame('black')
    assert_geometry(frame,neutral,'rear')
    assert (frame.width(),frame.height(),frame.devicePixelRatio())==(288,336,2)
    assert art.frame(CoatSpec.from_dict(spec.to_dict())).cacheKey()==frame.cacheKey()
    age,amount=1.8,.67
    region=art.input_region(age,amount,spec)
    assert not region.isEmpty()
    assert region==art.input_region(age,amount,'black')
    image,_=paint(lambda p:art.paint(p,age,spec,amount),144,168)
    expected,_=paint(lambda p:art.paint(p,age,'black',amount),144,168)
    assert_geometry(image,expected,'rear-transition')


def test_all_depth_and_directional_parkour_frames_keep_geometry_and_mouth_anchors(spec):
    depth=DepthArt();parkour=ParkourArt();cat=CatArt()
    assert COUNTS=={'away':8,'toward':4,'front':1} and tuple(KINDS)==('jump_up','jump_down','climb')
    for kind,count in COUNTS.items():
        for index in range(count):
            frame=depth.frame(kind,index,spec)
            assert_geometry(frame,depth.frame(kind,index,'black'),f'{kind}:{index}')
            assert (frame.width(),frame.height(),frame.devicePixelRatio())==(288,224,2)
            assert depth.frame(kind,index,CoatSpec.from_dict(spec.to_dict())).cacheKey()==frame.cacheKey()
    assert depth.load_error is None
    for kind in KINDS:
        for index in (0,1):
            frame=parkour.frame(kind,index,-1,spec)
            assert_geometry(frame,parkour.frame(kind,index,-1,'black'),f'{kind}:{index}')
            right=parkour.frame(kind,index,1,spec)
            assert rgba(right)==rgba(frame.toImage().mirrored(True,False))
            left_mouth=parkour.mouth_anchor(kind,index,-1,spec)
            assert left_mouth==parkour.mouth_anchor(kind,index,-1,'black')
            assert parkour.mouth_anchor(kind,index,1,spec)==pytest.approx((144-left_mouth[0],left_mouth[1]))
            assert parkour.frame(kind,index,-1,CoatSpec.from_dict(spec.to_dict())).cacheKey()==frame.cacheKey()
            state='climbing' if kind=='climb' else 'jumping'
            options=(dict(climb_phase=.25+.5*index) if kind=='climb' else
                     dict(jump_vy=-100 if kind=='jump_up' else 100,
                          jump_pose_phase=.25+.5*index,jump_vertical_span=200))
            assert rgba(cat.frame(state,.2,-1,spec,**options))==rgba(frame)
            assert cat.parkour_mouth(state,-1,spec,**options)==left_mouth
    assert parkour.load_error is None and cat.parkour_load_error is None
    assert_no_error()


def test_tree_painter_reuses_generated_ground_depth_and_climb_routes(spec):
    art,depth=CatArt(),DepthArt()
    routes=(dict(pose='sleeping',phase='rest'),dict(pose='grooming',phase='rest'),
        dict(pose='scratching',phase='rest'),dict(pose='walking',phase='recede'),
        dict(pose='walking',phase='advance'),dict(pose='idle',phase='turn',
            turn_motion_progress=.5,turn_from_degrees=-40,turn_to_degrees=140),
        dict(pose='jumping',phase='jump',jump_vy=-100,jump_vertical_span=140,jump_pose_phase=.65))
    for route in routes:
        snapshot=dict(scale=.65,facing=1,age=.2,gait_phase=.35,run_phase=.4,**route)
        image,okay=paint(lambda p:paint_tree_visit(p,snapshot,art,depth,spec))
        neutral,expected_okay=paint(lambda p:paint_tree_visit(p,snapshot,art,depth,'black'))
        assert okay and expected_okay
        assert_geometry(image,neutral,f'tree:{route["pose"]}:{route["phase"]}')
        repeat,_=paint(lambda p:paint_tree_visit(p,snapshot,art,depth,CoatSpec.from_dict(spec.to_dict())))
        assert rgba(image)==rgba(repeat)
    assert depth.load_error is None and art.parkour_load_error is None
