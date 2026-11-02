"""Frontal portrait source, anatomical color and handbook-safe rendering."""
import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PyQt6.QtGui import QImage, QPixmap, QColor
from PyQt6.QtWidgets import QApplication
from backyard_visitor import portrait_art as art
from backyard_visitor.coat_traits import CoatSpec
from backyard_visitor.coat_mapping import MAPS, CoatMappingError


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication(['portrait-art-test'])


@pytest.fixture(autouse=True)
def clear(qt):
    art.clear_portrait_cache()
    yield
    art.clear_portrait_cache()


def raw(frame):
    im=frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return im.constBits().asstring(im.sizeInBytes())


def frame(coat):
    return art.portrait_frame(SimpleNamespace(render_coat=coat))


@pytest.mark.parametrize('coat', ['ginger','gray','tuxedo','black',
    CoatSpec(73,white_mode='spotting',white_coverage=.7),
    CoatSpec(120,pigment='tortie',white_mode='spotting',white_coverage=.45),
    CoatSpec(21,white_mode='all-white',white_coverage=1),
    CoatSpec(28,pigment='orange',white_mode='spotting',white_coverage=.32)])
def test_each_identity_has_same_complete_transparent_portrait_canvas(coat):
    result=frame(coat)
    assert not result.isNull() and art.load_error is None
    assert (result.width(),result.height(),result.devicePixelRatio())==(288,336,2)
    neutral=QPixmap(str(art.ASSETS/'cat-front-neutral.png'))
    assert raw(result)[3::4]==raw(neutral)[3::4]
    im=result.toImage()
    assert all(im.pixelColor(x,y).alpha()==0 for x,y in [(0,0),(287,0),(0,335),(287,335)])
    # Portrait feet and head are present at distinct heights, not a cropped face.
    assert any(im.pixelColor(x,y).alpha()>128 for x in range(80,190) for y in range(280,310))
    assert any(im.pixelColor(x,y).alpha()>128 for x in range(90,180) for y in range(30,70))


def test_original_generated_source_is_unchanged_and_separate_from_action_catalog():
    provenance=json.loads((art.ASSETS/'provenance.json').read_text())
    assert hashlib.sha256((art.ASSETS/provenance['source']).read_bytes()).hexdigest()==provenance['source_sha256']
    original=QImage(str(art.ASSETS/provenance['source']))
    assert original.width()>288 and original.height()>336
    assert original.hasAlphaChannel()
    main=json.loads((MAPS/'manifest.json').read_text())['frames']
    portrait=art._catalog.rows()
    assert len(main)==60 and len(portrait)==1
    assert not set(main).intersection(portrait)


def test_solid_black_portrait_retains_authored_nonpatterned_fur_pixels():
    assert raw(frame('black'))==raw(QPixmap(str(art.ASSETS/'cat-front-neutral.png')))


def test_front_portrait_has_head_body_four_leg_regions_and_tail():
    row=next(iter(art._catalog.rows().values()))
    parts=[r['part'] for r in row['regions']]
    assert parts.count('front_leg')==parts.count('hind_leg')==2
    material,mapping,seams=art._source()
    counts={p:sum(mapping[o+3]>0 and mapping[o]&7==p for o in range(0,len(mapping),4)) for p in range(1,6)}
    assert min(counts.values())>100
    assert seams
    for o in range(0,len(material),4):
        assert bool(material[o+3])==bool(mapping[o+3])


def test_white_portrait_preserves_eyes_nose_and_inner_ear_protection():
    material,mapping,_=art._source()
    result=raw(frame(CoatSpec(21,white_mode='all-white',white_coverage=1)))
    protected=[o for o in range(0,len(material),4) if material[o+3]>=250 and mapping[o]>>4==15]
    assert len(protected)>30
    assert all(abs(result[o+i]-material[o+i])<=1 for o in protected for i in range(3))
    fur=[o for o in range(0,len(material),4) if material[o+3]>=250 and mapping[o]>>4==0]
    assert sum(result[o] for o in fur)/len(fur)>185
    assert len({result[o] for o in fur})>40


def test_same_saved_traits_are_stable_and_different_patterns_are_not_fixed_filters():
    spec=CoatSpec(120,pigment='tortie',white_mode='spotting',white_coverage=.45)
    first=raw(frame(spec));art.clear_portrait_cache()
    assert raw(frame(CoatSpec.from_dict(spec.to_dict())))==first
    samples=[raw(frame(CoatSpec(81,dark_pattern=pattern))) for pattern in ('solid','mackerel','spotted','classic','ticked')]
    assert len({hashlib.sha256(value).digest() for value in samples})==5
    assert len({value[3::4] for value in samples})==1


def test_existing_brown_gray_and_dark_tabby_remain_distinct_categories():
    samples={coat:raw(frame(coat)) for coat in ('ginger','gray','tuxedo')}
    material,mapping,_=art._source()
    fur=[o for o in range(0,len(material),4) if material[o+3]>=250 and mapping[o]>>4==0]
    means={coat:sum(samples[coat][o] for o in fur)/len(fur) for coat in samples}
    assert means['ginger']>means['tuxedo']
    assert all(abs(samples['gray'][o]-samples['gray'][o+1])<=1 and abs(samples['gray'][o+1]-samples['gray'][o+2])<=1 for o in fur)
    assert any(samples['ginger'][o]>samples['ginger'][o+2]+5 for o in fur)


def test_mutating_returned_pixmap_does_not_change_cached_portrait():
    first=frame('ginger');expected=raw(first);first.fill(QColor('red'))
    assert raw(frame('ginger'))==expected


def test_bad_map_is_contained_and_returns_null_for_accessible_ui_placeholder(caplog):
    with patch.object(art._catalog,'get',side_effect=CoatMappingError('broken portrait map')):
        assert frame('ginger').isNull()
        assert frame('black').isNull()
    assert '访客照片暂不可用' in art.load_error
    assert sum('broken portrait map' in r.message for r in caplog.records)==1
    art.clear_portrait_cache()
    assert not frame('black').isNull() and art.load_error is None


def test_rendering_does_not_read_names_or_relationships():
    class PrivateCat:
        render_coat='black'
        def __getattr__(self,name):
            raise AssertionError('portrait must only read render_coat')
    assert not art.portrait_frame(PrivateCat()).isNull()


def test_frontal_face_mapping_varies_laterally_and_skin_protection_stays_local():
    material,mapping,_=art._source()
    width=288
    # Across the forehead, u must vary toward each side of the head rather
    # than giving the entire row one u (which made a straight eye-cover band).
    row=[]
    for x in range(98,176):
        offset=(78*width+x)*4
        if mapping[offset+3] and mapping[offset]&7==2:
            row.append(mapping[offset+1])
    assert max(row)-min(row)>100
    for offset in range(0,len(mapping),4):
        if mapping[offset]>>4==12:
            assert (offset//4//width+.5)/2<41


def test_frontal_head_pattern_is_subtle_but_other_canonical_regions_unchanged():
    from backyard_visitor.procedural_coat import _atlas, _GRID
    from backyard_visitor.coat_mapping import PART_NAMES
    spec=CoatSpec(39,dark_pattern='mackerel')
    original=_atlas(spec);portrait=art._portrait_atlas(spec)
    pixels_per_part=_GRID*_GRID*3
    for side in (0,1):
        for part in range(len(PART_NAMES)):
            start=(side*len(PART_NAMES)+part)*pixels_per_part
            before=original[start:start+pixels_per_part]
            after=portrait[start:start+pixels_per_part]
            if part==1:
                before_range=max(before[0::3])-min(before[0::3])
                after_range=max(after[0::3])-min(after[0::3])
                assert before_range>30  # head still carries a visible pattern
                # Separate frontal strokes now use 45% material contrast so
                # the forehead remains legible. They must still be noticeably
                # softer than the action atlas, with other regions unchanged.
                assert 25<after_range<45
                assert after_range<=before_range*.55+1
            else:
                assert after==before
