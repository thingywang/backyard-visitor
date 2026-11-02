"""Anatomical tabby face markings, independent of torso pattern topology.

Profile input follows the packaged head chart (nose to back, crown to jaw).
Frontal input follows portrait-head's existing encoded half-face chart. These
functions return pigment intensity only; eyes, nose, alpha and fur lighting
remain owned by the renderer. No screen-space/time randomness is used.
"""
from __future__ import annotations
import math


def _smooth(a, b, x):
    t = max(0., min(1., (x-a)/(b-a)))
    return t*t*(3.-2.*t)


def _window(x, start, end, feather=.05):
    return _smooth(start-feather, start+feather, x)*(1.-_smooth(end-feather, end+feather, x))


def _stroke(distance, width):
    return 1.-_smooth(width*.48, width*1.65, abs(distance))


def _variation(seed, salt):
    n = (int(seed) ^ (salt*0x9e3779b1)) & 0xffffffff
    n ^= n >> 16
    n = (n*0x7feb352d) & 0xffffffff
    n ^= n >> 15
    return n/4294967295. - .5


def tabby_side_face(seed: int, u: float, v: float) -> float:
    """Short crown strokes and two cheek courses, never a whole-head comb."""
    shift = .025*_variation(seed, 1)
    mark = 0.
    # These short strokes live above the eye. Their unequal ends leave the
    # lower cheek and muzzle clear instead of carrying a diagonal stripe from
    # the ear all the way through the chin.
    for i, (center, end) in enumerate(((.37,.31),(.60,.38),(.81,.32))):
        center += shift + .035*math.sin(v*7.0+i*.8)
        width = .021 + .006*(_variation(seed, 10+i)+.5)
        crown = _stroke(u-center, width)*_window(v,.015,end,.065)
        mark = max(mark, .84*crown)
    # The profile UV axis slopes towards the ear. Increasing v compensates
    # for that slope, producing eye-to-cheek courses rather than slash marks.
    cheek_u = _window(u,.30,.94,.08)
    first = .45 + .26*(u-.30) + .025*math.sin((u-.30)*5.2) + shift*.4
    second = .64 + .18*(u-.35) - .025*math.sin((u-.35)*4.1) - shift*.3
    mark = max(mark, .76*_stroke(v-first,.024)*cheek_u,
               .63*_stroke(v-second,.021)*_window(u,.36,.88,.075))
    # Preserve a continuous light muzzle/jaw. The renderer still independently
    # protects the authored iris, nose and inner-ear pixels.
    return min(1.,max(0.,mark))*(1.-_smooth(.84,.94,v))


def frontal_lateral_coordinate(u: float, v: float) -> float:
    """Undo portrait-head's vertical offset, returning center-to-cheek x/24."""
    ly = 15. + 48.*v
    offset = .10*max(0.,43.-ly)/29. + .035*math.sin((ly-25.)/12.)
    return max(0.,min(1.4,(u-offset)/.62))


def tabby_frontal_face(seed: int, u: float, v: float) -> float:
    """Paired forehead pencillings and short outer cheek strokes in a portrait.

    u is encoded portrait-head u, not screen x. Both halves are naturally
    mirrored by the chart; small seeded width changes avoid a printed emblem.
    The layout is not obtained by rotating/scaling the profile stripes.
    """
    q = frontal_lateral_coordinate(u,v)
    shift = .016*_variation(seed, 21)
    center = _stroke(q,.035)*_window(v,.19,.46,.055)*.68
    inner_axis = .18 + .10*math.sin((v-.12)*6.1) + shift
    inner = _stroke(q-inner_axis,.031)*_window(v,.19,.52,.055)*.88
    outer_axis = .56 - .20*(v-.20) - shift
    outer = _stroke(q-outer_axis,.031)*_window(v,.21,.48,.06)*.72
    # Brows turn slightly toward the center before stopping above the eyes.
    brow_v = .50 - .11*(q-.20) + .015*math.sin(q*7.)
    brow = _stroke(v-brow_v,.019)*_window(q,.19,.48,.04)*.62
    upper_v = .61 + .19*(q-.48) + shift*.35
    lower_v = .76 + .11*(q-.58) - shift*.3
    cheek = max(.74*_stroke(v-upper_v,.023)*_window(q,.50,.96,.07),
                .59*_stroke(v-lower_v,.021)*_window(q,.59,.98,.07))
    return min(1.,max(center,inner,outer,brow,cheek))*(1.-_smooth(.87,.96,v))
