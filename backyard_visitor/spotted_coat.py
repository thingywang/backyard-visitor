"""Short, staggered limb markings for spotted coats, without flank-wide cuffs.

u follows the authored limb root-to-paw axis; v crosses the limb. The same
canonical field is used by standing, motion and frontal portrait maps.
"""
from __future__ import annotations
import math


def _hash(seed, x, y):
    n=(seed ^ (x*0x9E3779B1) ^ (y*0x85EBCA77)) & 0xffffffff
    n ^= n >> 16;n=(n*0x7FEB352D)&0xffffffff
    n ^= n >> 15;n=(n*0x846CA68B)&0xffffffff
    n ^= n >> 16
    return n/4294967295.


def _smooth(a,b,x):
    t=max(0.,min(1.,(x-a)/(b-a)))
    return t*t*(3.-2.*t)


def spotted_limb_pattern(seed: int, u: float, v: float) -> float:
    """Separate elongated spots continue onto the thigh instead of ring bands."""
    x=u*4.3;y=v*3.6
    # A slight sideways bend follows the limb without aligning full-width rows.
    y += .17*math.sin(u*4.7)
    ix,iy=math.floor(x),math.floor(y)
    closest=10.
    for dx in (-1,0,1):
        for dy in (-1,0,1):
            cx,cy=ix+dx,iy+dy
            px=cx+.17+.66*_hash(seed,cx,cy)
            py=cy+.17+.66*_hash(seed^0xA511E9B3,cx,cy)
            radius=.22+.12*_hash(seed^0x63D83595,cx,cy)
            angle=-.45+.9*_hash(seed^0x298F1D31,cx,cy)
            ox,oy=x-px,y-py
            along=ox*math.cos(angle)+oy*math.sin(angle)
            across=-ox*math.sin(angle)+oy*math.cos(angle)
            length=1.12+.32*_hash(seed^0xBC416891,cx,cy)
            closest=min(closest,math.hypot(along/length,across)/radius)
    return 1.-_smooth(.66,1.18,closest)
