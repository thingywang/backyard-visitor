"""No GUI, native windows, or Automation prompts are required by this suite."""

import ctypes
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from backyard_visitor.desktop import (
    DesktopAdapter, MacDesktopAdapter, Rect, _CoreGraphics, _DockAccessibility, make_adapter,
)


def window(number=1, **changes):
    record = {
        "kCGWindowNumber": number,
        "kCGWindowOwnerPID": 100,
        "kCGWindowOwnerName": "Example",
        "kCGWindowName": "Private document title",
        "kCGWindowLayer": 0,
        "kCGWindowIsOnscreen": True,
        "kCGWindowAlpha": 1,
        "kCGWindowBounds": {"X": 20, "Y": 40, "Width": 500, "Height": 300},
    }
    record.update(changes)
    return record


def result(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class DesktopGeometryTests(unittest.TestCase):
    def test_rect_negative_monitor_coordinates_and_exclusive_edges(self):
        rect = Rect(-1000, -500, 1000, 800)
        self.assertTrue(rect.contains(-1, 0))
        self.assertFalse(rect.contains(0, 0))
        self.assertEqual(rect.center, (-500, -100))
        self.assertFalse(rect.intersects(Rect(0, 0, 20, 20)))
        self.assertTrue(rect.intersects(Rect(-10, 0, 20, 20)))
        self.assertFalse(Rect(0, 0, float("nan"), 10).valid)

    def test_windows_keep_z_order_and_filter_unusable_geometry(self):
        native = Mock()
        native.display_rects.return_value = [Rect(0, 0, 1920, 1080), Rect(-1200, 0, 1200, 900)]
        native.window_info.return_value = [
            window(1),
            window(2, kCGWindowOwnerPID=999),
            window(3, kCGWindowLayer=20),
            window(4, kCGWindowIsOnscreen=False),
            window(5, kCGWindowAlpha=0),
            window(6, kCGWindowOwnerName="Dock"),
            window(7, kCGWindowBounds={"X": 0, "Y": 0, "Width": 20, "Height": 20}),
            window(8, kCGWindowBounds={"X": 3000, "Y": 0, "Width": 500, "Height": 300}),
            window(9, kCGWindowBounds={"X": -1100, "Y": 100, "Width": 500, "Height": 300}),
            window(10, kCGWindowBounds={"X": float("nan"), "Y": 1, "Width": 200, "Height": 300}),
            window(11, kCGWindowBounds={}),
            window(1),  # Do not expose duplicate surfaces.
        ]
        adapter = MacDesktopAdapter(native=native, own_pid=999)
        targets = adapter.windows()
        self.assertEqual([item.id for item in targets], ["mac-window-1", "mac-window-7", "mac-window-9"])
        self.assertEqual(targets[0].label, "Example")
        self.assertTrue(targets[0].supports_cat)
        self.assertFalse(targets[1].supports_cat)
        self.assertNotIn("Private", repr(targets))
        self.assertEqual(adapter.frontmost_window().id, "mac-window-1")

    def test_no_window_server_or_library_is_safe(self):
        native = Mock()
        native.window_info.side_effect = OSError("no server")
        adapter = MacDesktopAdapter(native=native)
        self.assertEqual(adapter.windows(), [])
        self.assertIn("no server", adapter.last_error)
        self.assertIsNone(adapter.frontmost_window())

    def test_non_mac_fallback_does_not_guess_system_targets(self):
        adapter = make_adapter("linux")
        self.assertIsInstance(adapter, DesktopAdapter)
        self.assertFalse(any(adapter.capabilities.values()))
        self.assertIsNone(adapter.recycle_rect())
        self.assertFalse(adapter.open_recycle())
        self.assertIsNone(adapter.current_wallpaper())


class IconPermissionTests(unittest.TestCase):
    def test_no_script_call_until_explicit_opt_in(self):
        runner = Mock()
        adapter = MacDesktopAdapter(runner=runner)
        self.assertEqual(adapter.desktop_icons(), [])
        self.assertFalse(adapter.enable_desktop_icons())
        runner.assert_not_called()

    def test_accepted_opt_in_reads_geometry_and_can_be_disabled(self):
        runner = Mock(side_effect=[
            result("15.0"),
            result(json.dumps([{"name": "a\n\"b.png", "x": 140, "y": 100, "size": 48}])),
        ])
        adapter = MacDesktopAdapter(runner=runner)
        self.assertTrue(adapter.request_icon_access())
        targets = adapter.desktop_icons()
        self.assertEqual(targets[0].label, "a\n\"b.png")
        self.assertEqual(targets[0].rect, Rect(116, 76, 48, 48))
        self.assertTrue(targets[0].id.startswith("mac-icon-"))
        self.assertFalse(adapter.enable_desktop_icons(False))
        self.assertEqual(adapter.desktop_icons(), [])
        self.assertEqual(runner.call_count, 2)

    def test_denial_or_revocation_never_reprompts_from_poll(self):
        runner = Mock(side_effect=[result("15.0"), result(stderr="Not authorized (-1743)", code=1)])
        adapter = MacDesktopAdapter(runner=runner)
        self.assertTrue(adapter.request_icon_access())
        self.assertEqual(adapter.desktop_icons(), [])
        self.assertFalse(adapter.icons_enabled)
        self.assertEqual(adapter.desktop_icons(), [])
        self.assertEqual(runner.call_count, 2)
        self.assertIn("Not authorized", adapter.last_error)

    def test_opt_in_timeout_is_bounded_and_fails_closed(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired("osascript", 30))
        adapter = MacDesktopAdapter(runner=runner)
        self.assertFalse(adapter.request_icon_access())
        self.assertFalse(adapter.icons_enabled)
        self.assertEqual(runner.call_args.kwargs["timeout"], 30)

    def test_malformed_icon_result_clears_opt_in(self):
        adapter = MacDesktopAdapter(runner=Mock(return_value=result("{}")))
        adapter.icons_enabled = True
        self.assertEqual(adapter.desktop_icons(), [])
        self.assertFalse(adapter.icons_enabled)


DISPLAY_ID = "a1234567-1234-4567-890a-123456789abc"


class WallpaperTests(unittest.TestCase):
    def test_read_wallpaper_without_automation_event(self):
        runner = Mock(return_value=result(json.dumps("/tmp/夜色.png")))
        adapter = MacDesktopAdapter(runner=runner)
        self.assertEqual(adapter.current_wallpaper(), "/tmp/夜色.png")
        script = runner.call_args.args[0][4]
        self.assertIn("NSWorkspace", script)
        self.assertNotIn("Application(", script)

    def test_missing_display_and_invalid_read_data(self):
        adapter = MacDesktopAdapter(runner=Mock(side_effect=[result('""'), result("bad data")]))
        self.assertIsNone(adapter.current_wallpaper())
        self.assertIsNone(adapter.current_wallpaper())
        self.assertTrue(adapter.last_error)

    def test_path_is_argv_and_no_shell_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cat ' $(whoami) `.png"
            original = Path(directory) / "original.png"
            path.write_bytes(b"test-image-placeholder")
            original.write_bytes(b"original-placeholder")
            runner = Mock(side_effect=[
                result(json.dumps({"path": str(original), "display_id": DISPLAY_ID, "options": {}})), result("true"),
            ])
            adapter = MacDesktopAdapter(runner=runner)
            self.assertTrue(adapter.set_wallpaper(path))
            argv = runner.call_args.args[0]
            self.assertEqual(argv[-2], str(path.resolve()))
            self.assertEqual(json.loads(argv[-1]), {"scaling": 1, "clipping": False})
            self.assertNotIn(str(path), argv[4])
            self.assertNotIn("shell", runner.call_args.kwargs)

    def test_set_failure_does_not_report_success(self):
        runner = Mock(return_value=result("false"))
        adapter = MacDesktopAdapter(runner=runner)
        self.assertFalse(adapter.set_wallpaper("/does/not/exist.png"))
        runner.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wall.png"
            path.write_bytes(b"placeholder")
            runner.side_effect = [result(json.dumps({"path": str(path), "display_id": DISPLAY_ID, "options": {}})), result("false")]
            self.assertFalse(adapter.set_wallpaper(path))
            self.assertEqual(runner.call_count, 2)
            self.assertIn("系统未能应用壁纸", adapter.last_error)

    def test_original_wallpaper_and_options_are_retained_and_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.jpg"
            preset = Path(directory) / "preset.png"
            original.write_bytes(b"original")
            preset.write_bytes(b"preset")
            snapshot = {"path": str(original), "display_id": DISPLAY_ID, "options": {
                "scaling": 3, "clipping": True, "fill": [0.1, 0.2, 0.3, 1.0],
            }}
            runner = Mock(side_effect=[result(json.dumps(snapshot)), result("true"), result("true"), result("true")])
            adapter = MacDesktopAdapter(runner=runner)
            self.assertTrue(adapter.set_wallpaper(preset))
            self.assertTrue(adapter.set_wallpaper(preset))
            self.assertEqual(adapter.wallpaper_backup, snapshot)
            self.assertTrue(adapter.restore_wallpaper())
            argv = runner.call_args.args[0]
            self.assertEqual(argv[-2], str(original))
            self.assertEqual(json.loads(argv[-1]), snapshot["options"])
            self.assertEqual(runner.call_count, 4)  # Only the first preset takes a backup.

    def test_failed_or_unusable_first_backup_never_changes_wallpaper(self):
        with tempfile.TemporaryDirectory() as directory:
            preset = Path(directory) / "preset.png"
            preset.write_bytes(b"preset")
            cases = {
                "read_oserror": OSError("simulated snapshot unavailable"),
                "read_timeout": subprocess.TimeoutExpired("simulated-jxa", 8),
                "invalid_json": result("{not-json"),
                "null_snapshot": result("null"),
                "empty_path": result(json.dumps({"path": "", "options": {}})),
                "missing_original": result(json.dumps({"path": str(Path(directory) / "missing.png"), "options": {}})),
            }
            for name, first in cases.items():
                with self.subTest(case=name):
                    runner = Mock(side_effect=[first, result("true")])
                    adapter = MacDesktopAdapter(runner=runner)
                    self.assertFalse(adapter.set_wallpaper(preset))
                    self.assertIn("尚未更改", adapter.last_error)
                    self.assertIsNone(adapter.wallpaper_backup)
                    self.assertEqual(runner.call_count, 1)  # Read only, no setter.
                    self.assertFalse(adapter.restore_wallpaper())
                    self.assertEqual(runner.call_count, 1)

    def test_invalid_backup_options_block_both_apply_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.png"
            preset = Path(directory) / "preset.png"
            original.write_bytes(b"original")
            preset.write_bytes(b"preset")
            invalid_options = [
                None, [], "invalid", {"scaling": True}, {"scaling": 1.0},
                {"scaling": -1}, {"scaling": 4}, {"clipping": 0},
                {"clipping": "false"}, {"fill": [0, 0, 1]},
                {"fill": "0001"}, {"fill": [False, 0, 0, 1]},
                {"fill": [0, 0, float("nan"), 1]},
                {"fill": [0, 0, float("inf"), 1]},
                {"fill": [-0.1, 0, 0, 1]}, {"fill": [0, 0, 0, 1.1]},
            ]
            for options in invalid_options:
                with self.subTest(options=options):
                    snapshot = {"path": str(original), "display_id": DISPLAY_ID, "options": options}
                    runner = Mock(side_effect=[result(json.dumps(snapshot)), result("true")])
                    adapter = MacDesktopAdapter(runner=runner)
                    self.assertFalse(adapter.set_wallpaper(preset))
                    self.assertIn("尚未更改", adapter.last_error)
                    self.assertIsNone(adapter.wallpaper_backup)
                    self.assertEqual(runner.call_count, 1)
                    adapter.wallpaper_backup = snapshot  # Invalid persisted input.
                    self.assertFalse(adapter.restore_wallpaper())
                    self.assertIs(adapter.wallpaper_backup, snapshot)
                    self.assertEqual(runner.call_count, 1)

    def test_existing_unusable_backup_is_not_replaced_or_used(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.png"
            preset = Path(directory) / "preset.png"
            original.write_bytes(b"original")
            preset.write_bytes(b"preset")
            snapshot = {"path": str(original), "display_id": DISPLAY_ID, "options": {}}
            runner = Mock(side_effect=[result(json.dumps(snapshot)), result("true")])
            adapter = MacDesktopAdapter(runner=runner)
            self.assertTrue(adapter.set_wallpaper(preset))
            retained = adapter.wallpaper_backup
            original.unlink()
            self.assertFalse(adapter.set_wallpaper(preset))
            self.assertIn("尚未更改", adapter.last_error)
            self.assertIs(adapter.wallpaper_backup, retained)
            self.assertFalse(adapter.restore_wallpaper())
            self.assertIs(adapter.wallpaper_backup, retained)
            self.assertEqual(runner.call_count, 2)  # No second backup or setter.

    def test_malformed_saved_backup_never_raises_or_executes_script(self):
        with tempfile.TemporaryDirectory() as directory:
            preset = Path(directory) / "preset.png"
            preset.write_bytes(b"preset")
            for snapshot in ([], True, {"path": None}, {"path": 7}, {"path": str(preset)},
                             {"path": str(preset), "options": {"other": object()}}):
                with self.subTest(snapshot_type=type(snapshot).__name__):
                    runner = Mock()
                    adapter = MacDesktopAdapter(runner=runner)
                    adapter.wallpaper_backup = snapshot
                    self.assertFalse(adapter.restore_wallpaper())
                    self.assertFalse(adapter.set_wallpaper(preset))
                    self.assertIn("尚未更改", adapter.last_error)
                    self.assertIs(adapter.wallpaper_backup, snapshot)
                    runner.assert_not_called()

    def test_omitted_default_options_and_valid_ranges_are_restorable(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.png"
            preset = Path(directory) / "preset.png"
            original.write_bytes(b"original")
            preset.write_bytes(b"preset")
            for options in ({}, {"scaling": 0}, {"scaling": 1}, {"scaling": 2},
                            {"scaling": 3}, {"clipping": False}, {"clipping": True},
                            {"fill": [0, 0.5, 1, 1]}):
                with self.subTest(options=options):
                    snapshot = {"path": str(original), "display_id": DISPLAY_ID, "options": options}
                    runner = Mock(side_effect=[result(json.dumps(snapshot)), result("true"), result("true")])
                    adapter = MacDesktopAdapter(runner=runner)
                    self.assertTrue(adapter.set_wallpaper(preset))
                    self.assertTrue(adapter.restore_wallpaper())
                    self.assertEqual(json.loads(runner.call_args.args[0][-1]), options)
                    self.assertEqual(adapter.wallpaper_backup, snapshot)

    def test_restore_without_saved_wallpaper_never_executes_script(self):
        runner = Mock()
        adapter = MacDesktopAdapter(runner=runner)
        self.assertFalse(adapter.restore_wallpaper())
        runner.assert_not_called()


class NativeOwnershipTests(unittest.TestCase):
    def test_cf_create_copy_objects_are_released_after_success(self):
        native = _CoreGraphics.__new__(_CoreGraphics)
        native.cg = Mock()
        native.cf = Mock()
        native.cg.CGWindowListCopyWindowInfo.return_value = 123
        native.cf.CFPropertyListCreateData.return_value = 456
        payload = plistlib.dumps([window()], fmt=plistlib.FMT_BINARY)
        buffer = ctypes.create_string_buffer(payload)
        native.cf.CFDataGetLength.return_value = len(payload)
        native.cf.CFDataGetBytePtr.return_value = ctypes.addressof(buffer)
        self.assertEqual(len(native.window_info()), 1)
        self.assertEqual([call.args[0] for call in native.cf.CFRelease.call_args_list], [456, 123])

    def test_cf_array_released_even_when_conversion_fails(self):
        native = _CoreGraphics.__new__(_CoreGraphics)
        native.cg = Mock()
        native.cf = Mock()
        native.cg.CGWindowListCopyWindowInfo.return_value = 123
        native.cf.CFPropertyListCreateData.return_value = None
        with self.assertRaises(OSError):
            native.window_info()
        native.cf.CFRelease.assert_called_once_with(123)




def dock_window(**changes):
    return window(90, kCGWindowOwnerName="Dock", kCGWindowOwnerPID=42,
                  kCGWindowLayer=20,
                  kCGWindowBounds={"X": 300, "Y": 950, "Width": 1000, "Height": 130}, **changes)


class RecycleAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.native = Mock()
        self.native.dock_window_level.return_value = 20
        self.native.display_rects.return_value = [Rect(0, 0, 1920, 1080)]
        self.native.window_info.return_value = [window(), dock_window()]
        self.ax = Mock()
        self.ax.trusted.return_value = True
        self.ax.trash_rect.return_value = Rect(1200, 980, 64, 64)
        self.adapter = MacDesktopAdapter(native=self.native, accessibility=self.ax, clock=lambda: self.now)

    def test_default_disabled_queries_nothing_and_declares_capability(self):
        self.assertTrue(self.adapter.capabilities["recycle_rect"])
        self.assertFalse(self.adapter.recycle_enabled)
        self.assertIsNone(self.adapter.recycle_rect())
        self.assertIsNone(self.adapter.query_recycle_rect())
        self.ax.assert_not_called()
        self.assertFalse(self.ax.method_calls)
        self.assertFalse(self.native.method_calls)

    def test_explicit_prompt_is_async_and_denial_never_reprompts(self):
        self.ax.trusted.return_value = False
        self.assertFalse(self.adapter.request_recycle_access())
        self.ax.trusted.assert_called_once_with(prompt=True)
        self.assertFalse(self.adapter.recycle_enabled)
        self.assertIn("再次启用", self.adapter.last_error)
        self.assertIsNone(self.adapter.query_recycle_rect())
        self.ax.trusted.assert_called_once()

    def test_verified_geometry_is_cached_only_and_expires(self):
        self.assertTrue(self.adapter.request_recycle_access())
        rect = self.adapter.query_recycle_rect()
        self.assertEqual(rect, Rect(1200, 980, 64, 64))
        self.ax.trash_rect.assert_called_once_with(42, [Rect(300, 950, 1000, 130)], [Rect(0, 0, 1920, 1080)])
        calls = len(self.ax.method_calls), len(self.native.method_calls)
        for _ in range(20):
            self.assertEqual(self.adapter.recycle_rect(), rect)
        self.assertEqual((len(self.ax.method_calls), len(self.native.method_calls)), calls)
        self.now += 1.1
        self.assertIsNone(self.adapter.recycle_rect())
        self.assertEqual((len(self.ax.method_calls), len(self.native.method_calls)), calls)

    def test_hidden_dock_clears_previous_cache_without_ax_tree(self):
        self.adapter.enable_recycle()
        self.adapter.query_recycle_rect()
        self.native.window_info.return_value = [window()]
        self.ax.trash_rect.reset_mock()
        self.assertIsNone(self.adapter.query_recycle_rect())
        self.assertIsNone(self.adapter.recycle_rect())
        self.ax.trash_rect.assert_not_called()

    def test_revoked_permission_stops_polls_until_explicit_reenable(self):
        self.adapter.enable_recycle()
        self.ax.trusted.return_value = False
        self.assertIsNone(self.adapter.query_recycle_rect())
        self.assertFalse(self.adapter.recycle_enabled)
        calls = self.ax.trusted.call_count
        self.assertIsNone(self.adapter.query_recycle_rect())
        self.assertEqual(self.ax.trusted.call_count, calls)
        self.ax.trash_rect.assert_not_called()

    def test_invalid_layer_and_offscreen_dock_never_traverse_ax(self):
        self.adapter.enable_recycle()
        record = dock_window()
        record["kCGWindowLayer"] = 0
        offscreen = dock_window()
        offscreen["kCGWindowBounds"] = {"X": 300, "Y": 1081, "Width": 1000, "Height": 130}
        self.native.window_info.return_value = [record, offscreen, {"kCGWindowOwnerName": "Dock"}]
        self.assertIsNone(self.adapter.query_recycle_rect())
        self.ax.trash_rect.assert_not_called()

    def test_disable_during_query_cannot_publish_old_target(self):
        self.adapter.enable_recycle()
        def finish(*args):
            self.adapter.enable_recycle(False)
            return Rect(1200, 980, 64, 64)
        self.ax.trash_rect.side_effect = finish
        self.assertIsNone(self.adapter.query_recycle_rect())
        self.assertIsNone(self.adapter.recycle_rect())

    def test_timeout_and_missing_native_service_fail_closed(self):
        self.adapter.enable_recycle()
        self.ax.trash_rect.side_effect = TimeoutError("Dock unavailable")
        self.assertIsNone(self.adapter.query_recycle_rect())
        self.assertIn("unavailable", self.adapter.last_error)
        self.assertIsNone(self.adapter.recycle_rect())
        self.ax.trusted.side_effect = OSError("no framework")
        self.assertFalse(self.adapter.request_recycle_access())
        self.assertFalse(self.adapter.recycle_enabled)

    def test_base_adapter_has_safe_optional_api(self):
        adapter = DesktopAdapter()
        self.assertFalse(adapter.request_recycle_access())
        self.assertFalse(adapter.enable_recycle())
        self.assertIsNone(adapter.query_recycle_rect())
        self.assertIsNone(adapter.recycle_rect())


class FakeDockAX(_DockAccessibility):
    def __init__(self, nodes=None, hit=4):
        self.nodes = nodes or {
            1: {"AXRole": "AXApplication", "AXChildren": [2]},
            2: {"AXRole": "AXList", "AXChildren": [3, 4, 5]},
            3: {"AXRole": "AXDockItem", "AXSubrole": "AXApplicationDockItem"},
            4: {"AXRole": "AXDockItem", "AXSubrole": "AXTrashDockItem", "AXPosition": (1200, 980), "AXSize": (64, 64)},
            5: {"pid": 999, "AXRole": "AXApplication", "AXChildren": [6]},
            6: {"pid": 999, "AXRole": "AXWindow"},
        }
        self.hit = hit
        self.released = []
        self.requests = []
        self.created = []

    def create_application(self, pid):
        self.created.append(pid)
        return 1

    def element_pid(self, element):
        return self.nodes[element].get("pid", 42)

    def attribute(self, element, name):
        self.requests.append((element, name))
        return self.nodes[element].get(name)

    def hit_test(self, root, x, y):
        self.requests.append((root, "hit_test"))
        return self.hit

    def release(self, value):
        self.released.append(value)


class DockTreeTests(unittest.TestCase):
    def locate(self, native):
        return native.trash_rect(42, [Rect(300, 950, 1000, 130)], [Rect(0, 0, 1920, 1080)])

    def test_exact_trash_subrole_and_only_dock_pid_are_inspected(self):
        native = FakeDockAX()
        self.assertEqual(self.locate(native), Rect(1200, 980, 64, 64))
        self.assertEqual(native.created, [42])
        self.assertFalse(any(element in {5, 6} for element, _ in native.requests))
        self.assertFalse(any(name in {"AXTitle", "AXValue", "AXURL"} for _, name in native.requests))
        self.assertCountEqual(native.released, [1, 2, 3, 4, 4, 5])

    def test_hidden_dock_or_item_is_never_a_drop_target(self):
        for element in (1, 2, 4):
            native = FakeDockAX()
            native.nodes[element]["AXHidden"] = True
            self.assertIsNone(self.locate(native))
            self.assertIn(1, native.released)

    def test_autohide_sliver_and_offscreen_position_fail_closed(self):
        for position, size in [((1200, 1070), (64, 64)), ((1200, 1081), (64, 64)),
                               ((1200, 980), (0, 64)), ((float("nan"), 980), (64, 64)),
                               ((1200, 980), (900, 900))]:
            native = FakeDockAX()
            native.nodes[4]["AXPosition"] = position
            native.nodes[4]["AXSize"] = size
            self.assertIsNone(self.locate(native))

    def test_hit_test_must_still_identify_trash(self):
        for hit in (None, 3, 5):
            native = FakeDockAX(hit=hit)
            self.assertIsNone(self.locate(native))
            self.assertIn(1, native.released)

    def test_negative_display_coordinates_and_magnified_size(self):
        native = FakeDockAX()
        native.nodes[4]["AXPosition"] = (-200, 700)
        native.nodes[4]["AXSize"] = (110, 110)
        rect = native.trash_rect(42, [Rect(-1000, 650, 1000, 250)], [Rect(-1200, 0, 1200, 900)])
        self.assertEqual(rect, Rect(-200, 700, 110, 110))

    def test_visible_children_empty_does_not_fall_back_to_hidden_children(self):
        native = FakeDockAX()
        native.nodes[2]["AXVisibleChildren"] = []
        self.assertIsNone(self.locate(native))
        self.assertNotIn((2, "AXChildren"), native.requests)

    def test_bounded_traversal_releases_unvisited_and_cyclic_references(self):
        nodes = {1: {"AXRole": "AXApplication", "AXChildren": list(range(2, 302))}}
        nodes.update({i: {"AXRole": "AXDockItem", "AXSubrole": "AXApplicationDockItem"} for i in range(2, 302)})
        native = FakeDockAX(nodes)
        self.assertIsNone(self.locate(native))
        self.assertEqual(len(native.released), 301)
        self.assertLessEqual(len({element for element, _ in native.requests}), 256)
        cycle = FakeDockAX({1: {"AXRole": "AXApplication", "AXChildren": [1]}})
        self.assertIsNone(self.locate(cycle))
        self.assertEqual(cycle.released, [1] * 5)

    def test_exception_releases_all_owned_elements(self):
        native = FakeDockAX()
        original = native.attribute
        def fail(element, name):
            if element == 4:
                raise TimeoutError("busy Dock")
            return original(element, name)
        native.attribute = fail
        with self.assertRaises(TimeoutError):
            self.locate(native)
        self.assertCountEqual(native.released, [1, 2, 3, 4, 5])


class AXOwnershipTests(unittest.TestCase):
    def test_attribute_strings_and_copied_value_release_on_decode(self):
        native = _DockAccessibility.__new__(_DockAccessibility)
        native.ax, native.cf = Mock(), Mock()
        native.ax.AXUIElementSetMessagingTimeout.return_value = 0
        native.cf.CFStringCreateWithCString.return_value = 100
        native.cf.CFGetTypeID.return_value = 1
        native.cf.CFStringGetTypeID.return_value = 1
        def copy(element, key, out):
            ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = 200
            return 0
        def decode(value, buffer, count, encoding):
            buffer.value = b"AXDockItem"
            return True
        native.ax.AXUIElementCopyAttributeValue.side_effect = copy
        native.cf.CFStringGetCString.side_effect = decode
        self.assertEqual(native.attribute(7, "AXRole"), "AXDockItem")
        self.assertEqual([call.args[0] for call in native.cf.CFRelease.call_args_list], [100, 200])
        native.ax.AXUIElementSetMessagingTimeout.assert_called_once_with(7, 0.1)
        with self.assertRaises(ValueError):
            native.attribute(7, "AXTitle")

    def test_permission_request_releases_dictionary_and_false_is_not_approval(self):
        native = _DockAccessibility.__new__(_DockAccessibility)
        native.ax, native.cf = Mock(), Mock()
        native.cf.CFDictionaryCreateMutable.return_value = 100
        native.ax.AXIsProcessTrustedWithOptions.return_value = False
        with patch("backyard_visitor.desktop.ctypes.c_void_p") as pointer:
            pointer.in_dll.side_effect = [10, 20]
            self.assertFalse(native.trusted(prompt=True))
        native.cf.CFDictionarySetValue.assert_called_once_with(100, 10, 20)
        native.cf.CFRelease.assert_called_once_with(100)
        native.ax.AXIsProcessTrustedWithOptions.assert_called_once_with(100)

    def test_nonprompt_trust_check_never_builds_prompt_dictionary(self):
        native = _DockAccessibility.__new__(_DockAccessibility)
        native.ax, native.cf = Mock(), Mock()
        native.ax.AXIsProcessTrusted.return_value = True
        self.assertTrue(native.trusted())
        native.cf.CFDictionaryCreateMutable.assert_not_called()
        native.ax.AXIsProcessTrustedWithOptions.assert_not_called()

if __name__ == "__main__":
    unittest.main()
