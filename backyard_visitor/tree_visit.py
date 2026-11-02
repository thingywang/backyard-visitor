"""Finite, foot-anchored trips from the desktop foreground to a real cat tree.

All positions are global logical pixels. A trip owns its small support graph;
no tree surface is disguised as an application window. Reservations supplied
by Engine keep both takeoff and landing platforms exclusive while airborne.
"""
from __future__ import annotations

from collections import deque
import math

from .tree_geometry import TreeLayout

FOOT = (72., 108.)
NORMAL_RETURN_REASONS = frozenset({'called', 'pet', 'drag', 'play', 'leaving',
    'fleeing', 'gate_closed', 'wallpaper_change', 'user', 'finished'})


def _point(value):
    return (isinstance(value, (tuple, list)) and len(value) == 2
            and all(type(n) in (int, float) and math.isfinite(n) for n in value))


def _path(layout, origin, target):
    pending = deque([(origin, ())]); seen = set()
    while pending:
        node, before = pending.popleft()
        if node == target:
            return before + (node,)
        if node in seen:
            continue
        seen.add(node)
        pending.extend((other, before+(node,)) for other in layout.neighbors.get(node, ()) if other not in seen)
    return ()


class TreeVisit:
    """Continuous approach, supported exploration and an interruptible return."""
    def __init__(self, layout: TreeLayout, start, *, facing=1, gait_phase=0.,
                 personality='graceful', sleep_seconds=None):
        if not isinstance(layout, TreeLayout) or not _point(start):
            raise ValueError('A tree visit needs a validated layout and finite foot')
        route = _path(layout, layout.base_id, layout.bed_id)
        if not route or len(route) < 2:
            raise ValueError('The tree bed must connect to its ground base')
        if (not math.isfinite(layout.scale) or abs(layout.scale-.65) > 1e-8
                or not all(_point(point) for point in (layout.entry, layout.approach, layout.scratch_anchor))
                or len(layout.screen_rect) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in layout.screen_rect)
                or not layout.by_id[layout.base_id].supports_foot(layout.entry, layout.scale)):
            raise ValueError('Invalid tree layout geometry')
        self.layout = layout
        self.screen_rect = tuple(layout.screen_rect)
        self.ground_y = float(layout.entry[1])
        self.start = tuple(map(float, start))
        self.foot = self.start
        self.scale = 1.
        self.facing = -1 if facing < 0 else 1
        self.gait_phase = gait_phase % 1.
        self.run_phase = 0.
        self.age = self.state_age = 0.
        self.phase, self.pose = 'approach', 'walking'
        self.support_id = None
        self.target_support = layout.base_id
        self.returning = self.done = self.paused = self.escaping = False
        self._invalidated = False
        self._route = route
        self._queue = deque()
        self._current = None
        self._elapsed = self._duration = self._wait = 0.
        self._from = self.foot
        self._from_scale = self.scale
        self._arc = 0.
        self.jump_vy = self.jump_pose_phase = self.jump_vertical_span = 0.
        self.heading_degrees = 0. if self.facing > 0 else 180.
        self.near = (float(layout.approach[0]), self.start[1])
        self.sleep_seconds = (float(sleep_seconds) if sleep_seconds is not None
                              else {'mischievous': 12., 'graceful': 18., 'shy': 25.}.get(personality, 18.))
        if not math.isfinite(self.sleep_seconds):
            raise ValueError('Tree sleep duration must be finite')
        self.sleep_seconds = max(0., min(40., self.sleep_seconds))
        self._queue.append(self._move(self.near, 1., 'approach', reserve=layout.base_id))
        self._queue.append(self._move(layout.entry, layout.scale, 'recede', support=layout.base_id))
        # The scratch feet, unlike the pole contact, remain on the base.
        self._queue.append(self._move(layout.scratch_anchor, layout.scale, 'walk', support=layout.base_id))
        self._queue.append(self._rest('scratching', 3.2, facing=layout.scratch_facing))
        for origin_id, support_id in zip(route, route[1:]):
            self._queue.extend(self._transfer(origin_id, support_id))
            if support_id == layout.bed_id:
                self._queue.append(self._rest('grooming', 3.5))
                self._queue.append(self._rest('sleeping' if self.sleep_seconds > 0 else 'idle', max(2., self.sleep_seconds)))
            elif support_id == route[-2]:
                self._queue.append(self._rest('grooming', 3.0))
            else:
                self._queue.append(self._rest('idle', .65))
        self._next(set())

    def _landing(self, support_id):
        support = self.layout.by_id[support_id]
        low, high = support.foot_range(self.layout.scale)
        return ((low+high)/2, support.y)

    def _transfer(self, origin, target):
        """Walk on boards first, then jump only their unavoidable lateral gap."""
        source, destination = self.layout.by_id[origin], self.layout.by_id[target]
        a, b = source.foot_range(self.layout.scale)
        c, d = destination.foot_range(self.layout.scale)
        if b < c:
            takeoff_x, landing_x = b, c
        elif d < a:
            takeoff_x, landing_x = a, d
        else:
            takeoff_x = landing_x = (max(a, c)+min(b, d))/2
        actions = [self._move((takeoff_x, source.y), self.layout.scale, 'walk', support=origin)]
        if abs(destination.y-source.y) >= 56*self.layout.scale:
            actions.append(self._rest('standing', .20, stage='takeoff', reserve=target))
        actions.extend((self._move((landing_x, destination.y), self.layout.scale, 'support', support=target),
                        self._move(self._landing(target), self.layout.scale, 'walk', support=target)))
        return tuple(actions)

    @staticmethod
    def _move(target, scale, phase, *, support=None, reserve=None, fall=False):
        return {'kind': 'move', 'target': tuple(target), 'scale': scale,
                'phase': phase, 'support': support, 'reserve': reserve, 'fall': fall}

    @staticmethod
    def _rest(pose, duration, *, facing=None, stage='rest', reserve=None):
        return {'kind': 'rest', 'pose': pose, 'duration': duration, 'facing': facing,
                'stage': stage, 'reserve': reserve}

    @property
    def reserved_supports(self):
        if self.done or self._invalidated:
            return frozenset()
        return frozenset(x for x in (self.support_id, self.target_support) if x is not None)

    @property
    def moving(self):
        return not self.done and self.phase not in {'rest', 'waiting'}

    def _begin(self, action):
        self._current = action
        self._elapsed = self.state_age = 0.
        self._from, self._from_scale = self.foot, self.scale
        self.jump_vy = self.jump_pose_phase = self.jump_vertical_span = 0.
        if action['kind'] == 'rest':
            self.phase, self.pose = action.get('stage', 'rest'), action['pose']
            self._duration = action['duration']
            self.target_support = action.get('reserve')
            if action['facing'] is not None:
                self.facing = -1 if action['facing'] < 0 else 1
            return
        target = action['target']
        dx, dy = target[0]-self.foot[0], target[1]-self.foot[1]
        distance = math.hypot(dx, dy)
        phase = action['phase']
        self.target_support = action['support'] or action['reserve']
        if abs(dx) > .01:
            self.facing = -1 if dx < 0 else 1
        if distance > .01:
            self.heading_degrees = math.degrees(math.atan2(dy, dx))
        # A tiny step on a continuous board is never turned into a hop. Actual
        # disconnected board transfers require a real vertical gap; their
        # validated graph never relies on an unsupported same-height leap.
        jumping = action['fall'] or (phase == 'support' and abs(dy) >= 56*self.scale)
        if jumping:
            self.phase, self.pose = 'jump', 'jumping'
            self.jump_vertical_span = abs(dy)
            self._duration = max(.65, min(1.7, distance/(185*self.scale)))
            # A true landing has downward velocity even when its destination
            # is much higher: dy+4*arc must stay positive at t=1. A capped arc
            # would otherwise arrive still shooting upward and snap to a walk.
            self._arc = 0. if action['fall'] else abs(dy)*.25+24*self.scale
        else:
            self.phase = phase
            self.pose = 'running' if self.escaping else 'walking'
            if self.escaping and phase in {'advance', 'recede'}:
                self.phase = 'escape_' + phase
            speed = (175 if self.escaping else 66) * max(.65, (self.scale+action['scale'])/2)
            self._duration = max(.001, distance/speed)
            self._arc = 0.

    def _next(self, occupied):
        while self._queue:
            action = self._queue[0]
            target = action.get('support') or action.get('reserve')
            if target is not None and target != self.support_id and target in occupied:
                self.phase, self.pose = 'waiting', 'idle'
                self.target_support = None
                self._current = None
                return False
            self._queue.popleft()
            if action['kind'] == 'move' and math.dist(self.foot, action['target']) < 1e-8 and abs(self.scale-action['scale']) < 1e-8:
                if action.get('support'):
                    self.support_id = action['support']
                continue
            self._wait = 0.
            self._begin(action)
            return True
        if not self.returning:
            self.returning = True
            self._plan_supported_return()
            return self._next(occupied)
        self.done = True
        self.phase, self.pose = 'done', 'idle'
        self.support_id = self.target_support = None
        self._current = None
        return False

    def _plan_front_return(self):
        if abs(self.scale-1.) > 1e-9 or abs(self.foot[1]-self.start[1]) > 1e-8:
            self._queue.append(self._move(self.near, 1., 'advance'))
        self._queue.append(self._move(self.start, 1., 'return'))

    def _plan_supported_return(self):
        self._queue.clear()
        if self.support_id is not None:
            path = _path(self.layout, self.support_id, self.layout.base_id)
            for origin, node in zip(path, path[1:]):
                self._queue.extend(self._transfer(origin, node))
            if path:
                self._queue.append(self._move(self.layout.entry, self.layout.scale, 'walk', support=self.layout.base_id))
        self._plan_front_return()

    def request_return(self, reason='called'):
        """Preserve an existing jump; invalidate old supports only when needed."""
        invalid = reason not in NORMAL_RETURN_REASONS
        panic = reason == 'fleeing'
        if self.done or (self.returning and (not invalid or self._invalidated) and (not panic or self.escaping)):
            return False
        self.returning = True
        self.escaping = self.escaping or panic
        if invalid:
            self._invalidated = True
            self.support_id = self.target_support = None
            self._current = None
            self._queue.clear()
            # A removed/obscured scene no longer supports jumping between old
            # shelves. Descend from the actual airborne foot to ground level.
            ground_y = max(self.foot[1], self.ground_y)
            self._queue.append(self._move((self.foot[0], ground_y), self.scale, 'fall', fall=True))
            self._plan_front_return()
            self._next(set())
        elif self._current and self.phase == 'jump':
            # Finish the original landing arc, then replace onward exploration.
            self._queue.clear()
            self._current['return_after'] = True
        elif self.support_id is None:
            self._current = None
            self._queue.clear()
            self._plan_front_return()
            self._next(set())
        else:
            self._current = None
            self._plan_supported_return()
            # Engine passes current reservations on the next tick; don't
            # acquire a neighbouring platform without checking its occupant.
            self.phase, self.pose = 'waiting', 'idle'
            self.target_support = None
        return True

    def rebase_screen(self, rect):
        if (not isinstance(rect, (tuple, list)) or len(rect) != 4
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in rect)):
            raise ValueError('A finite screen rectangle is required')
        sx, sy, sw, sh = rect
        old = self.screen_rect
        self.screen_rect = tuple(rect)
        dx, dy = sx-old[0], sy-old[1]
        def shifted(point): return point[0]+dx, point[1]+dy
        self.foot = shifted(self.foot)
        self.ground_y = min(sy+sh-4*self.scale, max(sy+108*self.scale, self.ground_y+dy))
        self.start = (min(sx+sw-72, max(sx+72, self.start[0]+dx)), sy+sh-4.)
        self.near = (min(sx+sw-72, max(sx+72, self.near[0]+dx)), self.start[1])
        # Keep the current apparent size; a new display may remove all old
        # supports, so a direct continuous descent takes precedence.
        self._invalidated = False
        self.request_return('screen_changed')

    def step(self, dt, occupied=()):
        if type(dt) not in (int, float) or not math.isfinite(dt) or dt < 0:
            raise ValueError('Tree time must be finite and nonnegative')
        if self.done or self.paused or dt == 0:
            return
        self.age += dt
        occupied = set(occupied)
        remaining = dt
        for _ in range(80):
            if self.done or remaining <= 1e-9:
                break
            if self._current is None:
                if not self._next(occupied):
                    if not self.done:
                        self._wait += remaining
                        self.state_age += remaining
                        # Avoid two cats waiting forever face-to-face: an
                        # ascending visitor yields by returning to its base.
                        if self._wait >= 2.5 and not self.returning:
                            self.request_return('called')
                    break
            action = self._current
            used = min(remaining, self._duration-self._elapsed)
            self._elapsed += used
            self.state_age += used
            remaining -= used
            if action['kind'] == 'move':
                t = min(1., self._elapsed/self._duration)
                target = action['target']
                previous, previous_scale = self.foot, self.scale
                x = self._from[0]+(target[0]-self._from[0])*t
                y = self._from[1]+(target[1]-self._from[1])*t
                if self.phase == 'jump':
                    y -= 4*self._arc*t*(1-t)
                    self.jump_vy = ((target[1]-self._from[1])-4*self._arc*(1-2*t))/self._duration
                    self.jump_pose_phase = t
                self.foot = (x, y)
                self.scale = self._from_scale+(action['scale']-self._from_scale)*t
                if self.pose in {'walking', 'running'}:
                    distance = math.dist(previous, self.foot)
                    delta_scale = self.scale-previous_scale
                    # Integrate physical distance / scale exactly so a coarse
                    # repaint tick cannot speed up the gait during recession.
                    distance *= (math.log(self.scale/previous_scale)/delta_scale
                                 if abs(delta_scale) > 1e-12 else 1/self.scale)
                    self.gait_phase = (self.gait_phase+distance/74.) % 1.
                    self.run_phase = (self.run_phase+distance/100.) % 1.
            if self._elapsed >= self._duration-1e-9:
                landed = action['kind'] == 'move' and self.phase == 'jump'
                if action['kind'] == 'move':
                    self.foot, self.scale = action['target'], action['scale']
                    self.support_id = action['support']
                    self.target_support = None
                self._current = None
                if action.get('return_after'):
                    self._plan_supported_return()
                if landed:
                    self._queue.appendleft(self._rest('standing', .24, stage='landing'))
                self._next(occupied)

    def snapshot(self):
        return {'foot': self.foot, 'scale': self.scale, 'pose': self.pose,
                'phase': self.phase, 'age': self.age, 'state_age': self.state_age,
                'facing': self.facing, 'gait_phase': self.gait_phase,
                'run_phase': self.run_phase, 'jump_vy': self.jump_vy,
                'jump_pose_phase': self.jump_pose_phase,
                'jump_vertical_span': self.jump_vertical_span,
                'heading_degrees': self.heading_degrees,
                'takeoff_progress': min(1., self.state_age/.20) if self.phase == 'takeoff' else None,
                'land_progress': min(1., self.state_age/.24) if self.phase == 'landing' else None,
                'support_id': self.support_id, 'target_support': self.target_support,
                'returning': self.returning, 'escaping': self.escaping,
                'done': self.done}
