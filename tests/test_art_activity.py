"""CatArt maps real activity poses lazily while preserving existing fallbacks."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

import pytest
from PyQt6.QtWidgets import QApplication
from backyard_visitor.activity_art import ActivityArt,STATES
from backyard_visitor.art import CatArt


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["art-activity-integration"])


def test_activity_loads_on_first_sleep_and_uses_real_independent_frames(qt):
    art=CatArt(prefer_compiled=False)
    assert not art._activity_loaded and len(art.frames)==10
    original_sleep=art.frames[art.states["sleeping"][0]].toImage()
    expected=ActivityArt().load_frames()
    sleep=art.frame("sleeping",0,-1)
    assert art._activity_loaded and len(art.frames)==14
    assert sleep.toImage()==expected["belly"].toImage()!=original_sleep
    assert art.states["sleeping"]==art.states["belly"]
    for state in STATES:
        assert art.frame(state,0,-1).toImage()==expected[state].toImage()
    assert len(art.frames)==14


def test_activity_failure_retains_original_sleep_without_partial_state(qt,monkeypatch):
    art=CatArt(prefer_compiled=False)
    original_sleep=art.frames[art.states["sleeping"][0]].toImage()
    monkeypatch.setattr(ActivityArt,"load_frames",lambda self: {})
    assert art.frame("sleeping",0,-1).toImage()==original_sleep
    assert len(art.frames)==10 and "belly" not in art.states
    assert art.frame("belly",0,-1).toImage()==original_sleep
    assert art.frame("swatting",0,-1).toImage()==original_sleep


def test_activity_colors_and_mirrors_keep_existing_bounded_cache(qt):
    art=CatArt(prefer_compiled=False)
    for state in STATES:
        for facing in (-1,1):
            for coat in ("ginger","gray","tuxedo"):
                frame=art.frame(state,0,facing,coat)
                assert (frame.width(),frame.height(),frame.devicePixelRatio())==(288,224,2)
    assert len(art.frames)==14
    assert len(art.cache)<=art.cache_limit
