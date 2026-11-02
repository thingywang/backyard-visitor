"""Pure geometry guards for explicitly enabled, verified stretched wallpapers.

No desktop query, window action, Qt dependency or permission request belongs here.
The shared production DepthRoute supplies corridor and layer-handoff validation.
"""
from __future__ import annotations

import math
from .desktop import Rect
from .depth_visit import DepthRoute


def _rect(value):
    if isinstance(value, Rect):
        values = (value.x, value.y, value.width, value.height)
    elif isinstance(value, (tuple, list)) and len(value) == 4:
        values = value
    else:
        raise ValueError('Expected a finite rectangle')
    if not all(type(n) in (int, float) and math.isfinite(n) and abs(n) <= 100_000 for n in values):
        raise ValueError('Expected a finite rectangle')
    rect = Rect(*(float(n) for n in values))
    if not rect.valid or not math.isfinite(rect.right) or not math.isfinite(rect.bottom):
        raise ValueError('Expected a positive rectangle')
    return rect


def _point(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or not all(type(n) in (int, float) and math.isfinite(n) for n in value)):
        raise ValueError('Expected finite point coordinates')
    return tuple(float(n) for n in value)


def map_foot(normalized_xy, fullscreen_xywh):
    """Map image-normalized coordinates to global logical screen points."""
    x, y = _point(normalized_xy)
    screen = _rect(fullscreen_xywh)
    return screen.x + x * screen.width, screen.y + y * screen.height


def unmap_foot(global_xy, fullscreen_xywh):
    """Inverse of map_foot; neither work-area offsets nor DPR are applied."""
    x, y = _point(global_xy)
    screen = _rect(fullscreen_xywh)
    return (x-screen.x)/screen.width, (y-screen.y)/screen.height


def build_depth_route(scene, fullscreen_xywh, current_global_foot):
    """Return a validated fixed-corridor route, or None when it cannot fit.

    The front approach stays at the current foot's height. Wallpaper masks and
    all normalized route points share the full-screen stretch transform. The
    cat remains 144x112 logical pixels at scale 1, on any display size or DPR.
    """
    try:
        if not isinstance(scene, dict) or scene.get('fit') != 'stretch':
            return None
        screen = _rect(fullscreen_xywh)
        if screen.width < 144 or screen.height < 112:
            return None
        start = unmap_foot(current_global_foot, screen)
        regions = scene.get('occluders')
        if not isinstance(regions, (tuple, list)) or not 1 <= len(regions) <= 32:
            return None
        polygons = tuple(region['polygon'] for region in regions)
        return DepthRoute(start=start, near=(.60, start[1]), portal=(.72, .82), hide=(.91, .82),
                          polygons=polygons, cat_width=144/screen.width,
                          aspect_ratio=screen.width/screen.height)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def route_swept_rect(route, fullscreen_xywh):
    """Conservative global AABB of every full canvas along the round trip.

    Each leg linearly interpolates foot and scale with the same easing scalar,
    so each canvas corner lies between its endpoint positions. The endpoint
    union bounds the entire sweep, including reverse travel and stationary
    turns; it deliberately also blocks some empty space between route legs.
    """
    if not isinstance(route, DepthRoute):
        raise ValueError('Expected a validated DepthRoute')
    screen = _rect(fullscreen_xywh)
    corners = [map_foot(point, screen)
               for foot, scale in ((route.start, 1.), (route.near, 1.),
                                   (route.portal, route.far_scale), (route.hide, route.far_scale))
               for point in route.footprint(foot, scale)]
    left, right = min(x for x, _ in corners), max(x for x, _ in corners)
    top, bottom = min(y for _, y in corners), max(y for _, y in corners)
    return Rect(left, top, right-left, bottom-top)


def route_blocked(route, fullscreen_xywh, blockers):
    """Touching any ordinary-window/journal rectangle blocks the whole route.

    Unknown geometry fails closed. Callers exclude companion overlays and pass
    only current visible blockers; an empty list is safe only after a fresh,
    successful native query, a freshness decision owned by the controller.
    """
    try:
        sweep = route_swept_rect(route, fullscreen_xywh)
        if not isinstance(blockers, (tuple, list)):
            return True
        for value in blockers:
            rect = _rect(value)
            if (sweep.left <= rect.right and rect.left <= sweep.right
                    and sweep.top <= rect.bottom and rect.top <= sweep.bottom):
                return True
        return False
    except (ValueError, TypeError, OverflowError):
        return True


def escape_blocked(global_foot, scale, facing, fullscreen_xywh, blockers):
    """Cover the remaining horizontal escape, including past the old route.

    A new window cancels scenery clipping, never the urgent run itself.
    Only the part on the current selected screen can hide scenery.
    """
    try:
        screen = _rect(fullscreen_xywh)
        x, y = _point(global_foot)
        if (type(scale) not in (int, float) or not math.isfinite(scale)
                or not 0 < scale <= 1 or type(facing) is not int or facing not in (-1, 1)
                or not isinstance(blockers, (tuple, list))):
            return True
        left, right = x - 72*scale, x + 72*scale
        if facing < 0:
            left = min(left, screen.left)
        else:
            right = max(right, screen.right)
        top, bottom = y - 108*scale, y + 4*scale
        for value in blockers:
            rect = _rect(value)
            if left <= rect.right and rect.left <= right and top <= rect.bottom and rect.top <= bottom:
                return True
        return False
    except (ValueError, TypeError, OverflowError):
        return True
