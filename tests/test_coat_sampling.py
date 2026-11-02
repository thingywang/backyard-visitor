"""Continuous coat boundaries without blurring silhouette or authored fur."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from unittest.mock import patch
import pytest
from PyQt6.QtGui import QImage,QPixmap,QColor
from PyQt6.QtWidgets import QApplication
from backyard_visitor import procedural_coat as renderer
from backyard_visitor.coat_traits import CoatSpec
from backyard_visitor.coat_mapping import MappingCatalog,PART_NAMES,_project_region
from backyard_visitor.black_coat import source_key
from scripts.compile_black_coat import collect
from scripts.build_coat_maps import project


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication(['coat-interpolation-test'])


@pytest.fixture(scope='module')
def sources(qt):
    return collect()[1]


def affine_atlas():
    pixels=bytearray()
    for side in (0,1):
        for part in range(len(PART_NAMES)):
            for y in range(renderer._GRID):
                for x in range(renderer._GRID):
                    pixels.extend((x*3,y*2,part*17+side*11))
    return bytes(pixels)


@pytest.mark.parametrize('part,side,u,v',[(0,0,127,83),(1,1,1,254),(4,1,255,255),(4,0,0,0)])
def test_byte_uv_interpolation_reproduces_continuous_colour_ramp(part,side,u,v):
    color=renderer._sample_atlas(affine_atlas(),part,side,u,v)
    assert color==pytest.approx((u*63*3/255,v*63*2/255,part*17+side*11))


def test_colour_step_has_intermediate_values_instead_of_four_pixel_blocks():
    atlas=bytearray(len(PART_NAMES)*2*renderer._GRID**2*3)
    for y in range(renderer._GRID):
        for x in range(renderer._GRID):
            start=(y*renderer._GRID+x)*3
            atlas[start:start+3]=bytes((255 if x>=32 else 0,)*3)
    values=[renderer._sample_atlas(atlas,0,0,u,87)[0] for u in range(123,133)]
    assert values==sorted(values)
    assert len([v for v in values if 0<v<255])>=4
    assert max(b-a for a,b in zip(values,values[1:]))<=63.01
    assert values[0]==0 and values[-1]==255


def test_action_render_uses_fractional_albedo_but_keeps_local_fur_and_protection(qt):
    renderer.clear_procedural_cache()
    image=QImage(7,1,QImage.Format.Format_RGBA8888);image.fill(QColor(64,64,64,255))
    source=QPixmap.fromImage(image);source.setDevicePixelRatio(2)
    # One UV step is much smaller than an atlas cell. The old nearest-point
    # path returned identical RGB for several pixels and visibly stair-stepped.
    material=bytes((64,64,64,255))*6+bytes((19,83,31,255))
    mapping=b''.join(bytes((1,u,127,255)) for u in range(126,132))+bytes((241,129,127,255))
    with patch.object(renderer._catalog,'get',return_value=({'dpr':2},mapping)), \
         patch.object(renderer._catalog,'seam_samples',return_value={}), \
         patch.object(renderer,'_material',return_value=(7,1,material)), \
         patch.object(renderer,'_atlas',return_value=affine_atlas()):
        result=renderer.procedural_frame(source,CoatSpec(4))
    rendered=result.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    raw=rendered.constBits().asstring(rendered.sizeInBytes())
    assert result.devicePixelRatio()==2 and result.size()==source.size()
    assert raw[3::4]==material[3::4]
    red=list(raw[0:6*4:4])
    assert red==sorted(red) and len(set(red))>=4
    assert raw[-4:]==material[-4:]  # protected iris: authored RGB, not blurred
    renderer.clear_procedural_cache()


@pytest.mark.parametrize('label',['cat-018','cat-002','rear','depth-away-0','depth-toward-0'])
def test_runtime_body_projection_matches_authored_chart_builder(sources,label):
    frame=sources[label];catalog=MappingCatalog();row=catalog.rows()[source_key(frame)]
    body=next(r for r in row['regions'] if r['part']=='body')
    for x,y in [(20,20),(50,74),(100,80),(73,100)]:
        distance,u,v,side=project(x,y,body)
        assert _project_region(x,y,body)==pytest.approx((distance,u,v,int(side=='right')))


def test_hip_boundary_follows_full_flank_uv_instead_of_one_constant_anchor(sources):
    frame=sources['cat-018'];catalog=MappingCatalog();key=source_key(frame)
    row,mapping=catalog.get(key,(frame.width(),frame.height()))
    seams=catalog.seam_samples(key,mapping,frame.width(),frame.height(),row['dpr'])
    hip=[(mapping[o+1],sample) for o,sample in seams.items()
         if mapping[o]&7==5 and not mapping[o]&8 and sample[3]>.8]
    assert len(hip)>30
    assert max(sample[1] for _,sample in hip)-min(sample[1] for _,sample in hip)>25
    assert all(0<=n<=255 for _,sample in hip for n in sample[:2])
    # Feet and passing tail tips cannot inherit torso colours just because
    # they approach the body in a curled/sleeping pose.
    for o in seams:
        part=mapping[o]&7;u=mapping[o+1]/255
        assert (part==2 or part==3 and u<.30 or part in (4,5) and u<.68)
        assert mapping[o+3]>0
