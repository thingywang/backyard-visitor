"""Local-clock wallpaper intent; no Qt, timers, native I/O or model time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

SCHEMA_VERSION = 1


def phase_for_local(local_datetime: datetime) -> str:
    """Use the supplied local wall time, including its current time zone.

    The caller supplies datetime.now().astimezone() or an injected local clock.
    Nothing is converted through UTC or advanced using the cat simulation clock.
    """
    if not isinstance(local_datetime, datetime):
        raise TypeError('A local datetime is required')
    return 'morning' if 6 <= local_datetime.hour < 18 else 'dusk'


def _pair(scenes, theme_id, orientation):
    if not isinstance(theme_id, str) or orientation not in ('landscape', 'portrait'):
        return None
    matches = [scene for scene in scenes.values() if isinstance(scene, dict)
               and scene.get('theme_id') == theme_id and scene.get('orientation') == orientation
               and scene.get('cycle_ready') is True]
    if len(matches) != 2:
        return None
    pair = matches[0].get('cycle_pair')
    if not isinstance(pair, dict) or set(pair) != {'morning', 'dusk'} or pair['morning'] == pair['dusk']:
        return None
    for phase, scene_id in pair.items():
        scene = scenes.get(scene_id) if isinstance(scene_id, str) else None
        if (not isinstance(scene, dict) or scene.get('cycle_ready') is not True
                or scene.get('theme_id') != theme_id or scene.get('orientation') != orientation
                or scene.get('phase') != phase or scene.get('cycle_pair') != pair):
            return None
    return dict(pair)


def target_scene(scenes, theme_id, orientation, local_datetime: datetime) -> str | None:
    pair = _pair(scenes, theme_id, orientation)
    return pair[phase_for_local(local_datetime)] if pair else None


def _display_id(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and '\0' not in value


@dataclass(frozen=True, slots=True)
class CycleRequest:
    epoch: int
    scene_id: str
    theme_id: str
    orientation: str
    display_id: str


class CyclePolicy:
    """Latest-only intents with readback confirmation and bounded display scope.

    observe() requires a complete validated native snapshot from the controller.
    A bare path is insufficient: the controller must also check scaling/crop,
    actual primary size and stable display identity before marking known=True.
    Requests and resume verification are ephemeral; only confirmed identity and
    the explicit enabled setting persist. Failed writes retain confirmed state.
    """
    def __init__(self, scenes, state=None):
        self.scenes = scenes
        self.enabled = False
        self.theme_id = None
        self.orientation = None
        self.display_id = None
        self.confirmed_scene_id = None
        self.pending: CycleRequest | None = None
        self.pause_reason = None
        self.epoch = 0
        self._snapshot_ready = False
        self._resume_pending = False
        self._restore(state)

    def _restore(self, state):
        if (not isinstance(state, dict) or type(state.get('schema_version')) is not int
                or state['schema_version'] != SCHEMA_VERSION or type(state.get('enabled')) is not bool):
            return
        theme, orientation, display = (state.get(name) for name in ('theme_id', 'orientation', 'display_id'))
        pair = _pair(self.scenes, theme, orientation)
        confirmed = state.get('confirmed_scene_id')
        if pair is None or not _display_id(display) or (confirmed is not None and confirmed not in pair.values()):
            return
        self.theme_id, self.orientation, self.display_id = theme, orientation, display
        self.confirmed_scene_id, self.enabled = confirmed, state['enabled']
        self._resume_pending = self.enabled

    def enable(self, theme_id, orientation, display_id, confirmed_scene_id=None) -> None:
        pair = _pair(self.scenes, theme_id, orientation)
        if pair is None or not _display_id(display_id):
            raise ValueError('A complete day/night pair and primary display identity are required')
        if confirmed_scene_id is not None and confirmed_scene_id not in pair.values():
            raise ValueError('Confirmed scene must belong to the selected pair')
        self.epoch += 1
        self.enabled = True
        self.theme_id, self.orientation, self.display_id = theme_id, orientation, display_id
        self.confirmed_scene_id = confirmed_scene_id
        self.pending = None
        self.pause_reason = None
        self._snapshot_ready = False
        self._resume_pending = False

    def disable(self, reason=None) -> None:
        self.epoch += 1
        self.enabled = False
        self.pending = None
        self.pause_reason = reason
        self._snapshot_ready = False
        self._resume_pending = False

    def observe(self, current_scene_id, display_id, known=True) -> str:
        """Return ready/wait/paused/disabled; unknown snapshots grant no writes."""
        if not self.enabled:
            return 'disabled'
        if not known or not _display_id(display_id):
            self._snapshot_ready = False
            return 'wait'
        if display_id != self.display_id:
            self.disable('display_changed')
            return 'paused'
        pair = _pair(self.scenes, self.theme_id, self.orientation)
        if pair is None:
            self.disable('invalid_configuration')
            return 'paused'
        if current_scene_id in pair.values():
            # A fresh complete snapshot also reconciles an owned image left by
            # a late native task. Stored success must not hide that mismatch.
            self.confirmed_scene_id = current_scene_id
            self._resume_pending = False
            self._snapshot_ready = True
            return 'ready'
        if self._resume_pending or self.confirmed_scene_id is not None:
            self.disable('external_change')
            return 'paused'
        # First explicit enable authorizes replacing the currently external
        # wallpaper. A saved enabled flag alone never grants this exception.
        self._snapshot_ready = True
        return 'ready'

    def plan(self, local_datetime: datetime) -> CycleRequest | None:
        if not self.enabled or not self._snapshot_ready or self._resume_pending:
            return None
        scene_id = target_scene(self.scenes, self.theme_id, self.orientation, local_datetime)
        if scene_id is None:
            return None
        if self.pending is not None and self.pending.scene_id == scene_id:
            return self.pending
        if scene_id == self.confirmed_scene_id:
            if self.pending is not None:
                self.epoch += 1
                self.pending = None
            return None
        self.epoch += 1
        self.pending = CycleRequest(self.epoch, scene_id, self.theme_id, self.orientation, self.display_id)
        return self.pending

    def is_current(self, request) -> bool:
        return (self.enabled and isinstance(request, CycleRequest) and self.pending == request
                and request.epoch == self.epoch and request.display_id == self.display_id
                and request.theme_id == self.theme_id and request.orientation == self.orientation)

    def confirm(self, request, current_scene_id, display_id) -> bool:
        """Publish only the latest request after a complete matching readback."""
        if (not self.is_current(request) or current_scene_id != request.scene_id
                or display_id != self.display_id):
            return False
        self.confirmed_scene_id = request.scene_id
        self.pending = None
        self._snapshot_ready = True
        return True

    def fail(self, request) -> None:
        if self.is_current(request):
            self.pending = None

    def export(self) -> dict:
        return {'schema_version': SCHEMA_VERSION, 'enabled': self.enabled,
                'theme_id': self.theme_id, 'orientation': self.orientation,
                'display_id': self.display_id, 'confirmed_scene_id': self.confirmed_scene_id}
