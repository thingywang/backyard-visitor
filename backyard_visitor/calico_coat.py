"""Shared rounded pigment patches and white interspaces for calico coats.

White is part of the same composition as orange and black. These canonical
fields never depend on a pose, lighting, or the requested white-area threshold.
"""
from __future__ import annotations
from functools import lru_cache
import math


def _hash(seed, x, y):
    n=(seed ^ (x*0x9E3779B1) ^ (y*0x85EBCA77)) & 0xffffffff
    n ^= n >> 16;n=(n*0x7FEB352D)&0xffffffff
    n ^= n >> 15;n=(n*0x846CA68B)&0xffffffff
    n ^= n >> 16
    return n/4294967295.


@lru_cache(maxsize=64)
def _lobes(seed):
    rows=[]
    for index,center in enumerate((.23,.76)):
        cx=center+.22*(_hash(seed,index,61)-.5)
        cy=.30+.28*_hash(seed,index,62)
        rx=.22+.055*_hash(seed,index,63)
        ry=.32+.16*_hash(seed,index,64)
        tilt=-.24+.48*_hash(seed,index,65)
        rows.append((cx,cy,rx,ry,tilt))
    return tuple(rows),_hash(seed,66,0)>.5


def calico_body_fields(seed: int, u: float, v: float) -> tuple[float,float]:
    """Return coupled white propensity and orange ownership, before thresholds."""
    lobes,swap=_lobes(seed)
    values=[]
    for cx,cy,rx,ry,tilt in lobes:
        x=(u-cx)/rx;y=(v-cy)/ry
        x+=tilt*y+.11*math.sin(y*1.6)
        values.append(math.exp(-.5*(x*x+y*y)))
    a,b=values
    # Smoothly join overlapping coloured regions. Outside those rounded
    # regions white can surround a complete patch instead of slicing it off
    # with a separate horizontal belly mask.
    pigment=min(1.,(a**6+b**6)**(1/6))
    # A broad white bay follows the gap between complete pigment lobes. It
    # can reach the spine instead of being confined to a horizontal belly
    # strip; total white is still calibrated by the existing area threshold.
    between=math.exp(-((a-b)/.20)**2)*min(1.,(a+b)/.6)
    # Some bays descend from the back, others rise from below. They do not
    # all form a full-height white waistband at the same body position.
    distance = v if _hash(seed,67,0)>.5 else 1-v
    t=max(0.,min(1.,(distance-.26)/.58))
    bay=1-t*t*(3-2*t)
    white=.45+.85*(1-pigment)+.24*between*bay+.08*v*v
    orange=a/(a+b) if a+b else .5
    return white,1-orange if swap else orange


@lru_cache(maxsize=64)
def _whole_body_patches(seed):
    """Unequal two/three-patch arrangement shared around both body sides."""
    count=3 if _hash(seed,81,0)>.38 else 2
    primary=min(count-1,int(_hash(seed,82,0)*count))
    primary_orange=_hash(seed,83,0)>.5
    centers=(.18,.50,.82) if count==3 else (.28,.73)
    rows=[]
    for index,center in enumerate(centers):
        main=index==primary
        cx=center+.14*(_hash(seed,index,84)-.5)
        cy=.42+.22*_hash(seed,index,85)
        # In a three-patch coat the single-colour patch balances two patches
        # of the other colour, including the limb colour they may continue.
        rx=((.315 if count==3 else .275)+.035*_hash(seed,index,86) if main else
            (.17 if count==3 else .22)+.035*_hash(seed,index,86))
        ry=((.44 if count==3 else .40)+.055*_hash(seed,index,87) if main else .32+.065*_hash(seed,index,87))
        if count==3 and main and primary==1:
            # Both end patches continue onto the legs. A central single-
            # colour patch needs a modest area allowance to balance them.
            rx*=1.04;ry*=1.02
        tilt=-.24+.48*_hash(seed,index,88)
        orange=primary_orange if main else not primary_orange
        rows.append((cx,cy,rx,ry,tilt,orange))
    return tuple(rows)


def calico_patch_fields(seed: int, u: float, v: float, side: str) -> tuple[float,float]:
    """Whole rounded patches with correlated, gently asymmetric body sides."""
    values=[];orange=dark=0.
    sign=1. if side=='left' else -1.
    for index,(cx,cy,rx,ry,tilt,is_orange) in enumerate(_whole_body_patches(seed)):
        cx+=sign*.020*(_hash(seed,index,89)-.5)
        cy+=sign*.032*(_hash(seed,index,90)-.5)
        x=(u-cx)/rx;y=(v-cy)/ry
        x+=tilt*y+.10*math.sin(y*1.5)
        value=math.exp(-.5*(x*x+y*y));values.append(value)
        if is_orange:orange=max(orange,value)
        else:dark=max(dark,value)
    pigment=min(1.,sum(value**6 for value in values)**(1/6))
    between=math.exp(-((orange-dark)/.20)**2)*min(1.,(orange+dark)/.6)
    distance=v if _hash(seed,91,0)>.5 else 1-v
    t=max(0.,min(1.,(distance-.26)/.58));bay=1-t*t*(3-2*t)
    # White occupies the actual inter-patch gaps, including a gentle
    # continuation around the flank. Otherwise a broad central lobe can
    # bridge both neighbours into one uninterrupted saddle.
    white=.45+.85*(1-pigment)+.48*between*(.40+.60*bay)+.06*v*v
    ownership=orange/(orange+dark) if orange+dark else .5
    return white,ownership
