"""Successful pose replacement retires old pixels without changing visible frames."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from itertools import permutations
from types import SimpleNamespace
import importlib.util
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt


@pytest.fixture(scope='module', autouse=True)
def qt():
    yield QApplication.instance() or QApplication([])


def live_bytes(art):
    by_key = {frame.cacheKey(): frame for frame in art.frames if not frame.isNull()}
    return sum(frame.width()*frame.height()*frame.depth()//8 for frame in by_key.values())


@pytest.mark.parametrize('order', list(permutations(('_load_motion', '_load_transitions', '_load_activity'))))
def test_successful_replacement_keeps_indices_and_releases_four_old_base_frames(order):
    art = CatArt(prefer_compiled=False)
    untouched = {i: art.frames[i].cacheKey() for i in (3, 4, 6, 7, 8, 9)}
    for method in order:
        getattr(art, method)()
    assert len(art.frames) == 22
    assert [i for i, frame in enumerate(art.frames) if frame.isNull()] == [0, 1, 2, 5]
    assert live_bytes(art) == 18*288*224*4
    assert {i: art.frames[i].cacheKey() for i in untouched} == untouched
    assert all(not art.frames[i].isNull() for indices in art.states.values() for i in indices)
    assert art.cache_limit == 16


@pytest.mark.parametrize('method,module,klass,result', [
    ('_load_motion', 'motion_art', 'MotionArt', []),
    ('_load_transitions', 'transition_art', 'TransitionArt', []),
    ('_load_activity', 'activity_art', 'ActivityArt', {}),
])
def test_absent_or_failed_source_group_keeps_original_fallback(monkeypatch, method, module, klass, result):
    imported = __import__('backyard_visitor.'+module, fromlist=[klass])
    monkeypatch.setattr(getattr(imported, klass), 'load_frames', lambda self: result)
    art = CatArt(prefer_compiled=False)
    states = {state: list(indices) for state, indices in art.states.items()}
    keys = [frame.cacheKey() for frame in art.frames]
    getattr(art, method)()
    assert art.states == states
    assert [frame.cacheKey() for frame in art.frames] == keys
    assert all(not frame.isNull() for frame in art.frames)


def test_other_state_or_posture_reference_keeps_old_pixels():
    art = CatArt(prefer_compiled=False)
    art.states['still_uses_original_walk'] = [0]
    art._posture_groups['still_uses_original_idle'] = [2]
    keys = {i: art.frames[i].cacheKey() for i in (0, 2)}
    art._load_motion(); art._load_transitions()
    assert {i: art.frames[i].cacheKey() for i in keys} == keys
    assert art.frames[1].isNull()


def test_retirement_drops_all_stale_variants_without_disturbing_live_lru_order():
    art = CatArt(prefer_compiled=False)
    for index in (0, 1, 3):
        for coat in ('ginger', 'gray'):
            for facing in (-1, 1):
                art.cache[index, facing, coat] = QPixmap(art.frames[index])
    kept = [(key, frame.cacheKey()) for key, frame in art.cache.items() if key[0] == 3]
    art._load_motion()
    assert [(key, frame.cacheKey()) for key, frame in art.cache.items()] == kept
    assert all(art.frames[i].isNull() for i in (0, 1))


def compiled_test_art():
    art = CatArt(prefer_compiled=False)
    # Initial source indices have the same meanings as the production compiled base.
    art._compiled_indices = {i: i for i in range(10)}
    calls = []
    def decode(index):
        calls.append(index)
        frame = QPixmap(288, 224); frame.fill(QColor(20+index, 50, 70, 180)); frame.setDevicePixelRatio(2)
        return frame
    art._compiled = SimpleNamespace(
        manifest={'states': {'walking': [10, 11, 12, 13], 'original_walk': [0, 1]}},
        frame=decode)
    return art, calls


def test_retired_compiled_indices_can_be_decoded_again_without_resurrecting_empty_slots():
    art, calls = compiled_test_art()
    assert art._load_compiled_group([10, 11, 12, 13], ('walking',))
    current_walk = art.states['walking'][:]
    assert all(i not in art._compiled_indices for i in (0, 1))
    assert art.frames[0].isNull() and art.frames[1].isNull()
    assert art._load_compiled_group([0, 1], ('original_walk',))
    assert calls == [10, 11, 12, 13, 0, 1]
    assert art.states['original_walk'] == [14, 15]
    assert art.states['walking'] == current_walk
    assert all(not art.frames[i].isNull() for i in art.states['original_walk'])
    assert art._load_compiled_group([0, 1], ('original_walk',))
    assert calls == [10, 11, 12, 13, 0, 1]  # Repeated requests reuse only live mappings.


@pytest.mark.parametrize('failure', ['last_decode', 'missing_state'])
def test_failed_compiled_group_keeps_base_and_mapping_atomically(failure):
    art, calls = compiled_test_art()
    before = {i: art.frames[i].cacheKey() for i in range(10)}
    old_map = dict(art._compiled_indices)
    if failure == 'last_decode':
        real = art._compiled.frame
        art._compiled.frame = lambda index: None if index == 13 else real(index)
    else:
        del art._compiled.manifest['states']['walking']
    assert not art._load_compiled_group([10, 11, 12, 13], ('walking',))
    assert len(art.frames) == 10
    assert art._compiled_indices == old_map
    assert {i: art.frames[i].cacheKey() for i in range(10)} == before
    assert art.states['walking'] == [0, 1]


def test_released_and_retained_modes_render_identical_pixels_after_all_groups():
    retired = CatArt(prefer_compiled=False)
    retained = CatArt(prefer_compiled=False, retain_source_frames=True)
    poses = [('walking', age) for age in (0, .2, .4, .6)] + [
        ('idle', 0), ('sleeping', 0), ('window_peeking', 0), ('purring', 2.8),
        ('carried', 0), ('jumping', 0), ('eating', 0), ('grooming', 0),
        ('standing', 0), ('takeoff', 0), ('landing', 0), ('running', .2),
        ('belly', 0), ('swatting', 0), ('resting', 0), ('play_crouch', 0)]
    for kind, source, target in [('crouch_rise', 'play_crouch', 'standing'),
                                  ('crouch_lower', 'standing', 'play_crouch'),
                                  ('belly_roll', 'sleeping', 'resting'),
                                  ('belly_lie', 'play_crouch', 'sleeping')]:
        name = retired.posture_state(kind, source, target)
        assert name == retained.posture_state(kind, source, target)
        poses.append((name, 0))
    for state, age in poses:
        for facing in (-1, 1):
            for coat in ('ginger', 'gray', 'tuxedo'):
                for progress in (0, .5, .99):
                    a = retired.frame(state, age, facing, coat, pose_progress=progress)
                    b = retained.frame(state, age, facing, coat, pose_progress=progress)
                    assert a.devicePixelRatio() == b.devicePixelRatio() == 2
                    assert a.toImage() == b.toImage(), (state, age, facing, coat, progress)
    assert all(not frame.isNull() for frame in retained.frames)
    assert live_bytes(retained)-live_bytes(retired) == 1_032_192


def test_compiler_keeps_original_fallback_indices_and_pixels(tmp_path):
    path = Path(__file__).resolve().parents[1]/'scripts/compile_sprite_cache.py'
    spec = importlib.util.spec_from_file_location('retirement_compiler_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    manifest = module.compile_cache(tmp_path/'cache')
    # Export contract: all original base indices still exist, including retired runtime slots.
    import json
    from PyQt6.QtGui import QImage
    data = json.loads((tmp_path/'cache/manifest.json').read_text())
    initial = CatArt(prefer_compiled=False)
    assert data['base_frame_count'] == len(initial.frames) == 10
    for i, expected in enumerate(initial.frames):
        actual = QImage(str(tmp_path/'cache'/data['frames'][i]['file']))
        assert module.pixels(actual) == module.pixels(expected.toImage())
    assert data['frame_count'] == 18
    assert all(row['pixel_roundtrip_equal'] for row in data['frames'])
