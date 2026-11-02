"""Versioned appearance data and anatomical fields stay stable across poses/load."""
from dataclasses import FrozenInstanceError, replace
import json
import math
import os
import random
import subprocess
import sys

import pytest

from backyard_visitor.coat_traits import (
    COAT_VERSION, COAT_MAP_VERSION, MAX_SEED, PARTS, PATTERNS, SIDES,
    CoatField, CoatSample, CoatSpec, coat_field, derive_seed, describe_coat,
    field, generate_coat, _CANONICAL_GRID,
)


@pytest.mark.parametrize('seed', [0, 1, 123, MAX_SEED])
def test_complete_data_roundtrip_is_frozen_hashable_and_detached(seed):
    original = generate_coat(seed)
    data = original.to_dict()
    loaded = CoatSpec.from_dict(json.loads(json.dumps(data)))
    assert loaded == original and hash(loaded) == hash(original)
    assert loaded.to_dict() == data
    assert loaded.version == COAT_VERSION == 1
    assert loaded.map_version == COAT_MAP_VERSION == 1
    data['seed'] = 789
    assert original.seed == seed
    with pytest.raises(FrozenInstanceError):
        original.seed = 888


@pytest.mark.parametrize('seed', [True, False, -1, 2**32, 1.0, '7', None, float('nan')])
def test_invalid_seed_is_rejected_at_every_public_generation_boundary(seed):
    for action in (lambda: CoatSpec(seed), lambda: generate_coat(seed),
                   lambda: derive_seed(seed, 'layout/white')):
        with pytest.raises(ValueError):
            action()


@pytest.mark.parametrize('key,value', [
    ('version', True), ('version', 2), ('map_version', 0), ('map_version', '1'),
    ('pigment', 'calico'), ('pigment', []), ('dilute', 1), ('dilute', 'false'),
    ('dark_pattern', 'striped'), ('orange_pattern', None), ('white_mode', 'cream'),
    ('white_coverage', True), ('white_coverage', -0.01), ('white_coverage', 1.01),
    ('white_coverage', float('inf')), ('white_coverage', float('nan')),
    ('white_coverage', 10**400), ('white_coverage', -(10**400)),
])
def test_json_validation_never_silently_reinterprets_invalid_fields(key, value):
    data = CoatSpec(7).to_dict()
    data[key] = value
    with pytest.raises(ValueError):
        CoatSpec.from_dict(data)


@pytest.mark.parametrize('value', [None, [], {}, {'seed': 7}])
def test_incomplete_or_wrong_json_shape_rejected(value):
    with pytest.raises(ValueError):
        CoatSpec.from_dict(value)


def test_unknown_future_traits_require_explicit_migration():
    data = CoatSpec(7).to_dict()
    data['future_trait'] = 'unknown'
    with pytest.raises(ValueError):
        CoatSpec.from_dict(data)


@pytest.mark.parametrize('mode,coverage', [('none', .1), ('all-white', .99),
                                          ('spotting', 0), ('spotting', 1)])
def test_white_mode_is_explicit_and_consistent(mode, coverage):
    with pytest.raises(ValueError):
        CoatSpec(4, white_mode=mode, white_coverage=coverage)


def test_version_one_golden_traits_and_named_substreams():
    assert generate_coat(123).to_dict() == dict(
        version=1, map_version=1, seed=123, pigment='tortie', dilute=False,
        dark_pattern='mackerel', orange_pattern='classic', white_mode='spotting',
        white_coverage=.65024)
    assert derive_seed(0, 'layout/white/body/left') == 109051938
    assert derive_seed(MAX_SEED, 'layout/white/body/left') == 1381574050
    before = generate_coat(0)
    random.seed(9481)
    for index in range(100):
        random.random()
        derive_seed(0, f'future-unrelated-channel/{index}')
    assert generate_coat(0) == before
    names = ('layout/white/body/left', 'layout/orange/body/left',
             'layout/pattern/body/left', 'layout/white/body/right')
    assert len({derive_seed(3, name) for name in names}) == len(names)


def test_layout_does_not_use_python_process_hash_randomization():
    source = '''import json
from backyard_visitor.coat_traits import CoatSpec, field, generate_coat
s=CoatSpec(73,pigment="tortie",white_mode="spotting",white_coverage=.45,dark_pattern="classic")
print(json.dumps([generate_coat(73).to_dict(),list(field(s,"body",.31,.66))],sort_keys=True))
'''
    outputs = []
    for hash_seed in ('13', '991'):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE='1')
        outputs.append(subprocess.check_output([sys.executable, '-c', source], env=env, text=True))
    assert outputs[0] == outputs[1]


def test_generation_exposes_all_supported_appearance_combinations_without_population_claims():
    specs = [generate_coat(seed) for seed in range(1200)]
    assert {s.pigment for s in specs} == {'dark', 'orange', 'tortie'}
    assert {s.dark_pattern for s in specs} == set(PATTERNS)
    assert {s.orange_pattern for s in specs} == set(PATTERNS) - {'solid'}
    assert {s.white_mode for s in specs} == {'none', 'spotting', 'all-white'}
    assert {s.dilute for s in specs} == {False, True}
    for pigment in ('dark', 'orange', 'tortie'):
        assert any(s.pigment == pigment and s.white_mode == 'spotting' for s in specs)
        assert any(s.pigment == pigment and s.white_mode == 'none' for s in specs)


@pytest.mark.parametrize('spec,label', [
    (CoatSpec(1), '纯黑'), (CoatSpec(1, dilute=True), '纯蓝灰'),
    (CoatSpec(1, white_mode='all-white', white_coverage=1), '纯白'),
    (CoatSpec(1, white_mode='spotting', white_coverage=.45), '黑白'),
    (CoatSpec(1, pigment='orange', white_mode='spotting', white_coverage=.45), '橘白'),
    (CoatSpec(1, pigment='tortie'), '玳瑁'),
    (CoatSpec(1, pigment='tortie', white_mode='spotting', white_coverage=.45), '三花'),
    (CoatSpec(1, pigment='tortie', dilute=True, white_mode='spotting', white_coverage=.45), '淡三花'),
])
def test_descriptions_name_appearance_and_handle_pure_colours(spec, label):
    assert describe_coat(spec).startswith(label)


@pytest.mark.parametrize('pattern', PATTERNS)
def test_all_parts_sides_and_domain_edges_return_finite_masks(pattern):
    spec = CoatSpec(33, pigment='tortie', dark_pattern=pattern, orange_pattern=pattern,
                    white_mode='spotting', white_coverage=.45)
    f = CoatField(spec)
    for part in PARTS:
        for side in SIDES:
            for u in (0., .23, .54, 1.):
                for v in (0., .53, .85, 1.):
                    sample = f.sample(part, u, v, side)
                    assert isinstance(sample, CoatSample)
                    assert all(math.isfinite(value) and 0 <= value <= 1 for value in sample)


@pytest.mark.parametrize('args', [('unknown', .5, .5, 'left'), ('body', .5, .5, 'front'),
                                 ('body', -.01, .5, 'left'), ('body', .5, 1.1, 'left'),
                                 ('body', True, .5, 'left'), ('body', .5, float('nan'), 'left'),
                                 ('body', 10**400, .5, 'left')])
def test_canonical_input_errors_do_not_leak_invalid_masks(args):
    with pytest.raises(ValueError):
        CoatField(CoatSpec(0)).sample(*args)


def test_no_pattern_pure_black_has_no_pattern_mosaic_or_white_on_any_region():
    f = CoatField(CoatSpec(88, dark_pattern='solid', orange_pattern='solid'))
    assert all(f.sample(part, u, v, side) == (0, 0, 0, 0)
               for part, u, v, side, weight in _CANONICAL_GRID[::17])
    white = CoatField(CoatSpec(88, white_mode='all-white', white_coverage=1))
    assert all(white.sample(part, .5, .5).white == 1 for part in PARTS)
    orange = CoatField(CoatSpec(88, pigment='orange'))
    assert all(orange.sample(part, .5, .5).orange == 1 for part in PARTS)


@pytest.mark.parametrize('seed', [0, 42, MAX_SEED])
@pytest.mark.parametrize('coverage', [.01, .15, .45, .80, .99])
def test_white_area_calibrates_in_shared_anatomical_domain(seed, coverage):
    f = CoatField(CoatSpec(seed, white_mode='spotting', white_coverage=coverage))
    actual = sum(f.sample(part, u, v, side).white * weight
                 for part, u, v, side, weight in _CANONICAL_GRID)
    assert actual == pytest.approx(coverage, abs=.015)


def test_body_areas_can_differ_without_rethresholding_each_pose():
    f = CoatField(CoatSpec(42, white_mode='spotting', white_coverage=.15))
    threshold = f._white_threshold
    paws = [f.sample(part, u, v, side).white for part in ('front_leg', 'hind_leg')
            for u in (.9, .95, 1) for v in (.2, .5, .8) for side in SIDES]
    back = [f.sample('body', u, .05, side).white for u in (.1, .3, .5, .7, .9) for side in SIDES]
    assert sum(paws) / len(paws) > sum(back) / len(back) + .5
    a = f.sample('front_leg', .93, .55, 'left')
    f.sample_many([('body', x / 100, .9) for x in range(101)])
    assert f._white_threshold == threshold
    assert f.sample('front_leg', .93, .55, 'left') == a


def test_sample_order_pose_subset_and_persistence_cannot_move_markings():
    spec = CoatSpec(777, pigment='tortie', dark_pattern='spotted',
                    white_mode='spotting', white_coverage=.45)
    points = [('body', .27, .62, 'right'), ('front_leg', .95, .5, 'left'),
              ('head', .2, .8, 'left'), ('tail', .7, .3, 'right')]
    first = CoatField(spec)
    expected = first.sample_many(points)
    assert first.sample_many(reversed(points)) == list(reversed(expected))
    restored = CoatField(CoatSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
    assert restored.sample_many(points) == expected
    assert [field(spec, part, u, v, side=side) for part, u, v, side in points] == expected
    assert coat_field(spec) is coat_field(replace(spec))
    with pytest.raises(AttributeError):
        first.spec = CoatSpec(3)


def test_independent_pattern_channels_and_dilution_do_not_move_orange_or_white_blocks():
    spec = CoatSpec(31, pigment='tortie', white_mode='spotting', white_coverage=.45)
    a = CoatField(spec)
    b = CoatField(replace(spec, dilute=True, dark_pattern='classic', orange_pattern='ticked'))
    for part, u, v, side, _ in _CANONICAL_GRID[::73]:
        assert a.sample(part, u, v, side)[2:] == b.sample(part, u, v, side)[2:]


def test_five_patterns_have_different_fields_and_ticked_has_no_high_contrast_body_bars():
    values = {}
    for pattern in PATTERNS:
        f = CoatField(CoatSpec(8, dark_pattern=pattern))
        values[pattern] = tuple(round(f.sample('body', x / 30, y / 20).dark_tabby, 6)
                                for y in range(21) for x in range(31))
    assert len(set(values.values())) == 5
    assert set(values['solid']) == {0}
    assert .09 <= min(values['ticked']) < max(values['ticked']) <= .27
    for pattern in ('mackerel', 'spotted', 'classic'):
        assert min(values[pattern]) < .05 and max(values[pattern]) > .9


def test_continuous_tortie_and_white_blocks_have_soft_spatial_boundaries():
    f = CoatField(CoatSpec(49, pigment='tortie', dark_pattern='classic',
                           white_mode='spotting', white_coverage=.45))
    blocks = [f.sample('body', x / 40, y / 30) for y in range(31) for x in range(41)]
    assert any(s.orange < .05 for s in blocks) and any(s.orange > .95 for s in blocks)
    assert any(.1 < s.orange < .9 for s in blocks)
    for u, v in ((.2, .4), (.54, .53), (.7, .8)):
        left, right = f.sample('body', u - 1e-5, v), f.sample('body', u + 1e-5, v)
        assert all(abs(a - b) < .01 for a, b in zip(left, right))


def test_unreleased_version_one_visual_refinement_has_fixed_layout_samples():
    # User-requested pre-release correction: keep identity/trait JSON v1 while
    # replacing broken/noisy contours and the later G/leaf badge rejected by
    # the user after real-cat photo review. These lock the corrected field;
    # historical pre-correction values remain in Git and the output checkpoint.
    # Latest revision couples white with complete pigment lobes and separates
    # profile facial strokes from the torso field; it retains trait identity.
    # Calico redesign shares whole-body lobes and calibrates the actual smooth
    # white area after attached limb roots replace independent leg patches.
    spec = CoatSpec(49, pigment='tortie', dark_pattern='classic',
                    white_mode='spotting', white_coverage=.45)
    f = CoatField(spec)
    assert f._orange_threshold == pytest.approx(.5, abs=1e-12)
    assert f._white_threshold == pytest.approx(.7887666704509824, abs=1e-12)
    assert f.sample('body', .54, .53, 'right') == pytest.approx(
        (1., 0., .10438873313978457, 1.), abs=1e-12)
    assert f.sample('head', .2, .8, 'left') == pytest.approx(
        (0., 0., 1., 1.), abs=1e-12)
    assert f.sample('tail', .7, .3, 'right') == pytest.approx(
        (0., 0., 0., 0.), abs=1e-12)



def test_mackerel_flank_bands_vary_in_width_spacing_and_curve_towards_belly():
    # Unreleased v1 refinement: avoid equal-width ruler lines at every height.
    f = CoatField(CoatSpec(8, dark_pattern='mackerel'))
    rows = [[f.sample('body', i / 400, v).dark_tabby for i in range(401)]
            for v in (.2, .5, .8)]
    runs, start = [], None
    for i, value in enumerate(rows[0] + [0]):
        if value > .5 and start is None:
            start = i
        elif value <= .5 and start is not None:
            runs.append((start, i - 1))
            start = None
    interior = [(a, b) for a, b in runs if a > 0 and b < 400]
    widths = [b - a + 1 for a, b in interior]
    centres = [(a + b) / 2 for a, b in interior]
    gaps = [b - a for a, b in zip(centres, centres[1:])]
    assert len(interior) >= 5
    assert max(widths) > min(widths) * 1.6
    assert max(gaps) > min(gaps) * 1.3
    assert sum(abs(a - b) for a, b in zip(rows[0], rows[1])) / 401 > .20
    assert sum(rows[2]) < sum(rows[1]) * .75


@pytest.mark.parametrize("seed", [0, 42, 122, 551])
def test_tortie_and_calico_keep_both_pigments_visible_after_white_spotting(seed):
    for coverage in (0., .4):
        f = CoatField(CoatSpec(seed, pigment="tortie",
                              white_mode="spotting" if coverage else "none",
                              white_coverage=coverage))
        visible = orange = 0.
        for part,u,v,side,weight in _CANONICAL_GRID:
            sample = f.sample(part,u,v,side)
            weight *= 1 - sample.white
            visible += weight
            orange += sample.orange * weight
        # Hidden orange must not count toward the requested calico balance.
        # No-white torties preserve their previous, wider pigment variation.
        low, high = (.40, .60) if coverage else (.34, .66)
        assert low < orange / visible < high
