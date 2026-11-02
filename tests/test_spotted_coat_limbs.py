"""Spotted thighs must not inherit continuous horizontal tabby cuffs."""
import pytest
from backyard_visitor.spotted_coat import spotted_limb_pattern
from backyard_visitor.coat_traits import CoatSpec, CoatField


@pytest.mark.parametrize('seed',[0,42,122,551,222])
def test_spotted_limb_has_visible_but_separate_marks_across_the_thigh(seed):
    rows=[[spotted_limb_pattern(seed,u/60,v/80) for v in range(81)] for u in range(61)]
    values=[v for row in rows for v in row]
    assert min(values)==0 and max(values)>.95
    assert .08 < sum(values)/len(values) < .45
    # A leg ring fills most/all transverse samples at one u. Short spots
    # must leave clear fur between neighbouring marks, including the root.
    assert max(sum(v>.5 for v in row)/len(row) for row in rows) < .78


def test_spotted_limb_is_fixed_by_seed_and_smooth_at_subpixel_steps():
    points=[(u/29,v/31) for u in range(30) for v in range(32)]
    first=[spotted_limb_pattern(122,*point) for point in points]
    assert first==[spotted_limb_pattern(122,*point) for point in points]
    assert first!=[spotted_limb_pattern(551,*point) for point in points]
    assert max(abs(spotted_limb_pattern(122,u+1e-5,v)-value)
               for (u,v),value in zip(points,first)) < .003


@pytest.mark.parametrize('part',['front_leg','hind_leg'])
def test_production_spotted_legs_use_spots_instead_of_generic_ring_fallback(part):
    field=CoatField(CoatSpec(122,dark_pattern='spotted'))
    rows=[[field.sample(part,u/40,v/60).dark_tabby for v in range(61)] for u in range(41)]
    assert max(sum(value>.5 for value in row)/len(row) for row in rows)<.8
    assert any(max(row)>.9 for row in rows)
