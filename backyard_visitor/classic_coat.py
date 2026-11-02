"""Photo-referenced broad blotches and surrounding classic-tabby bands.

This is an appearance field, not a genetic simulation. The existing per-side
pattern seed and anatomical coordinates remain the only inputs.
"""
from __future__ import annotations

import math


def _hash(seed: int, x: int) -> float:
    n = (seed ^ (x * 0x9E3779B1)) & 0xFFFFFFFF
    n ^= n >> 16
    n = (n * 0x7FEB352D) & 0xFFFFFFFF
    n ^= n >> 15
    n = (n * 0x846CA68B) & 0xFFFFFFFF
    n ^= n >> 16
    return n / 4294967295.0


def _smooth(low: float, high: float, value: float) -> float:
    t = min(1., max(0., (value-low)/(high-low)))
    return t*t*(3.-2.*t)


def classic_body_pattern(seed: int, u: float, v: float) -> float:
    """Visible-flank blotch with two unequal wraps and shoulder continuation."""
    phase=_hash(seed,0)*math.tau
    x=u+.016*math.sin(v*5.1+phase)
    y=v+.028*math.sin(u*5.8+phase*.7)
    cx=.64+.070*(_hash(seed,1)-.5)
    cy=.60+.060*(_hash(seed,2)-.5)
    bx=(x-cx)/(.092+.014*_hash(seed,3))
    by=(y-cy)/(.145+.025*_hash(seed,4))
    # Scale to the visible side (roughly v=.3..9). Several broad courses must
    # be visible on the actual animal, not hidden on an unseen dorsal half.
    bx += .37*math.sin(by*1.5)+.065*by*by
    by += .13*math.sin(bx*1.7+phase*.3)
    radius=math.hypot(bx,by)
    angle=math.atan2(by,bx)
    # Large low-frequency lobes change phase with distance from the centre.
    # Consequently the inner and outer contours are not scaled copies of one
    # ellipse, while their boundaries remain smooth and related.
    sway=radius/(radius+.45)
    d=radius+sway*((.26+.070*radius)*math.sin(2*angle+.78*radius+phase)
                   +(.14+.035*radius)*math.sin(3*angle-.43*radius+phase*.5))
    d += .10*math.sin(x*8+y*3+phase)+.06*math.sin(y*8-x*4)
    # Carry the outer courses into narrower shoulder curves. Taking a union
    # of several distance fields used to collapse all the surrounding rings
    # to a dark slab, leaving only one bright emblem-shaped outline.
    shoulder_base=2.15+(.39-x)/.085
    # Shoulder courses bow in the same direction as the left half of the
    # flank wraps. Unequal monotone spacing makes one course broader without
    # introducing detached strokes or a new motif at the junction.
    shoulder=shoulder_base-.65*math.exp(-((y-(cy-.04))/.31)**2)
    shoulder += .10*math.sin(y*4.5+phase*.3)
    shoulder += .22*math.sin(shoulder_base*2.3+phase*.2)*(.55+.35*y)
    mix=1-_smooth(.28,.44,u)
    d=d*(1-mix)+shoulder*mix
    core_edge=1.04+.18*math.cos(angle+phase*.7)
    core=1-_smooth(core_edge-.17,core_edge+.12,d)
    inner=1.49+.16*math.sin(x*5.5+y*3+phase)
    outer=2.08+.20*math.sin(y*4.9-x*3.3+phase*.4)
    wrap=_smooth(inner-.10,inner+.09,d)*(1-_smooth(outer-.09,outer+.12,d))
    second_inner=2.63+.15*math.sin(x*5-y*2.7+phase*.5)
    second_outer=3.20+.20*math.sin(y*4.4+x*2.0)
    second=_smooth(second_inner-.12,second_inner+.10,d)*(1-_smooth(second_outer-.11,second_outer+.13,d))
    third=_smooth(3.78,3.92,d)*(1-_smooth(4.34,4.52,d))
    return max(core,wrap,second,third*.90)*(1-_smooth(.86,.995,v))
