"""Authored tree geometry fits real boards at one fixed excursion scale."""
from copy import deepcopy
from pathlib import Path

import pytest

from backyard_visitor.scenery import load_scenes, same_layout_geometry
from backyard_visitor.tree_geometry import build_tree_layout, TREE_SCALE

ASSETS = Path(__file__).resolve().parents[1]/'backyard_visitor/assets'


def scene(orientation='portrait', phase='morning'):
    scenes = load_scenes(ASSETS)
    return next(value for value in scenes.values() if value.get('theme_id') == 'cat-tree'
                and value['orientation'] == orientation and value['phase'] == phase)


@pytest.mark.parametrize('orientation,screen', [
    ('portrait', (0, 0, 720, 1280)), ('portrait', (-1080, -120, 1080, 1920)),
    ('landscape', (0, 0, 1280, 800)), ('landscape', (1600, -100, 1920, 1080)),
    ('landscape', (0, 0, 2560, 1440)),
])
@pytest.mark.parametrize('phase', ['morning', 'dusk'])
def test_all_authored_platforms_fit_supported_logical_displays(orientation, screen, phase):
    layout = build_tree_layout(scene(orientation, phase), screen)
    assert layout is not None and layout.scale == TREE_SCALE
    assert len(layout.supports) == 4
    assert layout.base_id == 'tree-base' and layout.bed_id == 'tree-bed'
    sx, sy, sw, sh = screen
    for surface in layout.supports:
        low, high = surface.foot_range()
        assert low <= high and surface.supports_foot(surface.center)
        assert sx <= low-72*TREE_SCALE and high+72*TREE_SCALE <= sx+sw
        assert sy <= surface.y-108*TREE_SCALE
        assert surface.y+4*TREE_SCALE <= sy+sh
    assert layout.by_id[layout.base_id].supports_foot(layout.entry)
    assert layout.by_id[layout.scratch_support_id].supports_foot(layout.scratch_anchor)
    assert layout.scratch_facing == 1
    assert layout.scratch_contact == pytest.approx((layout.scratch_anchor[0]+6.5*TREE_SCALE,
                                                   layout.scratch_anchor[1]-73*TREE_SCALE))
    assert layout.approach[0] < layout.entry[0] and layout.approach[1] > layout.entry[1]


@pytest.mark.parametrize('orientation,screen', [('portrait', (0, 0, 720, 1280)),
                                               ('landscape', (0, 0, 1440, 900))])
def test_paired_lighting_has_same_reviewed_supports_and_no_fake_plant_mask(orientation, screen):
    day, night = scene(orientation), scene(orientation, 'dusk')
    assert day['occluders'] == night['occluders'] == []
    assert same_layout_geometry(day, night)
    first, second = build_tree_layout(day, screen), build_tree_layout(night, screen)
    assert first.supports == second.supports and first.entry == second.entry
    assert first.fingerprint == second.fingerprint
    altered = deepcopy(night)
    altered['layout_geometry']['anchors']['tree-scratch-post'][0] += .001
    assert build_tree_layout(altered, screen).fingerprint != first.fingerprint


def test_screen_origin_translates_every_support_and_anchor_without_changing_geometry_identity():
    value = scene('landscape')
    original = build_tree_layout(value, (0, 0, 1440, 900))
    shifted = build_tree_layout(value, (-1440, 100, 1440, 900))
    assert original.fingerprint == shifted.fingerprint
    for before, after in zip(original.supports, shifted.supports):
        assert after.left == pytest.approx(before.left-1440)
        assert after.right == pytest.approx(before.right-1440)
        assert after.y == pytest.approx(before.y+100)
    for name in ('entry', 'approach', 'scratch_anchor', 'scratch_contact'):
        old, new = getattr(original, name), getattr(shifted, name)
        assert new == pytest.approx((old[0]-1440, old[1]+100))


@pytest.mark.parametrize('bad', [None, (), (0, 0, 720), (0, 0, 0, 1280), (0, 0, -720, 1280),
                               (False, 0, 720, 1280), (0, 0, float('nan'), 1280),
                               (0, 0, 10**10000, 1280), (0, 0, 1280, 720)])
def test_invalid_display_or_wrong_orientation_never_makes_supports(bad):
    assert build_tree_layout(scene(), bad) is None


@pytest.mark.parametrize('orientation,screen', [('portrait', (0, 0, 360, 640)),
                                               ('portrait', (0, 0, 720, 4000)),
                                               ('landscape', (0, 0, 640, 480)),
                                               ('landscape', (0, 0, 8000, 2000))])
def test_unfit_boards_or_unreachable_route_disable_instead_of_shrinking(orientation, screen):
    assert build_tree_layout(scene(orientation), screen) is None


@pytest.mark.parametrize('kind', ['missing-support', 'duplicate-support', 'unknown-support',
                                  'reversed-board', 'nan-board', 'bool-board', 'missing-anchor',
                                  'entry-off-board', 'scratch-off-board', 'approach-behind-base',
                                  'same-height-disjoint', 'unsupported-theme'])
def test_bad_authored_geometry_is_not_partially_enabled(kind):
    value = deepcopy(scene()); g = value['layout_geometry']
    if kind == 'missing-support': g['supports'].pop()
    elif kind == 'duplicate-support': g['supports'].append(deepcopy(g['supports'][0]))
    elif kind == 'unknown-support': g['supports'][0]['surface_id'] = 'window-pretend'
    elif kind == 'reversed-board': g['supports'][0]['left'] = .99
    elif kind == 'nan-board': g['supports'][0]['y'] = float('nan')
    elif kind == 'bool-board': g['supports'][0]['left'] = False
    elif kind == 'missing-anchor': del g['anchors']['tree-approach']
    elif kind == 'entry-off-board': g['anchors']['tree-entry'][0] = .05
    elif kind == 'scratch-off-board': g['anchors']['tree-scratch-post'][0] = .95
    elif kind == 'approach-behind-base': g['anchors']['tree-approach'][1] = .1
    elif kind == 'same-height-disjoint':
        by_id = {r['surface_id']:r for r in g['supports']}
        by_id['tree-step']['y'] = by_id['tree-roof']['y']-.001
    elif kind == 'unsupported-theme': value['theme_id'] = 'classic'
    assert build_tree_layout(value, (0, 0, 720, 1280)) is None


def test_classic_wallpapers_do_not_acquire_tree_supports():
    for key, value in load_scenes(ASSETS).items():
        if value.get('theme_id') != 'cat-tree':
            assert build_tree_layout(value, (0, 0, 1440, 900)) is None
