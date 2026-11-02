"""Rebase settled foreground activity when artwork size changes.

The simulation keeps its 144×112 scene units. Only the desktop mapping and
safe viewport change; scaling is not a new lifecycle action or elapsed tick.
"""
from __future__ import annotations

from dataclasses import fields
import math

from .display_scale import SceneTransform
from .gift_geometry import GIFT_SIZE
from .model import SPRITE_HEIGHT, SPRITE_WIDTH
from . import resident_bed as bed_model

_SAFE_STATES = frozenset({'idle', 'walking', 'running', 'purring', 'sleeping', 'resting', 'playing'})
_TRANSITION_TIMERS = ('posture_remaining', 'takeoff_remaining', 'landing_remaining',
                      'rise_remaining', 'settle_remaining', 'turn_remaining')
_SUPPORT_REFS = ('window_edge_id', 'window_target_id', 'takeoff_edge_id',
                 'route_edge_id', 'climb_side_id')


def is_scale_safe(engine) -> bool:
    """Read only: let physical interactions finish before changing their scale."""
    if engine._teaser is not None or engine._fight_pair is not None:
        return False
    if any(gift.state == 'held' for gift in engine.gifts):
        return False
    for visitor in engine.visitors.values():
        if (visitor.state not in _SAFE_STATES
                or visitor.depth_trip is not None or visitor.tree_trip is not None
                or visitor.depth_layer != 'front' or abs(visitor.depth_scale-1.) > 1e-7
                or abs(visitor.tree_scale-1.) > 1e-7
                or any(getattr(visitor, ref) is not None for ref in _SUPPORT_REFS)
                or visitor.clip_y is not None or visitor.rear_blend > 1e-7
                or visitor.route_stage or visitor.route_motion in {'leaving', 'fleeing'}
                or any(getattr(visitor, key) > 1e-7 for key in _TRANSITION_TIMERS)
                or visitor.arrival_state == 'eating' or visitor.meal_bowl is not None
                or abs(visitor.y-engine.floor_y) > 1e-6
                or (visitor.moving and abs(visitor.target_y-engine.floor_y) > 1e-6)
                or ((visitor.route_final_x is None) != (visitor.route_final_y is None))
                or (visitor.route_final_y is not None and abs(visitor.route_final_y-engine.floor_y) > 1e-6)):
            return False
        if visitor.bed_nap and not bed_model.at_bed(engine, visitor):
            return False
        if (visitor.bed_route_revision is not None
                and (engine.bed is None or visitor.bed_route_revision != engine._bed_revision
                     or visitor.arrival_state != 'bed')):
            return False
    return True


def _snapshot(value):
    return {field.name: getattr(value, field.name) for field in fields(value)}


def _restore(value, state):
    for key, item in state.items():
        setattr(value, key, item)


def rebase_scene(engine, old: SceneTransform, new: SceneTransform, new_bounds: tuple,
                 *, translate_origin=True) -> None:
    """Preserve safe foreground feet/centres through a display-scale change.

    If changing monitors, the default preserves position relative to the new
    monitor origin. The caller may disable that translation when preserving
    absolute desktop points is intentional. All validation precedes mutation.
    Ground bodies remain on the new floor; only their horizontal foot anchors
    can stay at the exact old display point when their visual size changes.
    """
    if not isinstance(old, SceneTransform) or not isinstance(new, SceneTransform):
        raise ValueError('Expected scene transforms')
    if not isinstance(new_bounds, (tuple, list)) or len(new_bounds) != 4:
        raise ValueError('Expected four scene bounds')
    try:
        bounds = tuple(float(value) for value in new_bounds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Invalid scene bounds') from exc
    if (any(type(value) not in (int, float) for value in new_bounds)
            or not all(math.isfinite(value) for value in bounds)
            or bounds[2] < SPRITE_WIDTH or bounds[3] < SPRITE_HEIGHT):
        raise ValueError('Scene bounds must fit a cat')
    if not is_scale_safe(engine):
        raise ValueError('Artwork scale must wait for settled foreground activity')

    dx = new.origin_x-old.origin_x if translate_origin else 0.
    dy = new.origin_y-old.origin_y if translate_origin else 0.

    def mapped(x, y):
        x, y = old.to_display(x, y)
        return new.to_scene(x+dx, y+dy)

    # Precompute coordinates before invoking setters, so a malformed transform
    # cannot leave only part of the scene moved.
    visitors = [(visitor, _snapshot(visitor),
                 mapped(visitor.x+72, visitor.y+108),
                 mapped(visitor.target_x+72, visitor.target_y+108),
                 mapped(visitor.jump_start_x+72, visitor.jump_start_y+108),
                 mapped(visitor.route_final_x+72, visitor.route_final_y+108)
                 if visitor.route_final_x is not None else None)
                for visitor in engine.visitors.values()]
    gifts = [(gift, _snapshot(gift), mapped(gift.x+GIFT_SIZE/2, gift.y+GIFT_SIZE/2))
             for gift in engine.gifts]
    bowl = mapped(*engine.bowl)
    bed = engine.bed
    bed_state = _snapshot(bed) if bed is not None else None
    bed_x = mapped(bed.x, engine.bed_y)[0] if bed is not None else None
    screen_top = mapped(old.origin_x, engine.screen_top)[1]
    old_revision = engine._bed_revision
    rng_state = engine.rng.getstate()
    events = list(engine._events)

    # Reuse bounds validation and native-route invalidation, then undo only its
    # lifecycle side effects (nap wake, mouse restart, RNG draws). No time runs.
    engine.set_bounds(*bounds)
    engine.rng.setstate(rng_state)
    engine._events[:] = events
    engine._bed_revision = old_revision
    engine.screen_top = screen_top
    if bed is not None:
        _restore(bed, bed_state)
        bed.x = bed_model.clamped_x(engine, bed_x)
    engine.set_bowl(*bowl)
    left, top, width, height = engine.bounds
    # The bowl has a 62×40 input canvas; retain its centre unless a larger
    # artwork scale needs a few points of edge clearance.
    engine.bowl = (min(left+width-31, max(left+31, engine.bowl[0])), engine.bowl[1])
    for visitor, state, foot, target, jump_start, final in visitors:
        _restore(visitor, state)
        visitor.x = engine._inside_x(foot[0]-72)
        visitor.y = engine.floor_y
        visitor.target_x = engine._inside_x(target[0]-72)
        visitor.target_y = engine.floor_y
        visitor.jump_start_x, visitor.jump_start_y = jump_start[0]-72, jump_start[1]-108
        # External icon/native route hints require the next desktop snapshot;
        # never restore stale geometry which set_bounds deliberately discarded.
        visitor.gift_spot = None
        if final is not None:
            visitor.route_final_x, visitor.route_final_y = engine._inside_x(final[0]-72), engine.floor_y
        if visitor.bed_nap:
            visitor.x = visitor.target_x = bed_model.bed_target(engine)[0]
        elif visitor.bed_route_revision is not None:
            visitor.target_x = bed_model.bed_target(engine)[0]
    for gift, state, centre in gifts:
        _restore(gift, state)
        gift.x = min(left+width-GIFT_SIZE, max(left, centre[0]-GIFT_SIZE/2))
        gift.y = min(top+height-GIFT_SIZE, max(top, centre[1]-GIFT_SIZE/2))
