"""Stable pigment coordinates at white-spotted cats' limb attachments.

The standing reference is an anatomical rest chart, not a per-frame projection
onto whichever torso pixels a bent limb happens to cover. Source alpha and
landmarks are never moved. Limb tabby markings remain on their own chart.
"""
from __future__ import annotations

import math
from typing import NamedTuple

from .coat_mapping import PART_NAMES, CoatMappingError, _project_region


class JointSample(NamedTuple):
    part: int
    side: int
    u: float
    v: float
    body_distance: float
    body_u: float
    body_v: float
    body_side: int


def uses_joint_pigments(spec) -> bool:
    return spec.white_mode == 'spotting' and spec.white_coverage >= .2


def _point_on_limb(region, u, signed):
    path = region['path']
    lengths = [math.hypot(b[0]-a[0], b[1]-a[1])
               for a, b in zip(path, path[1:])]
    if not lengths or sum(lengths) <= 0:
        raise CoatMappingError('Degenerate reference limb')
    distance = u * sum(lengths)
    before = 0.
    selected = len(lengths)-1
    for i, length in enumerate(lengths):
        if distance <= before+length:
            selected = i
            break
        before += length
    a, b = path[selected], path[selected+1]
    length = lengths[selected]
    if length <= 0:
        raise CoatMappingError('Degenerate reference limb segment')
    t = (distance-before)/length
    dx, dy = (b[0]-a[0])/length, (b[1]-a[1])/length
    offset = signed*region['radius']/region['sign']
    return (a[0]+t*(b[0]-a[0])-dy*offset,
            a[1]+t*(b[1]-a[1])+dx*offset)


def _root_extension(x, y, region):
    """Only a round root cap may extend before canonical limb u=0."""
    a, b = region['path'][:2]
    dx, dy = b[0]-a[0], b[1]-a[1]
    first = math.hypot(dx, dy)
    total = sum(math.hypot(q[0]-p[0], q[1]-p[1])
                for p, q in zip(region['path'], region['path'][1:]))
    if first <= 0 or total <= 0:
        raise CoatMappingError('Degenerate limb root')
    return min(0., ((x-a[0])*dx+(y-a[1])*dy)/first/total)


def reference_projection(part, side, u, v, reference_body, reference_legs):
    """Project a fixed anatomical limb point; screen position is not an input."""
    reference = reference_legs[(PART_NAMES[part], 'right' if side else 'left')]
    x, y = _point_on_limb(reference, u, v*2-1)
    return _project_region(x, y, reference_body)


def limb_subject(code, u_byte, v_byte, x, y, regions, *, allow_head_cap=False):
    """Resolve limbs, including a narrow head-cap/raised-forearm overlap.

    The head capsule can cover the middle of a raised forearm. Only an
    unprotected nose-end cap with a close, distal front-leg projection is
    eligible. Eyes/ears and actual tail overlap keep their authored ownership.
    """
    part = (code & 7)-1
    side = int(bool(code & 8))
    u, v = u_byte/255, v_byte/255
    if part in (3, 4):
        choices = [r for r in regions if r['part'] == PART_NAMES[part]
                   and _project_region(x, y, r)[3] == side]
        if not choices:
            raise CoatMappingError('Limb pixel has no matching anatomical side')
        leg = min(choices, key=lambda r: _project_region(x, y, r)[0])
        # Using the baked coordinates avoids the nearest-segment switch at a
        # bent knee. Reproject only the tiny cap that the byte map clamps to 0.
        return part, side, _root_extension(x, y, leg) if not u_byte else u, v
    if allow_head_cap and part == 1 and not code >> 4 and u_byte == 0:
        front = [r for r in regions if r['part'] == 'front_leg']
        if front:
            leg = min(front, key=lambda r: _project_region(x, y, r)[0])
            distance, lu, lv, ls = _project_region(x, y, leg)
            if distance < 1.35 and lu > .35:
                return 3, ls, lu, lv
    return None


def build_joint_samples(catalog, key, raw, width, height, dpr):
    # Animated poses carry pigment on one immutable standing reference; the
    # static frontal portrait uses its own fixed anatomical reference.
    from .coat_mapping import _catalog as action_catalog
    row = catalog.rows()[key]
    # The seated frontal portrait is its own fixed rest chart. Forcing its
    # front-view chest through the side-view shoulder chart creates colour
    # cuffs despite otherwise stable limb coordinates. It never animates.
    reference = (row if row.get('label') == 'portrait-front-seated' else
                 next((r for r in action_catalog.rows().values() if r.get('label') == 'cat-018'), None))
    if reference is None:
        raise CoatMappingError('Standing limb reference is unavailable')
    reference_body = next((r for r in reference['regions'] if r['part'] == 'body'), None)
    if reference_body is None:
        raise CoatMappingError('Reference body is unavailable')
    reference_legs = {(r['part'], r['side']): r for r in reference['regions']
                      if r['part'] in ('front_leg', 'hind_leg')}
    if set(reference_legs) != {(part,side) for part in ('front_leg','hind_leg')
                               for side in ('left','right')}:
        raise CoatMappingError('Reference limbs are incomplete')
    regions = row.get('regions', [])
    # These two raised-arm maps are the only verified head-cap overlaps.
    # Grooming/sleeping paws can genuinely overlap the face: never infer
    # front/back occlusion there from distance alone.
    allow_head_cap = row.get('label') in ('parkour-jump_up-0', 'parkour-jump_up-1')
    samples = {}
    for y in range(height):
        for x in range(width):
            offset = (y*width+x)*4
            code, u, v, valid = raw[offset:offset+4]
            if not valid or (code & 7) not in (2, 4, 5):
                continue
            subject = limb_subject(code, u, v, (x+.5)/dpr, (y+.5)/dpr, regions,
                                   allow_head_cap=allow_head_cap)
            if subject is None:
                continue
            part, side, lu, lv = subject
            distance, bu, bv, bs = reference_projection(
                part, side, lu, lv, reference_body, reference_legs)
            if row.get('label') == 'portrait-front-seated':
                # A frontal chest is the shoulder's ventral surface, not a
                # trip along the entire side-view torso from neck to rump.
                bu = .24 if part == 3 else .82
            samples[offset] = JointSample(part, side, max(0., min(1., lu)), lv,
                                          distance, bu, bv, bs)
    return samples
