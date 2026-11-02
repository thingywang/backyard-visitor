"""Pigment at real limb attachments must not change as a paw folds or walks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage
from backyard_visitor import portrait_art, procedural_coat
from backyard_visitor.coat_mapping import MappingCatalog, PART_NAMES, _project_region
from backyard_visitor.coat_joints import (JointSample, uses_joint_pigments,
    limb_subject, reference_projection, _point_on_limb)
from backyard_visitor.coat_traits import CoatSpec, CoatSample, coat_field


@pytest.fixture(scope='module', autouse=True)
def qt():
    return QApplication.instance() or QApplication(['coat-joint-regression'])


@pytest.fixture(scope='module')
def catalog():
    return MappingCatalog()


def row_named(catalog, label):
    return next((key,row) for key,row in catalog.rows().items() if row['label']==label)


def image_bytes(frame):
    image=frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return image.constBits().asstring(image.sizeInBytes())


@pytest.mark.parametrize('spec,expected', [
    (CoatSpec(42),False),
    (CoatSpec(42,white_mode='all-white',white_coverage=1),False),
    (CoatSpec(42,white_mode='spotting',white_coverage=.19),False),
    (CoatSpec(42,white_mode='spotting',white_coverage=.2),True),
    (CoatSpec(42,pigment='tortie',white_mode='spotting',white_coverage=.4),True)])
def test_only_substantial_white_spotting_uses_joint_pigments(spec,expected):
    assert uses_joint_pigments(spec)==expected


@pytest.mark.parametrize('seed',[0,42,122,551])
def test_same_limb_point_keeps_pigment_when_standing_walking_sleeping_jumping(catalog,seed):
    _,reference=row_named(catalog,'cat-018')
    body=next(r for r in reference['regions'] if r['part']=='body')
    legs={(r['part'],r['side']):r for r in reference['regions'] if r['part'] in ('front_leg','hind_leg')}
    field=coat_field(CoatSpec(seed,pigment='tortie',white_mode='spotting',white_coverage=.4))
    labels=('cat-018','cat-010','cat-011','cat-012','cat-013','cat-029','parkour-jump_up-0','parkour-climb-0')
    for part in (3,4):
        for side in (0,1):
            for ub in (38,89,140,204,245):
                for vb in (64,128,191):
                    samples=[]
                    for label in labels:
                        _,row=row_named(catalog,label)
                        leg=next((r for r in row['regions'] if r['part']==PART_NAMES[part] and r['side']==('right' if side else 'left')),None)
                        if leg is None:
                            continue  # a far hind leg is genuinely occluded in some poses
                        x,y=_point_on_limb(leg,ub/255,2*vb/255-1)
                        subject=limb_subject(part+1+side*8,ub,vb,x,y,row['regions'])
                        assert subject==(part,side,ub/255,vb/255)
                        d,u,v,s=reference_projection(*subject,body,legs)
                        joint=JointSample(*subject,d,u,v,s)
                        value=procedural_coat._joint_pigment(field,joint)
                        samples.append((value.orange,value.white))
                    assert len(set(samples))==1
                    if ub==245:
                        assert samples[0][1]==1  # folded feet never become coloured again


def test_joint_uses_continuous_body_pigment_without_replacing_leg_tabby():
    class DistinctFields:
        def sample(self,part,u,v,side):
            return (CoatSample(.11,.22,.33,.44) if part=='body'
                    else CoatSample(.77,.88,.99,.01))
    sample=procedural_coat._joint_pigment(DistinctFields(),JointSample(3,0,.4,.5,.9,.3,.6,0))
    assert sample==CoatSample(.77,.88,.33,.44)
    # A foot may sit on top of the torso in a sleeping sprite; own leg-u still
    # keeps its sock white even when geometric body distance is zero.
    sample=procedural_coat._joint_pigment(DistinctFields(),JointSample(3,0,.96,.5,0,.3,.6,0))
    assert sample.white==1


def test_proven_upjump_head_cap_pixels_follow_forearm_but_face_and_tail_do_not(catalog):
    key,row=row_named(catalog,'parkour-jump_up-0')
    width,height=row['size'];_,mapping=catalog.get(key,(width,height))
    joints=catalog.joint_samples(key,mapping,width,height,row['dpr'])
    # Actual forearm pixels incorrectly authored as head, including narrow
    # arm edges that extend outside the front-leg capsule's nominal radius.
    for x,y in ((90,60),(95,65),(102,67),(82,59),(95,68)):
        offset=(y*width+x)*4
        assert mapping[offset]&7==2 and mapping[offset+1]==0
        assert joints[offset].part==3
    for offset in range(0,len(mapping),4):
        if mapping[offset]&7==3 or mapping[offset]&7==2 and mapping[offset]>>4:
            assert offset not in joints  # real tail and protected face organs
    # Identical geometry in an unverified pose must not infer occlusion.
    offset=(60*width+90)*4
    assert limb_subject(*mapping[offset:offset+3],90.5/row['dpr'],60.5/row['dpr'],row['regions']) is None


@pytest.mark.parametrize('label',['cat-018','cat-029','cat-030','parkour-climb-0'])
def test_unverified_pose_head_ownership_is_unchanged(catalog,label):
    key,row=row_named(catalog,label);w,h=row['size'];_,mapping=catalog.get(key,(w,h))
    joints=catalog.joint_samples(key,mapping,w,h,row['dpr'])
    assert all(mapping[o]&7 in (4,5) for o in joints)


def test_static_portrait_joint_matches_its_front_view_chest_instead_of_side_view(catalog):
    portrait_art.clear_portrait_cache()
    material,mapping,_=portrait_art._source()
    key,row=next(iter(portrait_art._catalog.rows().items()));w,h=row['size']
    joints=portrait_art._catalog.joint_samples(key,mapping,w,h,row['dpr'])
    body=next(r for r in row['regions'] if r['part']=='body')
    legs={(r['part'],r['side']):r for r in row['regions'] if r['part'] in ('front_leg','hind_leg')}
    field=coat_field(CoatSpec(0,pigment='tortie',white_mode='spotting',white_coverage=.4))
    # Root pigments are inherited from this fixed frontal anatomy, not from
    # the side-view orange shoulder that used to create orange lower sleeves.
    checked=0
    for joint in joints.values():
        if .08<joint.u<.30:
            distance,u,v,side=reference_projection(joint.part,joint.side,joint.u,joint.v,body,legs)
            u=.24 if joint.part==3 else .82
            assert (joint.body_distance,joint.body_u,joint.body_v,joint.body_side)==pytest.approx((distance,u,v,side))
            expected=field.sample('body',u,v,'right' if side else 'left')
            assert procedural_coat._joint_pigment(field,joint).orange==expected.orange
            checked+=1
    assert checked>100
    rendered=portrait_art.portrait_frame(SimpleNamespace(render_coat=field.spec))
    assert portrait_art.load_error is None
    assert image_bytes(rendered)[3::4]==material[3::4]


@pytest.mark.parametrize('coat',['ginger','gray','tuxedo','black'])
def test_legacy_portraits_do_not_enter_generated_joint_path(coat):
    portrait_art.clear_portrait_cache()
    with patch.object(portrait_art._catalog,'joint_samples',side_effect=AssertionError('legacy coat entered generated joint path')):
        rendered=portrait_art.portrait_frame(SimpleNamespace(render_coat=coat))
        assert not rendered.isNull() and portrait_art.load_error is None


@pytest.mark.parametrize('missing',['body','hind_leg'])
def test_incomplete_reference_raises_mapping_error_for_safe_qt_fallback(catalog,missing):
    from backyard_visitor.coat_mapping import CoatMappingError, _catalog
    from backyard_visitor.coat_joints import build_joint_samples
    key,row=row_named(catalog,'cat-018')
    broken=dict(row,regions=[r for r in row['regions'] if r['part']!=missing])
    with patch.object(_catalog,'rows',return_value={key:broken}):
        with pytest.raises(CoatMappingError,match='Reference'):
            build_joint_samples(catalog,key,bytes((4,100,128,255)),1,1,2)


def test_static_portrait_does_not_depend_on_action_manifest():
    from backyard_visitor.coat_mapping import _catalog, CoatMappingError
    portrait_art.clear_portrait_cache()
    spec=CoatSpec(0,pigment='tortie',white_mode='spotting',white_coverage=.4)
    with patch.object(_catalog,'rows',side_effect=CoatMappingError('actions unavailable')):
        rendered=portrait_art.portrait_frame(SimpleNamespace(render_coat=spec))
        assert not rendered.isNull() and portrait_art.load_error is None


def test_frontal_body_and_neck_seam_do_not_traverse_full_flank_pigments():
    spec=CoatSpec(0,pigment='tortie',orange_pattern='solid',white_mode='spotting',white_coverage=.4)
    field=coat_field(spec);palette=procedural_coat._palette(spec)
    for side in (0,1):
        for v in (166,190,215,240,255):
            colors=[portrait_art._frontal_body_albedo(spec,field,u,v,side,palette)
                    for u in (0,40,100,200,255)]
            assert all(color==colors[0] for color in colors)
