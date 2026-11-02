"""Natural pigment integration preserves plain coats and actual fur geometry."""
import hashlib
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication
from backyard_visitor.art import CatArt
from backyard_visitor.coat_traits import CoatSpec, CoatSample
from backyard_visitor import procedural_coat as material


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication(['natural-fur-material'])


@pytest.mark.parametrize('spec,digest',[
    (CoatSpec(122),'75941c7b0d9c235f462698633903eaa046fdecbce5b91ac9499637a615ae0936'),
    (CoatSpec(122,dilute=True),'f1f74eda3c0f2f559af8b3a48d9c231237369b5e560b4d67754c05f3d8f9c6e5'),
    (CoatSpec(122,white_mode='all-white',white_coverage=1),'83ae5ac133a567ff7ba7c391b94ef9c0604c8193bdd94e5b7ff224bc908f65c7'),
])
def test_plain_coats_are_pixel_identical_to_previous_candidate(qt,spec,digest):
    # Captured from the verified af51109 candidate, before natural material
    # changes. This guards all fine fur/alpha pixels, not just mean brightness.
    frame=CatArt().frame('standing',0,-1,spec)
    image=frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    raw=image.constBits().asstring(image.sizeInBytes())
    assert hashlib.sha256(raw).hexdigest()==digest


def _raster(values,alpha=255):
    return b''.join(bytes((v,v,v,alpha)) for v in values)


def test_fur_residual_rejects_flat_and_linear_lighting_but_retains_a_fine_hair():
    # A broad lighting ramp must not warp the coat. A narrow authored hair is
    # allowed a strictly sub-byte UV offset, much smaller than one atlas cell.
    flat=_raster([60]*9)
    ramp=_raster([30,40,50,40,50,60,50,60,70])
    hair=_raster([50,50,50,50,80,50,50,50,50])
    assert material._fur_offset(flat,16,3,3)==0
    assert material._fur_offset(ramp,16,3,3)==0
    assert 0<material._fur_offset(hair,16,3,3)<1
    inverse=_raster([80,80,80,80,50,80,80,80,80])
    assert -1<material._fur_offset(inverse,16,3,3)<0


def test_transparency_cannot_create_false_hair_detail_at_the_silhouette():
    raster=bytearray(_raster([0]*9,alpha=0))
    raster[16:20]=bytes((60,60,60,255))
    assert material._fur_offset(raster,16,3,3)==0


def test_fractional_fur_shift_stays_inside_region_and_cannot_create_flat_coat_pattern():
    atlas=bytes((43,44,47))*(10*material._GRID**2)
    for u in (-.85,0,128.45,255,255.85):
        assert material._sample_atlas(atlas,4,1,u,255)==pytest.approx((43,44,47))


@pytest.mark.parametrize('dilute',[False,True])
def test_tabby_ground_has_modest_ventral_warmth_with_visible_nonblack_markings(dilute):
    spec=CoatSpec(12,dark_pattern='classic',dilute=dilute)
    palette=material._palette(spec)
    bare=CoatSample(0,0,0,0)
    marking=CoatSample(1,0,0,0)
    back=material._coat_albedo(spec,bare,'body',.5,0,palette)
    belly=material._coat_albedo(spec,bare,'body',.5,1,palette)
    dark=material._coat_albedo(spec,marking,'body',.5,.5,palette)
    assert 0<sum(belly)/3-sum(back)/3<35
    assert min(dark)>30 and sum(back)/3-sum(dark)/3>45
    assert material._coat_albedo(spec,CoatSample(1,1,.5,1),'body',.5,.5,palette)==palette[-1]


@pytest.mark.parametrize('dilute',[False,True])
def test_solid_tortie_orange_has_ghost_bars_while_torbie_and_orange_keep_tabby(dilute):
    # Keep pigment identity and pattern fields intact: only non-agouti mixed
    # coats get the softer orange ghost contrast visible in real calicos.
    specs=[CoatSpec(551,pigment='tortie',dilute=dilute),
           CoatSpec(551,pigment='tortie',dark_pattern='mackerel',dilute=dilute),
           CoatSpec(551,pigment='orange',dilute=dilute)]
    contrasts=[]
    for spec in specs:
        palette=material._palette(spec)
        ground=material._coat_albedo(spec,CoatSample(0,0,1,0),'body',.5,.5,palette)
        bar=material._coat_albedo(spec,CoatSample(0,1,1,0),'body',.5,.5,palette)
        contrasts.append(sum(ground)-sum(bar))
    assert contrasts[0]==pytest.approx(contrasts[1]*.24)
    assert contrasts[1]==pytest.approx(contrasts[2])
    assert contrasts[0]>0


def test_tortie_ghost_material_is_shared_with_portrait_head(qt):
    from backyard_visitor import portrait_art
    from backyard_visitor.coat_traits import coat_field
    spec=CoatSpec(551,pigment='tortie')
    atlas=portrait_art._portrait_atlas(spec)
    palette=material._palette(spec)
    field=coat_field(spec)
    # The portrait uses its established softer face contrast but the same
    # material function, rather than restoring strong bars on the face.
    for side_index,side in enumerate(('left','right')):
        for x,y in ((11,12),(24,32),(48,50)):
            u,v=x/(material._GRID-1),y/(material._GRID-1)
            sample=portrait_art._frontal_sample(field,u,v,side)
            expected=material._coat_albedo(spec,sample,'head',u,v,palette,pattern_strength=.45)
            offset=((side_index*5+1)*material._GRID**2+y*material._GRID+x)*3
            assert atlas[offset:offset+3]==bytes(round(value) for value in expected)


@pytest.mark.parametrize('coat,baseline',[
    ('ginger',(109.392,101.262,88.400)),
    ('gray',(102.309,101.957,101.749)),
    ('tuxedo',(78.782,72.975,64.243)),
    ('black',(42.446,38.041,36.149)),
])
def test_legacy_portraits_keep_their_existing_color_calibration(qt,coat,baseline):
    from backyard_visitor import portrait_art
    image=portrait_art._frame(coat).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    raw=image.constBits().asstring(image.sizeInBytes())
    visible=[raw[o:o+3] for o in range(0,len(raw),4) if raw[o+3]>240]
    means=[sum(pixel[channel] for pixel in visible)/len(visible) for channel in range(3)]
    # Recorded from the previous verified candidate, not re-derived from the
    # renderer. Pattern shape may change modestly; a 20-30 point lightening
    # would visibly detach these old portraits from their unchanged sprites.
    assert means==pytest.approx(baseline,abs=4)
    if coat=='gray':
        side=CatArt().frame('standing',0,-1,coat).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        data=side.constBits().asstring(side.sizeInBytes())
        luminances=[data[o] for o in range(0,len(data),4) if data[o+3]>240]
        assert abs(means[0]-sum(luminances)/len(luminances))<10


def test_legacy_and_generated_same_spec_do_not_pollute_each_others_atlas_cache(qt):
    from backyard_visitor import portrait_art
    spec=CoatSpec(39,dark_pattern='mackerel')
    def pixels(coat):
        image=portrait_art._frame(coat).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        return image.constBits().asstring(image.sizeInBytes())
    portrait_art.clear_portrait_cache()
    generated_first=pixels(spec)
    legacy_second=pixels('ginger')
    portrait_art.clear_portrait_cache()
    legacy_first=pixels('ginger')
    generated_second=pixels(spec)
    assert legacy_first==legacy_second
    assert generated_first==generated_second
    assert legacy_first!=generated_first


def test_two_axis_fur_interpolation_tracks_gradient_without_part_bleeding():
    grid=material._GRID
    atlas=bytearray([255]*(10*grid*grid*3))
    for y in range(grid):
        for x in range(grid):
            i=(y*grid+x)*3
            atlas[i:i+3]=bytes((x,y,0))
    for u,v in ((-.85,-1.1),(128.45,91.7),(255.85,256.1)):
        actual=material._sample_atlas(atlas,0,0,u,v)
        expected=(min(255.,max(0.,u))*(grid-1)/255,
                  min(255.,max(0.,v))*(grid-1)/255,0.)
        assert actual==pytest.approx(expected)
