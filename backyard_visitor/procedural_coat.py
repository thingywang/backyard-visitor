"""Stable canonical coat fields over plain-black authored fur illumination.

Only colour is evaluated here. Pose selection, shape, alpha and canvas remain
owned by the existing art modules. No source image is changed. Pattern fields
are shared across poses and sampled in anatomical coordinates, never in time.
"""
from __future__ import annotations
from collections import OrderedDict
import math
import logging
from PyQt6.QtGui import QImage,QPixmap,QColor
from .black_coat import source_key
from .coat_mapping import _catalog,PART_NAMES,CoatMappingError
from .coat_traits import CoatSpec,CoatSample,coat_field
from .coat_joints import uses_joint_pigments

_GRID=64
# Authored maps use byte UV coordinates. Keep the fractional position rather
# than snapping to a 64-cell colour grid; fur illumination stays per-pixel.
_UV_BILINEAR=tuple((value*(_GRID-1)//255,
                    min(_GRID-1,value*(_GRID-1)//255+1),
                    (value*(_GRID-1)%255)/255)
                   for value in range(256))
_atlases=OrderedDict()
_frames=OrderedDict()
_materials=OrderedDict()
load_error=None
pending_error=None
_warned=False
_LOG=logging.getLogger(__name__)


def _mix(a,b,t):return tuple(x+(y-x)*t for x,y in zip(a,b))


def _sample_atlas(atlas,part,side,u,v):
    """Interpolate only canonical albedo, never silhouette or fur detail.

    Clamp the upper neighbour within this anatomical region so edge samples
    cannot bleed into the next part or the opposite side of the animal.
    """
    if isinstance(u,int):
        x0,x1,fx=_UV_BILINEAR[u]
    else:
        coordinate=max(0.,min(255.,u))*(_GRID-1)/255
        x0=int(coordinate);x1=min(_GRID-1,x0+1);fx=coordinate-x0
    if isinstance(v,int):
        y0,y1,fy=_UV_BILINEAR[v]
    else:
        coordinate=max(0.,min(255.,v))*(_GRID-1)/255
        y0=int(coordinate);y1=min(_GRID-1,y0+1);fy=coordinate-y0
    base=(side*len(PART_NAMES)+part)*_GRID*_GRID*3
    a=base+(y0*_GRID+x0)*3;b=base+(y0*_GRID+x1)*3
    c=base+(y1*_GRID+x0)*3;d=base+(y1*_GRID+x1)*3
    return tuple((atlas[a+i]+(atlas[b+i]-atlas[a+i])*fx)*(1-fy)
                 +(atlas[c+i]+(atlas[d+i]-atlas[c+i])*fx)*fy
                 for i in range(3))


def _fur_light(luminance, albedo):
    light=min(1.08,.30+.68*(max(0.,luminance)/64)**.72)
    # White fur scatters more light; only its broad shadow response changes.
    # The black coat and neutral microtexture remain untouched.
    white_fraction=max(0.,min(1.,(min(albedo)-180)/55))
    return light+max(0.,1-light)*.50*white_fraction


def _palette(spec):
    if spec.dilute:
        dark=(112,120,134) if spec.dark_pattern=='solid' else (155,160,166)
        marks=(85,89,100);orange=(231,196,149);orange_marks=(183,143,101)
    else:
        dark=(43,44,47) if spec.dark_pattern=='solid' else (153,133,104)
        marks=(53,44,36);orange=(226,158,83);orange_marks=(151,86,40)
    return dark,marks,orange,orange_marks,(247,243,234)



def _coat_albedo(spec, sample, part, u, v, palette, pattern_strength=1.):
    """Pigmented hairs over a warm agouti ground, shared with portraits.

    The gradient is anatomical and modest; it is not a lighting gradient or a
    second random pattern. Flat black/blue and pure white bypass it exactly.
    """
    dark,marks,orange,orange_marks,white=palette
    if sample.white==1:
        return white
    amount=(.20*v*v if part=='body' else .075*u if part in ('front_leg','hind_leg')
            else .035*v if part=='tail' else 0.)
    if spec.dark_pattern!='solid':
        cream=(204,195,177) if spec.dilute else (198,177,140)
        dark=_mix(dark,cream,amount)
    if spec.orange_pattern!='solid':
        orange=_mix(orange,(241,211,164),amount)
    a=_mix(dark,marks,sample.dark_tabby*pattern_strength)
    # Non-agouti torties/calicos keep faint orange ghost striping. Strong
    # orange bars belong to orange tabbies and patterned torties (torbies).
    # This changes material contrast only, not the stored pigment identity.
    orange_strength=.24 if spec.pigment=='tortie' and spec.dark_pattern=='solid' else 1.
    b=_mix(orange,orange_marks,sample.orange_tabby*pattern_strength*orange_strength)
    return _mix(_mix(a,b,sample.orange),white,sample.white)


def _joint_pigment(field, joint, fiber=0.):
    """Keep limb markings but carry body pigments from the fixed rest chart."""
    part,side,u,v,distance,bu,bv,body_side=joint
    own=field.sample(PART_NAMES[part],max(0.,min(1.,u+fiber/255)),v,
                     'right' if side else 'left')
    body=field.sample('body',max(0.,min(1.,bu+fiber/255)),bv,
                      'right' if body_side else 'left')
    # Distance belongs to the immutable reference, never the current body
    # position. A folded foot therefore cannot regain pigment near the torso.
    a=max(0.,min(1.,(distance-1.)/.6))
    b=max(0.,min(1.,(u-.65)/.30))
    sock=max(a*a*(3-2*a),b*b*(3-2*b))
    return CoatSample(own.dark_tabby,own.orange_tabby,body.orange,
                      body.white+(1-body.white)*sock)


def _joint_albedo(spec,field,joint,palette,fiber=0.):
    sample=_joint_pigment(field,joint,fiber)
    return _coat_albedo(spec,sample,PART_NAMES[joint.part],joint.u,joint.v,palette)


def _has_fur_boundaries(spec):
    return (spec.white_mode!='all-white' and
            (spec.white_mode=='spotting' or spec.pigment=='tortie'
             or (spec.pigment=='dark' and spec.dark_pattern!='solid')
             or (spec.pigment=='orange' and spec.orange_pattern!='solid')))


def _fur_offset(material, offset, width, height):
    """Subpixel pigment interleaving driven by authored, unpatterned fur.

    The local high-frequency residual follows existing hairs. Broad shadows
    cancel, transparent neighbours are excluded, and no new random raster is
    introduced. This shifts pigment only, never image pixels or alpha.
    """
    pixel=offset//4;x=pixel%width;y=pixel//width
    r,g,b=material[offset:offset+3];center=(54*r+183*g+19*b)/256
    total=0.;count=0
    for neighbor in (offset-4 if x else -1,offset+4 if x+1<width else -1,
                     offset-width*4 if y else -1,offset+width*4 if y+1<height else -1):
        if neighbor<0 or material[neighbor+3]<240:continue
        nr,ng,nb=material[neighbor:neighbor+3]
        total+=(54*nr+183*ng+19*nb)/256;count+=1
    if not count:return 0.
    return max(-.85,min(.85,(center-total/count)/12))


def _fur_uv(spec, part, u, v, fiber):
    """Interleave broad classic edges along the authored fine hairs.

    The extra offset is bounded to 1.7/1.1 byte-UV units. It only affects the
    curved torso field; a smooth light/shadow ramp or a flat coat adds none.
    No independent noise, new silhouette or per-frame randomness is added.
    """
    classic = part == 0 and (
        spec.pigment != 'orange' and spec.dark_pattern == 'classic'
        or spec.pigment != 'dark' and spec.orange_pattern == 'classic')
    return (u + 2.0*fiber, v + 1.3*fiber) if classic else (u + fiber, v)


def _atlas(spec):
    if spec in _atlases:
        _atlases.move_to_end(spec);return _atlases[spec]
    field=coat_field(spec);palette=_palette(spec)
    pixels=bytearray(len(PART_NAMES)*2*_GRID*_GRID*3)
    offset=0
    for side in ('left','right'):
        for part in PART_NAMES:
            for y in range(_GRID):
                for x in range(_GRID):
                    sample=field.sample(part,x/(_GRID-1),y/(_GRID-1),side)
                    color=_coat_albedo(spec,sample,part,x/(_GRID-1),y/(_GRID-1),palette)
                    pixels[offset:offset+3]=bytes(round(v) for v in color);offset+=3
    result=bytes(pixels);_atlases[spec]=result
    while len(_atlases)>4:_atlases.popitem(last=False)
    return result


def _material(key,row,neutral):
    if neutral is not None:
        image=neutral.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    elif key in _materials:
        _materials.move_to_end(key);return _materials[key]
    else:
        image=QImage(str(_catalog.neutral_path(row))).convertToFormat(QImage.Format.Format_RGBA8888)
    if image.isNull() or [image.width(),image.height()]!=row['size']:
        raise CoatMappingError('Neutral fur canvas differs from its source pose')
    raw=image.constBits().asstring(image.sizeInBytes())
    result=(image.width(),image.height(),raw)
    if neutral is None:
        _materials[key]=result
        while len(_materials)>12:_materials.popitem(last=False)
    return result


def _render_frame(original:QPixmap,spec:CoatSpec,*,pose_key:str|None=None,
                     neutral:QPixmap|None=None)->QPixmap:
    """Return same canvas/DPR using the authored plain-black alpha geometry.

    pose_key is diagnostic only: source-key registration is authoritative, so
    optional action load order cannot change a mapping. Parkour callers may
    supply their already validated independent black material via neutral.
    Unknown or changed maps raise CoatMappingError rather than quietly adding
    a pattern in screen coordinates or falling back to the original tabby.
    """
    if not isinstance(spec,CoatSpec):raise TypeError('Expected CoatSpec')
    if original.isNull():return QPixmap(original)
    key=source_key(original);cache_key=(key,spec,original.devicePixelRatio())
    if cache_key in _frames:
        _frames.move_to_end(cache_key);return QPixmap(_frames[cache_key])
    row,uv=_catalog.get(key,(original.width(),original.height()))
    width,height,material=_material(key,row,neutral)
    atlas=_atlas(spec);out=bytearray(width*height*4)
    textured=_has_fur_boundaries(spec)
    seams=_catalog.seam_samples(key,uv,width,height,row["dpr"])
    joints=(_catalog.joint_samples(key,uv,width,height,row["dpr"])
            if uses_joint_pigments(spec) else {})
    field=coat_field(spec) if joints else None
    palette=_palette(spec) if joints else None
    for offset in range(0,len(out),4):
        r,g,b,alpha=material[offset:offset+4]
        if not alpha:continue
        code,u,v,valid=uv[offset:offset+4]
        part=(code&7)-1;side=1 if code&8 else 0
        protect=code>>4
        if not valid or not 0<=part<len(PART_NAMES):raise CoatMappingError('Opaque fur pixel has no anatomical part')
        fiber=_fur_offset(material,offset,width,height) if textured and not protect else 0.
        fu,fv=_fur_uv(spec,part,u,v,fiber)
        albedo=_sample_atlas(atlas,part,side,fu,fv)
        seam=seams.get(offset)
        joint=joints.get(offset)
        if joint is not None:
            albedo=_joint_albedo(spec,field,joint,palette,fiber)
            seam=None  # Do not reintroduce unrelated torso/limb RGB mixtures.
        if seam is not None:
            u2,v2,side2,weight=seam
            fu2,fv2=_fur_uv(spec,0,u2,v2,fiber)
            albedo=_mix(albedo,_sample_atlas(atlas,0,side2,fu2,fv2),weight)
        # Neutral illumination comes solely from the authored unpatterned coat.
        # Gamma-softened dark detail retains fur and volume even on white fur.
        luminance=(54*r+183*g+19*b)/256
        light=_fur_light(luminance,albedo)
        keep=protect/15
        out[offset:offset+4]=bytes((min(255,round(albedo[0]*light*(1-keep)+r*keep)),
                                   min(255,round(albedo[1]*light*(1-keep)+g*keep)),
                                   min(255,round(albedo[2]*light*(1-keep)+b*keep)),alpha))
    image=QImage(bytes(out),width,height,width*4,QImage.Format.Format_RGBA8888).copy()
    image.setDevicePixelRatio(original.devicePixelRatio());result=QPixmap.fromImage(image)
    _frames[cache_key]=QPixmap(result)
    while len(_frames)>24:_frames.popitem(last=False)
    return result


def procedural_frame(original:QPixmap,spec:CoatSpec,*,pose_key:str|None=None,
                     neutral:QPixmap|None=None)->QPixmap:
    """Qt paint callbacks must survive missing/corrupt optional coat assets."""
    global load_error,pending_error,_warned
    try:
        result=_render_frame(original,spec,pose_key=pose_key,neutral=neutral)
        load_error=None
        return result
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RuntimeError) as error:
        load_error='随机毛色素材暂不可用，已使用临时纯色外观；身份数据未改变。'+str(error)
        pending_error=load_error
        if not _warned:_LOG.warning(load_error);_warned=True
        # Prefer the validated plain-black material. If even that is missing,
        # retain only source alpha on flat gray; never reveal baked tabby RGB.
        try:
            if neutral is not None and neutral.size()==original.size():fallback=QPixmap(neutral)
            else:
                row=_catalog.rows()[source_key(original)]
                fallback=QPixmap(str(_catalog.neutral_path(row)))
            if fallback.isNull() or fallback.size()!=original.size():raise ValueError('neutral unavailable')
            fallback.setDevicePixelRatio(original.devicePixelRatio())
            return fallback
        except (OSError,ValueError,TypeError,KeyError,AttributeError,RuntimeError):
            source=original.toImage();source.setDevicePixelRatio(1)
            alpha=source.convertToFormat(QImage.Format.Format_Alpha8)
            image=QImage(source.size(),QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(70,72,76));image.setAlphaChannel(alpha)
            image.setDevicePixelRatio(original.devicePixelRatio())
            return QPixmap.fromImage(image)


def clear_procedural_cache():
    global load_error,pending_error,_warned
    _frames.clear();_materials.clear();_atlases.clear();_catalog.clear()
    load_error=None;pending_error=None;_warned=False
