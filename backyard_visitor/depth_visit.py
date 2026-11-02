"""Validated depth routes and continuous trips, independent of Qt and desktop APIs.

Coordinates are normalized to the selected full screen. Artwork selection is
semantic: headings never request rotating or mirroring the whole cat.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

EPSILON = 1e-12
HOLD_SECONDS = 2.0
REAR_PHASES = frozenset({'rear_traverse', 'hold', 'rear_return'})
RETURN_PHASES = frozenset({'rear_return', 'advance', 'front_return', 'done'})


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or not all(_finite(n) and 0 <= n <= 1 for n in value)):
        raise ValueError('Route and mask points must be finite normalized coordinate pairs')
    return tuple(float(n) for n in value)


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _on_segment(point, a, b):
    return (abs(_cross(a, b, point)) <= EPSILON
            and min(a[0], b[0])-EPSILON <= point[0] <= max(a[0], b[0])+EPSILON
            and min(a[1], b[1])-EPSILON <= point[1] <= max(a[1], b[1])+EPSILON)


def _edges(polygon):
    return zip(polygon, polygon[1:]+polygon[:1])


def _segments_intersect(a, b, c, d):
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    if ab_c*ab_d < -EPSILON**2 and cd_a*cd_b < -EPSILON**2:
        return True
    return (abs(ab_c) <= EPSILON and _on_segment(c, a, b)
            or abs(ab_d) <= EPSILON and _on_segment(d, a, b)
            or abs(cd_a) <= EPSILON and _on_segment(a, c, d)
            or abs(cd_b) <= EPSILON and _on_segment(b, c, d))


def _inside(point, polygon):
    if any(_on_segment(point, a, b) for a, b in _edges(polygon)):
        return True
    x, y = point
    inside = False
    for a, b in _edges(polygon):
        if (a[1] > y) != (b[1] > y):
            crossing = a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if x < crossing:
                inside = not inside
    return inside


def polygons_intersect(first, second):
    """Touching also counts: a safe layer handoff requires a true clear gap."""
    return (any(_segments_intersect(a, b, c, d)
                for a, b in _edges(first) for c, d in _edges(second))
            or _inside(first[0], second) or _inside(second[0], first))


def _hull(points):
    points = sorted(set(points))
    halves = []
    for sequence in (points, list(reversed(points))):
        half = []
        for point in sequence:
            while len(half) >= 2 and _cross(half[-2], half[-1], point) <= EPSILON:
                half.pop()
            half.append(point)
        halves.append(half[:-1])
    return tuple(halves[0]+halves[1])


def _polygon(value):
    if not isinstance(value, (tuple, list)) or not 3 <= len(value) <= 256:
        raise ValueError('Each mask needs 3 to 256 vertices')
    polygon = tuple(_point(p) for p in value)
    if polygon[-1] == polygon[0]:
        polygon = polygon[:-1]
    edges = tuple(_edges(polygon))
    if len(polygon) < 3 or any(a == b for a, b in edges):
        raise ValueError('Mask has repeated adjacent vertices')
    area = sum(a[0]*b[1]-b[0]*a[1] for a, b in edges)
    if abs(area) <= EPSILON:
        raise ValueError('Mask has no area')
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j <= i+1 or (i == 0 and j == len(edges)-1):
                continue
            if _segments_intersect(a, b, c, d):
                raise ValueError('Mask must be a simple polygon')
    return polygon


@dataclass(frozen=True, slots=True)
class DepthRoute:
    start: tuple = (.52, .94)
    near: tuple = (.60, .94)
    portal: tuple = (.72, .82)
    hide: tuple = (.91, .82)
    polygons: tuple = ()
    far_scale: float = .64
    aspect_ratio: float = 1.6
    cat_width: float = .1

    def __post_init__(self):
        for name in ('start', 'near', 'portal', 'hide'):
            object.__setattr__(self, name, _point(getattr(self, name)))
        if (not _finite(self.far_scale) or not 0 < self.far_scale <= 1
                or not _finite(self.aspect_ratio) or self.aspect_ratio <= 0
                or not _finite(self.cat_width) or not 0 < self.cat_width < 1):
            raise ValueError('Invalid scale, scene aspect ratio, or cat width')
        if not isinstance(self.polygons, (tuple, list)) or not 1 <= len(self.polygons) <= 32:
            raise ValueError('A depth route needs 1 to 32 occluders')
        object.__setattr__(self, 'polygons', tuple(_polygon(p) for p in self.polygons))
        if (abs(self.start[1]-self.near[1]) > EPSILON
                or abs(self.portal[1]-self.hide[1]) > EPSILON
                or self.portal[1] >= self.near[1] or self.portal == self.hide):
            raise ValueError('Front/rear legs must be horizontal, with a distinct farther portal')
        for point, scale in ((self.start, 1), (self.near, 1),
                             (self.portal, self.far_scale), (self.hide, self.far_scale)):
            if any(not -EPSILON <= n <= 1+EPSILON for p in self.footprint(point, scale) for n in p):
                raise ValueError('The full cat canvas must fit inside the screen')
        portal = self.footprint(self.portal, self.far_scale)
        if any(polygons_intersect(portal, mask) for mask in self.polygons):
            raise ValueError('Portal does not clear the entire cat canvas')
        # Scale and foot position share one interpolation parameter. Their
        # affine corner tracks sweep exactly this convex hull, not just samples.
        corridor = _hull(self.footprint(self.near, 1)+portal)
        if any(polygons_intersect(corridor, mask) for mask in self.polygons):
            raise ValueError('The receding corridor intersects an occluder')
        if not any(polygons_intersect(self.footprint(self.hide, self.far_scale), mask)
                   for mask in self.polygons):
            raise ValueError('The rear endpoint must actually meet an occluder')

    def footprint(self, foot, scale):
        """Conservative normalized 144x112 canvas, with its foot at (72,108)."""
        width = self.cat_width*scale
        left, right = foot[0]-width/2, foot[0]+width/2
        top = foot[1]-width*(108/144)*self.aspect_ratio
        bottom = foot[1]+width*(4/144)*self.aspect_ratio
        return ((left, top), (right, top), (right, bottom), (left, bottom))


class DepthVisit:
    def __init__(self, route: DepthRoute):
        if not isinstance(route, DepthRoute):
            raise TypeError('DepthVisit requires a validated DepthRoute')
        self.route = route
        self.elapsed = 0.0
        self.paused = False
        self.return_reason = None
        self._foot, self._depth = route.start, 0.0
        self._gait = 0.0
        self._facing = 1 if route.near[0] >= route.start[0] else -1
        self._begin('front_approach', route.near, 0.0)

    def _scale(self, depth):
        return 1+(self.route.far_scale-1)*depth

    def _distance(self, a, b):
        return math.hypot(b[0]-a[0], (b[1]-a[1])/self.route.aspect_ratio)

    def _begin(self, phase, target, depth):
        self.phase = phase
        self._from, self._to = self._foot, target
        self._from_depth, self._to_depth = self._depth, depth
        self._progress = 0.0
        distance = self._distance(self._from, self._to)
        average_scale = (self._scale(self._from_depth)+self._scale(depth))/2
        self._duration = (HOLD_SECONDS if phase == 'hold' else
                          distance/(self.route.cat_width*.5*average_scale))
        dx = target[0]-self._foot[0]
        if abs(dx) > EPSILON:
            self._facing = 1 if dx > 0 else -1

    def _next(self):
        r = self.route
        following = {
            'front_approach': ('recede', r.portal, 1.),
            'recede': ('rear_traverse', r.hide, 1.),
            'rear_traverse': ('hold', r.hide, 1.),
            'hold': ('rear_return', r.portal, 1.),
            'rear_return': ('advance', r.near, 0.),
            'advance': ('front_return', r.start, 0.),
            'front_return': ('done', r.start, 0.),
        }
        self._begin(*following[self.phase])

    def step(self, dt):
        if not _finite(dt) or dt < 0:
            raise ValueError('Elapsed time must be finite and nonnegative')
        if self.paused or self.phase == 'done':
            return self.snapshot()
        remaining = float(dt)
        while self.phase != 'done':
            if self._duration <= EPSILON:
                self._next()
                continue
            if remaining <= 0:
                break
            consumed = min(remaining, max(0., self._duration-self._progress))
            before_scale = self._scale(self._depth)
            before_foot = self._foot
            self._progress = min(self._duration, self._progress+consumed)
            fraction = self._progress/self._duration
            eased = fraction*fraction*(3-2*fraction)
            self._foot = tuple(a+(b-a)*eased for a, b in zip(self._from, self._to))
            self._depth = self._from_depth+(self._to_depth-self._from_depth)*eased
            after_scale = self._scale(self._depth)
            distance = self._distance(before_foot, self._foot)
            # Integrate inverse scale along this affine segment. Stepping in
            # smaller dt pieces gives the same planted-foot gait phase.
            scale_delta = after_scale-before_scale
            inverse_scale = (math.log(after_scale/before_scale)/scale_delta
                             if abs(scale_delta) > EPSILON else 1/before_scale)
            self._gait = (self._gait + distance*inverse_scale/(self.route.cat_width*(74/144))) % 1
            self.elapsed += consumed
            remaining = max(0., remaining-consumed)
            if self._progress >= self._duration-EPSILON:
                self._foot, self._depth = self._to, self._to_depth
                self._next()
            else:
                break
        return self.snapshot()

    def request_return(self, reason='called'):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Return reason must be a nonempty string')
        if self.phase == 'done' or self.return_reason is not None:
            return False
        self.return_reason = reason
        if self.phase in RETURN_PHASES:
            return True  # Already returning: keep the current segment clock.
        if self.phase == 'front_approach':
            self._begin('front_return', self.route.start, 0.)
        elif self.phase == 'recede':
            self._begin('advance', self.route.near, 0.)
        else:
            self._begin('rear_return', self.route.portal, 1.)
        return True

    def set_paused(self, paused):
        if type(paused) is not bool:
            raise ValueError('Pause must be a boolean')
        self.paused = paused

    def snapshot(self):
        done = self.phase == 'done'
        moving = not done and self.phase != 'hold' and self._duration > EPSILON
        return {'phase': self.phase, 'foot': list(self._foot), 'depth': self._depth,
                'scale': self._scale(self._depth), 'facing': self._facing,
                'pose': 'walking' if moving else 'idle', 'gait_phase': self._gait,
                'layer': 'rear' if self.phase in REAR_PHASES else 'front',
                'done': done, 'elapsed': self.elapsed, 'paused': self.paused,
                'return_reason': self.return_reason}


# Gather the feet, pivot, then settle before translating again. Actual pictures
# for these subphases remain an artwork requirement, not a model guarantee.
GATHER_SECONDS = .12
SETTLE_SECONDS = .12


def _delta_degrees(start, end):
    delta = (end - start + 180.) % 360. - 180.
    # Either direction is equivalent for a half turn; choose one consistently.
    return 180. if abs(delta + 180.) <= EPSILON else delta


def _smooth(value):
    value = min(1., max(0., value))
    return value * value * (3. - 2. * value)


class TurningDepthVisit(DepthVisit):
    """Stop at direction changes and finish a turn before moving onward.

    A recall during an outbound turn is queued until that turn settles. A second
    turn can then begin at the same spot; there is never a one-frame heading
    jump or a brief outbound translation. Recall during a return turn leaves
    that turn's clock intact. The renderer must support pose='turn' explicitly. Each turn lasts 0.4s.
    """

    def __init__(self, route: DepthRoute, *, initial_heading: float | None = None):
        if initial_heading is not None and not _finite(initial_heading):
            raise ValueError("Initial heading must be finite")
        self.phase = "idle"
        self._heading = initial_heading
        self._current_layer = 'front'
        self._pending_segment = None
        self._queued_return = False
        self._turn_from = self._turn_to = None
        self._turn_origin_phase = None
        self._pivot_duration = 0.
        self._turn_serial = 0
        self.escaping = False
        self._escape_done = False
        self._escape_run_phase = 0.
        super().__init__(route)

    @staticmethod
    def _escape_rect(value):
        if (not isinstance(value, (tuple, list)) or len(value) != 4
                or not all(_finite(n) for n in value) or value[2] <= 0 or value[3] <= 0):
            raise ValueError('Escape geometry must be a finite positive screen rectangle')
        return tuple(float(n) for n in value)

    def begin_escape(self, mapping_rect, screen_rect, *, run_phase=0., speed=210., stride=100.):
        """Run from the current depth plane directly to the nearest side.

        Screen geometry may differ from the original route's locked mapping.
        Neither foot, depth, scale nor layer changes on this call. Side-view
        running art owns the immediate panic pose, without a slow return turn.
        """
        if self.escaping or self.phase == 'done':
            return False
        mapping = self._escape_rect(mapping_rect)
        screen = self._escape_rect(screen_rect)
        if (not _finite(run_phase) or not _finite(speed) or speed <= 0
                or not _finite(stride) or stride <= 0):
            raise ValueError('Escape phase, speed and stride must be finite and valid')
        x = mapping[0] + self._foot[0]*mapping[2]
        half = 72*self._scale(self._depth)
        left, right = screen[0]-half-1, screen[0]+screen[2]+half+1
        self._escape_direction = -1 if abs(x-left) < abs(right-x) else 1
        self._escape_speed, self._escape_stride = float(speed), float(stride)
        self._escape_run_phase = run_phase % 1.
        self.escaping = True
        self.phase, self.return_reason = 'escape', 'startle'
        self._facing = self._escape_direction
        self._heading = 0. if self._facing > 0 else 180.
        self._pending_segment = None
        self._queued_return = False
        self._turn_from = self._turn_to = self._turn_origin_phase = None
        self.rebase_escape(mapping, screen)
        return True

    def rebase_escape(self, mapping_rect, screen_rect):
        """Keep direction/phase/scale and target the current selected screen.

        Repeated geometry notifications are harmless. The caller translates
        the mapping origin first; this method never relocates the current foot.
        """
        if not self.escaping:
            return False
        mapping, screen = self._escape_rect(mapping_rect), self._escape_rect(screen_rect)
        self._escape_mapping = mapping
        half = 72*self._scale(self._depth)
        target = screen[0]-half-1 if self._escape_direction < 0 else screen[0]+screen[2]+half+1
        self._escape_target = (target-mapping[0])/mapping[2]
        self._escape_done = self._escape_direction*(self._escape_target-self._foot[0]) <= EPSILON
        return True

    def step(self, dt):
        if not self.escaping:
            return super().step(dt)
        if not _finite(dt) or dt < 0:
            raise ValueError('Elapsed time must be finite and nonnegative')
        if self.paused or self._escape_done:
            return self.snapshot()
        width = self._escape_mapping[2]
        distance = max(0., self._escape_direction*(self._escape_target-self._foot[0])*width)
        moved = min(distance, self._escape_speed*dt)
        self._foot = (self._foot[0]+self._escape_direction*moved/width, self._foot[1])
        self._escape_run_phase = (self._escape_run_phase + moved/(self._escape_stride*self._scale(self._depth))) % 1.
        self.elapsed += moved/self._escape_speed
        if moved >= distance-EPSILON:
            self._foot = (self._escape_target, self._foot[1])
            self._escape_done = True
        return self.snapshot()

    def _segment_heading(self, target):
        dx = target[0] - self._foot[0]
        dy = (target[1] - self._foot[1]) / self.route.aspect_ratio
        if math.hypot(dx, dy) <= EPSILON:
            return None
        return math.degrees(math.atan2(dy, dx))

    def _start_segment(self, phase, target, depth):
        self._pending_segment = None
        self._turn_from = self._turn_to = None
        self._turn_origin_phase = None
        self._pivot_duration = 0.
        self._current_layer = 'rear' if phase in REAR_PHASES else 'front'
        super()._begin(phase, target, depth)

    def _begin(self, phase, target, depth):
        desired = self._segment_heading(target)
        if self._heading is None:
            self._heading = desired if desired is not None else 0.
            self._start_segment(phase, target, depth)
            return
        delta = 0. if desired is None else _delta_degrees(self._heading, desired)
        if abs(delta) <= EPSILON:
            self._start_segment(phase, target, depth)
            return
        previous_phase = self.phase
        self._pending_segment = (phase, target, depth)
        self._turn_from = self._heading
        self._turn_to = self._heading + delta
        self._turn_origin_phase = previous_phase
        self._pivot_duration = .16  # Accepted brief 0.4-second gather/pivot/settle.
        self._turn_serial += 1
        self.phase = 'turn'
        self._from = self._to = self._foot
        self._from_depth = self._to_depth = self._depth
        self._progress = 0.
        self._duration = GATHER_SECONDS + self._pivot_duration + SETTLE_SECONDS
        # Leave facing, gait, foot, depth and layer unchanged until settling ends.

    def _return_segment_here(self):
        if self._current_layer == 'rear':
            return ('rear_return', self.route.portal, 1.)
        if self._depth > EPSILON:
            return ('advance', self.route.near, 0.)
        return ('front_return', self.route.start, 0.)

    def _next(self):
        if self.phase != 'turn':
            super()._next()
            return
        pending = self._pending_segment
        self._heading = self._turn_to
        if self._queued_return:
            self._queued_return = False
            # Keep the new settled heading, then choose a safe return route.
            # _begin may start another turn, but cannot translate on this call.
            self._begin(*self._return_segment_here())
        else:
            self._start_segment(*pending)

    def request_return(self, reason='called'):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Return reason must be a nonempty string')
        if self.escaping or self.phase == 'done' or self.return_reason is not None:
            return False
        if self.phase == 'turn':
            self.return_reason = reason
            # An already queued return route must keep its turn clock.
            if self._pending_segment[0] not in RETURN_PHASES:
                self._queued_return = True
            return True
        return super().request_return(reason)

    def snapshot(self):
        state = super().snapshot()
        turning = self.phase == 'turn'
        if turning:
            pivot = (self._progress - GATHER_SECONDS) / self._pivot_duration
            motion = _smooth(pivot)
            heading = self._turn_from + (self._turn_to - self._turn_from) * motion
            stage = ('gather' if self._progress < GATHER_SECONDS else
                     'pivot' if self._progress < GATHER_SECONDS + self._pivot_duration else 'settle')
        else:
            heading, motion, stage = self._heading, None, None
        state.update(layer=self._current_layer, turning=turning,
                     route_phase=self._pending_segment[0] if turning else self.phase,
                     heading_degrees=heading, turn_serial=self._turn_serial,
                     turn_progress=self._progress / self._duration if turning else None,
                     turn_motion_progress=motion, turn_stage=stage,
                     turn_from_degrees=self._turn_from,
                     turn_to_degrees=self._turn_to,
                     turn_from_phase=self._turn_origin_phase,
                     queued_return=self._queued_return,
                     artwork_required=turning)
        if turning:
            state['pose'] = 'turn'
        if self.escaping:
            state.update(pose='running', escaping=True, done=self._escape_done,
                         run_phase=self._escape_run_phase, escape_direction=self._escape_direction)
        return state


def _view(heading):
    heading = (heading + 180.) % 360. - 180.
    if -70. < heading < -10.:
        return 'away'
    if 110. < heading < 170.:
        return 'toward'
    return 'side_left' if abs(heading) > 90. else 'side_right'


def return_pose(snapshot, route):
    # Only the right/up corridor has matching authored away/toward views.
    if route.portal[0] <= route.near[0]:
        return None
    if snapshot['phase'] == 'advance':
        return {'kind': 'toward', 'index': min(3, int(snapshot['gait_phase'] * 4))}
    if snapshot['phase'] != 'turn':
        return None
    motion = snapshot['turn_motion_progress']
    source, target = snapshot['turn_from_degrees'], snapshot['turn_to_degrees']
    if abs(target - source) > 100. and .25 <= motion < .75:
        kind = 'front'
    else:
        kind = _view(source if motion < .5 else target)
    return {'kind': kind, 'index': min(3, int(snapshot['gait_phase'] * 4))}
