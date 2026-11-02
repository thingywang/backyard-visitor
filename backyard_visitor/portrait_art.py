"""Full-body frontal seated portraits, independent of desktop action canvases.

The original generated neutral photo remains unchanged. The packaged Qt-sized
canvas uses its own anatomical map while sharing Q15's canonical coat fields.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import logging

from PyQt6.QtGui import QImage, QPixmap
from .black_coat import source_key
from .coat_mapping import MappingCatalog, PART_NAMES, CoatMappingError
from .coat_traits import CoatSpec, coat_field
from .coat_joints import uses_joint_pigments
from .tabby_face import tabby_frontal_face
from .procedural_coat import _atlas, _fur_light, _mix, _palette, _GRID, _sample_atlas, _coat_albedo, _has_fur_boundaries, _fur_offset, _fur_uv, _joint_albedo

WIDTH, HEIGHT, DPR = 144, 168, 2
ASSETS = Path(__file__).with_name('assets') / 'portraits'
_catalog = MappingCatalog(ASSETS)
_cache = OrderedDict()
_head_atlases = OrderedDict()
_material = None
_warned = False
load_error = None
_LOG = logging.getLogger(__name__)
_LEGACY_TABBY = CoatSpec(39, dark_pattern='mackerel')


def _source():
    global _material
    if _material is not None:
        return _material
    rows = _catalog.rows()
    if len(rows) != 1:
        raise CoatMappingError('Portrait must have exactly one anatomical map')
    key, row = next(iter(rows.items()))
    image = QImage(str(_catalog.neutral_path(row))).convertToFormat(QImage.Format.Format_RGBA8888)
    if image.isNull() or (image.width(), image.height()) != (WIDTH * DPR, HEIGHT * DPR):
        raise CoatMappingError('Portrait canvas is unavailable')
    if source_key(QPixmap.fromImage(image)) != key:
        raise CoatMappingError('Portrait source fingerprint differs')
    _, mapping = _catalog.get(key, (image.width(), image.height()))
    raw = image.constBits().asstring(image.sizeInBytes())
    seams = _catalog.seam_samples(key, mapping, image.width(), image.height(), DPR)
    _material = raw, mapping, seams
    return _material


def _frontal_sample(field, u, v, side):
    """Share pigments with all poses, but interpret the frontal head chart."""
    sample = field.sample('head', u, v, side)
    mark = tabby_frontal_face(field._seeds['head', side, 'pattern'], u, v)
    return sample._replace(
        dark_tabby=0. if field.spec.dark_pattern == 'solid' else mark,
        orange_tabby=0. if field.spec.orange_pattern == 'solid' else mark)



def _frontal_body_albedo(spec, field, u, v, side, palette):
    """A front chest and its neck seam share the same ventral shoulder."""
    cu, cv = max(0., min(1., u/255)), max(0., min(1., v/255))
    name = 'right' if side else 'left'
    own = field.sample('body', cu, cv, name)
    chest = field.sample('body', .24, cv, name)
    sample = own._replace(orange=chest.orange, white=chest.white)
    return _coat_albedo(spec, sample, 'body', cu, cv, palette)

def _portrait_atlas(spec, *, legacy=False):
    cache_key = (spec, legacy)
    if cache_key in _head_atlases:
        _head_atlases.move_to_end(cache_key)
        return _head_atlases[cache_key]
    pixels = (bytearray(len(PART_NAMES) * 2 * _GRID * _GRID * 3) if legacy
              else bytearray(_atlas(spec)))
    field = coat_field(spec)
    palette = _palette(spec)
    # Generated identities share the new material; old string identities keep
    # the palette calibrated against their unchanged action PNGs. In
    # particular, the legacy 1.35 adjustment must not brighten the new base.
    # Both paths retain the same natural anatomical pattern field.
    for side_index, side in enumerate(('left', 'right')):
        for part_index, part in enumerate(PART_NAMES):
            if part != 'head' and not legacy:
                continue
            strength = (.30 if legacy else .45) if part == 'head' else 1.
            for y in range(_GRID):
                for x in range(_GRID):
                    u, v = x / (_GRID - 1), y / (_GRID - 1)
                    sample = (_frontal_sample(field, u, v, side) if part == 'head' and not legacy
                              else field.sample(part, u, v, side))
                    color = (_mix((121, 112, 97), (31, 30, 31),
                                  sample.dark_tabby * strength) if legacy else
                             _coat_albedo(spec, sample, part, u, v, palette,
                                          pattern_strength=strength))
                    index = (((side_index * len(PART_NAMES) + part_index) * _GRID + y) * _GRID + x) * 3
                    pixels[index:index+3] = bytes(round(value) for value in color)
    result = bytes(pixels)
    _head_atlases[cache_key] = result
    while len(_head_atlases) > 4:
        _head_atlases.popitem(last=False)
    return result


def _legacy_color(color, coat):
    # Existing identities are brown tabby, gray tabby, dark tabby and solid
    # black. The historical "tuxedo" key never meant white bicolor markings.
    r, g, b = (min(255, value * 1.35) for value in color)
    if coat == 'gray':
        value = (54 * r + 183 * g + 19 * b) / 256
        return value, value, value
    if coat == 'tuxedo':
        return _mix((r, g, b), (33, 31, 29), 94 / 255)
    return r, g, b


def _frame(coat):
    if not isinstance(coat, CoatSpec) and coat not in ('ginger', 'gray', 'tuxedo', 'black'):
        raise ValueError('Unsupported portrait appearance')
    if coat in _cache:
        _cache.move_to_end(coat)
        return QPixmap(_cache[coat])
    material, mapping, seams = _source()
    if coat == 'black':
        output = material
    else:
        generated = isinstance(coat, CoatSpec)
        spec = coat if generated else _LEGACY_TABBY
        atlas = _portrait_atlas(spec, legacy=not generated)
        textured = _has_fur_boundaries(spec)
        joints = {}
        if generated and uses_joint_pigments(spec):
            key = next(iter(_catalog.rows()))
            joints = _catalog.joint_samples(key, mapping, WIDTH * DPR, HEIGHT * DPR, DPR)
        field = coat_field(spec) if joints else None
        palette = _palette(spec) if joints else None
        output = bytearray(len(material))
        for offset in range(0, len(material), 4):
            r, g, b, alpha = material[offset:offset + 4]
            if not alpha:
                continue
            code, u, v, valid = mapping[offset:offset + 4]
            part = (code & 7) - 1
            if not valid or not 0 <= part < len(PART_NAMES):
                raise CoatMappingError('Portrait fur pixel has no body part')
            side = 1 if code & 8 else 0
            fiber = _fur_offset(material, offset, WIDTH * DPR, HEIGHT * DPR) if textured and not code >> 4 else 0.
            fu, fv = _fur_uv(spec, part, u, v, fiber)
            albedo = _sample_atlas(atlas, part, side, fu, fv)
            seam = seams.get(offset)
            joint = joints.get(offset)
            if joints and part == 0:
                albedo = _frontal_body_albedo(spec, field, fu, fv, side, palette)
                seam = None
            if joint is not None:
                albedo = _joint_albedo(spec, field, joint, palette, fiber)
                seam = None
            if seam:
                u2, v2, side2, weight = seam
                fu2, fv2 = _fur_uv(spec, 0, u2, v2, fiber)
                body_albedo = (_frontal_body_albedo(spec, field, fu2, fv2, side2, palette)
                               if joints else _sample_atlas(atlas, 0, side2, fu2, fv2))
                albedo = _mix(albedo, body_albedo, weight)
            if not generated:
                albedo = _legacy_color(albedo, coat)
            light = _fur_light((54 * r + 183 * g + 19 * b) / 256, albedo)
            keep = (code >> 4) / 15
            output[offset:offset + 4] = bytes((min(255, round(albedo[0] * light * (1 - keep) + r * keep)),
                                               min(255, round(albedo[1] * light * (1 - keep) + g * keep)),
                                               min(255, round(albedo[2] * light * (1 - keep) + b * keep)), alpha))
    image = QImage(bytes(output), WIDTH * DPR, HEIGHT * DPR, WIDTH * DPR * 4,
                   QImage.Format.Format_RGBA8888).copy()
    image.setDevicePixelRatio(DPR)
    result = QPixmap.fromImage(image)
    _cache[coat] = QPixmap(result)
    while len(_cache) > 24:
        _cache.popitem(last=False)
    return result


def portrait_frame(cat) -> QPixmap:
    """Transparent 144×168 logical-pixel seated portrait of a CatProfile.

    Missing/corrupt optional portrait assets return a null pixmap; the handbook
    can show its accessible photo placeholder instead of a side-facing sprite.
    Identity, nickname and trust are not read, stored or exposed by rendering.
    """
    global load_error, _warned
    try:
        result = _frame(cat.render_coat)
        load_error = None
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
        load_error = '访客照片暂不可用。' + str(error)
        if not _warned:
            _LOG.warning(load_error)
            _warned = True
        return QPixmap()


def clear_portrait_cache():
    global _material, load_error, _warned
    _cache.clear()
    _head_atlases.clear()
    _catalog.clear()
    _material = None
    load_error = None
    _warned = False
