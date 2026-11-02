"""Transient window-side routes with physical wall contact and safe interruption.

The left-facing artwork touches its wall at x=88, y=24..96 in the unchanged
144x112 logical canvas. Its mirrored contact is x=56. This leaves a 56px
mantle hop, within the existing short-horizontal-jump contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

WIDTH, HEIGHT = 144.0, 112.0
PAW_X = 88.0
PAW_TOP, PAW_BOTTOM = 24.0, 96.0
CLIMB_SPEED = 190.0
CLIMB_STRIDE = 74.0
MAX_ATTACH_JUMP = 220.0


@dataclass(frozen=True)
class Side:
    id: str
    edge_id: str
    wall_x: float
    top: float
    bottom: float
    facing: int

    @property
    def canvas_x(self):
        return self.wall_x - (PAW_X if self.facing < 0 else WIDTH - PAW_X)

    @property
    def landing_x(self):
        return self.wall_x - WIDTH if self.facing < 0 else self.wall_x

    @property
    def stop_y(self):
        return self.top - PAW_TOP


def clean(rows, edges, bounds, low_ids=()):
    """Validate geometry from the same native snapshot as its top segments."""
    left, top, width, height = bounds
    result = {}
    for row in rows:
        try:
            identity, edge_id, wall_x, edge_top, bottom, facing = row
            if (not isinstance(identity, str) or not 0 < len(identity) <= 128
                    or identity in result or edge_id not in edges or edge_id in low_ids
                    or type(facing) is not int or facing not in (-1, 1)
                    or not all(type(n) in (int, float) and math.isfinite(n)
                               for n in (wall_x, edge_top, bottom))):
                continue
            side = Side(identity, edge_id, float(wall_x), float(edge_top), float(bottom), facing)
            edge = edges[edge_id]
            expected_wall = edge[1] if facing < 0 else edge[0]
            if (abs(wall_x-expected_wall) > 1e-7 or abs(edge_top-edge[2]) > 1e-7
                    or not top+HEIGHT <= edge_top < bottom <= top+height
                    or bottom-edge_top < HEIGHT
                    or not left <= side.canvas_x <= left+width-WIDTH
                    or not edge[0] <= side.landing_x <= edge[1]-WIDTH):
                continue
            result[identity] = side
            if len(result) >= 128:
                break
        except (TypeError, ValueError, KeyError):
            continue
    return result


def clear(visitor):
    visitor.climb_side_id = None
    visitor.climb_phase = 0.0


def candidate(engine, visitor, edge_id):
    """Choose a climb reachable from a currently supported horizontal surface."""
    if visitor.carrying_gift or visitor.demo_gift_pending or visitor.state in {'jumping', 'climbing', 'carried'}:
        return None
    source = engine._source_interval(visitor)
    if source is None:
        return None
    choices = []
    for side in engine.window_climb_sides.values():
        if side.edge_id != edge_id or not source[0] <= side.canvas_x <= source[1]:
            continue
        attach_y = min(visitor.y, side.bottom-PAW_BOTTOM)
        if (0 <= visitor.y-attach_y <= MAX_ATTACH_JUMP
                and attach_y-side.stop_y >= 32.0):
            choices.append(side)
    return min(choices, key=lambda side: abs(side.canvas_x-visitor.x)) if choices else None


def begin(engine, visitor, side, final_x, arrival, motion):
    engine._clear_route(visitor)
    engine._clear_peek(visitor)
    visitor.climb_side_id = side.id
    visitor.route_final_x, visitor.route_final_y = final_x, side.top-HEIGHT
    visitor.route_edge_id, visitor.route_motion = side.edge_id, motion
    visitor.arrival_state, visitor.arrival_facing = arrival, None
    visitor.window_target_id = None
    if abs(visitor.x-side.canvas_x) > 1e-7:
        visitor.target_x, visitor.target_y = side.canvas_x, visitor.y
        visitor.route_stage = 'climb_approach'
        engine._state(visitor, motion)
    else:
        _attach(engine, visitor, side)


def _attach(engine, visitor, side):
    support = engine._takeoff_support(visitor)
    attach_y = min(visitor.y, side.bottom-PAW_BOTTOM)
    if visitor.y-attach_y > MAX_ATTACH_JUMP or attach_y <= side.stop_y:
        abort(engine, visitor)
        return
    if abs(attach_y-visitor.y) > 1e-7:
        engine._clear_window(visitor)
        visitor.target_x, visitor.target_y = side.canvas_x, attach_y
        visitor.route_stage = 'climb_attach'
        engine._begin_jump(visitor, support=support)
    else:
        _grip(engine, visitor, side)


def _grip(engine, visitor, side):
    engine._clear_window(visitor)
    visitor.target_x, visitor.target_y = side.canvas_x, side.stop_y
    visitor.route_stage = 'climb'
    engine._state(visitor, 'climbing')
    visitor.facing = side.facing
    visitor.landing_remaining = 0.0


def advance_waypoint(engine, visitor):
    if visitor.route_stage not in {'climb_approach', 'climb_attach'}:
        return False
    side = engine.window_climb_sides.get(visitor.climb_side_id)
    if side is None:
        abort(engine, visitor)
    elif visitor.route_stage == 'climb_approach':
        _attach(engine, visitor, side)
    else:
        _grip(engine, visitor, side)
    return True


def step(engine, visitor, dt):
    side = engine.window_climb_sides.get(visitor.climb_side_id)
    if side is None:
        abort(engine, visitor)
        return
    distance = min(max(0.0, visitor.y-side.stop_y), CLIMB_SPEED*dt)
    visitor.y -= distance
    visitor.climb_phase = (visitor.climb_phase+distance/CLIMB_STRIDE) % 1.0
    visitor.facing = side.facing
    if visitor.y <= side.stop_y+1e-7:
        # A short airborne mantle reaches the fully supported top. The model
        # then performs its existing horizontal finish along that top.
        visitor.y = side.stop_y
        clear(visitor)
        engine._start_route_jump(visitor, support=(False, None), landing_x=side.landing_x)


def abort(engine, visitor):
    arrival, facing = visitor.arrival_state, visitor.arrival_facing
    motion = visitor.route_motion
    engine._clear_route(visitor)
    engine._clear_window(visitor)
    engine._walk(visitor, visitor.x, engine.floor_y, arrival=arrival,
                 arrival_facing=facing, motion=motion)


def refresh(engine, visitor, previous):
    """Handle a side-route snapshot before ordinary top-route rebinding."""
    if visitor.climb_side_id is None:
        return False
    old = previous.get(visitor.climb_side_id)
    side = engine.window_climb_sides.get(visitor.climb_side_id)
    source_ok = (visitor.route_stage != 'climb_approach'
                 or engine._takeoff_support(visitor)[0])
    same_wall = (old is not None and side is not None
                 and (old.wall_x, old.top, old.bottom, old.facing)
                 == (side.wall_x, side.top, side.bottom, side.facing))
    final_x = visitor.route_final_x
    if same_wall and source_ok and final_x is not None and math.isfinite(final_x):
        # A resize or distant occluder can shorten this side's connected top
        # without moving the wall. Keep the current grip/phase, but constrain
        # the later finish walk to that same segment's remaining support.
        edge_left, edge_right, _ = engine.window_edges[side.edge_id]
        visitor.route_final_x = min(edge_right-WIDTH, max(edge_left, final_x))
        visitor.route_edge_id = side.edge_id
    else:
        abort(engine, visitor)
    return True
