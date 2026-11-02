"""Calico pigment balance is evaluated after white covers the coat."""
from dataclasses import replace
import pytest
from backyard_visitor.coat_traits import CoatField,CoatSpec,_CANONICAL_GRID

SEEDS=(0,42,122,551,222)


@pytest.mark.parametrize('seed',SEEDS)
@pytest.mark.parametrize('coverage',(.2,.4,.85))
def test_calico_retains_both_pigments_on_each_visible_side(seed,coverage):
    spec=CoatSpec(seed,pigment='tortie',white_mode='spotting',white_coverage=coverage)
    field=CoatField(spec)
    # Pattern/dilution cannot move the joint three-colour layout. White may
    # now frame complete calico lobes, rather than copying a bicolour mask.
    reference_white=CoatField(replace(spec,dilute=True,dark_pattern='classic',orange_pattern='ticked'))
    white_area=0.
    for side in ('left','right'):
        visible=orange=0.
        for part,u,v,item_side,weight in _CANONICAL_GRID:
            if item_side!=side:continue
            sample=field.sample(part,u,v,side)
            assert sample.white==reference_white.sample(part,u,v,side).white
            white_area+=weight*sample.white
            area=weight*(1-sample.white)
            visible+=area;orange+=area*sample.orange
        # Complete lobes take priority over forcing exact numerical halves;
        # both pigments still need a substantial visible area.
        assert .30<orange/visible<.70
    assert white_area==pytest.approx(coverage,abs=.015)


@pytest.mark.parametrize('seed',SEEDS)
def test_calico_torso_cannot_trade_all_its_orange_for_tiny_head_or_tail_patches(seed):
    field=CoatField(CoatSpec(seed,pigment='tortie',white_mode='spotting',white_coverage=.4))
    for side in ('left','right'):
        visible=orange=0.
        for part,u,v,item_side,weight in _CANONICAL_GRID:
            if item_side!=side or part!='body':continue
            sample=field.sample(part,u,v,side)
            area=weight*(1-sample.white)
            visible+=area;orange+=area*sample.orange
        assert .35<orange/visible<.65


@pytest.mark.parametrize('seed',SEEDS)
def test_calico_edges_remain_continuous_and_do_not_reroll_when_traits_are_restored(seed):
    spec=CoatSpec(seed,pigment='tortie',white_mode='spotting',white_coverage=.4)
    field=CoatField(spec);restored=CoatField(CoatSpec.from_dict(spec.to_dict()))
    assert spec.version==spec.map_version==1
    for u,v in ((.1,.12),(.3,.25),(.5,.5),(.8,.3)):
        a=field.sample('body',u,v)
        assert a==restored.sample('body',u,v)
        b=field.sample('body',u+1e-5,v)
        assert abs(a.orange-b.orange)<.01


def _components(points):
    points=set(points);groups=[]
    while points:
        start=points.pop();group={start};todo=[start]
        while todo:
            x,y=todo.pop()
            for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if point in points:
                    points.remove(point);group.add(point);todo.append(point)
        groups.append(group)
    return groups


@pytest.mark.parametrize('seed',SEEDS)
def test_complete_calico_lobes_do_not_contain_a_thin_reverse_colour_slice(seed):
    field=CoatField(CoatSpec(seed,pigment='tortie',white_mode='spotting',white_coverage=.4))
    for side in ('left','right'):
        samples={(x,y):field.sample('body',x/80,y/64,side)
                 for y in range(65) for x in range(81)}
        groups=_components(point for point,value in samples.items() if value.white<.5)
        assert 1<=len(groups)<=3
        for group in groups:
            assert len(group)>100  # a coherent lobe, not a confetti fragment
            orange=sum(samples[point].orange>.5 for point in group)
            minority=min(orange,len(group)-orange)/len(group)
            # Separate lobes are one colour. When two lobes touch, both
            # colours remain broad; a small contrasting wedge is a regression.
            assert minority<.01 or minority>.20


@pytest.mark.parametrize('pigment',('dark','orange','tortie'))
@pytest.mark.parametrize('coverage',(.2,.4,.65,.85))
def test_rounded_white_interspaces_keep_requested_area_and_are_not_one_belly_cut(pigment,coverage):
    field=CoatField(CoatSpec(222,pigment=pigment,white_mode='spotting',white_coverage=coverage))
    area=sum(field.sample(part,u,v,side).white*weight for part,u,v,side,weight in _CANONICAL_GRID)
    assert area==pytest.approx(coverage,abs=.015)
    if coverage in (.4,.65):
        # At the same height the flank contains white bays and coloured
        # regions, instead of an entire row erased by a belly-height cutoff.
        rows=[[field.sample('body',x/80,v).white for x in range(81)]
              for v in (.25,.50,.70)]
        assert sum(max(row)-min(row)>.8 for row in rows)>=2



@pytest.mark.parametrize('seed',SEEDS)
def test_calico_upper_legs_inherit_one_root_colour_and_white_only_grows_towards_sock(seed):
    field=CoatField(CoatSpec(seed,pigment='tortie',white_mode='spotting',white_coverage=.4))
    coloured_roots=0
    for side in ('left','right'):
        for part in ('front_leg','hind_leg'):
            root=field.sample(part,0.,.5,side)
            coloured_roots+=root.white<.25
            for v in (.1,.3,.5,.7,.9):
                values=[field.sample(part,u/40,v,side) for u in range(41)]
                assert all(value.orange==pytest.approx(root.orange,abs=1e-12) for value in values)
                assert all(b.white>=a.white-1e-12 for a,b in zip(values,values[1:]))
                assert all(value.white==pytest.approx(root.white,abs=1e-12) for value in values[:13])
                assert values[-1].white>.95
    # This repair must not conceal the joint problem by turning all legs white.
    assert coloured_roots>=2


@pytest.mark.parametrize('seed',SEEDS)
def test_calico_large_patches_wrap_around_body_instead_of_inverting_near_and_far_legs(seed):
    field=CoatField(CoatSpec(seed,pigment='tortie',white_mode='spotting',white_coverage=.4))
    masks=[]
    for side in ('left','right'):
        values=[field.sample('body',x/60,y/40,side) for y in range(41) for x in range(61)]
        masks.append([2 if value.white>.5 else int(value.orange>.5) for value in values])
    assert sum(a!=b for a,b in zip(*masks))/len(masks[0])<.12


@pytest.mark.parametrize('pigment',('dark','orange'))
def test_bicolour_upper_leg_white_cannot_leave_a_detached_dark_island(pigment):
    field=CoatField(CoatSpec(551,pigment=pigment,white_mode='spotting',white_coverage=.4))
    for side in ('left','right'):
        for part in ('front_leg','hind_leg'):
            for v in (.2,.5,.8):
                values=[field.sample(part,u/40,v,side).white for u in range(41)]
                assert all(b>=a-1e-12 for a,b in zip(values,values[1:]))
