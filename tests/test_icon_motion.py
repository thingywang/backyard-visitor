"""No Finder permission or real desktop icon is used by these tests."""
import base64
import json
import math
from unittest.mock import Mock

import pytest

from backyard_visitor.desktop import (
    DesktopAdapter, DesktopTarget, MacDesktopAdapter, Rect, _FINDER_ICON_REPLICA,
)
from backyard_visitor.icon_motion import IconReplicaPolicy

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg=="
)


def target(identity="approved", x=100, y=100):
    return DesktopTarget(identity, "test icon", Rect(x, y, 64, 64))


@pytest.fixture
def policy():
    now = [100.0]
    return IconReplicaPolicy(clock=lambda: now[0]), now


@pytest.fixture
def adapter():
    now = [100.0]
    native = Mock()
    native.display_rects.return_value = [Rect(0, 0, 1440, 900)]
    instance = MacDesktopAdapter(native=native, clock=lambda: now[0])
    instance.icons_enabled = True
    instance._jxa = Mock(return_value=json.dumps([
        {"name": "Test icon.png", "x": 200, "y": 200, "size": 64},
    ]))
    item = instance.desktop_icons()[0]
    instance._jxa.reset_mock()
    instance._jxa.return_value = json.dumps({"x": 200, "y": 200, "png": base64.b64encode(PNG).decode()})
    return instance, item, now


def test_policy_requires_separate_opt_in_and_whitelist(policy):
    motion, now = policy
    assert not motion.begin(target(), now[0])
    assert not motion.configure(True, [])
    assert motion.configure(True, ["other"])
    assert not motion.begin(target(), now[0])
    assert motion.configure(True, ["approved"])
    assert motion.begin(target(), now[0])


def test_replica_is_bounded_short_lived_and_returns_no_restore_command(policy):
    motion, now = policy
    motion.configure(True, ["approved"])
    original = target()
    assert motion.begin(original, now[0])
    positions = []
    for step in range(65):
        now[0] = 100 + step / 100
        frame = motion.frame(original, 100)
        assert frame is not None
        assert abs(frame.rect.x - original.rect.x) <= 3
        assert frame.rect.y == original.rect.y
        assert frame.rect.width == original.rect.width
        assert 0 <= frame.opacity <= 1
        positions.append(frame.rect.x)
    assert min(positions) < original.rect.x < max(positions)
    now[0] = 100.66
    assert motion.frame(original, 100) is None
    assert original.rect == Rect(100, 100, 64, 64)


@pytest.mark.parametrize("change", ["moved", "renamed", "missing", "resized"])
def test_user_changes_cancel_replica_without_restoration(policy, change):
    motion, now = policy
    original = target()
    motion.configure(True, [original.id])
    assert motion.begin(original, now[0])
    changed = {
        "moved": target(x=101),
        "renamed": DesktopTarget(original.id, "new name", original.rect),
        "missing": None,
        "resized": DesktopTarget(original.id, original.label, Rect(100, 100, 72, 72)),
    }[change]
    assert motion.frame(changed, now[0]) is None
    assert motion.frame(original, now[0]) is None


def test_disable_and_shutdown_cancel_without_a_final_motion(policy):
    motion, now = policy
    motion.configure(True, ["approved"])
    motion.begin(target(), now[0])
    motion.configure(False, ["approved"])
    assert motion.frame(target(), now[0]) is None
    now[0] += 8
    motion.configure(True, ["approved"])
    assert motion.begin(target(), now[0])
    motion.cancel()
    assert motion.frame(target(), now[0]) is None


def test_one_replica_at_a_time_and_cooldown_survives_reconfigure(policy):
    motion, now = policy
    motion.configure(True, ["approved", "other"])
    assert motion.begin(target(), now[0])
    assert not motion.begin(target("other"), now[0])
    motion.cancel()
    motion.configure(True, ["approved"])
    assert not motion.begin(target(), now[0])
    now[0] += 8
    assert motion.begin(target(), now[0])


@pytest.mark.parametrize("stamp", [97.99, 101, math.nan, None, True])
def test_stale_or_invalid_observation_never_starts(policy, stamp):
    motion, _ = policy
    motion.configure(True, ["approved"])
    assert not motion.begin(target(), stamp)


def test_stale_observation_cancels_during_animation(policy):
    motion, now = policy
    motion.configure(True, ["approved"])
    assert motion.begin(target(), 98.1)
    now[0] += 0.2
    assert motion.frame(target(), 98.1) is None


def test_invalid_geometry_never_starts(policy):
    motion, now = policy
    motion.configure(True, ["approved"])
    for rect in (Rect(math.nan, 0, 64, 64), Rect(0, 0, 0, 64), Rect(0, 0, 1000, 64)):
        assert not motion.begin(DesktopTarget("approved", "test", rect), now[0])


def test_native_real_motion_is_not_claimed_or_enabled(adapter):
    instance, _, _ = adapter
    assert not instance.capabilities["icon_motion"]
    assert instance.capabilities["icon_replica"]
    assert not instance.icon_replica_enabled
    assert "Finder" in instance.icon_motion_limitation
    fallback = DesktopAdapter()
    fallback.icons_enabled = True
    assert not fallback.configure_icon_replica(True, ["approved"])
    assert fallback.icon_replica_png("approved") is None


def test_adapter_default_read_permission_and_whitelist_are_independent(adapter):
    instance, item, _ = adapter
    assert instance.icon_replica_png(item.id) is None
    instance._jxa.assert_not_called()
    instance.icons_enabled = False
    assert not instance.configure_icon_replica(True, [item.id])
    assert instance.icon_replica_png(item.id) is None
    instance.icons_enabled = True
    assert instance.configure_icon_replica(True, ["not-selected"])
    assert instance.icon_replica_png(item.id) is None
    instance._jxa.assert_not_called()


def test_adapter_reads_png_only_for_selected_fresh_icon(adapter):
    instance, item, _ = adapter
    assert instance.configure_icon_replica(True, [item.id])
    assert instance.icon_replica_png(item.id) == PNG
    args, kwargs = instance._jxa.call_args
    assert args[0] == _FINDER_ICON_REPLICA
    request = json.loads(args[1])
    assert request == {"name": item.label, "x": 200, "y": 200, "pixels": 128}
    assert kwargs["timeout"] == 3


def test_filename_is_data_not_script_source(adapter):
    instance, item, _ = adapter
    label = 'quote"\\name\n; finder.delete(all);'
    item = DesktopTarget(item.id, label, item.rect)
    instance._icon_snapshot[item.id] = item
    instance.configure_icon_replica(True, [item.id])
    assert instance.icon_replica_png(item.id) == PNG
    script, argument = instance._jxa.call_args.args
    assert label not in script
    assert json.loads(argument)["name"] == label


def test_stale_snapshot_and_read_cooldown_skip_native_work(adapter):
    instance, item, now = adapter
    instance.configure_icon_replica(True, [item.id])
    now[0] += 2.1
    assert instance.icon_replica_png(item.id) is None
    instance._jxa.assert_not_called()
    instance._icon_snapshot_at = now[0]
    assert instance.icon_replica_png(item.id) == PNG
    assert instance.icon_replica_png(item.id) is None
    assert instance._jxa.call_count == 1


def test_offscreen_and_ambiguous_icons_cannot_get_replicas(adapter):
    instance, item, _ = adapter
    instance.configure_icon_replica(True, [item.id])
    instance._native.display_rects.return_value = [Rect(1000, 0, 200, 200)]
    assert instance.icon_replica_png(item.id) is None
    instance._jxa.assert_not_called()
    instance._jxa.return_value = json.dumps([
        {"name": "Test icon.png", "x": 200, "y": 200, "size": 64},
        {"name": "other.png", "x": 200, "y": 200, "size": 64},
    ])
    instance.desktop_icons()
    assert instance._icon_snapshot == {}


@pytest.mark.parametrize("response", [
    None, [], {"x": 201, "y": 200, "png": base64.b64encode(PNG).decode()},
    {"x": 200, "y": 200, "png": "invalid base64"},
    {"x": 200, "y": 200, "png": base64.b64encode(b"not png").decode()},
    {"x": 200, "y": 200, "png": "A" * 262145},
])
def test_changed_or_invalid_native_reply_never_produces_replica(adapter, response):
    instance, item, _ = adapter
    instance.configure_icon_replica(True, [item.id])
    instance._jxa.return_value = json.dumps(response)
    assert instance.icon_replica_png(item.id) is None


def test_oversized_png_dimensions_are_rejected(adapter):
    instance, item, _ = adapter
    instance.configure_icon_replica(True, [item.id])
    oversized = PNG[:16] + (4000).to_bytes(4, "big") + PNG[20:]
    instance._jxa.return_value = json.dumps({"x": 200, "y": 200, "png": base64.b64encode(oversized).decode()})
    assert instance.icon_replica_png(item.id) is None


@pytest.mark.parametrize("change", ["disable", "move", "stale", "revoke"])
def test_late_worker_result_is_discarded_after_authorization_or_geometry_change(adapter, change):
    instance, item, now = adapter
    instance.configure_icon_replica(True, [item.id])
    reply = instance._jxa.return_value
    def finish(*args, **kwargs):
        if change == "disable":
            instance.configure_icon_replica(False)
        elif change == "move":
            instance._icon_snapshot[item.id] = DesktopTarget(item.id, item.label, Rect(201, 200, 64, 64))
        elif change == "stale":
            now[0] += 3
        else:
            instance.icons_enabled = False
        return reply
    instance._jxa.side_effect = finish
    assert instance.icon_replica_png(item.id) is None


def test_revocation_stops_replicas_without_requesting_permission(adapter):
    instance, item, _ = adapter
    instance.configure_icon_replica(True, [item.id])
    instance._jxa.side_effect = OSError("Automation not authorized")
    assert instance.icon_replica_png(item.id) is None
    assert not instance.icon_replica_enabled
    instance._jxa.reset_mock()
    assert instance.icon_replica_png(item.id) is None
    instance._jxa.assert_not_called()


def test_disabling_base_icon_access_also_disables_replica(adapter):
    instance, item, _ = adapter
    instance.configure_icon_replica(True, [item.id])
    instance.enable_desktop_icons(False)
    assert not instance.icon_replica_enabled
    assert instance.icon_replica_png(item.id) is None
    instance._jxa.assert_not_called()


def test_unknown_or_malformed_icon_ids_do_not_query_finder(adapter):
    instance, item, _ = adapter
    instance.configure_icon_replica(True, [item.id])
    for identity in ("unknown", None, [], {}):
        assert instance.icon_replica_png(identity) is None
    instance._jxa.assert_not_called()


def test_native_replica_script_has_no_finder_write_or_file_operation():
    assert ".desktopPosition =" not in _FINDER_ICON_REPLICA
    assert ".desktopPosition(" in _FINDER_ICON_REPLICA
    assert "finder.delete" not in _FINDER_ICON_REPLICA
    assert "finder.move" not in _FINDER_ICON_REPLICA
    assert "writeToFile" not in _FINDER_ICON_REPLICA
