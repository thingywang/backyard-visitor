"""Resident-only floor bed and bounded effective-time rest scheduling.

This module knows no GUI, wall clock, native window, or secret names. Only the
bed's owner and horizontal position persist. Its rest budget resets each launch.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

BED_WIDTH = 172
BED_HEIGHT = 44
BED_SLEEP_FRACTIONS = {"mischievous": .30, "graceful": .45, "shy": .60}
BED_TARGET_NAP = 40.0
BED_MIN_NAP = 12.0
BED_MAX_NAP = 120.0
BED_CREDIT_LIMIT = 120.0
BED_OUTSIDE_MIN = 12.0


@dataclass(slots=True)
class ResidentBed:
    cat_id: str
    x: float
    personality: str
    credit: float = 0.0
    active_elapsed: float = 0.0
    sleep_elapsed: float = 0.0
    nap_elapsed: float = 0.0
    outside_remaining: float = 0.0

    @property
    def sleep_fraction(self):
        return BED_SLEEP_FRACTIONS.get(self.personality, .45)

    @property
    def due(self):
        return self.outside_remaining <= 0 and self.credit >= (1-self.sleep_fraction)*BED_TARGET_NAP


def clamped_x(engine, value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    left, _, width, _ = engine.bounds
    margin = min(BED_WIDTH/2, width/2)
    return min(left+width-margin, max(left+margin, float(value)))


def bed_target(engine):
    bed = engine.bed
    if bed is None:
        return None
    from .model import SPRITE_WIDTH
    return engine._inside_x(bed.x-SPRITE_WIDTH/2), engine.floor_y


def at_bed(engine, visitor):
    bed = engine.bed
    if bed is None or visitor.cat_id != bed.cat_id or bed.cat_id != engine.resident_id:
        return False
    x, y = bed_target(engine)
    return abs(visitor.x-x) <= 1e-6 and abs(visitor.y-y) <= 1e-6


def invalidate_bed_intent(engine):
    """Keep current coordinates and airborne arcs when furniture is changed."""
    from .interaction_model import begin_daily_wake
    engine._bed_revision += 1
    for visitor in engine.visitors.values():
        if visitor.bed_route_revision is not None:
            visitor.bed_route_revision = None
            if visitor.arrival_state == "bed":
                visitor.arrival_state = "idle"
        was_napping = visitor.bed_nap
        visitor.bed_nap = False
        if was_napping and visitor.state == "sleeping":
            begin_daily_wake(engine, visitor)
        # A half-complete lying pose finishes at its old location, then rises;
        # it must not instantly show standing or move with the bed window.


def place_bed(engine, cat_id, x=None):
    if not isinstance(cat_id, str) or cat_id != engine.resident_id:
        return False
    cat = engine.cats.get(cat_id)
    if cat is None or not cat.adopted or not cat.discovered:
        return False
    if x is None:
        visitor = engine.visitors.get(cat_id)
        x = visitor.x+72 if visitor is not None else engine.bounds[0]+engine.bounds[2]*.65
    x = clamped_x(engine, x)
    if x is None:
        return False
    if engine.bed is not None:
        return move_bed(engine, x)
    engine.bed = ResidentBed(cat_id, x, cat.personality)
    engine._bed_revision += 1
    engine._emit("bed_placed", cat_id, f"你为{engine.cat_reference(cat_id)}布置了一个猫窝。")
    return True


def move_bed(engine, x):
    bed = engine.bed
    x = clamped_x(engine, x)
    if bed is None or bed.cat_id != engine.resident_id or x is None:
        return False
    if abs(x-bed.x) <= 1e-7:
        return False
    invalidate_bed_intent(engine)
    bed.x = x
    bed.outside_remaining = max(bed.outside_remaining, BED_OUTSIDE_MIN)
    return True


def remove_bed(engine):
    bed = engine.bed
    if bed is None:
        return False
    invalidate_bed_intent(engine)
    engine.bed = None
    engine._emit("bed_removed", bed.cat_id, "猫窝暂时收起来了。")
    return True


def restore_bed(engine, value):
    if not isinstance(value, dict):
        return
    cat_id = value.get("cat_id")
    x = clamped_x(engine, value.get("x"))
    if not isinstance(cat_id, str) or cat_id != engine.resident_id or x is None:
        return
    engine.bed = ResidentBed(cat_id, x, engine.cats[cat_id].personality)


def account_bed_time(engine, visitor, dt):
    bed = engine.bed
    if (bed is None or not engine.gate_open or visitor.cat_id != bed.cat_id
            or bed.cat_id != engine.resident_id
            or visitor.state in {"peeking", "entering", "leaving", "fleeing"}
            or visitor.route_motion in {"leaving", "fleeing"}):
        return
    sleeping = ((at_bed(engine, visitor) and visitor.on_back)
                or (getattr(visitor, "tree_trip", None) is not None
                    and (visitor.tree_pose or {}).get("pose") == "sleeping"))
    bed.active_elapsed += dt
    bed.sleep_elapsed += dt if sleeping else 0.0
    bed.credit = max(-BED_CREDIT_LIMIT, min(BED_CREDIT_LIMIT,
                        bed.credit + bed.sleep_fraction*dt - (dt if sleeping else 0.0)))
    bed.outside_remaining = max(0.0, bed.outside_remaining-dt)
    if visitor.bed_nap and sleeping:
        bed.nap_elapsed += dt


def choose_bed(engine, visitor):
    bed = engine.bed
    if (bed is None or not engine.gate_open or bed.cat_id != visitor.cat_id
            or bed.cat_id != engine.resident_id or not bed.due
            or visitor.carrying_gift or visitor.demo_gift_pending
            or visitor.true_name_pending or visitor.true_name_remaining > 0
            or visitor.gift_response_pending or visitor.gift_response_remaining > 0
            or engine.active_teaser_id is not None):
        return False
    engine.cancel_parkour(visitor)
    engine._walk(visitor, *bed_target(engine), arrival="bed")
    visitor.bed_route_revision = engine._bed_revision
    return True


def begin_bed_lie(engine, visitor):
    from .interaction_model import CROUCH_RISE_SECONDS
    if visitor.bed_route_revision != engine._bed_revision or not at_bed(engine, visitor):
        visitor.bed_route_revision = None
        engine._state(visitor, "idle")
        return False
    engine._state(visitor, "bed_laying")
    engine._begin_posture(visitor, "crouch_lower", CROUCH_RISE_SECONDS, "standing", "play_crouch")
    return True


def tick_bed_lie(engine, visitor, dt, landing_wait):
    if visitor.state != "bed_laying":
        return False
    from .interaction_model import _consume_posture, BELLY_ROLL_SECONDS, begin_daily_wake
    from .model import SETTLE_SECONDS
    previous_kind = visitor.posture_kind
    if previous_kind not in {"crouch_lower", "belly_lie"}:
        # Screen reconfiguration can clear posture progress; never strand this
        # state or invent a completed sleep at a possibly different location.
        engine._state(visitor, "idle")
        visitor.settle_remaining = SETTLE_SECONDS
        return True
    usable = _consume_posture(engine, visitor, max(0.0, dt-landing_wait))
    if visitor.posture_remaining > 0:
        return True
    if previous_kind == "crouch_lower":
        engine._begin_posture(visitor, "belly_lie", BELLY_ROLL_SECONDS, "play_crouch", "sleeping")
        _consume_posture(engine, visitor, usable)
        if visitor.posture_remaining > 0:
            return True
    valid = visitor.bed_route_revision == engine._bed_revision and at_bed(engine, visitor)
    engine._state(visitor, "sleeping")
    visitor.bed_nap = valid
    if valid:
        engine.bed.nap_elapsed = 0.0
        engine._emit("bed_sleep", visitor.cat_id, f"{engine.cat_reference(visitor.cat_id)}在猫窝里安心地睡下了。")
    else:
        begin_daily_wake(engine, visitor)
    return True


def tick_bed_nap(engine, visitor):
    """Return true only for a bed-owned nap, including a normal wake decision."""
    if not visitor.bed_nap or visitor.state != "sleeping":
        return False
    from .interaction_model import begin_daily_wake
    bed = engine.bed
    if (bed is None or not at_bed(engine, visitor)
            or bed.nap_elapsed >= BED_MAX_NAP
            or (bed.nap_elapsed >= BED_MIN_NAP and bed.credit <= 0)):
        if bed is not None:
            bed.outside_remaining = BED_OUTSIDE_MIN
        begin_daily_wake(engine, visitor)
    return True
