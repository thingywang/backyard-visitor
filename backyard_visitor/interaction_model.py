"""Short deliberate play and belly-touch reactions, independent of Qt.

Sessions and pointer samples are transient; no cursor or desktop permissions
are controlled here. Ordinary Engine routes retain ownership of airborne cats.
"""
from __future__ import annotations
from dataclasses import dataclass

TEASER_SECONDS = 15.0
SWAT_SECONDS = .65
CROUCH_RISE_SECONDS = .20
BELLY_ROLL_SECONDS = .55
TEASER_STATES = frozenset({'teasing', 'jumping', 'walking', 'running', 'idle'})


@dataclass(slots=True)
class TeaserState:
    cat_id: str
    remaining: float = TEASER_SECONDS
    phase: str = 'watch'
    wait: float = .4


def current_session(engine):
    session = engine._teaser
    if session is not None and session.cat_id not in engine.visitors:
        engine._teaser = session = None
    return session


def cancel_teaser(engine, cat_id):
    """Urgent actions clear ownership without scheduling a competing route."""
    session = current_session(engine)
    if session is None or session.cat_id != cat_id:
        return False
    engine._teaser = None
    visitor = engine.visitors.get(cat_id)
    if visitor is not None:
        engine._clear_posture(visitor)
    return True


def start_teaser(engine, cat_id):
    if not isinstance(cat_id, str):
        return False
    v = engine.visitors.get(cat_id)
    if (current_session(engine) is not None or not engine.gate_open or v is None
            or v.state not in {'idle', 'sleeping', 'purring', 'walking', 'running', 'playing', 'resting', 'window_rear'}
            or v.route_motion in {'leaving', 'fleeing'}
            or v.posture_remaining > 0
            or v.carrying_gift or v.demo_gift_pending or v.true_name_pending or v.true_name_remaining > 0):
        return False
    engine.cancel_parkour(v)
    engine._clear_route(v)
    # Watching replaces the former route, including a half-finished reversal.
    # Keep the current facing; a future chase decides its own turn direction.
    v.ground_speed = v.turn_remaining = 0.0
    v.turn_target_facing = None
    engine._teaser = TeaserState(cat_id)
    v.stay_remaining = max(v.stay_remaining, TEASER_SECONDS+30)
    v.next_action = TEASER_SECONDS+1
    if v.on_back:
        # Roll out of the actual belly pose before any standing/chasing route.
        # A cat sleeping on a window retains its support during this interval.
        engine._state(v, 'teasing')
        engine._begin_posture(v, 'belly_roll', BELLY_ROLL_SECONDS, 'sleeping', 'play_crouch')
    elif abs(v.y-engine.floor_y) > 1e-7:
        engine._walk(v, v.x, engine.floor_y, arrival='teasing')
        engine._teaser.phase = 'approach'
    else:
        # Seated/sleeping cats rise before their first chase, at this position.
        if v.state in {'purring', 'resting'}:
            engine._state(v, 'idle')
            v.settle_remaining = 0.0
        engine._state(v, 'teasing')
    engine._emit('play_started', cat_id, f'逗猫棒轻轻晃起来了。{engine.cat_reference(cat_id)}正盯着你手里的小猎物。')
    return True


def stop_teaser(engine, cat_id):
    if not cancel_teaser(engine, cat_id):
        return False
    v = engine.visitors.get(cat_id)
    if v is None:
        return True
    engine.cancel_parkour(v)
    if v.state in TEASER_STATES and v.route_motion not in {'leaving', 'fleeing'}:
        v.next_action = engine.rng.uniform(8, 14)
        if v.state == 'jumping':
            # The jump finishes with its original landing buffer before rest.
            v.arrival_state = 'resting'
            v.route_motion = 'walking'
        else:
            engine._clear_route(v)
            if engine.rng.random() < .35:
                x = engine._inside_x(v.x+engine.rng.choice((-1, 1))*engine.rng.uniform(45, 110))
                engine._walk(v, x, engine.floor_y, arrival='resting')
            else:
                engine._state(v, 'resting')
        engine._emit('play_finished', cat_id, f'玩累了。{engine.cat_reference(cat_id)}慢慢安静下来，找个舒服的地方趴一会儿。')
    return True


def scratch_on_pet(engine, cat, visitor):
    if not visitor.on_back or cat.trust >= 100 or visitor.pet_cooldown > 0:
        return False
    probability = .12+.25*(1-max(0., cat.trust)/100)
    if engine.rng.random() >= probability:
        return False
    engine.cancel_parkour(visitor)
    engine._clear_route(visitor)
    engine._state(visitor, 'swatting')
    visitor.pet_cooldown = 8.0
    engine._emit('scratch', cat.id, '爪子忽然伸过来了——肚皮这次先不摸。')
    return True


def _consume_posture(engine, visitor, dt):
    """Consume only available time; return the unused portion of this tick."""
    if visitor.posture_remaining <= 0:
        return dt
    consumed = min(dt, visitor.posture_remaining)
    visitor.posture_remaining = max(0., visitor.posture_remaining-consumed)
    if visitor.posture_remaining <= 1e-9:
        engine._clear_posture(visitor)
    return max(0., dt-consumed)



def begin_daily_wake(engine, visitor):
    """Natural decisions wait for a sleeping cat to roll and stand on its support."""
    if visitor.state != 'sleeping':
        return False
    engine._state(visitor, 'waking')
    engine._begin_posture(visitor, 'belly_roll', BELLY_ROLL_SECONDS, 'sleeping', 'play_crouch')
    return True


def _daily_wake_motion(engine, visitor, dt, landing_wait):
    usable = max(0., dt-landing_wait)
    previous_kind = visitor.posture_kind
    usable = _consume_posture(engine, visitor, usable)
    if visitor.posture_remaining > 0:
        return
    if previous_kind == 'belly_roll':
        engine._begin_posture(visitor, 'crouch_rise', CROUCH_RISE_SECONDS, 'play_crouch', 'standing')
        _consume_posture(engine, visitor, usable)
        if visitor.posture_remaining > 0:
            return
    # Decide only after standing, using the current bowl/food/support rather
    # than retaining a destination that may have moved during the transition.
    # An interrupted/cleared posture also reaches this safe fallback; urgent
    # states have already replaced 'waking' and never enter this branch.
    from .model import SETTLE_SECONDS
    engine._state(visitor, 'idle')
    visitor.settle_remaining = SETTLE_SECONDS  # Fully standing: do not rise twice.
    if visitor.demo_gift_pending:
        # Fulfil the explicit demo request only now that the mouth is standing,
        # instead of randomly choosing another nap before the gift is shown.
        engine._advance_demo_gift(visitor)
    else:
        engine._choose_action(visitor)


def _teasing_motion(engine, visitor, session, dt, landing_wait):
    usable = max(0., dt-landing_wait)
    if usable <= 1e-9:
        return
    usable = _consume_posture(engine, visitor, usable)
    if visitor.posture_remaining > 0:
        return
    if session.phase == 'rise':
        session.phase = 'chase'
    if abs(visitor.y-engine.floor_y)>1e-7:
        # A sleeping window visitor first rolls in place, then uses the same
        # continuous descent as a standing visitor. Never chase diagonally.
        engine._walk(visitor, visitor.x, engine.floor_y, arrival='teasing')
        session.phase = 'approach'
        return
    cursor = engine._cursor
    if cursor is None:
        # Hold a fresh observation interval while the toy is absent. A committed
        # jump is handled by Engine and reaches this branch only after landing.
        session.phase, session.wait = 'watch', .4
        visitor.ground_speed = visitor.turn_remaining = 0.0
        visitor.turn_target_facing = None
        return
    if session.phase in {'approach', 'pounce'}:
        session.phase = 'watch'
        session.wait = engine.rng.uniform(.25, .65)
    if session.phase == 'watch':
        visitor.ground_speed = 0.0
        watch = min(usable, max(0., session.wait))
        session.wait = max(0., session.wait-watch)
        usable -= watch
        # The initial seated rise retains its original clock. Watching may
        # overlap it, but each second is consumed only once from that clock.
        visitor.rise_remaining = max(0., visitor.rise_remaining-watch)
        if session.wait > 1e-9:
            return
        if visitor.rise_remaining > 0:
            rise = min(usable, visitor.rise_remaining)
            visitor.rise_remaining = max(0., visitor.rise_remaining-rise)
            usable -= rise
            if visitor.rise_remaining > 1e-9:
                return
        session.phase = 'rise'
        engine._begin_posture(visitor, 'crouch_rise', CROUCH_RISE_SECONDS, 'play_crouch', 'standing')
        usable = _consume_posture(engine, visitor, usable)
        if visitor.posture_remaining > 0:
            return
        session.phase = 'chase'
    if usable <= 1e-9:
        return
    from .model import RUN_SPEED, SPRITE_WIDTH
    desired_x = engine._inside_x(cursor[0]-SPRITE_WIDTH/2)
    visitor.target_x, visitor.target_y = desired_x, engine.floor_y
    if engine._ground_step(visitor, usable, RUN_SPEED, stop_distance=68):
        dx = max(-64., min(64., desired_x-visitor.x))
        visitor.target_x = engine._inside_x(visitor.x+dx)
        visitor.target_y = engine.floor_y
        visitor.arrival_state, visitor.arrival_facing = 'teasing', None
        engine._begin_jump(visitor)
        visitor.jump_duration = .5
        visitor.jump_arc = min(88., max(34., (visitor.y+84-cursor[1])*.35))
        session.phase = 'pounce'


def tick_interaction(engine, visitor, dt, landing_wait=0.0):
    """True means this visitor's pose/motion is fully handled for this tick."""
    if visitor.state == 'waking':
        _daily_wake_motion(engine, visitor, dt, landing_wait)
        return True
    if visitor.state == 'swatting':
        if visitor.state_age >= SWAT_SECONDS:
            leftover = min(dt, max(0., visitor.state_age-SWAT_SECONDS))
            engine._state(visitor, 'resting')
            visitor.next_action = 8.0
            engine._begin_posture(visitor, 'belly_roll', BELLY_ROLL_SECONDS, 'swatting', 'resting')
            _consume_posture(engine, visitor, max(0., leftover-landing_wait))
        return True
    if visitor.state == 'resting':
        if visitor.posture_remaining > 0:
            unused = _consume_posture(engine, visitor, max(0., dt-landing_wait))
            if visitor.posture_remaining > 0:
                return True
            visitor.state_age = unused
        if visitor.state_age >= visitor.next_action:
            engine._state(visitor, 'idle')
            visitor.next_action = 5.0
        return True
    session = current_session(engine)
    if session is None or session.cat_id != visitor.cat_id:
        # An orphaned interaction pose must never freeze permanently.
        if visitor.state == 'teasing':
            engine._state(visitor, 'idle')
            return True
        return False
    if (not engine.gate_open or visitor.state not in TEASER_STATES
            or visitor.route_motion in {'leaving', 'fleeing'}):
        cancel_teaser(engine, visitor.cat_id)
        return False
    usable = min(dt, session.remaining)
    session.remaining = max(0., session.remaining-dt)
    handled = visitor.state == 'teasing'
    if handled:
        _teasing_motion(engine, visitor, session, usable, landing_wait)
    if session.remaining <= 1e-9:
        stop_teaser(engine, visitor.cat_id)
    # A newly started hop is advanced by Engine on its next tick. Existing
    # airborne routes continue here even if this tick ended the session.
    return handled
