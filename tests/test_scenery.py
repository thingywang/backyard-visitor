import json
from pathlib import Path
import struct
import zlib

import pytest

from backyard_visitor.scenery import load_scenes



def write_png(path, width, height):
    def chunk(kind, data):
        return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data))
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    pixels = zlib.compress((b'\0'+b'\x80\x90\x70'*width)*height)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', header)+chunk(b'IDAT', pixels)+chunk(b'IEND', b''))

def make_scene(root, **updates):
    width, height = (6, 10) if updates.get('orientation') == 'portrait' else (10, 6)
    write_png(root / 'backyard-test.png', width, height)
    item = {'id': 'backyard-test', 'title': '测试后院', 'file': 'backyard-test.png', 'fit': 'stretch',
            'occluders': [{'polygon': [[0, 0], [0.5, 0], [0, 0.5]]}]}
    item.update(updates)
    (root / 'backyard-test.json').write_text(json.dumps(item))


def test_scene_coordinates_are_normalized_and_bounded(tmp_path):
    make_scene(tmp_path, occluders=[{'polygon': [[0, 0], [1, 0], [0, 1]]},
        {'polygon': [[0, 0], [float('nan'), 0], [0, 1]]},
        {'polygon': [[0, 0], [2, 0], [0, 1]]}, {'polygon': [[True, 0], [1, 0], [0, 1]]}])
    scenes = load_scenes(tmp_path)
    assert len(scenes['backyard-test']['occluders']) == 1


def test_catalog_rejects_missing_images_and_external_paths(tmp_path):
    make_scene(tmp_path, file='../somewhere.png')
    assert load_scenes(tmp_path) == {}
    make_scene(tmp_path)
    (tmp_path / 'backyard-test.png').unlink()
    assert load_scenes(tmp_path) == {}


def test_scene_rejects_unmatched_fit_and_bad_shapes(tmp_path):
    make_scene(tmp_path, fit='aspect_fill')
    assert load_scenes(tmp_path) == {}
    make_scene(tmp_path, occluders=None)
    assert load_scenes(tmp_path) == {}
    make_scene(tmp_path, occluders=[{}, {'polygon': 'bad'}])
    assert load_scenes(tmp_path)['backyard-test']['occluders'] == []


def test_installed_scenes_have_files_and_foreground_regions():
    root = Path(__file__).resolve().parents[1] / 'backyard_visitor/assets'
    scenes = load_scenes(root)
    assert 'backyard-dusk' in scenes
    assert all(scenes[key]['occluders'] for key in ('backyard-dusk', 'backyard-morning'))
    portrait = scenes['backyard-cat-tree-portrait']
    assert portrait['orientation'] == 'portrait' and portrait['occluders'] == []


def test_background_only_portrait_needs_no_mask(tmp_path):
    make_scene(tmp_path, orientation='portrait', occluders=[])
    scene = load_scenes(tmp_path)['backyard-test']
    assert scene['occluders'] == [] and scene['orientation'] == 'portrait'


def test_orientation_is_validated_and_old_scenes_default_to_landscape(tmp_path):
    make_scene(tmp_path)
    assert load_scenes(tmp_path)['backyard-test']['orientation'] == 'landscape'
    for invalid in ('square', [], None, 1):
        make_scene(tmp_path, orientation=invalid)
        assert load_scenes(tmp_path) == {}


def make_pair(root, *, orientation='landscape', geometry=None):
    dimensions = [10, 6] if orientation == 'landscape' else [6, 10]
    pair = {'morning': 'backyard-a', 'dusk': 'backyard-b'}
    for phase, scene_id in pair.items():
        write_png(root/f'{scene_id}.png', *dimensions)
        metadata = {'id': scene_id, 'title': phase, 'file': f'{scene_id}.png',
            'fit': 'stretch', 'canvas': dimensions, 'orientation': orientation,
            'theme_id': 'cat-tree', 'phase': phase, 'layout_id': 'same-label',
            'cycle_pair': pair, 'occluders': []}
        if geometry is not None:
            metadata['layout_geometry'] = geometry
        (root/f'{scene_id}.json').write_text(json.dumps(metadata))
    return pair


def alter(root, scene_id, **updates):
    path = root/f'{scene_id}.json'
    data = json.loads(path.read_text()); data.update(updates)
    path.write_text(json.dumps(data))


def test_actual_png_canvas_is_read_and_wrong_claim_is_rejected(tmp_path):
    make_scene(tmp_path, canvas=[10, 6])
    assert load_scenes(tmp_path)['backyard-test']['canvas'] == [10, 6]
    for invalid in ([100, 60], [True, 6], [10.0, 6], '10x6', None):
        make_scene(tmp_path, canvas=invalid)
        assert load_scenes(tmp_path) == {}
    make_scene(tmp_path)
    (tmp_path/'backyard-test.png').write_bytes(b'not a png')
    assert load_scenes(tmp_path) == {}


def test_orientation_must_match_actual_canvas_not_just_label(tmp_path):
    make_scene(tmp_path, orientation='portrait')
    write_png(tmp_path/'backyard-test.png', 10, 6)
    assert load_scenes(tmp_path) == {}


def test_reciprocal_pair_is_ready_but_empty_background_has_no_geometry_claim(tmp_path):
    from backyard_visitor.scenery import same_layout_geometry
    make_pair(tmp_path)
    scenes = load_scenes(tmp_path)
    assert all(scene['cycle_ready'] and scene['cycle_error'] is None for scene in scenes.values())
    assert all(not scene['geometry_available'] and scene['geometry_fingerprint'] is None for scene in scenes.values())
    assert not same_layout_geometry(*scenes.values())


@pytest.mark.parametrize('bad', [
    {'theme_id': None}, {'theme_id': '../theme'}, {'layout_id': True},
    {'phase': 'day'}, {'cycle_pair': {'morning': 'backyard-a', 'dusk': 'backyard-a'}},
    {'cycle_pair': {'morning': 'backyard-a', 'dusk': '../bad'}},
    {'cycle_pair': {'morning': 'backyard-a', 'dusk': 'backyard-c'}},
    {'phase': 'dusk'}, {'theme_id': 'other'},
])
def test_bad_or_mismatched_metadata_remains_manual_but_cannot_cycle(tmp_path, bad):
    make_pair(tmp_path)
    alter(tmp_path, 'backyard-a', **bad)
    scenes = load_scenes(tmp_path)
    assert set(scenes) == {'backyard-a', 'backyard-b'}
    assert not any(scene['cycle_ready'] for scene in scenes.values())


def test_missing_partner_does_not_disable_valid_manual_image(tmp_path):
    make_pair(tmp_path)
    (tmp_path/'backyard-b.png').unlink()
    scenes = load_scenes(tmp_path)
    assert set(scenes) == {'backyard-a'} and not scenes['backyard-a']['cycle_ready']


def test_same_id_never_substitutes_for_real_geometry_fingerprint(tmp_path):
    from backyard_visitor.scenery import same_layout_geometry
    geometry = {'supports': [{'surface_id': 'platform', 'left': .2, 'right': .7, 'y': .5}],
                'anchors': {'rest': [.4, .5]}}
    make_pair(tmp_path, geometry=geometry)
    scenes = load_scenes(tmp_path)
    assert same_layout_geometry(scenes['backyard-a'], scenes['backyard-b'])
    geometry['anchors']['rest'][0] = .6
    alter(tmp_path, 'backyard-b', layout_geometry=geometry,
          geometry_fingerprint=scenes['backyard-a']['geometry_fingerprint'])
    scenes = load_scenes(tmp_path)
    assert not same_layout_geometry(scenes['backyard-a'], scenes['backyard-b'])
    assert scenes['backyard-a']['cycle_ready'] and scenes['backyard-b']['cycle_ready']


def test_different_classic_layouts_can_pair_without_claiming_shared_geometry(tmp_path):
    from backyard_visitor.scenery import same_layout_geometry
    make_pair(tmp_path)
    for scene_id in ('backyard-a', 'backyard-b'):
        alter(tmp_path, scene_id, theme_id='classic', layout_id=scene_id,
              occluders=[{'polygon': [[0, 0], [.3, 0], [0, .5]]}])
    scenes = load_scenes(tmp_path)
    assert all(scene['cycle_ready'] for scene in scenes.values())
    assert not same_layout_geometry(*scenes.values())


@pytest.mark.parametrize('geometry', [
    [], {'supports': None}, {'anchors': []},
    {'anchors': {'rest': [float('nan'), .5]}},
    {'supports': [{'surface_id': 'top', 'left': .8, 'right': .2, 'y': .5}]},
    {'supports': [{'surface_id': 'top', 'left': False, 'right': .8, 'y': .5}]},
])
def test_bad_support_or_anchor_data_cannot_be_published(tmp_path, geometry):
    make_pair(tmp_path, geometry=geometry)
    assert load_scenes(tmp_path) == {}
