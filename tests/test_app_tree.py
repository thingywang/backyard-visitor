"""Real Controller routes tree interaction only through verified desktop context."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import time
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter, DesktopTarget, Rect


class Screen:
    def geometry(self): return QRect(0, 0, 1440, 900)
    def availableGeometry(self): return QRect(0, 24, 1440, 876)
    def devicePixelRatio(self): return 1.
    def name(self): return 'tree-test-primary'


def refresh(c):
    c._depth_snapshot = {'path': str(c._wallpaper_path()), 'display_id': 'tree-display-A',
                         'options': {'scaling': 1, 'clipping': False}, 'primary_size': [1440, 900]}
    c._windows_checked = c._wallpaper_checked = time.monotonic()


@pytest.fixture
def c(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    c = Controller(qt, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=5)
    assert not c.settings['tree_enabled']
    c.timer.stop(); c.native_timer.stop()
    c.screen = Screen(); c._apply_screen_geometry()
    monkeypatch.setattr(qt, 'primaryScreen', lambda: c.screen)
    monkeypatch.setattr(c, '_run_worker', lambda *args: False)
    c._enable_native = True
    c.engine.calm_mode = True
    c.engine.open_gate(); c.engine.invite('amber')
    v = c.engine.visitors['amber']; v.state = 'idle'; v.state_age = 0
    v.x = v.target_x = 550.; v.y = v.target_y = c.engine.floor_y
    v.next_action = 1000; v.stay_remaining = 1000; v.carrying_gift = None
    v.takeoff_remaining = v.landing_remaining = v.settle_remaining = 0
    c.engine.cats['amber'].trust = 100
    c.settings.update(preset_wallpaper=True, wallpaper_id='backyard-cat-tree-landscape-morning', tree_enabled=True)
    c._mask_data = c._read_mask(); refresh(c)
    c._sync()
    yield c
    c.shutdown(); qt.processEvents()


def run_to(c, predicate, seconds=200):
    for _ in range(int(seconds/.05)):
        if predicate(): return
        refresh(c); c.engine.tick(.05); c._sync()
    assert predicate(), 'Tree journey did not reach expected stage'


def start(c):
    assert c.send_to_tree('amber')
    return c.engine.visitors['amber']


@pytest.mark.parametrize('failure', ['off', 'unmatched', 'stale-wallpaper', 'stale-windows', 'future',
                                      'scaling', 'clipping', 'secondary', 'size', 'display-id', 'no-native', 'classic'])
def test_unverified_desktop_cannot_start(c, monkeypatch, failure):
    if failure == 'off': c.settings['tree_enabled'] = False
    elif failure == 'unmatched': c._depth_snapshot['path'] = '/not-this-wallpaper.png'
    elif failure == 'stale-wallpaper': c._wallpaper_checked -= 10
    elif failure == 'stale-windows': c._windows_checked -= 10
    elif failure == 'future': c._wallpaper_checked += 10
    elif failure == 'scaling': c._depth_snapshot['options']['scaling'] = 3
    elif failure == 'clipping': c._depth_snapshot['options']['clipping'] = True
    elif failure == 'secondary': monkeypatch.setattr(c.qt, 'primaryScreen', lambda: object())
    elif failure == 'size': c._depth_snapshot['primary_size'] = [1920, 1080]
    elif failure == 'display-id': c._depth_snapshot.pop('display_id')
    elif failure == 'no-native': c._enable_native = False
    else:
        c.settings['wallpaper_id'] = 'backyard-morning'; c._mask_data = c._read_mask(); refresh(c)
    assert not c.send_to_tree('amber')
    assert c.engine.visitors['amber'].tree_trip is None


def test_selected_window_cover_blocks_route_without_granting_window_paths(c):
    c.settings['window_paths'] = False
    c._windows = [DesktopTarget('cover', 'test', Rect(960, 180, 400, 550))]
    assert not c.send_to_tree('amber')
    assert not c.engine.window_edges


def test_actual_roundtrip_uses_scaled_overlay_then_returns_original_foot(c):
    v = c.engine.visitors['amber']; origin = v.x, v.y
    start(c)
    run_to(c, lambda: v.tree_scale < .66)
    window = c.cat_windows['amber']
    assert window.mask().boundingRect().width() < 144
    assert c.cat_clip(window.scene_position(), window.size(), 'amber') is None
    run_to(c, lambda: v.tree_pose.get('pose') == 'sleeping')
    assert v.tree_pose['support_id'] == v.tree_trip.layout.bed_id
    run_to(c, lambda: v.tree_trip is None)
    assert (v.x, v.y) == pytest.approx(origin, abs=1e-6)
    assert v.tree_scale == 1


@pytest.mark.parametrize('reason', ['cover', 'external-wallpaper', 'display-change', 'lost-snapshot'])
def test_context_loss_returns_continuously_without_new_anchor(c, reason):
    v = start(c)
    run_to(c, lambda: v.tree_pose.get('support_id') == v.tree_trip.layout.bed_id)
    before = v.x, v.y, v.tree_scale
    if reason == 'cover': c._windows = [DesktopTarget('cover', 'test', Rect(960, 160, 400, 500))]
    elif reason == 'external-wallpaper': c._depth_snapshot['path'] = '/changed-by-user.png'
    elif reason == 'display-change': c._depth_snapshot['display_id'] = 'other-display'
    else: c._depth_snapshot = None
    c._sync()
    assert (v.x, v.y, v.tree_scale) == pytest.approx(before)
    assert v.tree_pose.get('returning')
    run_to(c, lambda: v.tree_trip is None)


def test_same_geometry_morning_dusk_publish_preserves_airborne_state(c):
    v = start(c)
    run_to(c, lambda: v.tree_pose.get('pose') == 'jumping')
    before = v.x, v.y, v.tree_scale, v.tree_pose['age']
    trip = v.tree_trip
    new_id = 'backyard-cat-tree-landscape-dusk'
    snap = dict(c._depth_snapshot, path=str(c._wallpaper_path(new_id)))
    c._publish_wallpaper(new_id, snap)
    assert v.tree_trip is trip
    assert (v.x, v.y, v.tree_scale, v.tree_pose['age']) == pytest.approx(before)
    assert not v.tree_pose['returning']


def test_different_layout_wallpaper_request_waits_for_actual_return(c, monkeypatch):
    v = start(c)
    run_to(c, lambda: v.tree_pose.get('pose') == 'sleeping')
    called = []
    monkeypatch.setattr(c, '_run_worker', lambda *args: called.append(args))
    c._queue_wallpaper(lambda: None, lambda *args: None, wait_for_depth=True)
    assert not called and v.tree_trip is not None
    assert c.send_to_tree('amber')  # Existing visit may still be recalled.
    run_to(c, lambda: v.tree_trip is None)
    assert len(called) == 1 and called[0][0] == 'wallpaper'


def test_disable_option_and_save_do_not_persist_runtime_trip(c):
    v = start(c)
    run_to(c, lambda: v.tree_scale < .66)
    assert c._save()
    data = json.loads(c.state_path.read_text())
    assert data['settings']['tree_enabled'] is True
    assert 'tree_trip' not in json.dumps(data) and 'tree_visiting' not in json.dumps(data)
    c.set_tree_enabled(False)
    assert v.tree_pose['returning']
    run_to(c, lambda: v.tree_trip is None)


def test_handbook_button_closes_only_own_window_before_tree_journey(c):
    j = c.journal; j.show_page(1); j.show(); c.qt.processEvents(); j.refresh()
    card = next(x for x in j.cards if x.cat_id == 'amber')
    assert card.tree_button.isVisible()
    card.tree_button.click()
    assert c.engine.visitors['amber'].tree_trip is not None
    assert not j.isVisible()


def test_poll_observes_window_and_wallpaper_even_without_window_parkour(c, monkeypatch):
    c.settings['window_paths'] = False; c.settings['depth_enabled'] = False
    seen = []
    monkeypatch.setattr(c.adapter, 'windows', lambda: seen.append('windows') or [])
    monkeypatch.setattr(c.adapter, 'current_wallpaper_snapshot', lambda: seen.append('wallpaper') or c._depth_snapshot)
    requests = []
    monkeypatch.setattr(c, '_run_worker', lambda *args: requests.append(args))
    c._native_poll()
    key, operation, done = requests[0]
    done(operation())
    assert seen == ['windows', 'wallpaper'] and c._tree_context_ready()


def test_play_waits_for_tree_return_before_starting_its_fifteen_seconds(c, monkeypatch):
    v = start(c)
    run_to(c, lambda: v.tree_pose.get('pose') == 'sleeping')
    assert c.start_play('amber')
    assert c._tree_play_pending == 'amber' and c._teaser_session is None
    run_to(c, lambda: v.tree_trip is None)
    play = Mock(return_value=True); monkeypatch.setattr(c, 'start_play', play)
    c._continue_tree_play()
    play.assert_called_once_with('amber')
    assert c._tree_play_pending is None


def test_paused_scene_and_closed_gate_do_not_auto_start(c):
    c._tree_next_offer = 0
    c.paused = True; c._maybe_tree_visit()
    assert c.engine.visitors['amber'].tree_trip is None
    c.paused = False; c.engine.gate_open = False; c._maybe_tree_visit()
    assert c.engine.visitors['amber'].tree_trip is None


def test_explicit_stop_cancels_queued_play_without_resurrecting_it(c, monkeypatch):
    v = start(c)
    assert c.start_play('amber') and c._tree_play_pending == 'amber'
    c.stop_play('cancelled')
    assert c._tree_play_pending is None
    run_to(c, lambda: v.tree_trip is None)
    play = Mock(); monkeypatch.setattr(c, 'start_play', play)
    c._continue_tree_play(); play.assert_not_called()


def test_enabled_unobscured_idle_cat_can_choose_tree_without_manual_button(c, monkeypatch):
    c._tree_next_offer = 0
    monkeypatch.setattr(c.engine.rng, 'random', lambda: 0.)
    c._maybe_tree_visit()
    assert c.engine.visitors['amber'].tree_trip is not None
    assert c._tree_next_offer > c.engine._time


def test_tree_opt_in_does_not_write_wallpaper_or_enable_other_desktop_features(c, monkeypatch):
    write = Mock(); monkeypatch.setattr(c.adapter, 'set_wallpaper', write)
    c.settings.update(tree_enabled=False, depth_enabled=False, window_paths=False)
    c.set_tree_enabled(True)
    assert c.settings['tree_enabled']
    assert not c.settings['depth_enabled'] and not c.settings['window_paths']
    write.assert_not_called()


def test_background_without_tree_disables_only_tree_permission(c):
    scene = 'backyard-morning'
    snapshot = dict(c._depth_snapshot, path=str(c._wallpaper_path(scene)))
    c._publish_wallpaper(scene, snapshot)
    assert not c.settings['tree_enabled']
    assert c.settings['preset_wallpaper'] and c.settings['wallpaper_id'] == scene


def test_window_covering_original_return_corridor_recalls_cat_from_tree_bed(c):
    v = c.engine.visitors['amber']
    v.x = v.target_x = 150.-72
    v.y = v.target_y = 896.-108
    start(c)
    run_to(c, lambda: v.tree_pose.get('pose') == 'sleeping')
    before = v.x, v.y, v.tree_scale
    c._windows = [DesktopTarget('floor-cover', 'test', Rect(120, 850, 120, 40))]
    c._sync()
    assert v.tree_pose['returning']
    assert (v.x, v.y, v.tree_scale) == pytest.approx(before)
    run_to(c, lambda: v.tree_trip is None)
