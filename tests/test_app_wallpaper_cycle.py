"""Fake-native Q20 controller integration; never writes a system wallpaper."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import threading
import time

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from backyard_visitor import app
from backyard_visitor.desktop import DesktopAdapter


class Clock(datetime):
    value = datetime(2026, 10, 29, 12, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.value


class FakeGuardedWallpaper(DesktopAdapter):
    def __init__(self):
        super().__init__()
        self.capabilities.update(wallpaper=True, wallpaper_guarded=True)
        self.snapshot = {'path': '/fake/original.png', 'options': {'scaling': 0, 'clipping': False},
                         'primary_size': [1600, 900], 'display_id': 'display-a'}
        self.calls, self.reads, self.restore_calls = [], [], []
        self.wallpaper_backup = None
        self.available, self.succeed, self.bad_readback = True, True, False
        self.read_started, self.write_started = threading.Event(), threading.Event()
        self.read_release, self.write_release = threading.Event(), threading.Event()
        self.read_release.set(); self.write_release.set()
        self.race_external = False
        self.raise_on_read = False

    def current_wallpaper_snapshot(self):
        self.reads.append(threading.get_ident())
        self.read_started.set()
        if not self.read_release.wait(2):
            raise TimeoutError('fake snapshot deadline')
        if self.raise_on_read:
            raise OSError('fake snapshot unavailable')
        return deepcopy(self.snapshot) if self.available else None

    def set_wallpaper_guarded(self, path, expected_display_id, expected_snapshot):
        self.calls.append((str(path), expected_display_id, deepcopy(expected_snapshot), threading.get_ident()))
        self.write_started.set()
        if not self.write_release.wait(2):
            raise TimeoutError('fake write deadline')
        if self.race_external:
            self.snapshot['path'] = '/fake/external-race.png'
        if (not self.succeed or expected_display_id != self.snapshot['display_id']
                or expected_snapshot != self.snapshot):
            self.last_error = 'fake native guard rejected'
            return False
        if self.wallpaper_backup is None:
            self.wallpaper_backup = deepcopy(self.snapshot)
        self.snapshot['path'] = str(path)
        self.snapshot['options'] = {'scaling': 2 if self.bad_readback else 1, 'clipping': False}
        return True

    def restore_wallpaper_guarded(self, expected_display_id, expected_snapshot=None):
        self.restore_calls.append(threading.get_ident())
        if (not isinstance(self.wallpaper_backup, dict)
                or self.wallpaper_backup['display_id'] != expected_display_id
                or self.snapshot != expected_snapshot):
            return False
        self.snapshot = deepcopy(self.wallpaper_backup)
        return True

    def set_wallpaper(self, path):
        raise AssertionError('guard-capable adapter must not use unguarded write')

    def restore_wallpaper(self):
        raise AssertionError('guard-capable adapter must not use unguarded restore')


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication(['wallpaper-cycle-tests'])


def until(qt, predicate, timeout=3):
    deadline = time.monotonic()+timeout
    while not predicate() and time.monotonic() < deadline:
        qt.processEvents(); QTest.qWait(5)
    qt.processEvents()
    assert predicate(), 'fake wallpaper task did not finish'


def settle(qt, c):
    qt.processEvents()
    until(qt, lambda: not c._workers and c._wallpaper_pending is None and not c._cycle_poll_pending)


@pytest.fixture
def factory(qt, tmp_path, monkeypatch):
    Clock.value = datetime(2026, 10, 29, 12)
    monkeypatch.setattr(app, 'datetime', Clock)
    load_scenes = app.load_scenes
    def paired_scenes(assets):
        # Policy metadata is a fixture, independent of artwork still in review.
        # The scene loader/policy tests validate production metadata separately.
        scenes = load_scenes(assets)
        pair = {'morning': 'backyard-morning', 'dusk': 'backyard-dusk'}
        for phase, scene_id in pair.items():
            scenes[scene_id].update(theme_id='test-classic', orientation='landscape',
                                    phase=phase, cycle_pair=pair, cycle_ready=True)
        return scenes
    monkeypatch.setattr(app, 'load_scenes', paired_scenes)
    controllers = []
    def create(state=None, *, adapter=None, demo=False):
        folder = tmp_path/str(len(controllers)); folder.mkdir()
        if state is not None:
            (folder/'state.json').write_text(json.dumps(state))
        adapter = adapter or FakeGuardedWallpaper()
        c = app.Controller(qt, state_dir=folder, hidden=True, adapter=adapter,
                           demo=demo, enable_native=False, seed=12)
        controllers.append(c)
        return c, adapter
    yield create
    for c in controllers:
        c.adapter.read_release.set(); c.adapter.write_release.set()
        until(qt, lambda: not c._workers)
        c.shutdown()
    qt.processEvents()


def choose(c, scene='backyard-dusk'):
    choices = c.journal.wallpaper_choices
    index = choices.findData(scene)
    assert index >= 0
    choices.setCurrentIndex(index)


def enable(c):
    choose(c)
    c.journal.wallpaper_cycle_check.setChecked(True)


def test_default_off_preview_does_not_write_or_start_timer(factory, qt):
    c, adapter = factory()
    choose(c, 'backyard-morning')
    assert not c.wallpaper_cycle_enabled and not c.wallpaper_timer.isActive()
    assert not c.journal.wallpaper_cycle_check.isChecked()
    assert adapter.calls == adapter.reads == []
    assert not c.timer.isActive()


def test_independent_timer_switches_at_boundary_and_does_not_rewrite(factory, qt):
    c, adapter = factory(); enable(c); settle(qt, c)
    assert c.wallpaper_timer.isActive() and c.wallpaper_timer.interval() == 60_000
    assert not c.timer.isActive() and not c.engine.gate_open
    assert c.settings['wallpaper_id'] == 'backyard-morning' and len(adapter.calls) == 1
    assert all(thread != threading.get_ident() for thread in adapter.reads)
    assert adapter.calls[0][3] != threading.get_ident()
    backup = deepcopy(adapter.wallpaper_backup)
    clock = c.engine._time
    c.set_paused(True)
    Clock.value = datetime(2026, 10, 29, 18)
    c.wallpaper_timer.timeout.emit(); settle(qt, c)
    assert c.settings['wallpaper_id'] == 'backyard-dusk' and len(adapter.calls) == 2
    c.wallpaper_timer.timeout.emit(); settle(qt, c)
    assert len(adapter.calls) == 2 and adapter.wallpaper_backup == backup
    assert c.engine._time == clock
    Clock.value = datetime(2026, 10, 30, 6)
    c.wallpaper_timer.timeout.emit(); settle(qt, c)
    assert c.settings['wallpaper_id'] == 'backyard-morning' and len(adapter.calls) == 3


def test_unknown_snapshot_waits_and_reports_once_then_retries(factory, qt):
    c, adapter = factory(); adapter.available = False
    enable(c); settle(qt, c)
    initial = len(c._notes)
    assert c.wallpaper_cycle_enabled and c.wallpaper_timer.isActive()
    for _ in range(2):
        c._poll_wallpaper_cycle(); settle(qt, c)
    assert len(c._notes) == initial and not adapter.calls
    adapter.available = True
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert c.settings['wallpaper_id'] == 'backyard-morning' and len(adapter.calls) == 1


@pytest.mark.parametrize('change', ['wallpaper', 'display', 'fit'])
def test_external_change_pauses_without_overwrite_or_repeated_notice(factory, qt, change):
    c, adapter = factory(); enable(c); settle(qt, c)
    if change == 'wallpaper':
        adapter.snapshot['path'] = '/fake/user-chosen.png'
    elif change == 'display':
        adapter.snapshot['display_id'] = 'display-b'
    else:
        adapter.snapshot['options']['scaling'] = 2
    Clock.value = datetime(2026, 10, 29, 20)
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert not c.wallpaper_cycle_enabled and not c.wallpaper_timer.isActive()
    note_count = len(c._notes)
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert len(adapter.calls) == 1 and len(c._notes) == note_count


def test_turning_off_during_snapshot_cancels_pending_apply(factory, qt):
    c, adapter = factory(); adapter.read_release.clear()
    enable(c); until(qt, adapter.read_started.is_set)
    c.set_wallpaper_cycle(False)
    adapter.read_release.set(); settle(qt, c)
    assert not adapter.calls and not c.wallpaper_cycle_enabled
    assert c.settings['wallpaper_id'] == 'backyard-dusk'


def test_turning_off_during_write_ignores_stale_result_but_retains_backup(factory, qt):
    c, adapter = factory(); adapter.write_release.clear()
    enable(c); until(qt, adapter.write_started.is_set)
    old_mask = c._mask_data
    c.set_wallpaper_cycle(False)
    adapter.write_release.set(); settle(qt, c)
    assert c._mask_data is old_mask and c.settings['wallpaper_id'] == 'backyard-dusk'
    assert not c.wallpaper_cycle_enabled and adapter.wallpaper_backup['path'] == '/fake/original.png'
    assert c._save()
    saved = json.loads(c.state_path.read_text())
    assert saved['wallpaper_backup']['path'] == '/fake/original.png'
    assert not saved['wallpaper_cycle']['enabled']


def test_restore_replaces_queued_manual_work_and_disables_cycle(factory, qt):
    c, adapter = factory(); adapter.write_release.clear()
    enable(c); until(qt, adapter.write_started.is_set)
    choose(c, 'backyard-dusk'); c.apply_wallpaper()
    c.restore_wallpaper()
    adapter.write_release.set(); settle(qt, c)
    assert len(adapter.calls) == 1 and len(adapter.restore_calls) == 1
    assert adapter.snapshot['path'] == '/fake/original.png'
    assert not c.wallpaper_cycle_enabled and not c.settings['preset_wallpaper']
    assert c._wallpaper_pending is None


def test_latest_time_after_suspended_read_skips_intermediate_phases(factory, qt):
    c, adapter = factory(); adapter.read_release.clear()
    enable(c); until(qt, adapter.read_started.is_set)
    Clock.value = datetime(2026, 11, 2, 22)
    for _ in range(3):
        c._poll_wallpaper_cycle()
    adapter.read_release.set(); settle(qt, c)
    assert len(adapter.calls) == 1 and c.settings['wallpaper_id'] == 'backyard-dusk'


@pytest.mark.parametrize('manual', [False, True])
def test_bad_readback_never_publishes_new_scene_or_mask(factory, qt, manual):
    c, adapter = factory(); adapter.bad_readback = True
    old_id, old_mask = c.settings['wallpaper_id'], c._mask_data
    if manual:
        choose(c, 'backyard-morning'); c.apply_wallpaper()
    else:
        enable(c)
    settle(qt, c)
    assert c.settings['wallpaper_id'] == old_id and c._mask_data is old_mask
    assert not c._wallpaper_match
    assert c.wallpaper_cycle.confirmed_scene_id is None


def test_native_guard_race_does_not_overwrite_external_wallpaper(factory, qt):
    c, adapter = factory(); adapter.race_external = True
    enable(c); settle(qt, c)
    assert adapter.snapshot['path'] == '/fake/external-race.png'
    assert not c.settings['preset_wallpaper']
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert not c.wallpaper_cycle_enabled and len(adapter.calls) == 1


def test_formal_restart_only_resumes_while_still_on_authorized_pair(factory, qt):
    c, adapter = factory(); enable(c); settle(qt, c)
    c._save(); saved = json.loads(c.state_path.read_text())
    restored_adapter = FakeGuardedWallpaper(); restored_adapter.snapshot = deepcopy(adapter.snapshot)
    Clock.value = datetime(2026, 10, 30, 20)
    resumed, actual = factory(saved, adapter=restored_adapter)
    settle(qt, resumed)
    assert resumed.wallpaper_cycle_enabled
    assert resumed.settings['wallpaper_id'] == 'backyard-dusk' and len(actual.calls) == 1
    external, native = factory(saved)
    settle(qt, external)
    assert not external.wallpaper_cycle_enabled and not native.calls


def test_demo_and_unguarded_adapter_cannot_enable_cycle(factory, qt):
    c, adapter = factory(demo=True)
    c.set_wallpaper_cycle(True); settle(qt, c)
    assert not c.wallpaper_cycle_enabled and not adapter.reads and not adapter.calls
    formal, native = factory(); native.capabilities['wallpaper_guarded'] = False
    formal.set_wallpaper_cycle(True); settle(qt, formal)
    assert not formal.wallpaper_cycle_enabled and not native.reads and not native.calls


def test_snapshot_exception_is_notified_once_and_preserves_backup(factory, qt):
    c, adapter = factory(); enable(c); settle(qt, c)
    backup = deepcopy(adapter.wallpaper_backup)
    adapter.raise_on_read = True
    c._poll_wallpaper_cycle(); settle(qt, c)
    count = len(c._notes)
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert len(c._notes) == count and adapter.wallpaper_backup == backup
    assert c.wallpaper_cycle_enabled


def test_changing_preview_does_not_switch_running_cycle_theme(factory, qt):
    c, adapter = factory(); enable(c); settle(qt, c)
    theme = c.wallpaper_cycle.theme_id
    choose(c, 'backyard-cat-tree-portrait')
    assert c.wallpaper_cycle.theme_id == theme and len(adapter.calls) == 1
    Clock.value = datetime(2026, 10, 29, 20)
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert c.settings['wallpaper_id'] == 'backyard-dusk'


def test_rotation_pauses_even_when_current_phase_does_not_need_a_new_image(factory, qt):
    c, adapter = factory(); enable(c); settle(qt, c)
    adapter.snapshot['primary_size'] = [900, 1600]
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert not c.wallpaper_cycle_enabled and not c.wallpaper_timer.isActive()
    assert len(adapter.calls) == 1 and not c._wallpaper_match


def test_external_change_after_initial_write_failure_does_not_gain_fresh_permission(factory, qt):
    c, adapter = factory(); adapter.succeed = False
    enable(c); settle(qt, c)
    assert len(adapter.calls) == 1 and c.wallpaper_cycle_enabled
    adapter.succeed = True
    adapter.snapshot['path'] = '/fake/user-change-after-error.png'
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert len(adapter.calls) == 1 and not c.wallpaper_cycle_enabled


def test_same_snapshot_after_transient_initial_failure_can_retry(factory, qt):
    c, adapter = factory(); adapter.succeed = False
    enable(c); settle(qt, c)
    adapter.succeed = True
    c._poll_wallpaper_cycle(); settle(qt, c)
    assert c.wallpaper_cycle_enabled and len(adapter.calls) == 2
    assert c.settings['wallpaper_id'] == 'backyard-morning'


def test_primary_change_invalidates_inflight_write_and_old_callback(factory, qt):
    c, adapter = factory(); adapter.write_release.clear()
    enable(c); until(qt, adapter.write_started.is_set)
    old_mask = c._mask_data
    adapter.snapshot['display_id'] = 'display-b'
    c._primary_screen_changed(c.qt.primaryScreen())
    adapter.write_release.set(); settle(qt, c)
    assert not c.wallpaper_cycle_enabled and len(adapter.calls) == 1
    assert adapter.snapshot['path'] == '/fake/original.png'
    assert c._mask_data is old_mask and not c._wallpaper_match


def test_manual_latest_request_replaces_intermediate_queued_request(factory, qt):
    c, adapter = factory(); choose(c, 'backyard-morning')
    adapter.write_release.clear(); c.apply_wallpaper()
    until(qt, adapter.write_started.is_set)
    choose(c, 'backyard-dusk'); c.apply_wallpaper()
    choose(c, 'backyard-morning'); c.apply_wallpaper()
    adapter.write_release.set(); settle(qt, c)
    assert len(adapter.calls) == 2
    assert all(Path(call[0]).name == 'backyard-morning.png' for call in adapter.calls)
    assert c.settings['wallpaper_id'] == 'backyard-morning' and c._wallpaper_match
    assert not c.wallpaper_cycle_enabled


def depth_visit_fixture(c, adapter, monkeypatch):
    from backyard_visitor.depth_context import build_depth_route
    c.engine.set_bounds(0, 24, 1600, 876)
    c.engine.open_gate(); c.engine.invite('amber')
    visitor = c.engine.visitors['amber']
    visitor.state, visitor.x, visitor.y = 'idle', 720, c.engine.floor_y
    visitor.next_action = visitor.stay_remaining = 1000
    c.engine.cats['amber'].trust = 100
    c.settings.update(preset_wallpaper=True, wallpaper_id='backyard-morning', depth_enabled=True)
    c._mask_data = c._read_mask(); c._wallpaper_match = True
    adapter.wallpaper_backup = deepcopy(adapter.snapshot)
    adapter.snapshot.update(path=str(c._wallpaper_path()), options={'scaling': 1, 'clipping': False})
    route = build_depth_route(c._mask_data, (0, 0, 1600, 900), (visitor.x+72, visitor.y+108))
    assert route is not None and c.engine.begin_depth_visit('amber', route, screen_rect=(0, 0, 1600, 900))
    monkeypatch.setattr(c, '_depth_context_ready', lambda: True)
    monkeypatch.setattr(c, '_native_poll', lambda: None)
    for _ in range(800):
        c.engine.tick(.025)
        if visitor.depth_layer == 'rear':
            break
    assert visitor.depth_trip is not None and visitor.depth_layer == 'rear'
    c.timer.stop(); c.native_timer.stop()
    return visitor


def finish_depth(c, visitor, adapter):
    c.timer.stop()
    for _ in range(2000):
        if visitor.depth_trip is None:
            break
        assert not adapter.calls and not adapter.restore_calls
        c.engine.tick(.025)
    assert visitor.depth_trip is None and visitor.depth_layer == 'front' and visitor.depth_scale == 1
    c._sync()


def test_different_layout_waits_for_real_depth_return_before_write(factory, qt, monkeypatch):
    c, adapter = factory(); visitor = depth_visit_fixture(c, adapter, monkeypatch)
    before = visitor.x, visitor.y, visitor.depth_scale
    Clock.value = datetime(2026, 10, 29, 20); enable(c)
    until(qt, lambda: c._wallpaper_pending is not None and not c._workers)
    assert (visitor.x, visitor.y, visitor.depth_scale) == before
    assert visitor.depth_pose['return_reason'] == 'wallpaper_change'
    assert not adapter.calls and c._wallpaper_depth_wait
    c.engine.invite('mist'); c.engine.visitors['mist'].state = 'idle'
    assert not c.send_to_backyard('mist')
    finish_depth(c, visitor, adapter); settle(qt, c)
    assert len(adapter.calls) == 1 and c.settings['wallpaper_id'] == 'backyard-dusk'
    assert not c._wallpaper_depth_wait


@pytest.mark.parametrize('change', ['disable', 'latest_time', 'restore', 'external'])
def test_depth_wait_discards_obsolete_scene_request(factory, qt, monkeypatch, change):
    c, adapter = factory(); visitor = depth_visit_fixture(c, adapter, monkeypatch)
    Clock.value = datetime(2026, 10, 29, 20); enable(c)
    until(qt, lambda: c._wallpaper_pending is not None and not c._workers)
    if change == 'disable':
        c.set_wallpaper_cycle(False)
    elif change == 'latest_time':
        Clock.value = datetime(2026, 10, 30, 12)
    elif change == 'restore':
        c.restore_wallpaper()
    else:
        adapter.snapshot['path'] = '/fake/external-during-return.png'
    finish_depth(c, visitor, adapter); settle(qt, c)
    assert c._wallpaper_pending is None and not c._wallpaper_depth_wait
    if change == 'restore':
        assert not adapter.calls and len(adapter.restore_calls) == 1
        assert adapter.snapshot['path'] == '/fake/original.png'
    elif change == 'external':
        assert len(adapter.calls) == 1 and not c.wallpaper_cycle_enabled
        assert adapter.snapshot['path'] == '/fake/external-during-return.png'
    else:
        assert not adapter.calls
        assert c.wallpaper_cycle_enabled is (change == 'latest_time')


def test_readback_crossing_boundary_confirms_own_write_without_publishing_old_phase(factory, qt, monkeypatch):
    c, adapter = factory()
    published = []
    publish = c._publish_wallpaper
    monkeypatch.setattr(c, '_publish_wallpaper', lambda scene, snapshot=None: (published.append(scene), publish(scene, snapshot)))
    setter = adapter.set_wallpaper_guarded
    def cross_boundary(*args):
        result = setter(*args)
        Clock.value = datetime(2026, 10, 29, 18, 0, 1)
        return result
    monkeypatch.setattr(adapter, 'set_wallpaper_guarded', cross_boundary)
    Clock.value = datetime(2026, 10, 29, 17, 59, 59)
    enable(c); settle(qt, c)
    assert len(adapter.calls) == 2 and published == ['backyard-dusk']
    assert c.wallpaper_cycle.confirmed_scene_id == 'backyard-dusk' and c.wallpaper_cycle_enabled


@pytest.mark.parametrize('operation', ['apply', 'restore'])
def test_native_rejection_detail_survives_readback_clearing_error(factory, qt, monkeypatch, operation):
    c, adapter = factory(); choose(c, 'backyard-morning')
    reader = adapter.current_wallpaper_snapshot
    def read():
        result = reader(); adapter.last_error = ''; return result
    monkeypatch.setattr(adapter, 'current_wallpaper_snapshot', read)
    def reject(*args):
        adapter.last_error = 'fixture backup belongs to another display'; return False
    if operation == 'apply':
        monkeypatch.setattr(adapter, 'set_wallpaper_guarded', reject); c.apply_wallpaper()
    else:
        monkeypatch.setattr(adapter, 'restore_wallpaper_guarded', reject); c.restore_wallpaper()
    settle(qt, c)
    assert any('fixture backup belongs to another display' in note for note in c._notes)


def test_restore_accepts_omitted_default_clipping(factory, qt, monkeypatch):
    c, adapter = factory(); enable(c); settle(qt, c)
    adapter.wallpaper_backup['options'].pop('clipping')
    reader = adapter.current_wallpaper_snapshot
    def normalized():
        value = reader(); value['options'].setdefault('clipping', False); return value
    monkeypatch.setattr(adapter, 'current_wallpaper_snapshot', normalized)
    c.restore_wallpaper(); settle(qt, c)
    assert not c.settings['preset_wallpaper'] and not c._wallpaper_match
    assert any('此前的壁纸已还原' in note for note in c._notes)


def test_recovery_conflicts_refuse_overwrite_and_legacy_owner_can_upgrade(factory):
    c, _ = factory()
    original = {'path': '/fake/original.png', 'options': {'scaling': 1}, 'display_id': 'display-a'}
    c._write_wallpaper_recovery(original)
    legacy = {'path': original['path'], 'options': {'scaling': 1}}
    assert c._same_wallpaper_backup(legacy, original)
    with pytest.raises(ValueError):
        c._write_wallpaper_recovery(dict(original, display_id='display-b'))
    assert json.loads(c._wallpaper_recovery_path.read_text())['wallpaper_backup'] == original


def test_real_adapter_persists_backup_before_late_write_and_restart_can_restore(qt, tmp_path, monkeypatch):
    from backyard_visitor.desktop import MacDesktopAdapter, _SET_WALLPAPER
    original = tmp_path/'original.png'; original.write_bytes(b'fixture original')
    native = {'path': str(original), 'options': {'scaling': 1, 'clipping': False},
              'primary_size': [1600, 900], 'display_id': 'a1234567-1234-4567-890a-123456789abc'}
    captured, release = threading.Event(), threading.Event()
    reads = 0; writes = []
    adapter = MacDesktopAdapter()
    folder = tmp_path/'controller'
    def fake_jxa(source, *args):
        nonlocal reads
        if source == _SET_WALLPAPER:
            recovery = json.loads((folder/'wallpaper-recovery.json').read_text())
            assert recovery['wallpaper_backup']['path'] == str(original)
            writes.append(args[2]); native['path'] = args[2]; native['options'] = json.loads(args[3])
            return 'true'
        reads += 1
        if reads == 2:
            captured.set(); assert release.wait(2)
        return json.dumps(native)
    monkeypatch.setattr(adapter, '_jxa', fake_jxa)
    c = app.Controller(qt, state_dir=folder, hidden=True, adapter=adapter, enable_native=False)
    try:
        choose(c, 'backyard-morning'); c.apply_wallpaper(); until(qt, captured.is_set)
        c.shutdown()  # Main state has no backup yet; the worker is still in the read.
        release.set(); until(qt, lambda: not c._workers)
        assert len(writes) == 1
        assert json.loads((folder/'wallpaper-recovery.json').read_text())['wallpaper_backup']['path'] == str(original)
    finally:
        release.set(); c.shutdown()
    restored_adapter = MacDesktopAdapter(); monkeypatch.setattr(restored_adapter, '_jxa', fake_jxa)
    restored = app.Controller(qt, state_dir=folder, hidden=True, adapter=restored_adapter, enable_native=False)
    try:
        assert restored_adapter.wallpaper_backup['path'] == str(original)
        restored.restore_wallpaper(); settle(qt, restored)
        assert native['path'] == str(original) and len(writes) == 2
        assert any('此前的壁纸已还原' in note for note in restored._notes)
    finally:
        restored.shutdown()
