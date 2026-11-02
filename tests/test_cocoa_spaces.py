"""Space policy contracts without loading Cocoa, permissions, or Qt windows."""
import ctypes

import pytest

from backyard_visitor import desktop


class Bridge(desktop._CocoaSpaces):
    def __init__(self, flags=258, *, main=True, is_view=True, window=True, retain=True):
        self.flags = flags
        self.main = main
        self.is_view = is_view
        self.window = 22 if window else None
        self.retain = retain
        self.calls = []

    def cls(self, name):
        return {"NSThread": 1, "NSView": 2}[name]

    def send(self, obj, selector, result=ctypes.c_void_p, args=(), values=()):
        self.calls.append((obj, selector, result, args, values))
        if selector == "isMainThread":
            assert obj == 1 and result is ctypes.c_bool
            return self.main
        if selector == "isKindOfClass:":
            assert obj == 11 and result is ctypes.c_bool
            assert args == (ctypes.c_void_p,) and values == (2,)
            return self.is_view
        if selector == "window":
            assert obj == 11
            return self.window
        if selector == "collectionBehavior":
            assert obj == 22 and result is ctypes.c_ulong
            return self.flags
        if selector == "setCollectionBehavior:":
            assert obj == 22 and result is None
            assert args == (ctypes.c_ulong,)
            if self.retain:
                self.flags = values[0]
            return None
        raise AssertionError(f"Unexpected native call: {selector}")

    @property
    def writes(self):
        return [row for row in self.calls if row[1] == "setCollectionBehavior:"]


@pytest.mark.parametrize("old", [258, 128, 0, 512, 1 | 2 | 128 | 256 | 512])
def test_on_replaces_space_and_mutually_exclusive_fullscreen_flags(old):
    bridge = Bridge(old)
    result = bridge.configure(11, True)
    assert result == {"topmost": True, "collection_behavior_before": old,
                      "collection_behavior": 257, "changed": old != 257,
                      "spaces_verified": False}
    assert bridge.flags == 257


@pytest.mark.parametrize("old", [258, 257, 512, 128])
def test_off_returns_normal_window_policy_not_previous_tool_policy(old):
    bridge = Bridge(old)
    result = bridge.configure(11, False)
    assert result["collection_behavior"] == 128
    assert result["changed"] == (old != 128)
    assert result["spaces_verified"] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_preserves_unrelated_valid_mission_control_and_cycle_flags(enabled):
    unrelated = 8 | 64 | 4096
    bridge = Bridge(258 | unrelated)
    result = bridge.configure(11, enabled)
    assert result["collection_behavior"] == unrelated | (257 if enabled else 128)


def test_on_off_on_is_idempotent_and_does_not_touch_input_or_focus():
    bridge = Bridge()
    for enabled, expected in [(True, 257), (True, 257), (False, 128), (False, 128), (True, 257)]:
        bridge.configure(11, enabled)
        assert bridge.flags == expected
    assert [row[4] for row in bridge.writes] == [(257,), (128,), (257,)]
    # Any call outside this small allowlist fails inside the fake bridge.
    assert {row[1] for row in bridge.calls} == {
        "isMainThread", "isKindOfClass:", "window", "collectionBehavior", "setCollectionBehavior:"}


@pytest.mark.parametrize("kwargs, message", [
    ({"main": False}, "main thread"),
    ({"is_view": False}, "caller-owned NSView"),
    ({"window": False}, "no NSWindow"),
])
def test_invalid_native_context_fails_before_writing(kwargs, message):
    bridge = Bridge(**kwargs)
    with pytest.raises(RuntimeError, match=message):
        bridge.configure(11, True)
    assert not bridge.writes


def test_readback_mismatch_is_an_error_not_success():
    bridge = Bridge(retain=False)
    with pytest.raises(RuntimeError, match="not retained"):
        bridge.configure(11, True)
    assert len(bridge.writes) == 1


def test_public_entry_uses_only_the_independent_policy(monkeypatch):
    bridge = Bridge()
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop, "_CocoaSpaces", lambda: bridge)
    monkeypatch.setattr(desktop, "configure_cocoa_input_surface",
                        lambda *args: pytest.fail("Space policy must not modify input surface"))
    assert desktop.configure_cocoa_spaces(11, True)["collection_behavior"] == 257
    assert desktop.configure_cocoa_spaces(11, False)["collection_behavior"] == 128


@pytest.mark.parametrize("platform, view, enabled, error", [
    ("linux", 11, True, RuntimeError),
    ("darwin", 0, True, ValueError),
    ("darwin", -1, True, ValueError),
    ("darwin", None, True, ValueError),
    ("darwin", True, True, ValueError),
    ("darwin", "11", True, ValueError),
    ("darwin", 11, 1, TypeError),
])
def test_invalid_public_arguments_never_load_native_bridge(monkeypatch, platform, view, enabled, error):
    monkeypatch.setattr(desktop.sys, "platform", platform)
    monkeypatch.setattr(desktop, "_CocoaSpaces", lambda: pytest.fail("Must not load native bridge"))
    with pytest.raises(error):
        desktop.configure_cocoa_spaces(view, enabled)


class ApplicationBridge(desktop._CocoaAccessoryApplication):
    def __init__(self, policy=0, *, main=True, application=True, accept=True, retain=True):
        self.policy, self.main = policy, main
        self.application = 44 if application else None
        self.accept, self.retain = accept, retain
        self.calls = []

    def cls(self, name):
        return {"NSThread": 1, "NSApplication": 3}[name]

    def send(self, obj, selector, result=ctypes.c_void_p, args=(), values=()):
        self.calls.append(selector)
        if selector == "isMainThread":
            assert obj == 1 and result is ctypes.c_bool
            return self.main
        if selector == "sharedApplication":
            assert obj == 3
            return self.application
        if selector == "activationPolicy":
            assert obj == 44 and result is ctypes.c_long
            return self.policy
        if selector == "setActivationPolicy:":
            assert obj == 44 and result is ctypes.c_bool
            assert args == (ctypes.c_long,) and values == (1,)
            if self.accept and self.retain:
                self.policy = 1
            return self.accept
        raise AssertionError(f"Unexpected native call: {selector}")


@pytest.mark.parametrize("initial", [0, 1, 2])
def test_accessory_policy_readback_and_idempotence(initial):
    bridge = ApplicationBridge(initial)
    first = bridge.configure()
    assert first == {"activation_policy_before": initial, "activation_policy": 1,
                     "changed": initial != 1}
    assert bridge.configure() == {"activation_policy_before": 1, "activation_policy": 1,
                                  "changed": False}
    assert bridge.calls.count("setActivationPolicy:") == int(initial != 1)
    assert set(bridge.calls) <= {"isMainThread", "sharedApplication", "activationPolicy", "setActivationPolicy:"}


@pytest.mark.parametrize("kwargs, message", [
    ({"main": False}, "main thread"),
    ({"application": False}, "no shared NSApplication"),
    ({"accept": False}, "rejected"),
    ({"retain": False}, "not retained"),
])
def test_accessory_policy_failure_is_not_silent_success(kwargs, message):
    bridge = ApplicationBridge(**kwargs)
    with pytest.raises(RuntimeError, match=message):
        bridge.configure()
    if not kwargs.get("main", True) or not kwargs.get("application", True):
        assert "setActivationPolicy:" not in bridge.calls


def test_accessory_public_entry_uses_own_application_only(monkeypatch):
    bridge = ApplicationBridge()
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop, "_CocoaAccessoryApplication", lambda: bridge)
    assert desktop.configure_cocoa_accessory_application()["activation_policy"] == 1


def test_accessory_non_macos_does_not_load_native_bridge(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop, "_CocoaAccessoryApplication", lambda: pytest.fail("Must not load Cocoa"))
    with pytest.raises(RuntimeError, match="only available on macOS"):
        desktop.configure_cocoa_accessory_application()
