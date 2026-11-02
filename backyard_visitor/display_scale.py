"""One explicit mapping between artwork scene units and Qt desktop points.

Screen dimensions here are Qt logical dimensions. Device pixel ratio belongs
only to raster allocation; applying it again would double the size on Retina.
The native screen, menus and native desktop targets remain in desktop points.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .desktop import Rect

REFERENCE_WIDTH = 1440.0
REFERENCE_HEIGHT = 900.0
MIN_AUTO_SCALE = .75
MAX_AUTO_SCALE = 1.75
MIN_USER_SCALE = .75
MAX_USER_SCALE = 1.5
MIN_ARTWORK_SCALE = .6
MAX_ARTWORK_SCALE = 2.5


def _finite_number(value):
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_user_scale(value) -> float:
    """Accept finite numeric preferences, clamp their range, default bad saves."""
    number = _finite_number(value)
    return 1.0 if number is None else min(MAX_USER_SCALE, max(MIN_USER_SCALE, number))


def automatic_scale(width, height) -> float:
    """Equal-area portrait and landscape screens receive the same base scale.

    A transient unavailable screen does not invent a large or zero-size sprite;
    the controller still owns whether that screen is valid for any activity.
    """
    width, height = _finite_number(width), _finite_number(height)
    if width is None or height is None or width <= 0 or height <= 0:
        return 1.0
    ratio = math.sqrt(width / REFERENCE_WIDTH) * math.sqrt(height / REFERENCE_HEIGHT)
    return min(MAX_AUTO_SCALE, max(MIN_AUTO_SCALE, ratio))


def artwork_scale(width, height, user_factor=1.0) -> float:
    """Automatic monitor adaptation with a bounded relative user preference."""
    combined = automatic_scale(width, height) * normalize_user_scale(user_factor)
    return min(MAX_ARTWORK_SCALE, max(MIN_ARTWORK_SCALE, combined))


def _coordinate(value) -> float:
    number = _finite_number(value)
    if number is None:
        raise ValueError("Scene coordinates must be finite numbers")
    return number


@dataclass(frozen=True, slots=True)
class SceneTransform:
    """Scale about a real display origin, including a negative monitor origin.

    Scene coordinates retain that origin but have an inversely scaled viewport.
    For example a 1440-point screen at scale 1.5 gives 960 scene units of width.
    Conversion is explicit: QWidget geometry itself is never monkey-patched.
    """
    scale: float
    origin_x: float
    origin_y: float

    def __post_init__(self):
        for field in ("scale", "origin_x", "origin_y"):
            object.__setattr__(self, field, _coordinate(getattr(self, field)))
        if self.scale <= 0:
            raise ValueError("Scene scale must be positive")

    def to_display(self, x, y) -> tuple[float, float]:
        x, y = _coordinate(x), _coordinate(y)
        return (_coordinate(self.origin_x + (x-self.origin_x)*self.scale),
                _coordinate(self.origin_y + (y-self.origin_y)*self.scale))

    def to_scene(self, x, y) -> tuple[float, float]:
        x, y = _coordinate(x), _coordinate(y)
        return (_coordinate(self.origin_x + (x-self.origin_x)/self.scale),
                _coordinate(self.origin_y + (y-self.origin_y)/self.scale))

    def rect_to_display(self, rect: Rect) -> Rect:
        return self._rect(rect, to_display=True)

    def rect_to_scene(self, rect: Rect) -> Rect:
        return self._rect(rect, to_display=False)

    def _rect(self, rect: Rect, *, to_display: bool) -> Rect:
        if not isinstance(rect, Rect):
            raise ValueError("Expected a desktop Rect")
        x, y = _coordinate(rect.x), _coordinate(rect.y)
        width, height = _coordinate(rect.width), _coordinate(rect.height)
        if width < 0 or height < 0:
            raise ValueError("Rectangle extents must be nonnegative")
        x, y = self.to_display(x, y) if to_display else self.to_scene(x, y)
        ratio = self.scale if to_display else 1.0/self.scale
        return Rect(x, y, _coordinate(width*ratio), _coordinate(height*ratio))
