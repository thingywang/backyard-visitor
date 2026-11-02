"""Conservative desktop visibility guards for calibrated tree excursions."""
from __future__ import annotations

from .desktop import Rect
from .depth_context import _point, _rect


def tree_swept_rect(layout, current_foot, *, return_points=()):
    """Cover the approach, all platforms and their jumping/head clearance.

    This deliberately rejects a partly covered route rather than pretending
    an ordinary application window is transparent background scenery.
    """
    screen = _rect(layout.screen_rect)
    start = _point(current_foot)
    supports = layout.supports
    scale = layout.scale
    points = [start, _point(layout.entry), _point(layout.approach)]
    points.extend(_point(point) for point in return_points)
    points.extend((x, support.y) for support in supports
                  for x in (support.left, support.right))
    left = max(screen.left, min(x for x, _ in points)-72)
    right = min(screen.right, max(x for x, _ in points)+72)
    top = max(screen.top, min(y for _, y in points)-220*scale)
    bottom = min(screen.bottom, max(y for _, y in points)+4)
    return Rect(left, top, right-left, bottom-top)


def tree_route_blocked(layout, current_foot, blockers, *, return_points=()):
    try:
        if not isinstance(blockers, (list, tuple)):
            return True
        area = tree_swept_rect(layout, current_foot, return_points=return_points)
        if not area.valid:
            return True
        for value in blockers:
            rect = _rect(value)
            if (area.left <= rect.right and rect.left <= area.right
                    and area.top <= rect.bottom and rect.top <= area.bottom):
                return True
        return False
    except (AttributeError, ValueError, TypeError, OverflowError):
        return True
