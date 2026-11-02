"""Geometric guards supplement, but cannot replace, real-photo visual review."""
import math

import pytest

from backyard_visitor.classic_coat import classic_body_pattern
from backyard_visitor.coat_traits import derive_seed


def _pattern_seed(identity):
    return derive_seed(identity, "layout/pattern/body/left")


def _components(points):
    points = set(points)
    groups = []
    while points:
        start = points.pop()
        todo, group = [start], {start}
        while todo:
            x,y = todo.pop()
            for neighbor in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if neighbor in points:
                    points.remove(neighbor)
                    group.add(neighbor)
                    todo.append(neighbor)
        groups.append(group)
    return groups


@pytest.mark.parametrize("identity", [0, 8, 42, 122, 551])
def test_classic_has_a_filled_flank_blotch_and_several_visible_wraps(identity):
    seed = _pattern_seed(identity)
    points = {(x,y) for y in range(65) for x in range(97)
              if classic_body_pattern(seed,x/96,y/64) > .5}
    groups = _components(points)
    # Real classic cats can have an internal closed blotch and closed rings.
    # Requiring every component to reach the body edge forced the rejected
    # parallel racing stripes. Keep a substantial filled mid-flank area.
    core = next(g for g in groups if (61,38) in g)
    assert len(core) > 180
    assert max(x for x,y in core)-min(x for x,y in core) >= 12
    assert max(y for x,y in core)-min(y for x,y in core) >= 12
    assert .25 < len(points)/(97*65) < .65
    assert sum(len(g) for g in groups if len(g) < 8) < len(points)*.01
    # At actual side height the solid centre and surrounding courses must
    # coexist. One dark slab with a hairline outline cannot satisfy this.
    row = [classic_body_pattern(seed,x/200,.6) for x in range(201)]
    runs, start = [], None
    for x,value in enumerate(row+[0.]):
        if value > .5 and start is None:
            start = x
        elif value <= .5 and start is not None:
            runs.append((start,x-1)); start = None
    assert len(runs) >= 5
    centre_run = next((a,b) for a,b in runs if a <= 128 <= b)
    assert centre_run[1]-centre_run[0]+1 >= 20
    assert min(row) < .05 and max(row) > .95


@pytest.mark.parametrize("identity", [0, 42, 122, 551])
def test_classic_field_is_bounded_smooth_and_order_independent(identity):
    seed = _pattern_seed(identity)
    points = [(x/30,y/20) for x in range(31) for y in range(21)]
    before = [classic_body_pattern(seed,u,v) for u,v in points]
    assert all(math.isfinite(x) and 0 <= x <= 1 for x in before)
    after = [classic_body_pattern(seed,u,v) for u,v in reversed(points)]
    assert before == list(reversed(after))
    for u,v in points:
        if 0 < u < 1 and 0 < v < 1:
            value = classic_body_pattern(seed,u,v)
            assert abs(value-classic_body_pattern(seed,u+1e-6,v)) < .002
            assert abs(value-classic_body_pattern(seed,u,v+1e-6)) < .002


def test_classic_seed_changes_whole_flank_organisation_not_only_colour():
    masks = []
    for identity in (0,42,122,551):
        seed = _pattern_seed(identity)
        masks.append([classic_body_pattern(seed,x/60,y/40) > .5
                      for x in range(61) for y in range(41)])
    distances = [sum(a != b for a,b in zip(mask,other))/len(mask)
                 for i,mask in enumerate(masks) for other in masks[i+1:]]
    assert min(distances) > .12
    assert sum(distances)/len(distances) > .20
