"""Face markings remain local to anatomy rather than repeating torso bands."""
import math
import pytest
from backyard_visitor.tabby_face import (tabby_side_face, tabby_frontal_face,
                                        frontal_lateral_coordinate)


def encoded(q,v):
    ly=15+48*v
    return .62*q+.10*max(0,43-ly)/29+.035*math.sin((ly-25)/12)


@pytest.mark.parametrize('seed',[0,42,122,551,4294967295])
def test_profile_has_separate_crown_and_cheek_marks_but_a_clear_muzzle(seed):
    values=[tabby_side_face(seed,x/80,y/80) for y in range(81) for x in range(81)]
    assert min(values)>=0 and max(values)<=1
    crown=[tabby_side_face(seed,x/80,y/80) for x in range(20,78) for y in range(0,29)]
    cheek=[tabby_side_face(seed,x/80,y/80) for x in range(28,74) for y in range(35,64)]
    assert max(crown)>.65 and max(cheek)>.60
    # A mouth/chin must not inherit stripes just because the UV axis slopes.
    assert max(tabby_side_face(seed,u/40,v/40) for u in range(0,10) for v in range(22,41))<.03
    assert max(tabby_side_face(seed,u/40,.97) for u in range(41))==0
    # Even a marked longitude cannot turn into a bar across the whole head.
    assert max(sum(tabby_side_face(seed,u/40,v/80)>.2 for v in range(81)) for u in range(41))<48


@pytest.mark.parametrize('seed',[0,42,122,551])
def test_portrait_forehead_and_cheeks_have_marks_without_a_nose_to_chin_bar(seed):
    forehead=[tabby_frontal_face(seed,encoded(q/30,v/40),v/40)
              for q in range(0,22) for v in range(8,22)]
    cheeks=[tabby_frontal_face(seed,encoded(q/30,v/40),v/40)
            for q in range(17,29) for v in range(23,34)]
    assert max(forehead)>.7 and max(cheeks)>.6
    muzzle=[tabby_frontal_face(seed,encoded(q/30,v/40),v/40)
            for q in range(0,10) for v in range(26,39)]
    assert max(muzzle)<.03
    # Facial strokes stop well before the chin, rather than repeating forever.
    assert max(tabby_frontal_face(seed,encoded(q/30,.99),.99) for q in range(31))==0


def test_portrait_mapping_recovers_lateral_anatomy_across_head_heights():
    for q in (0.,.2,.5,.9):
        for v in (.2,.4,.6,.8):
            assert frontal_lateral_coordinate(encoded(q,v),v)==pytest.approx(q)


@pytest.mark.parametrize('fn',[tabby_side_face,tabby_frontal_face])
def test_face_pattern_is_deterministic_seeded_and_continuous(fn):
    points=[(u/24,v/24) for v in range(25) for u in range(25)]
    first=[fn(122,*point) for point in points]
    assert first==[fn(122,*point) for point in points]
    assert first!=[fn(551,*point) for point in points]
    assert max(abs(fn(122,u+1e-6,v)-a) for (u,v),a in zip(points,first))<.001
