"""Production small-frame cache preserves actual poses and lazy load order."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["compiled-art-test"])


@pytest.mark.parametrize("first_state", ["walking", "purring", "standing", "idle", "settling", "takeoff", "landing", "running", "fleeing", "sleeping", "belly", "swatting", "resting", "play_crouch"])
def test_compiled_groups_match_source_pixels_in_either_load_order(qt, first_state):
    cached = CatArt()
    source = CatArt(prefer_compiled=False)
    assert cached._compiled is not None, "Recompile sprite cache after source or asset changes"
    assert len(cached.frames) == len(source.frames) == 10
    assert cached.tail.toImage() == source.tail.toImage()
    cached.frame(first_state, 0, -1)
    source.frame(first_state, 0, -1)
    assert len(cached.frames) == (11 if first_state == "standing" else 14)
    poses = [("walking", age) for age in (0, .2, .4, .6)] + [
        ("window_peeking", 0), ("window_peeking", 3.8),
        ("purring", 0), ("purring", 2.8), ("idle", 0),
        ("sleeping", 0), ("belly", 0), ("swatting", 0), ("resting", 0), ("play_crouch", 0), ("carried", 0), ("jumping", 0),
        ("eating", 0), ("grooming", 0), ("standing", 0), ("landing", 0), ("running", .2), ("fleeing", .3),
    ]
    for state, age in poses:
        for facing in (-1, 1):
            for coat in ("ginger", "gray", "tuxedo"):
                actual = cached.frame(state, age, facing, coat)
                expected = source.frame(state, age, facing, coat)
                assert actual.devicePixelRatio() == expected.devicePixelRatio() == 2
                assert actual.toImage() == expected.toImage(), (state, age, facing, coat)
    assert len(cached.frames) == 33
    assert len(cached.cache) <= cached.cache_limit


def test_corrupt_optional_group_falls_back_atomically(qt):
    cached = CatArt()
    source = CatArt(prefer_compiled=False)
    compiled = cached._compiled
    assert compiled is not None

    class BrokenInteraction:
        manifest = compiled.manifest
        def frame(self, index):
            return None if index in self.manifest["interaction_frame_indices"][-1:] else compiled.frame(index)

    cached._compiled = BrokenInteraction()
    assert cached.frame("purring", 2.8, -1).toImage() == source.frame("purring", 2.8, -1).toImage()
    assert len(cached.frames) == 14  # No partial cached append followed by duplicate source append.
    assert cached.frame("walking", .4, 1).toImage() == source.frame("walking", .4, 1).toImage()
    assert len(cached.frames) == 18


def test_explicit_source_mode_ignores_compiled_cache(qt, monkeypatch):
    monkeypatch.setenv("BV_DISABLE_SPRITE_CACHE", "1")
    assert CatArt()._compiled is None
