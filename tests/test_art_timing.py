"""The desktop pose follows traveled ground distance and contact states."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["motion-timing-test"])


@pytest.fixture
def art(qt):
    return CatArt(prefer_compiled=False)


def render(art, state, **options):
    canvas = QImage(144, 112, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    art.paint(painter, QRectF(0, 0, 144, 112), state, age=100, **options)
    painter.end()
    return canvas


def test_pose_holds_when_no_distance_is_traveled(art):
    phase = .3
    first = art.frame("walking", 0, -1, gait_phase=phase)
    for age in (.1, .5, 3, 1000):
        assert art.frame("walking", age, -1, gait_phase=phase).cacheKey() == first.cacheKey()
    assert art.frame("walking", 1000, -1, gait_phase=.55).cacheKey() != first.cacheKey()


@pytest.mark.parametrize("state", ["entering", "leaving", "playing"])
def test_all_ground_movement_uses_the_same_distance_phase(art, state):
    for phase in (0, .25, .5, .75):
        reference = art.frame("walking", 0, 1, "gray", gait_phase=phase)
        assert art.frame(state, 200, 1, "gray", gait_phase=phase).toImage() == reference.toImage()


def test_distance_phase_wraps_without_resetting_at_state_change(art):
    expected = art.frame("walking", 0, -1, gait_phase=.25).cacheKey()
    assert art.frame("entering", 999, -1, gait_phase=3.25).cacheKey() == expected
    assert art.frame("leaving", 0, -1, gait_phase=-.75).cacheKey() == expected


def test_explicit_phase_does_not_change_jump_pose(art):
    first = art.frame("jumping", 0, -1, gait_phase=0)
    assert art.frame("jumping", 2, -1, gait_phase=.75).cacheKey() == first.cacheKey()


def test_landing_compresses_then_returns_to_anchored_standing_pose(art):
    standing = render(art, "standing")
    assert standing == render(art, "idle", settling=True)
    assert standing == render(art, "idle", land_progress=0)
    assert standing == render(art, "idle", land_progress=.9)
    assert standing != render(art, "idle", land_progress=.3)
    assert render(art, "idle", land_progress=.3) != render(art, "idle", land_progress=.6)
    assert standing != render(art, "idle", land_progress=1)
    # A new interaction is not delayed by a lingering stop timer.
    assert render(art, "purring", settling=True) == render(art, "purring")
    assert render(art, "carried", land_progress=.5) == render(art, "carried")


def test_turn_pause_plants_feet_without_delaying_escape(art):
    assert render(art, "walking", turning=True) == render(art, "standing")
    assert render(art, "playing", turning=True) == render(art, "standing")
    assert render(art, "fleeing", turning=True) == render(art, "fleeing")


def test_sitting_sequence_has_real_distinct_poses_and_shared_idle_endpoint(art):
    poses = [render(art, "idle", settle_progress=p) for p in (0, .3, .55, .8)]
    assert all(a != b for i, a in enumerate(poses) for b in poses[i+1:])
    assert poses[0] == render(art, "standing")
    assert poses[-1] == render(art, "idle") == render(art, "idle", settle_progress=1)


@pytest.mark.parametrize("seated_fraction", [.2, .45, .7, 1.0])
def test_rise_reverses_the_same_anchored_sitting_frames(art, seated_fraction):
    assert render(art, "walking", rise_progress=1-seated_fraction) == render(
        art, "idle", settle_progress=seated_fraction)


def test_posture_sequence_does_not_override_urgent_or_interaction_states(art):
    for state in ("fleeing", "leaving", "carried", "purring", "eating", "jumping"):
        assert render(art, state, settle_progress=.5, rise_progress=.5) == render(art, state)


def test_missing_transition_assets_preserve_original_idle(art, monkeypatch, tmp_path):
    from backyard_visitor import art as module
    original = art.frames[art.states["idle"][0]].toImage()
    monkeypatch.setattr(module, "ASSETS", tmp_path)
    assert art.frame("idle", 0, art.facing).toImage() == original
    assert "settling" not in art.states


def test_takeoff_uses_real_pose_progress_before_airborne_image(art):
    assert render(art, "jumping", takeoff_progress=0) == render(art, "standing")
    poses = [render(art, "jumping", takeoff_progress=p) for p in (0, .5, .9)]
    assert all(a != b for i, a in enumerate(poses) for b in poses[i+1:])
    assert poses[-1] != render(art, "jumping")
    for state in ("carried", "leaving", "fleeing", "purring"):
        assert render(art, state, takeoff_progress=.8) == render(art, state)


def test_missing_jump_assets_fall_back_to_standing(art, monkeypatch, tmp_path):
    standing = render(art, "standing")
    from backyard_visitor import art as module
    monkeypatch.setattr(module, "ASSETS", tmp_path)
    assert render(art, "idle", land_progress=.5) == standing
    assert render(art, "jumping", takeoff_progress=.8) == standing
    assert art.takeoff_mouth(.8, -1) == (21, 74)


def test_takeoff_mouth_tracks_pose_and_mirrors_about_canvas(art):
    mouths = [art.takeoff_mouth(p, -1) for p in (0, .5, .9)]
    assert mouths[0] == (21, 74)
    assert len(set(mouths)) == 3
    for progress, (x, y) in zip((0,.5,.9), mouths):
        assert art.takeoff_mouth(progress, 1) == (144-x, y)
        assert 0 < x < 144 and 0 < y < 112


@pytest.mark.parametrize("state", ["running", "fleeing"])
def test_running_has_its_own_distance_driven_poses(art, state):
    for phase in (0, .25, .5, .75):
        running = art.frame(state, 0, 1, "gray", run_phase=phase)
        assert running.toImage() != art.frame("walking", 0, 1, "gray", gait_phase=phase).toImage()
        assert running.toImage() == art.frame(state, 1000, 1, "gray", run_phase=phase).toImage()
        assert running.toImage() == art.frame("running", 10, 1, "gray", run_phase=phase).toImage()
    assert render(art, "running", turning=True) == render(art, "standing")


def test_running_mouth_follows_each_pose(art):
    mouths = [art.run_mouth(p, -1) for p in (0,.25,.5,.75)]
    assert len(set(mouths)) == 4
    for phase, (x,y) in zip((0,.25,.5,.75), mouths):
        assert art.run_mouth(phase, 1) == (144-x,y)


@pytest.mark.parametrize("state", ["walking", "running", "entering"])
def test_route_continuation_still_draws_contact_compression(art, state):
    assert render(art, state, land_progress=.4) == render(art, "idle", land_progress=.4)
    x,y = art.landing_mouth(.4, -1)
    assert art.landing_mouth(.4, 1) == (144-x,y)
    assert (x,y) != art.landing_mouth(0, -1)


@pytest.mark.parametrize("state", ["running", "fleeing"])
def test_first_run_paint_has_no_age_driven_whole_body_bob(art, state):
    def at_age(age):
        canvas=QImage(144,112,QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        painter=QPainter(canvas)
        art.paint(painter,QRectF(0,0,144,112),state,age=age,run_phase=.5)
        painter.end()
        return canvas
    first=at_age(0)
    assert not art._motion_loaded
    assert all(at_age(age)==first for age in (.05,.1,.2,1,30))


@pytest.mark.parametrize("state", ["resting", "play_crouch"])
def test_activity_destinations_still_show_landing_contact(art, state):
    assert render(art,state,land_progress=.4)==render(art,"idle",land_progress=.4)
    assert render(art,state,land_progress=1)==render(art,state)


@pytest.mark.parametrize("progress", [0,.25,.5,.9])
def test_initial_play_watch_rise_starts_from_real_seated_idle(art, progress):
    # Model's only teaser rise timer starts from idle, not a prior watch crouch.
    assert render(art,"play_crouch",rise_progress=progress)==render(art,"walking",rise_progress=progress)
    if progress==0:
        assert render(art,"play_crouch",rise_progress=progress)==render(art,"idle")


def test_completed_play_watch_rise_returns_to_distinct_crouch(art):
    assert render(art,"play_crouch",rise_progress=1)==render(art,"play_crouch")
    assert render(art,"play_crouch")!=render(art,"idle")
