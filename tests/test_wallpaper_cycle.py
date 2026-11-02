"""Local wall time and latest-only intent: no real clocks or wallpaper APIs."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from backyard_visitor.wallpaper_cycle import CyclePolicy, phase_for_local, target_scene


def catalog():
    scenes = {}
    for theme, orientation in (('cat-tree', 'portrait'), ('cat-tree', 'landscape'), ('classic', 'landscape')):
        pair = {phase: f'{theme}-{orientation}-{phase}' for phase in ('morning', 'dusk')}
        for phase, scene_id in pair.items():
            scenes[scene_id] = {'id': scene_id, 'theme_id': theme, 'orientation': orientation,
                               'phase': phase, 'cycle_pair': pair, 'cycle_ready': True}
    return scenes


def local(hour, minute=0, *, day=1, tz=timezone(timedelta(hours=8))):
    return datetime(2026, 11, day, hour, minute, tzinfo=tz)


def enabled(*, confirmed=None):
    policy = CyclePolicy(catalog())
    policy.enable('cat-tree', 'portrait', 'primary-A', confirmed)
    return policy


def ready(*, confirmed=None):
    policy = enabled(confirmed=confirmed)
    assert policy.observe(confirmed, 'primary-A') == 'ready'
    return policy


@pytest.mark.parametrize('hour,minute,expected', [(0, 0, 'dusk'), (5, 59, 'dusk'),
    (6, 0, 'morning'), (17, 59, 'morning'), (18, 0, 'dusk'), (23, 59, 'dusk')])
def test_local_boundaries(hour, minute, expected):
    assert phase_for_local(local(hour, minute)) == expected
    assert phase_for_local(datetime(2026, 11, 1, hour, minute)) == expected


def test_timezone_or_wall_clock_change_recomputes_current_phase():
    clock = datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc)
    assert phase_for_local(clock) == 'dusk'
    assert phase_for_local(clock.astimezone(timezone(timedelta(hours=8)))) == 'morning'
    assert phase_for_local(clock.replace(hour=18)) == 'dusk'
    with pytest.raises(TypeError): phase_for_local(600)


@pytest.mark.parametrize('theme,orientation', [('cat-tree', 'portrait'), ('cat-tree', 'landscape'), ('classic', 'landscape')])
def test_targets_support_both_tree_orientations_and_classic(theme, orientation):
    assert target_scene(catalog(), theme, orientation, local(6)) == f'{theme}-{orientation}-morning'
    assert target_scene(catalog(), theme, orientation, local(18)) == f'{theme}-{orientation}-dusk'
    assert target_scene(catalog(), 'unknown', orientation, local(6)) is None


def test_invalid_incomplete_or_ambiguous_pair_produces_no_target():
    scenes = catalog(); del scenes['cat-tree-portrait-dusk']
    assert target_scene(scenes, 'cat-tree', 'portrait', local(6)) is None
    scenes = catalog(); scenes['duplicate'] = dict(scenes['cat-tree-portrait-morning'])
    assert target_scene(scenes, 'cat-tree', 'portrait', local(6)) is None
    scenes = catalog(); scenes['cat-tree-portrait-dusk']['orientation'] = 'landscape'
    assert target_scene(scenes, 'cat-tree', 'portrait', local(6)) is None


def test_default_preview_and_unknown_snapshot_never_request_a_write():
    policy = CyclePolicy(catalog())
    assert not policy.enabled and policy.plan(local(6)) is None
    target_scene(catalog(), 'cat-tree', 'portrait', local(6))
    assert policy.export()['enabled'] is False
    policy.enable('cat-tree', 'portrait', 'primary-A')
    assert policy.plan(local(6)) is None
    assert policy.observe(None, None, known=False) == 'wait'
    assert policy.plan(local(6)) is None and policy.enabled
    assert policy.observe(None, 'primary-A') == 'ready'
    assert policy.plan(local(6)) is not None


def test_repeated_ticks_share_one_latest_request_and_confirm_only_once():
    policy = ready()
    request = policy.plan(local(6))
    for minute in range(1, 10): assert policy.plan(local(6, minute)) is request
    assert policy.confirmed_scene_id is None
    assert not policy.confirm(request, request.scene_id, 'primary-B')
    assert policy.confirmed_scene_id is None
    assert policy.confirm(request, request.scene_id, 'primary-A')
    assert not policy.confirm(request, request.scene_id, 'primary-A')
    assert policy.plan(local(10)) is None


def test_sleep_across_multiple_boundaries_applies_only_present_target():
    policy = ready()
    morning = policy.plan(local(6)); assert policy.confirm(morning, morning.scene_id, 'primary-A')
    assert policy.plan(local(12, day=4)) is None
    dusk = policy.plan(local(20, day=7))
    assert dusk.scene_id.endswith('-dusk')
    assert policy.pending == dusk


def test_latest_target_invalidates_old_callback_and_does_not_publish_it():
    policy = ready()
    morning = policy.plan(local(17, 59))
    dusk = policy.plan(local(18))
    assert dusk.epoch > morning.epoch and policy.pending is dusk
    assert not policy.confirm(morning, morning.scene_id, 'primary-A')
    assert policy.confirmed_scene_id is None
    assert policy.confirm(dusk, dusk.scene_id, 'primary-A')


def test_clock_return_to_confirmed_scene_cancels_old_target_then_reconciles_readback():
    policy = ready(confirmed='cat-tree-portrait-morning')
    pending = policy.plan(local(18))
    assert policy.plan(local(10)) is None and not policy.is_current(pending)
    # A worker may have physically written before its stale callback arrived.
    assert policy.observe('cat-tree-portrait-dusk', 'primary-A') == 'ready'
    latest = policy.plan(local(10))
    assert latest.scene_id == 'cat-tree-portrait-morning'


def test_disable_clears_pending_without_restoring_or_replaying_wallpaper():
    policy = ready(confirmed='cat-tree-portrait-morning')
    request = policy.plan(local(18)); policy.disable()
    assert policy.pending is None and not policy.enabled
    assert policy.confirmed_scene_id == 'cat-tree-portrait-morning'
    assert policy.plan(local(18)) is None
    assert not policy.confirm(request, request.scene_id, 'primary-A')


@pytest.mark.parametrize('kind', ['external', 'display'])
def test_external_change_or_primary_replacement_pauses_and_rejects_late_result(kind):
    policy = ready(confirmed='cat-tree-portrait-morning')
    request = policy.plan(local(18))
    assert policy.observe(None if kind == 'external' else 'cat-tree-portrait-morning',
                          'primary-A' if kind == 'external' else 'primary-B') == 'paused'
    assert not policy.enabled and policy.pending is None
    assert policy.pause_reason == ('external_change' if kind == 'external' else 'display_changed')
    assert not policy.confirm(request, request.scene_id, 'primary-A')
    assert policy.observe(None, 'primary-A') == 'disabled'


def test_unknown_snapshot_holds_pending_and_does_not_invent_success():
    policy = ready(); request = policy.plan(local(6))
    assert policy.observe(None, None, known=False) == 'wait'
    assert policy.pending == request and policy.confirmed_scene_id is None
    assert policy.plan(local(18)) is None
    policy.observe(None, 'primary-A')
    assert policy.plan(local(18)).scene_id.endswith('-dusk')


def test_failed_write_retains_confirmed_scene_and_can_retry_latest_time():
    policy = ready(confirmed='cat-tree-portrait-morning')
    request = policy.plan(local(18)); policy.fail(request)
    assert policy.confirmed_scene_id == 'cat-tree-portrait-morning' and policy.pending is None
    retry = policy.plan(local(20))
    assert retry.epoch > request.epoch
    policy.fail(request)
    assert policy.pending == retry


def test_restart_requires_verified_authorized_pair_and_same_display():
    policy = ready(); request = policy.plan(local(6))
    policy.confirm(request, request.scene_id, 'primary-A')
    saved = json.loads(json.dumps(policy.export()))
    restored = CyclePolicy(catalog(), saved)
    assert restored.enabled and restored.plan(local(18)) is None
    assert restored.observe(None, None, known=False) == 'wait'
    assert restored.observe('cat-tree-portrait-morning', 'primary-A') == 'ready'
    assert restored.plan(local(18)).scene_id.endswith('-dusk')
    for current, display in ((None, 'primary-A'), ('classic-landscape-morning', 'primary-A'),
                             ('cat-tree-portrait-morning', 'primary-B')):
        other = CyclePolicy(catalog(), saved)
        assert other.observe(current, display) == 'paused'
        assert not other.enabled


def test_restart_of_enabled_but_never_confirmed_session_cannot_overwrite_external_picture():
    policy = ready(); policy.plan(local(6))
    restored = CyclePolicy(catalog(), policy.export())
    assert restored.observe(None, 'primary-A') == 'paused'
    assert restored.plan(local(6)) is None


@pytest.mark.parametrize('bad', [None, [], {}, {'schema_version': True, 'enabled': True},
    {'schema_version': 1, 'enabled': 1},
    {'schema_version': 1, 'enabled': True, 'theme_id': 'cat-tree', 'orientation': 'portrait', 'display_id': None}])
def test_bad_persistent_state_is_disabled(bad):
    policy = CyclePolicy(catalog(), bad)
    assert not policy.enabled and policy.plan(local(6)) is None


def test_new_explicit_enable_invalidates_prior_theme_request():
    policy = ready(); old = policy.plan(local(6))
    policy.enable('classic', 'landscape', 'primary-A')
    assert not policy.confirm(old, old.scene_id, 'primary-A')
    policy.observe(None, 'primary-A')
    assert policy.plan(local(6)).scene_id == 'classic-landscape-morning'
    assert set(policy.export()) == {'schema_version', 'enabled', 'theme_id', 'orientation', 'display_id', 'confirmed_scene_id'}
