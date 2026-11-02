"""Read-only desktop geometry and explicit wallpaper operations.

Coordinates are logical desktop points, with the main display's top-left at
(0, 0). Importing this module never requests a permission or opens a window.
Native window polling is inexpensive at a low frequency, not every frame.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @property
    def valid(self) -> bool:
        return (
            all(math.isfinite(n) for n in (self.x, self.y, self.width, self.height))
            and self.width > 0
            and self.height > 0
        )

    def contains(self, x: float, y: float) -> bool:
        return self.valid and self.left <= x < self.right and self.top <= y < self.bottom

    def intersects(self, other: Rect) -> bool:
        return (
            self.valid and other.valid
            and self.left < other.right and other.left < self.right
            and self.top < other.bottom and other.top < self.bottom
        )


@dataclass(frozen=True)
class DesktopTarget:
    id: str
    label: str
    rect: Rect
    # Small native surfaces can occlude a route without offering a safe top.
    supports_cat: bool = True


LOW_HEADROOM_TOLERANCE = 12.0
TAIL_EXTENT = 56.0


def visible_window_edges(
    windows: list[DesktopTarget], screen: Rect, sprite_width: float = 144, sprite_height: float = 112,
) -> list[tuple[str, float, float, float]]:
    """Return ordinary, fully visible tops; low-headroom support is opt-in."""
    return _visible_window_supports(windows, screen, sprite_width, sprite_height,
                                    screen_rect=screen, allow_low=False)[0]


def visible_window_supports(
    windows: list[DesktopTarget], available_rect: Rect, sprite_width: float = 144, sprite_height: float = 112,
    *, screen_rect: Rect | None = None,
) -> tuple[list[tuple[str, float, float, float]], set[str]]:
    """Return native top segments and IDs suitable only for rear sitting.

    Low headroom is a geometry category, not a claim about native full-screen
    Spaces. Only near-full available-area windows qualify. The full display
    rectangle clips the visible body/tail band; menu bars are not a new floor.
    """
    return _visible_window_supports(windows, available_rect, sprite_width, sprite_height,
                                    screen_rect=screen_rect or available_rect, allow_low=True)



def visible_window_climbs(
    windows: list[DesktopTarget], available_rect: Rect,
    sprite_width: float = 144, sprite_height: float = 112,
    *, screen_rect: Rect | None = None,
) -> list[tuple[str, str, float, float, float, int]]:
    """Return unobstructed exterior sides connected to ordinary visible tops.

    Rows are ``(side_id, top_edge_id, wall_x, top, bottom, facing)``. The
    native left-facing climb has its paw line at x=88 on a 144-point canvas;
    its mirror uses x=56. This leaves only 56 points to cross when mantling
    onto a full-width top. Low-headroom tops retain their existing upper-entry
    behavior rather than acquiring a climb from the desktop floor.

    Geometry is deliberately conservative: a more-front window anywhere in
    the full climb/mantle lane rejects that side. Bounding rectangles cannot
    establish a safe route through a partially obscured side, and window
    ordering is supplied by the same front-to-back snapshot as the top edges.
    No native service or permission prompt is used here.
    """
    def usable(rect):
        return (isinstance(rect, Rect)
                and all(type(n) in (int, float) and math.isfinite(n)
                        for n in (rect.x, rect.y, rect.width, rect.height))
                and rect.width > 0 and rect.height > 0
                and math.isfinite(rect.right) and math.isfinite(rect.bottom))

    screen = screen_rect if screen_rect is not None else available_rect
    if not usable(available_rect) or not usable(screen):
        return []
    if (type(sprite_width) not in (int, float)
            or type(sprite_height) not in (int, float)
            or not math.isfinite(sprite_width) or not math.isfinite(sprite_height)
            or sprite_width <= 0 or sprite_height <= 0):
        return []
    sprite_width, sprite_height = float(sprite_width), float(sprite_height)
    cleaned, seen = [], set()
    for window in windows:
        if (not isinstance(window, DesktopTarget)
                or not isinstance(window.id, str) or not window.id
                or window.id in seen or not usable(window.rect)
                or not window.rect.intersects(available_rect)):
            continue
        seen.add(window.id)
        cleaned.append(window)
    tops, low_ids = visible_window_supports(
        cleaned, available_rect, sprite_width, sprite_height, screen_rect=screen)
    ordinary = [row for row in tops if row[0] not in low_ids]
    paw = sprite_width * (88.0 / 144.0)
    outside = sprite_width - paw
    result, foreground = [], []
    for window in cleaned:
        rect = window.rect
        bottom = min(rect.bottom, available_rect.bottom, screen.bottom)
        candidates = [row for row in ordinary
                      if row[0].startswith(f"{window.id}:top:")]
        if (window.id != "visitor-journal"
                and rect.top >= available_rect.top + sprite_height
                and bottom - rect.top >= sprite_height):
            for side, wall, facing in (("left", rect.left, 1),
                                       ("right", rect.right, -1)):
                # The entire 144-point canvas must fit, including transparent
                # padding; never invent a clipped screen edge as a wall.
                canvas_left = wall - (outside if facing > 0 else paw)
                if (canvas_left < screen.left
                        or canvas_left + sprite_width > screen.right):
                    continue
                landing_left = wall if facing > 0 else wall - sprite_width
                landing_right = landing_left + sprite_width
                top = next((row for row in candidates
                            if row[1] <= landing_left and landing_right <= row[2]
                            and (row[1] == wall if facing > 0 else row[2] == wall)), None)
                if top is None:
                    continue
                # Include the short top-out translation, not just the vertical
                # body strip, so mantling cannot cut through a front dialog.
                lane_left = min(canvas_left, landing_left)
                lane_right = max(canvas_left + sprite_width, landing_right)
                lane = Rect(lane_left, rect.top - sprite_height,
                            lane_right - lane_left,
                            bottom - rect.top + sprite_height)
                if any(lane.intersects(front) for front in foreground):
                    continue
                result.append((f"{window.id}:side:{side}", top[0], float(wall),
                               float(rect.top), float(bottom), facing))
        # Even narrow, short, journal, or low-headroom windows block later
        # candidates. They need not offer a route themselves to be occluders.
        foreground.append(rect)
    return result


def _visible_window_supports(windows, screen, sprite_width, sprite_height,
                             *, screen_rect, allow_low):
    try:
        sprite_width, sprite_height = float(sprite_width), float(sprite_height)
    except (TypeError, ValueError, OverflowError):
        return [], set()
    def usable(rect):
        return rect.valid and math.isfinite(rect.right) and math.isfinite(rect.bottom)
    if (not usable(screen) or not usable(screen_rect)
            or not math.isfinite(sprite_width) or sprite_width <= 0
            or not math.isfinite(sprite_height) or sprite_height <= 0):
        return [], set()
    result, low_ids, foreground, seen = [], set(), [], set()
    for window in windows:
        rect = window.rect
        if window.id in seen or not usable(rect) or not rect.intersects(screen):
            continue
        seen.add(window.id)
        left, right = max(rect.left, screen.left), min(rect.right, screen.right)
        top = rect.top
        low = (allow_low and screen_rect.top <= top < screen.top + sprite_height
               and all(abs(a-b) <= LOW_HEADROOM_TOLERANCE for a, b in (
                   (rect.left, screen.left), (rect.right, screen.right),
                   (rect.top, screen.top), (rect.bottom, screen.bottom))))
        normal = screen.top + sprite_height <= top < screen.bottom
        if window.supports_cat and right-left >= sprite_width and (normal or low):
            body_top = max(screen_rect.top, top-sprite_height) if low else top-sprite_height
            body_bottom = min(screen_rect.bottom, top+TAIL_EXTENT) if low else top
            blocked = sorted((max(left, front.left), min(right, front.right))
                for front in foreground
                if (front.top < body_bottom if low else front.top <= top)
                and front.bottom > body_top and front.right > left and front.left < right)
            gaps, cursor = [], left
            for blocked_left, blocked_right in blocked:
                if blocked_left > cursor:
                    gaps.append((cursor, blocked_left))
                cursor = max(cursor, blocked_right)
                if cursor >= right: break
            if cursor < right: gaps.append((cursor, right))
            for index, (gap_left, gap_right) in enumerate(gaps):
                if gap_right-gap_left >= sprite_width:
                    identity = f"{window.id}:top:{index}"
                    result.append((identity, float(gap_left), float(gap_right), float(top)))
                    if low: low_ids.add(identity)
        # Ineligible windows still occlude windows behind them.
        foreground.append(rect)
    return result, low_ids


class DesktopAdapter:
    """Safe fallback for unsupported or unavailable native desktop services.

    ``capabilities`` reports implementation support, not permission status.
    Errors are described in ``last_error`` and return an empty/false result.
    All subprocess-backed methods belong in the UI's worker, not paint events.
    """

    name = "基础桌面"

    def __init__(self) -> None:
        self.capabilities = {
            "windows": False,
            "desktop_icons": False,
            "recycle_rect": False,
            "wallpaper": False,
            "wallpaper_guarded": False,
            "icon_replica": False,
            "icon_motion": False,
        }
        self.last_error = ""
        self.icons_enabled = False
        self.recycle_enabled = False
        self.icon_replica_enabled = False
        self._icon_replica_allowed: frozenset[str] = frozenset()
        self._icon_replica_epoch = 0

    def display_awake(self, screen: Rect) -> bool | None:
        """Read-only presence signal; unknown never consumes a guest deadline."""
        return None

    def windows(self) -> list[DesktopTarget]:
        return []

    def frontmost_window(self) -> DesktopTarget | None:
        windows = self.windows()
        return windows[0] if windows else None

    def desktop_icons(self) -> list[DesktopTarget]:
        return []


    icon_motion_limitation = (
        "真实图标位移尚未启用：Finder 位置读写不是原子操作，异常恢复可能与用户拖动冲突。"
        "当前仅提供系统图标外观的短暂叠加副本，真实图标和文件不变。"
    )

    def configure_icon_replica(self, enabled: bool, allowed_ids: Iterable[str] = ()) -> bool:
        """Separate UI opt-in; does not request Automation or move any icon."""
        self._icon_replica_epoch += 1
        allowed = []
        try:
            for index, identity in enumerate(allowed_ids):
                if index >= 500:
                    break
                if isinstance(identity, str) and 0 < len(identity) <= 128:
                    allowed.append(identity)
        except TypeError:
            pass
        self._icon_replica_allowed = frozenset(allowed) if enabled is True else frozenset()
        self.icon_replica_enabled = bool(
            enabled is True and self.icons_enabled and self._icon_replica_allowed
            and self.capabilities.get("icon_replica", False)
        )
        return self.icon_replica_enabled

    def icon_replica_png(self, icon_id: str) -> bytes | None:
        """Optional worker-only read; never mutates desktop geometry."""
        return None

    def request_icon_access(self) -> bool:
        """Explicit opt-in entry point; may display an OS permission dialog."""
        return False

    def enable_desktop_icons(self, enabled: bool = True) -> bool:
        """Enable only after permission succeeds; disabling clears opt-in."""
        if not enabled:
            self.icons_enabled = False
            self.configure_icon_replica(False)
        return self.icons_enabled

    def request_recycle_access(self) -> bool:
        return False

    def enable_recycle(self, enabled: bool = True) -> bool:
        self.recycle_enabled = False
        return False

    def query_recycle_rect(self) -> Rect | None:
        return None

    def recycle_rect(self) -> Rect | None:
        # Cached-only read. The GUI must never perform native queries on drop.
        return None

    def open_recycle(self) -> bool:
        return False

    def current_wallpaper(self) -> str | None:
        return None

    def current_wallpaper_snapshot(self) -> dict[str, Any] | None:
        """Worker-only primary wallpaper identity, fit and logical display size."""
        return None

    def set_wallpaper(self, path: str | Path) -> bool:
        return False

    def restore_wallpaper(self) -> bool:
        return False

    def set_wallpaper_guarded(self, path: str | Path, expected_display_id: str,
                              expected_snapshot: dict[str, Any]) -> bool:
        """Unsupported adapters must never fall back to an unguarded write."""
        return False

    def restore_wallpaper_guarded(self, expected_display_id: str,
                                  expected_snapshot: dict[str, Any] | None = None) -> bool:
        return False


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


# Match the old QWidget canvas fill while keeping the raster buffer absent.
# This is a composited color, not NSWindow.alphaValue (which stays at 1).
COCOA_INPUT_ALPHA = 1.0 / 255.0


class _CocoaInputSurface:
    """Configure only a caller-owned Qt NSView, on the UI thread.

    A completely clear bare QWindow leaked real clicks in our native fixture.
    Give its existing layer and NSWindow a nonzero background using properties,
    not bitmap contents. This avoids a full-screen QBackingStore. Native flags
    and readback are diagnostic checks, not proof of WindowServer routing.

    Apple: Core Animation Programming Guide, Layer Style Property Animations;
    NSWindow backgroundColor / ignoresMouseEvents. No AX or screen permissions.
    """

    def __init__(self):
        self.objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self.objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self.objc.objc_getClass.restype = ctypes.c_void_p
        self.objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.objc.sel_registerName.restype = ctypes.c_void_p
        self.cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self.cg.CGColorCreateGenericRGB.argtypes = [ctypes.c_double] * 4
        self.cg.CGColorCreateGenericRGB.restype = ctypes.c_void_p
        self.cg.CGColorGetAlpha.argtypes = [ctypes.c_void_p]
        self.cg.CGColorGetAlpha.restype = ctypes.c_double
        self.cg.CGColorRelease.argtypes = [ctypes.c_void_p]
        self.cg.CGColorRelease.restype = None
        self._functions = {}

    def cls(self, name):
        result = self.objc.objc_getClass(name.encode())
        if not result:
            raise RuntimeError(f"Cocoa class unavailable: {name}")
        return result

    def send(self, obj, selector, result=ctypes.c_void_p, args=(), values=()):
        key = (result, args)
        if key not in self._functions:
            signature = ctypes.CFUNCTYPE(result, ctypes.c_void_p, ctypes.c_void_p, *args)
            self._functions[key] = signature(("objc_msgSend", self.objc))
        return self._functions[key](obj, self.objc.sel_registerName(selector.encode()), *values)

    def configure(self, view):
        if not view or not self.send(self.cls("NSThread"), "isMainThread", ctypes.c_bool):
            raise RuntimeError("Cocoa input surface requires its own NSView on the main thread")
        window = self.send(view, "window")
        if not window:
            raise RuntimeError("Cocoa input surface has no NSWindow")
        layer = self.send(view, "layer")
        if not layer:
            self.send(view, "setWantsLayer:", None, (ctypes.c_bool,), (True,))
            layer = self.send(view, "layer")
        if not layer:
            raise RuntimeError("Cocoa input surface has no CALayer")
        color = self.cg.CGColorCreateGenericRGB(0.0, 0.0, 0.0, COCOA_INPUT_ALPHA)
        if not color:
            raise RuntimeError("Could not create Cocoa input background color")
        try:
            transaction = self.cls("CATransaction")
            native_color = self.send(self.cls("NSColor"), "colorWithCGColor:",
                                     args=(ctypes.c_void_p,), values=(color,))
            if not native_color:
                raise RuntimeError("Could not create Cocoa window background color")
            self.send(transaction, "begin", None)
            try:
                self.send(transaction, "setDisableActions:", None, (ctypes.c_bool,), (True,))
                self.send(layer, "setBackgroundColor:", None, (ctypes.c_void_p,), (color,))
                self.send(window, "setOpaque:", None, (ctypes.c_bool,), (False,))
                self.send(window, "setBackgroundColor:", None, (ctypes.c_void_p,), (native_color,))
                self.send(window, "setIgnoresMouseEvents:", None, (ctypes.c_bool,), (False,))
                # Qt Tool is an NSPanel; keep it present when the app is inactive.
                selector = self.objc.sel_registerName(b"setHidesOnDeactivate:")
                if self.send(window, "respondsToSelector:", ctypes.c_bool,
                             (ctypes.c_void_p,), (selector,)):
                    self.send(window, "setHidesOnDeactivate:", None, (ctypes.c_bool,), (False,))
            finally:
                self.send(transaction, "commit", None)
        finally:
            self.cg.CGColorRelease(color)
        background = self.send(layer, "backgroundColor")
        alpha = float(self.cg.CGColorGetAlpha(background)) if background else 0.0
        ignores = bool(self.send(window, "ignoresMouseEvents", ctypes.c_bool))
        opaque = bool(self.send(window, "isOpaque", ctypes.c_bool))
        if not 0.0 < alpha <= 0.01 or ignores or opaque:
            raise RuntimeError("Cocoa input surface configuration was not retained")
        return {"layer_background_alpha": alpha, "window_opaque": opaque,
                "ignores_mouse_events": ignores,
                "layer_has_bitmap_contents": bool(self.send(layer, "contents")),
                "routing_verified": False}


def configure_cocoa_input_surface(view_id: int) -> dict:
    """Accept only the winId of our own live QWindow; call on Cocoa's UI thread.

    Color/layer ownership belongs to that window, so closing it releases the
    surface. No global hook, event injection or foreign window access is used.
    """
    if sys.platform != "darwin":
        raise RuntimeError("Cocoa input surfaces are only available on macOS")
    return _CocoaInputSurface().configure(view_id)


class _CocoaSpaces(_CocoaInputSurface):
    """Reuse the ObjC transport; never configure the input layer or focus.

    This policy applies only to caller-owned Qt NSViews on the main thread.
    Apple: NSWindow.CollectionBehavior canJoinAllSpaces/fullScreenAuxiliary.
    Native readback confirms the flags, not visibility in another app's Space.
    """

    def configure(self, view, topmost):
        if not self.send(self.cls("NSThread"), "isMainThread", ctypes.c_bool):
            raise RuntimeError("Cocoa Space policy requires the main thread")
        if not self.send(view, "isKindOfClass:", ctypes.c_bool,
                         (ctypes.c_void_p,), (self.cls("NSView"),)):
            raise RuntimeError("Cocoa Space policy requires a caller-owned NSView")
        window = self.send(view, "window")
        if not window:
            raise RuntimeError("Cocoa Space policy has no NSWindow")
        before = int(self.send(window, "collectionBehavior", ctypes.c_ulong))
        # FullScreenPrimary/Auxiliary/None are mutually exclusive. Remove our
        # Spaces/full-screen bits together, retaining unrelated valid choices
        # such as Mission Control/cycle behavior. OFF is the ordinary Window
        # policy, not the previous Tool's MoveToActiveSpace|Auxiliary (258).
        managed = 1 | 2 | 128 | 256 | 512
        target = (before & ~managed) | (1 | 256 if topmost else 128)
        changed = target != before
        if changed:
            self.send(window, "setCollectionBehavior:", None,
                      (ctypes.c_ulong,), (target,))
        actual = int(self.send(window, "collectionBehavior", ctypes.c_ulong))
        if actual != target:
            raise RuntimeError("Cocoa Space policy configuration was not retained")
        return {"topmost": topmost, "collection_behavior_before": before,
                "collection_behavior": actual, "changed": changed,
                "spaces_verified": False}


def configure_cocoa_spaces(view_id: int, topmost: bool) -> dict:
    """Set Space policy on our own live QWidget/QWindow's Cocoa NSView.

    Call on the UI thread after native creation and after Qt recreates a native
    window for a flag change; do not call every animation frame. No window is
    shown, activated, moved, or given an input/opacity/layer change here. The
    caller must supply its own valid Qt winId, never an arbitrary native pointer.
    """
    if sys.platform != "darwin":
        raise RuntimeError("Cocoa Space policies are only available on macOS")
    if isinstance(view_id, bool) or not isinstance(view_id, int) or view_id <= 0:
        raise ValueError("Cocoa Space policy requires a positive NSView address")
    if not isinstance(topmost, bool):
        raise TypeError("Cocoa Space policy topmost must be bool")
    return _CocoaSpaces().configure(view_id, topmost)


class _CocoaAccessoryApplication(_CocoaInputSurface):
    """Match the bundled LSUIElement policy without requesting activation."""

    def configure(self):
        if not self.send(self.cls("NSThread"), "isMainThread", ctypes.c_bool):
            raise RuntimeError("Cocoa application policy requires the main thread")
        application = self.send(self.cls("NSApplication"), "sharedApplication")
        if not application:
            raise RuntimeError("Cocoa application policy has no shared NSApplication")
        before = int(self.send(application, "activationPolicy", ctypes.c_long))
        changed = before != 1  # NSApplicationActivationPolicyAccessory
        if changed:
            accepted = self.send(application, "setActivationPolicy:", ctypes.c_bool,
                                 (ctypes.c_long,), (1,))
            if not accepted:
                raise RuntimeError("Cocoa accessory application policy was rejected")
        actual = int(self.send(application, "activationPolicy", ctypes.c_long))
        if actual != 1:
            raise RuntimeError("Cocoa accessory application policy was not retained")
        return {"activation_policy_before": before, "activation_policy": actual,
                "changed": changed}


def configure_cocoa_accessory_application() -> dict:
    """Configure our QApplication once on Cocoa's main thread after creation.

    The source launcher must match the frozen app's LSUIElement=True policy to
    join other apps' full-screen Spaces. This changes only our activation policy;
    it never activates/deactivates the app or touches a window, cursor, or focus.
    """
    if sys.platform != "darwin":
        raise RuntimeError("Cocoa application policies are only available on macOS")
    return _CocoaAccessoryApplication().configure()


class _CoreGraphics:
    """Small stdlib-only binding; every Copy/Create result is released."""

    def __init__(self) -> None:
        self.cg = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        self.cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self.cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        self.cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        self.cg.CGGetActiveDisplayList.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)
        ]
        self.cg.CGGetActiveDisplayList.restype = ctypes.c_int32
        self.cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        self.cg.CGDisplayBounds.restype = _CGRect
        self.cg.CGWindowLevelForKey.argtypes = [ctypes.c_int32]
        self.cg.CGWindowLevelForKey.restype = ctypes.c_int32
        self.cf.CFPropertyListCreateData.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p),
        ]
        self.cf.CFPropertyListCreateData.restype = ctypes.c_void_p
        self.cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
        self.cf.CFDataGetLength.restype = ctypes.c_long
        self.cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        self.cf.CFDataGetBytePtr.restype = ctypes.c_void_p
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.cf.CFRelease.restype = None

    def window_info(self) -> list[dict[str, Any]]:
        # On-screen, excluding desktop elements; returned in front-to-back order.
        array = self.cg.CGWindowListCopyWindowInfo(1 | 16, 0)
        if not array:
            return []
        data = None
        error = ctypes.c_void_p()
        try:
            # A binary property list avoids a large recursive Python CF bridge.
            data = self.cf.CFPropertyListCreateData(None, array, 200, 0, ctypes.byref(error))
            if not data:
                raise OSError("无法读取窗口几何信息")
            size = self.cf.CFDataGetLength(data)
            payload = ctypes.string_at(self.cf.CFDataGetBytePtr(data), size)
            records = plistlib.loads(payload)
            return records if isinstance(records, list) else []
        finally:
            if data:
                self.cf.CFRelease(data)
            if error.value:
                self.cf.CFRelease(error)
            self.cf.CFRelease(array)

    def dock_window_level(self) -> int:
        # kCGDockWindowLevelKey is enum key 7, not a hard-coded window level.
        return int(self.cg.CGWindowLevelForKey(7))

    def display_awake(self, screen: Rect) -> bool | None:
        # Public Quartz display/session reads; no capture or permissions.
        # developer.apple.com/documentation/coregraphics/cgdisplayisasleep(_:)
        self.cg.CGDisplayIsAsleep.argtypes = [ctypes.c_uint32]
        self.cg.CGDisplayIsAsleep.restype = ctypes.c_uint32
        self.cg.CGSessionCopyCurrentDictionary.argtypes = []
        self.cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        session = self.cg.CGSessionCopyCurrentDictionary()
        if not session:
            return None
        data = None
        error = ctypes.c_void_p()
        try:
            data = self.cf.CFPropertyListCreateData(None, session, 200, 0, ctypes.byref(error))
            if not data:
                return None
            size = self.cf.CFDataGetLength(data)
            record = plistlib.loads(ctypes.string_at(self.cf.CFDataGetBytePtr(data), size))
        finally:
            if data: self.cf.CFRelease(data)
            if error.value: self.cf.CFRelease(error)
            self.cf.CFRelease(session)
        if not isinstance(record, dict):
            return None
        if (record.get("CGSSessionScreenIsLocked") is True
                or record.get("kCGSessionOnConsoleKey") is False
                or record.get("kCGSessionLoginDoneKey") is False):
            return False
        # Current macOS sessions may omit the optional OnConsole field.
        # A completed current GUI login plus an awake matching display is
        # still observable; an explicit off-console/locked flag above wins.
        if (record.get("kCGSessionOnConsoleKey") is not True
                and record.get("kCGSessionLoginDoneKey") is not True):
            return None
        displays = (ctypes.c_uint32 * 32)()
        count = ctypes.c_uint32()
        if self.cg.CGGetActiveDisplayList(32, displays, ctypes.byref(count)) != 0:
            return None
        center = screen.center
        for display in displays[:min(32, count.value)]:
            native = self.cg.CGDisplayBounds(display)
            rect = Rect(native.origin.x, native.origin.y, native.size.width, native.size.height)
            if rect.contains(*center):
                return not bool(self.cg.CGDisplayIsAsleep(display))
        return False

    def display_rects(self) -> list[Rect]:
        displays = (ctypes.c_uint32 * 32)()
        count = ctypes.c_uint32()
        if self.cg.CGGetActiveDisplayList(32, displays, ctypes.byref(count)) != 0:
            return []
        result = []
        for display in displays[: count.value]:
            native = self.cg.CGDisplayBounds(display)
            rect = Rect(native.origin.x, native.origin.y, native.size.width, native.size.height)
            if rect.valid:
                result.append(rect)
        return result


class _DockAccessibility:
    """Minimal read-only AX binding, scoped to a caller-provided Dock process.

    Every Create/Copy result and retained child is released. No system-wide AX
    element, UI action, file attribute, app title or document content is read.
    """

    MAX_NODES = 256
    MAX_DEPTH = 4
    QUERY_SECONDS = 1.0
    CALL_SECONDS = 0.1
    UTF8 = 0x08000100

    def __init__(self) -> None:
        self.ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        signatures = {
            "AXIsProcessTrusted": ([], ctypes.c_bool),
            "AXIsProcessTrustedWithOptions": ([ctypes.c_void_p], ctypes.c_bool),
            "AXUIElementCreateApplication": ([ctypes.c_int], ctypes.c_void_p),
            "AXUIElementSetMessagingTimeout": ([ctypes.c_void_p, ctypes.c_float], ctypes.c_int32),
            "AXUIElementCopyAttributeValue": ([ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int32),
            "AXUIElementCopyElementAtPosition": ([ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int32),
            "AXUIElementGetPid": ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)], ctypes.c_int32),
            "AXUIElementGetTypeID": ([], ctypes.c_ulong),
            "AXValueGetTypeID": ([], ctypes.c_ulong),
            "AXValueGetValue": ([ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p], ctypes.c_bool),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.ax, name)
            function.argtypes, function.restype = arguments, result
        signatures = {
            "CFRelease": ([ctypes.c_void_p], None),
            "CFRetain": ([ctypes.c_void_p], ctypes.c_void_p),
            "CFGetTypeID": ([ctypes.c_void_p], ctypes.c_ulong),
            "CFStringGetTypeID": ([], ctypes.c_ulong),
            "CFArrayGetTypeID": ([], ctypes.c_ulong),
            "CFBooleanGetTypeID": ([], ctypes.c_ulong),
            "CFStringCreateWithCString": ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_void_p),
            "CFStringGetCString": ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32], ctypes.c_bool),
            "CFArrayGetCount": ([ctypes.c_void_p], ctypes.c_long),
            "CFArrayGetValueAtIndex": ([ctypes.c_void_p, ctypes.c_long], ctypes.c_void_p),
            "CFBooleanGetValue": ([ctypes.c_void_p], ctypes.c_bool),
            "CFDictionaryCreateMutable": ([ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p),
            "CFDictionarySetValue": ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.cf, name)
            function.argtypes, function.restype = arguments, result

    def trusted(self, prompt: bool = False) -> bool:
        if not prompt:
            return bool(self.ax.AXIsProcessTrusted())
        # These are immortal CF constants; null dictionary callbacks do not own
        # them. Use the actual exported key so pointer-based lookup is valid.
        prompt_key = ctypes.c_void_p.in_dll(self.ax, "kAXTrustedCheckOptionPrompt")
        true_value = ctypes.c_void_p.in_dll(self.cf, "kCFBooleanTrue")
        options = self.cf.CFDictionaryCreateMutable(None, 1, None, None)
        if not options:
            raise OSError("无法构造辅助功能许可请求")
        try:
            self.cf.CFDictionarySetValue(options, prompt_key, true_value)
            # Apple's prompt is asynchronous. False is never treated as approval.
            return bool(self.ax.AXIsProcessTrustedWithOptions(options))
        finally:
            self.release(options)

    def release(self, value) -> None:
        if value:
            self.cf.CFRelease(value)

    def create_application(self, pid: int):
        root = self.ax.AXUIElementCreateApplication(pid)
        if not root:
            raise OSError("无法取得 Dock 辅助功能对象")
        return root

    def element_pid(self, element) -> int | None:
        pid = ctypes.c_int()
        if self.ax.AXUIElementGetPid(element, ctypes.byref(pid)) != 0:
            return None
        return pid.value

    def _timeout(self, element) -> None:
        # AX timeouts belong to each individual element, not its descendants.
        if self.ax.AXUIElementSetMessagingTimeout(element, self.CALL_SECONDS) != 0:
            raise OSError("无法限制 Dock 查询等待时间")

    def _copy_attribute(self, element, name: str):
        self._timeout(element)
        key = self.cf.CFStringCreateWithCString(None, name.encode("ascii"), self.UTF8)
        if not key:
            raise OSError("无法读取 Dock 属性")
        value = ctypes.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(element, key, ctypes.byref(value))
        finally:
            self.release(key)
        if error != 0:
            self.release(value.value)
            if error == -25211:
                raise PermissionError("辅助功能许可不可用")
            if error == -25204:
                raise TimeoutError("Dock 暂时没有响应")
            return None
        return value.value

    def attribute(self, element, name: str):
        """Only the geometry/role fields required to identify Trash are allowed."""
        if name not in {"AXRole", "AXSubrole", "AXHidden", "AXPosition", "AXSize", "AXVisibleChildren", "AXChildren"}:
            raise ValueError("Unsupported Dock attribute")
        value = self._copy_attribute(element, name)
        if not value:
            return None
        try:
            kind = self.cf.CFGetTypeID(value)
            if name in {"AXRole", "AXSubrole"} and kind == self.cf.CFStringGetTypeID():
                buffer = ctypes.create_string_buffer(128)
                if self.cf.CFStringGetCString(value, buffer, len(buffer), self.UTF8):
                    return buffer.value.decode("utf-8")
            elif name == "AXHidden" and kind == self.cf.CFBooleanGetTypeID():
                return bool(self.cf.CFBooleanGetValue(value))
            elif name in {"AXPosition", "AXSize"} and kind == self.ax.AXValueGetTypeID():
                native = _CGPoint() if name == "AXPosition" else _CGSize()
                value_type = 1 if name == "AXPosition" else 2
                if self.ax.AXValueGetValue(value, value_type, ctypes.byref(native)):
                    return (native.x, native.y) if name == "AXPosition" else (native.width, native.height)
            elif name in {"AXVisibleChildren", "AXChildren"} and kind == self.cf.CFArrayGetTypeID():
                children = []
                for index in range(min(self.MAX_NODES, self.cf.CFArrayGetCount(value))):
                    child = self.cf.CFArrayGetValueAtIndex(value, index)
                    if child and self.cf.CFGetTypeID(child) == self.ax.AXUIElementGetTypeID():
                        self.cf.CFRetain(child)
                        children.append(child)
                return children
            return None
        finally:
            self.release(value)

    def hit_test(self, root, x: float, y: float):
        self._timeout(root)
        value = ctypes.c_void_p()
        error = self.ax.AXUIElementCopyElementAtPosition(root, x, y, ctypes.byref(value))
        if error != 0:
            self.release(value.value)
            return None
        return value.value

    def trash_rect(self, pid: int, dock_bounds: list[Rect], displays: list[Rect]) -> Rect | None:
        root = self.create_application(pid)
        # The root is kept alive for Dock-only hit tests, independently of stack.
        stack = [(root, 0, False)]
        retained_root = root
        processed = 0
        deadline = time.monotonic() + self.QUERY_SECONDS
        try:
            while stack and processed < self.MAX_NODES and time.monotonic() < deadline:
                element, depth, owned = stack.pop()
                processed += 1
                try:
                    if self.element_pid(element) != pid or self.attribute(element, "AXHidden") is True:
                        continue
                    role = self.attribute(element, "AXRole")
                    if role == "AXDockItem":
                        if self.attribute(element, "AXSubrole") != "AXTrashDockItem":
                            continue
                        position = self.attribute(element, "AXPosition")
                        size = self.attribute(element, "AXSize")
                        if position is None or size is None:
                            continue
                        rect = Rect(*position, *size)
                        if (not rect.valid or not (8 <= rect.width <= 512 and 8 <= rect.height <= 512)
                                or not any(_fully_contains(screen, rect) for screen in displays)
                                or not any(_fully_contains(dock, rect) for dock in dock_bounds)):
                            continue
                        hit = self.hit_test(root, *rect.center)
                        if not hit:
                            continue
                        try:
                            if self.element_pid(hit) == pid and self.attribute(hit, "AXSubrole") == "AXTrashDockItem":
                                return rect
                        finally:
                            self.release(hit)
                    elif role in {"AXApplication", "AXList", "AXGroup"} and depth < self.MAX_DEPTH:
                        children = self.attribute(element, "AXVisibleChildren")
                        if children is None:
                            children = self.attribute(element, "AXChildren") or []
                        remaining = max(0, self.MAX_NODES - processed - len(stack))
                        # Visit the last Dock child first: Trash normally comes last.
                        keep = children[-remaining:] if remaining else []
                        for child in children[:len(children) - len(keep)]:
                            self.release(child)
                        stack.extend((child, depth + 1, True) for child in keep)
                finally:
                    if owned:
                        self.release(element)
            return None
        finally:
            for element, _, owned in stack:
                if owned:
                    self.release(element)
            self.release(retained_root)


def _fully_contains(container: Rect, item: Rect) -> bool:
    return (container.valid and item.valid and container.left <= item.left
            and container.top <= item.top and item.right <= container.right
            and item.bottom <= container.bottom)


_FINDER_ICONS = r"""
var finder = Application('Finder');
var items = finder.desktop.items();
var size = 64;
try { size = finder.desktop.window.iconViewOptions.iconSize(); } catch (_) {}
var result = [];
for (var i = 0; i < Math.min(items.length, 500); i++) {
    try {
        var p = items[i].desktopPosition();
        result.push({name: items[i].name(), x: p[0], y: p[1], size: size});
    } catch (_) {}
}
JSON.stringify(result);
"""


# Rendering uses only NSWorkspace's system icon, not file contents or screenshots.
# NSBitmapImageFileTypePNG = 4, NSCompositingOperationSourceOver = 2.
_ICON_RENDER_FUNCTION = r"""
function renderSystemIcon(path, pixels) {
    var source = $.NSWorkspace.sharedWorkspace.iconForFile(path);
    var bitmap = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
        null, pixels, pixels, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
    if (bitmap.isNil()) { return null; }
    var context = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(bitmap);
    $.NSGraphicsContext.saveGraphicsState;
    try {
        $.NSGraphicsContext.setCurrentContext(context);
        source.drawInRectFromRectOperationFraction(
            $.NSMakeRect(0, 0, pixels, pixels), $.NSMakeRect(0, 0, 0, 0), 2, 1);
    } finally {
        $.NSGraphicsContext.restoreGraphicsState;
    }
    var png = bitmap.representationUsingTypeProperties(4, $.NSDictionary.dictionary);
    return png.isNil() ? null : ObjC.unwrap(png.base64EncodedStringWithOptions(0));
}
"""

_FINDER_ICON_REPLICA = _ICON_RENDER_FUNCTION + r"""
ObjC.import('AppKit');
function run(argv) {
    var request = JSON.parse(argv[0]);
    var finder = Application('com.apple.finder');
    var candidates = finder.desktop.items.whose({name: request.name})();
    if (candidates.length !== 1) { return 'null'; }
    var item = candidates[0];
    var position = item.desktopPosition();
    if (position[0] !== request.x || position[1] !== request.y) { return 'null'; }
    var urlText = String(item.url());
    var url = $.NSURL.URLWithString(urlText);
    if (url.isNil() || !url.isFileURL) { return 'null'; }
    var png = renderSystemIcon(ObjC.unwrap(url.path), request.pixels);
    position = item.desktopPosition();
    if (position[0] !== request.x || position[1] !== request.y || String(item.url()) !== urlText) {
        return 'null';
    }
    return JSON.stringify({x: position[0], y: position[1], png: png});
}
"""

_GET_WALLPAPER = r"""
ObjC.import('AppKit');
var screens = $.NSScreen.screens;
var path = '';
if (screens.count > 0) {
    var url = $.NSWorkspace.sharedWorkspace.desktopImageURLForScreen(screens.objectAtIndex(0));
    if (!url.isNil()) { path = ObjC.unwrap(url.path); }
}
JSON.stringify(path);
"""

# AppKit's first NSScreen is the primary desktop, not keyboard-focus mainScreen.
# ColorSync's UUID is stable identity; NSScreenNumber alone is a session ID.
# https://developer.apple.com/documentation/colorsync/cgdisplaycreateuuidfromdisplayid(_:)
_WALLPAPER_HELPERS = r"""
ObjC.import('AppKit');
ObjC.import('ColorSync');
ObjC.import('CoreFoundation');
function primaryScreen() {
    var screens = $.NSScreen.screens;
    return Number(screens.count) > 0 ? screens.objectAtIndex(0) : null;
}
function displayIdentity(screen) {
    if (!screen) { return null; }
    var number = screen.deviceDescription.objectForKey('NSScreenNumber');
    if (number.isNil()) { return null; }
    var uuid = $.CGDisplayCreateUUIDFromDisplayID(Number(ObjC.unwrap(number)));
    // C functions return JXA Ref values, not Objective-C wrapper objects.
    if (!uuid || ObjC.castRefToObject(uuid).isNil()) { return null; }
    var value = $.CFUUIDCreateString(null, uuid);
    if (!value || ObjC.castRefToObject(value).isNil()) { return null; }
    // Leave bridged references with JXA; explicit CFRelease crashes its bridge.
    // This bounded worker exits after the read/write operation.
    return String(ObjC.unwrap(ObjC.castRefToObject(value))).toLowerCase();
}
function wallpaperState(screen) {
    if (!screen) { return null; }
    var workspace = $.NSWorkspace.sharedWorkspace;
    var url = workspace.desktopImageURLForScreen(screen);
    if (url.isNil()) { return null; }
    var native = workspace.desktopImageOptionsForScreen(screen);
    var options = {};
    if (!native.isNil()) {
        var scaling = native.objectForKey($.NSWorkspaceDesktopImageScalingKey);
        var clipping = native.objectForKey($.NSWorkspaceDesktopImageAllowClippingKey);
        if (!scaling.isNil()) { options.scaling = Number(ObjC.unwrap(scaling)); }
        if (!clipping.isNil()) { options.clipping = Boolean(ObjC.unwrap(clipping)); }
        var color = native.objectForKey($.NSWorkspaceDesktopImageFillColorKey);
        if (!color.isNil()) {
            color = color.colorUsingColorSpace($.NSColorSpace.deviceRGBColorSpace);
            if (!color.isNil()) {
                options.fill = [Number(color.redComponent), Number(color.greenComponent),
                                Number(color.blueComponent), Number(color.alphaComponent)];
            }
        }
    }
    var frame = screen.frame;
    return {path: ObjC.unwrap(url.path), options: options,
            primary_size: [Number(frame.size.width), Number(frame.size.height)],
            display_id: displayIdentity(screen)};
}
function sameOptions(a, b) {
    return a.scaling === b.scaling && Boolean(a.clipping) === Boolean(b.clipping)
        && JSON.stringify(a.fill || null) === JSON.stringify(b.fill || null);
}
function sameSnapshot(a, b) {
    return a && b && a.display_id === b.display_id && a.path === b.path
        && JSON.stringify(a.primary_size) === JSON.stringify(b.primary_size)
        && sameOptions(a.options, b.options);
}
"""

_GET_WALLPAPER_STATE = _WALLPAPER_HELPERS + r"""
JSON.stringify(wallpaperState(primaryScreen()));
"""
_GET_WALLPAPER_SNAPSHOT = _GET_WALLPAPER_STATE

_SET_WALLPAPER = _WALLPAPER_HELPERS + r"""
function run(argv) {
    var expectedDisplay = argv[0];
    var expected = JSON.parse(argv[1]);
    var before = wallpaperState(primaryScreen());
    if (!before || !before.display_id || before.display_id !== expectedDisplay
            || (expected && !sameSnapshot(before, expected))) { return 'false'; }
    var state = JSON.parse(argv[3]);
    var options = $.NSMutableDictionary.dictionary;
    if (state.scaling !== undefined) {
        options.setObjectForKey($(state.scaling), $.NSWorkspaceDesktopImageScalingKey);
    }
    if (state.clipping !== undefined) {
        options.setObjectForKey($(state.clipping), $.NSWorkspaceDesktopImageAllowClippingKey);
    }
    if (state.fill && state.fill.length === 4) {
        var color = $.NSColor.colorWithDeviceRedGreenBlueAlpha(
            state.fill[0], state.fill[1], state.fill[2], state.fill[3]);
        options.setObjectForKey(color, $.NSWorkspaceDesktopImageFillColorKey);
    }
    // Recheck immediately before writing, then use this exact NSScreen object.
    // AppKit offers no compare-and-set transaction; never retarget another screen.
    var screen = primaryScreen();
    if (!sameSnapshot(wallpaperState(screen), before)) { return 'false'; }
    var error = Ref();
    var url = $.NSURL.fileURLWithPath(argv[2]);
    var ok = $.NSWorkspace.sharedWorkspace.setDesktopImageURLForScreenOptionsError(
        url, screen, options, error);
    if (!ok) { return 'false'; }
    var after = wallpaperState(primaryScreen());
    if (!after || after.display_id !== expectedDisplay || after.path !== argv[2]
            || JSON.stringify(after.primary_size) !== JSON.stringify(before.primary_size)) { return 'false'; }
    // Omitted restore options keep AppKit defaults. Explicit options must read back.
    if (state.scaling !== undefined && after.options.scaling !== state.scaling) { return 'false'; }
    if (state.clipping !== undefined && Boolean(after.options.clipping) !== state.clipping) { return 'false'; }
    if (state.fill && JSON.stringify(after.options.fill) !== JSON.stringify(state.fill)) { return 'false'; }
    return 'true';
}
"""


class MacDesktopAdapter(DesktopAdapter):
    """macOS geometry without screenshots or pyobjc.

    Optional Trash targeting uses read-only Accessibility scoped to Dock only.

    Finder icon access requires an explicit call to ``request_icon_access``.
    It reads only icon names and positions. No adapter method edits real files,
    moves Finder icons, or empties Trash.
    """

    name = "macOS"
    icon_permission_description = (
        "允许猫咪接近桌面图标：macOS 可能请求允许本应用自动化 Finder。"
        "仅用于读取图标名称和位置；不会移动、打开或删除文件。"
        "拒绝后仍可使用猫咪和窗口边缘互动。"
    )

    recycle_permission_description = (
        "可选：把虚拟礼物拖向 Dock 废纸篓。macOS 需要辅助功能许可；"
        "本功能只读取 Dock 的废纸篓位置，不读取其他应用界面，"
        "不打开、删除或清空真实文件。未授权时仍可使用应用内清理篮。"
    )
    RECYCLE_CACHE_SECONDS = 1.0

    def __init__(
        self,
        *,
        native: Any = None,
        accessibility: Any = None,
        clock: Callable[[], float] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        own_pid: int | None = None,
        wallpaper_backup_writer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__()
        self._native = native
        self._accessibility_native = accessibility
        self._clock = clock or time.monotonic
        self._recycle_cache: Rect | None = None
        self._recycle_cached_at = -math.inf
        self._runner = runner or subprocess.run
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self.wallpaper_backup: dict[str, Any] | None = None
        # Installed by the controller: write only this snapshot, never Engine
        # state from the native worker. Persistence must precede every OS write.
        self.wallpaper_backup_writer = wallpaper_backup_writer
        self.capabilities.update(windows=True, desktop_icons=True, wallpaper=True, wallpaper_guarded=True, recycle_rect=True, icon_replica=True)
        self._icon_snapshot: dict[str, DesktopTarget] = {}
        self._icon_snapshot_at = -math.inf
        self._icon_replica_last_read = -math.inf

    def _graphics(self) -> _CoreGraphics:
        if self._native is None:
            self._native = _CoreGraphics()
        return self._native

    def display_awake(self, screen: Rect) -> bool | None:
        try:
            return self._graphics().display_awake(screen)
        except (OSError, ValueError, AttributeError, TypeError, plistlib.InvalidFileException):
            return None

    def windows(self) -> list[DesktopTarget]:
        self.last_error = ""
        try:
            graphics = self._graphics()
            records = graphics.window_info()
            displays = graphics.display_rects()
        except (OSError, ValueError, plistlib.InvalidFileException) as exc:
            self.last_error = str(exc)
            return []
        result: list[DesktopTarget] = []
        seen: set[str] = set()
        for record in records:
            try:
                if int(record.get("kCGWindowOwnerPID", 0)) == self._own_pid:
                    continue
                if int(record.get("kCGWindowLayer", -1)) != 0:
                    continue
                if not record.get("kCGWindowIsOnscreen", False):
                    continue
                if float(record.get("kCGWindowAlpha", 1)) <= 0:
                    continue
                owner = str(record.get("kCGWindowOwnerName", "应用窗口"))
                if owner in {"Dock", "Window Server", "WindowManager", "loginwindow"}:
                    continue
                bounds = record["kCGWindowBounds"]
                rect = Rect(*(float(bounds[k]) for k in ("X", "Y", "Width", "Height")))
                if not rect.valid:
                    continue
                if displays and not any(rect.intersects(display) for display in displays):
                    continue
                identity = f"mac-window-{int(record['kCGWindowNumber'])}"
                if identity in seen:
                    continue
                seen.add(identity)
                # Do not expose document titles; application names suffice.
                # Keep visible small surfaces in the same z-order for occlusion.
                # The old native support cutoff remains separate from visibility.
                result.append(DesktopTarget(identity, owner, rect,
                    supports_cat=rect.width >= 100 and rect.height >= 60))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        return result

    def _accessibility(self) -> _DockAccessibility:
        if self._accessibility_native is None:
            self._accessibility_native = _DockAccessibility()
        return self._accessibility_native

    def _clear_recycle_cache(self) -> None:
        self._recycle_cache = None
        self._recycle_cached_at = -math.inf

    def request_recycle_access(self) -> bool:
        """Explicit UI opt-in only. A system prompt is not an approval result."""
        self.last_error = ""
        self._clear_recycle_cache()
        try:
            self.recycle_enabled = self._accessibility().trusted(prompt=True)
            if not self.recycle_enabled:
                self.last_error = "请在系统设置中允许辅助功能访问，然后再次启用；也可继续使用应用内清理篮"
        except (OSError, AttributeError, ValueError) as exc:
            self.last_error = str(exc)
            self.recycle_enabled = False
        return self.recycle_enabled

    def enable_recycle(self, enabled: bool = True) -> bool:
        self._clear_recycle_cache()
        self.recycle_enabled = False
        if enabled:
            try:
                self.recycle_enabled = self._accessibility().trusted(prompt=False)
            except (OSError, AttributeError, ValueError) as exc:
                self.last_error = str(exc)
        return self.recycle_enabled

    def recycle_rect(self) -> Rect | None:
        """GUI-safe cache read; never queries AX or requests permission."""
        if self.recycle_enabled and self._clock() - self._recycle_cached_at <= self.RECYCLE_CACHE_SECONDS:
            return self._recycle_cache
        return None

    def query_recycle_rect(self) -> Rect | None:
        """Worker-only refresh, restricted to a verified visible Dock process.

        Auto-hidden/offscreen/transitioning Dock items fail closed. The cached
        rectangle expires after one second, so refresh only while gift dragging.
        """
        self.last_error = ""
        self._clear_recycle_cache()
        if not self.recycle_enabled:
            return None
        try:
            accessibility = self._accessibility()
            if not accessibility.trusted(prompt=False):
                self.recycle_enabled = False
                self.last_error = "辅助功能许可已关闭，请在偏好中重新启用"
                return None
            graphics = self._graphics()
            level = graphics.dock_window_level()
            displays = graphics.display_rects()
            docks: dict[int, list[Rect]] = {}
            for record in graphics.window_info():
                try:
                    if (record.get("kCGWindowOwnerName") != "Dock"
                            or not record.get("kCGWindowIsOnscreen", False)
                            or int(record.get("kCGWindowLayer", -1)) != level
                            or float(record.get("kCGWindowAlpha", 1)) <= 0):
                        continue
                    pid = int(record["kCGWindowOwnerPID"])
                    bounds = record["kCGWindowBounds"]
                    rect = Rect(*(float(bounds[key]) for key in ("X", "Y", "Width", "Height")))
                    if pid > 0 and rect.valid and rect.width >= 8 and rect.height >= 8 and any(rect.intersects(screen) for screen in displays):
                        docks.setdefault(pid, []).append(rect)
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
            for pid, bounds in docks.items():
                rect = accessibility.trash_rect(pid, bounds, displays)
                if rect is not None and self.recycle_enabled:
                    self._recycle_cache = rect
                    self._recycle_cached_at = self._clock()
                    return rect
            return None
        except PermissionError as exc:
            self.recycle_enabled = False
            self.last_error = str(exc)
            return None
        except (OSError, AttributeError, ValueError, TypeError, plistlib.InvalidFileException) as exc:
            self.last_error = str(exc)
            return None

    def _jxa(self, script: str, *arguments: str, timeout: float = 8) -> str:
        completed = self._runner(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", script, *arguments],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        if completed.returncode != 0:
            raise OSError(completed.stderr.strip() or "macOS 桌面服务暂不可用")
        return completed.stdout.strip()

    def request_icon_access(self) -> bool:
        """Call only in response to the user's explicit icon opt-in control."""
        self.last_error = ""
        try:
            self._jxa("Application('Finder').version();", timeout=30)
            self.icons_enabled = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.last_error = str(exc)
            self.icons_enabled = False
        return self.icons_enabled

    def desktop_icons(self) -> list[DesktopTarget]:
        self._icon_snapshot = {}
        self._icon_snapshot_at = -math.inf
        if not self.icons_enabled:
            return []
        self.last_error = ""
        try:
            records = json.loads(self._jxa(_FINDER_ICONS))
            if not isinstance(records, list):
                raise ValueError("Finder 图标数据格式无效")
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            self.last_error = str(exc)
            # A revoked permission is never retried by a periodic poll.
            self.icons_enabled = False
            self.configure_icon_replica(False)
            return []
        result = []
        for record in records[:500]:
            try:
                x, y = float(record["x"]), float(record["y"])
                size = min(256, max(16, float(record.get("size", 64))))
                label = str(record["name"])
                # Finder reports desktop icon centers; its label is excluded.
                rect = Rect(x - size / 2, y - size / 2, size, size)
                if not rect.valid or x <= -30000 or y <= -30000:
                    continue
                identity = hashlib.sha256(label.encode("utf-8")).hexdigest()[:20]
                result.append(DesktopTarget(f"mac-icon-{identity}", label, rect))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        counts: dict[str, int] = {}
        centers: dict[tuple[float, float], int] = {}
        for target in result:
            counts[target.id] = counts.get(target.id, 0) + 1
            centers[target.rect.center] = centers.get(target.rect.center, 0) + 1
        self._icon_snapshot = {target.id: target for target in result
                               if counts[target.id] == 1 and centers[target.rect.center] == 1}
        self._icon_snapshot_at = self._clock()
        return result


    def icon_replica_png(self, icon_id: str) -> bytes | None:
        """Read a bounded PNG only for an opted-in, fresh, unambiguous target.

        Call in the existing desktop worker after desktop_icons(). Discard the
        result on disable, changed snapshot, or stale geometry. No file path is
        accepted from callers and no Finder setter is used.
        """
        now = self._clock()
        target = self._icon_snapshot.get(icon_id) if isinstance(icon_id, str) else None
        if (target is None or not self.icon_replica_enabled or not self.icons_enabled
                or icon_id not in self._icon_replica_allowed
                or not 0 <= now - self._icon_snapshot_at <= 2
                or now - self._icon_replica_last_read < 8):
            return None
        if not 0 < len(target.label) <= 1024:
            return None
        epoch = self._icon_replica_epoch
        observed_at = self._icon_snapshot_at
        try:
            if not any(_fully_contains(screen, target.rect) for screen in self._graphics().display_rects()):
                return None
            self._icon_replica_last_read = now
            x, y = target.rect.center
            request = {"name": target.label, "x": x, "y": y,
                       "pixels": min(128, max(16, math.ceil(max(target.rect.width, target.rect.height) * 2)))}
            value = json.loads(self._jxa(_FINDER_ICON_REPLICA, json.dumps(request), timeout=3))
            if (not isinstance(value, dict) or value.get("x") != x or value.get("y") != y
                    or not isinstance(value.get("png"), str) or len(value["png"]) > 262144):
                return None
            png = base64.b64decode(value["png"], validate=True)
            if (len(png) < 33 or len(png) > 196608 or png[:8] != b"\x89PNG\r\n\x1a\n"
                    or png[12:16] != b"IHDR"
                    or not 0 < int.from_bytes(png[16:20], "big") <= 128
                    or not 0 < int.from_bytes(png[20:24], "big") <= 128):
                return None
            if (epoch != self._icon_replica_epoch or not self.icon_replica_enabled or not self.icons_enabled
                    or icon_id not in self._icon_replica_allowed
                    or self._icon_snapshot.get(icon_id) != target
                    or not 0 <= self._clock() - observed_at <= 2):
                return None
            return png
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
            self.last_error = str(exc)
            self.configure_icon_replica(False)
            return None

    def current_wallpaper(self) -> str | None:
        """Return the primary display's current wallpaper file, when known."""
        self.last_error = ""
        try:
            value = json.loads(self._jxa(_GET_WALLPAPER))
            return value if isinstance(value, str) and value else None
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            self.last_error = str(exc)
            return None

    def current_wallpaper_snapshot(self) -> dict[str, Any] | None:
        """Worker-only read of the primary image and its current scaling options.

        This sends no Finder event, changes no wallpaper and asks for no
        permission. Missing fit/size data is unknown, never evidence that an
        old mask still matches. It does not read or alter wallpaper_backup.
        """
        self.last_error = ""
        try:
            value = json.loads(self._jxa(_GET_WALLPAPER_SNAPSHOT))
            if value is None:
                return None
            if not isinstance(value, dict):
                raise ValueError("壁纸状态数据不完整")
            path, options, size = value.get("path"), value.get("options"), value.get("primary_size")
            if (not isinstance(path, str) or not path or not isinstance(options, dict)
                    or not isinstance(size, list) or len(size) != 2
                    or not all(type(n) in (int, float) and math.isfinite(n) and 0 < n <= 100_000 for n in size)
                    or type(options.get("scaling")) is not int or options["scaling"] not in (0, 1, 2, 3)
                    or type(options.get("clipping", False)) is not bool):
                raise ValueError("壁纸状态数据不完整")
            # AppKit defines an omitted allowClipping key as false. Keep an
            # explicit invalid value unknown; only absence gets that default.
            clean_options = {"scaling": options["scaling"], "clipping": options.get("clipping", False)}
            if "fill" in options:
                fill = options["fill"]
                if (not isinstance(fill, list) or len(fill) != 4
                        or not all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in fill)):
                    raise ValueError("壁纸填充色数据无效")
                clean_options["fill"] = list(fill)
            identity = value.get("display_id")
            if identity is not None and not self._valid_display_id(identity):
                raise ValueError("主屏身份数据无效")
            return {"path": path, "options": clean_options, "primary_size": list(size),
                    "display_id": identity.lower() if identity else None}
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, OverflowError) as exc:
            self.last_error = str(exc)
            return None

    @staticmethod
    def _restorable_wallpaper_backup(backup: Any) -> bool:
        """Accept only snapshots that can be passed back to the wallpaper API."""
        if not isinstance(backup, dict):
            return False
        path, options = backup.get("path"), backup.get("options")
        if not isinstance(path, str) or not path or not isinstance(options, dict):
            return False
        try:
            if not Path(path).is_file():
                return False
            if "scaling" in options and (type(options["scaling"]) is not int
                    or options["scaling"] not in (0, 1, 2, 3)):
                return False
            if "clipping" in options and type(options["clipping"]) is not bool:
                return False
            if "fill" in options:
                fill = options["fill"]
                if (not isinstance(fill, list) or len(fill) != 4
                        or not all(type(n) in (int, float) and math.isfinite(n)
                                   and 0 <= n <= 1 for n in fill)):
                    return False
            # Preserve omitted defaults and other JSON-safe options unchanged.
            json.dumps(options, allow_nan=False)
        except (OSError, TypeError, ValueError, OverflowError):
            return False
        return True

    @staticmethod
    def _valid_display_id(value: Any) -> bool:
        # CoreGraphics/ColorSync returns a canonical CFUUID, not a transient ID.
        if not isinstance(value, str) or len(value) != 36:
            return False
        return (all(value[index] == "-" for index in (8, 13, 18, 23))
                and all(char in "0123456789abcdefABCDEF" for index, char in enumerate(value)
                        if index not in (8, 13, 18, 23)))

    @classmethod
    def _guard_snapshot(cls, snapshot: Any, display_id: Any) -> dict[str, Any] | None:
        """Validate guard data without reading any native service or local path."""
        if not cls._valid_display_id(display_id) or not isinstance(snapshot, dict):
            return None
        identity = snapshot.get("display_id")
        size, options = snapshot.get("primary_size"), snapshot.get("options")
        if (not cls._valid_display_id(identity) or identity.lower() != display_id.lower()
                or not isinstance(snapshot.get("path"), str) or not snapshot["path"]
                or not isinstance(size, list) or len(size) != 2
                or not all(type(n) in (int, float) and math.isfinite(n) and 0 < n <= 100_000 for n in size)
                or not isinstance(options, dict) or type(options.get("scaling")) is not int
                or options["scaling"] not in (0, 1, 2, 3)
                or type(options.get("clipping", False)) is not bool):
            return None
        clean_options = {"scaling": options["scaling"], "clipping": options.get("clipping", False)}
        if "fill" in options:
            fill = options["fill"]
            if (not isinstance(fill, list) or len(fill) != 4
                    or not all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in fill)):
                return None
            clean_options["fill"] = list(fill)
        return {"path": snapshot["path"], "options": clean_options,
                "primary_size": list(size), "display_id": identity.lower()}

    def _owned_wallpaper_backup(self, expected_display_id: str | None = None) -> dict[str, Any] | None:
        backup = self.wallpaper_backup
        if backup is not None and not self._restorable_wallpaper_backup(backup):
            self.last_error = "原壁纸备份不可恢复，尚未更改壁纸。"
            return None
        if backup is None:
            backup = json.loads(self._jxa(_GET_WALLPAPER_STATE))
            if not self._restorable_wallpaper_backup(backup):
                self.last_error = "无法保存可恢复的原壁纸，尚未更改壁纸。"
                return None
        identity = backup.get("display_id")
        if not self._valid_display_id(identity):
            # Legacy backup ownership is unknown. Only bind it when the user
            # has already restored that exact original image and fit themselves.
            current = json.loads(self._jxa(_GET_WALLPAPER_STATE)) if self.wallpaper_backup is not None else None
            def fit(options: dict[str, Any]) -> dict[str, Any]:
                return {**options, "clipping": options.get("clipping", False)}
            if (identity is None and self._restorable_wallpaper_backup(current)
                    and self._valid_display_id(current.get("display_id"))
                    and current.get("path") == backup["path"]
                    and isinstance(current.get("options"), dict)
                    and fit(current["options"]) == fit(backup["options"])):
                backup = {**backup, "display_id": current["display_id"].lower()}
                identity = backup["display_id"]
            else:
                self.last_error = "旧壁纸备份没有可靠的屏幕归属，尚未更改壁纸；已保留原图片，可手动恢复后重试。"
                return None
        identity = identity.lower()
        if expected_display_id is not None and identity != expected_display_id.lower():
            self.last_error = "原壁纸备份属于另一块屏幕，尚未更改壁纸。"
            return None
        if self.wallpaper_backup is None or backup is not self.wallpaper_backup:
            self.wallpaper_backup = backup
        return backup

    def _write_wallpaper(self, path: str, options: dict[str, Any], display_id: str,
                         expected_snapshot: dict[str, Any] | None) -> bool:
        if self.wallpaper_backup_writer is not None:
            try:
                # The caller has validated the original file and display owner.
                # A deep JSON copy prevents a writer from mutating retained fit.
                backup = json.loads(json.dumps(self.wallpaper_backup, allow_nan=False))
                self.wallpaper_backup_writer(backup)
            except Exception as exc:
                self.last_error = f"原壁纸备份未能持久保存，尚未更改壁纸：{exc}"
                return False
        ok = self._jxa(_SET_WALLPAPER, display_id.lower(),
                       json.dumps(expected_snapshot, allow_nan=False), path,
                       json.dumps(options, allow_nan=False)) == "true"
        if not ok:
            self.last_error = "系统未能应用壁纸或读回确认失败（主屏、当前图片或缩放可能已变化）；原备份已保留。"
        return ok

    def _apply_wallpaper(self, path: str | Path, expected_display_id: str | None,
                         expected_snapshot: dict[str, Any] | None) -> bool:
        self.last_error = ""
        try:
            image_path = Path(path).expanduser().resolve()
            if not image_path.is_file() or image_path.suffix.lower() not in {
                    ".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".bmp"}:
                self.last_error = "请选择已存在的本地壁纸图片"
                return False
            try:
                backup = self._owned_wallpaper_backup(expected_display_id)
            except (OSError, subprocess.TimeoutExpired, TypeError, ValueError, OverflowError) as exc:
                self.last_error = f"无法保存可恢复的原壁纸，尚未更改壁纸：{exc}"
                return False
            if backup is None:
                return False
            return self._write_wallpaper(str(image_path), {"scaling": 1, "clipping": False},
                                         backup["display_id"], expected_snapshot)
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError, OverflowError) as exc:
            self.last_error = f"壁纸操作未完成，尚未确认更改：{exc}"
            return False

    def set_wallpaper(self, path: str | Path) -> bool:
        """Explicit manual write, still guarded by the original backup's display."""
        return self._apply_wallpaper(path, None, None)

    def set_wallpaper_guarded(self, path: str | Path, expected_display_id: str,
                              expected_snapshot: dict[str, Any]) -> bool:
        """Worker-only auto write; compare identity/fit/size in the setter script."""
        expected = self._guard_snapshot(expected_snapshot, expected_display_id)
        if expected is None:
            self.last_error = "自动壁纸请求缺少可靠的主屏身份或完整快照，尚未更改壁纸。"
            return False
        return self._apply_wallpaper(path, expected_display_id, expected)

    def _restore_wallpaper(self, expected_display_id: str | None,
                           expected_snapshot: dict[str, Any] | None) -> bool:
        self.last_error = ""
        if self.wallpaper_backup is None:
            self.last_error = "没有可恢复的原壁纸记录"
            return False
        try:
            backup = self._owned_wallpaper_backup(expected_display_id)
            if backup is None:
                return False
            return self._write_wallpaper(backup["path"], backup["options"],
                                         backup["display_id"], expected_snapshot)
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError, OverflowError) as exc:
            self.last_error = f"壁纸恢复未完成：{exc}"
            return False

    def restore_wallpaper(self) -> bool:
        """Restore only on the saved owner display; never discard the backup."""
        return self._restore_wallpaper(None, None)

    def restore_wallpaper_guarded(self, expected_display_id: str,
                                  expected_snapshot: dict[str, Any] | None = None) -> bool:
        if not self._valid_display_id(expected_display_id):
            self.last_error = "壁纸恢复请求缺少可靠的主屏身份"
            return False
        expected = None
        if expected_snapshot is not None:
            expected = self._guard_snapshot(expected_snapshot, expected_display_id)
            if expected is None:
                self.last_error = "壁纸恢复请求缺少完整快照"
                return False
        return self._restore_wallpaper(expected_display_id, expected)


def make_adapter(platform: str | None = None) -> DesktopAdapter:
    """Construct lazily so tests/imports never require a graphical session."""
    if (platform or sys.platform) == "darwin":
        return MacDesktopAdapter()
    return DesktopAdapter()
