"""Short-lived icon replicas; this module never writes Finder positions.

Finder's desktopPosition is writable, but its get/set Apple events provide no
documented atomic compare-and-set or crash rollback. A future experimental
real-movement mode would need best-effort restoration and must disclose races.
It is deliberately not implemented by this replica policy.

References:
https://developer.apple.com/library/archive/releasenotes/InterapplicationCommunication/RN-JavaScriptForAutomation/Articles/OSX10-10.html
https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_control_statements.html
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable, Iterable

from .desktop import DesktopTarget, Rect


@dataclass(frozen=True)
class IconReplicaFrame:
    """Placement of the replica only; alpha fades out without restoring a file."""
    rect: Rect
    opacity: float


class IconReplicaPolicy:
    """One bounded visual gesture, requiring its own explicit opt-in.

    Call begin only after obtaining a verified PNG in a worker. Each GUI frame
    passes the latest observed target and its snapshot time to frame(). If the
    icon moved, disappeared, became stale, or the setting changed, the replica
    is discarded. No restore operation is needed because Finder never moves.
    """
    DURATION = 0.65
    AMPLITUDE = 3.0
    COOLDOWN = 8.0
    SNAPSHOT_MAX_AGE = 2.0

    def __init__(self, clock: Callable[[], float] | None = None):
        self._clock = clock or time.monotonic
        self.enabled = False
        self.allowed_ids: frozenset[str] = frozenset()
        self._target: DesktopTarget | None = None
        self._started = 0.0
        self._last_started = -math.inf

    def configure(self, enabled: bool, allowed_ids: Iterable[str] = ()) -> bool:
        allowed = []
        try:
            for index, identity in enumerate(allowed_ids):
                if index >= 500:
                    break
                if isinstance(identity, str) and 0 < len(identity) <= 128:
                    allowed.append(identity)
        except TypeError:
            pass
        self.cancel()
        self.allowed_ids = frozenset(allowed) if enabled is True else frozenset()
        self.enabled = enabled is True and bool(self.allowed_ids)
        return self.enabled

    def cancel(self) -> None:
        self._target = None

    def _fresh(self, observed_at: float, now: float) -> bool:
        return (type(observed_at) in (int, float) and math.isfinite(observed_at)
                and 0 <= now - observed_at <= self.SNAPSHOT_MAX_AGE)

    @staticmethod
    def _valid(target: DesktopTarget) -> bool:
        rect = target.rect
        return (rect.valid and math.isfinite(rect.right) and math.isfinite(rect.bottom)
                and 16 <= rect.width <= 256 and 16 <= rect.height <= 256)

    def begin(self, target: DesktopTarget, observed_at: float) -> bool:
        now = self._clock()
        if (not self.enabled or target.id not in self.allowed_ids
                or self._target is not None or now - self._last_started < self.COOLDOWN
                or not self._fresh(observed_at, now) or not self._valid(target)):
            return False
        self._target = target
        self._started = self._last_started = now
        return True

    def frame(self, latest: DesktopTarget | None, observed_at: float) -> IconReplicaFrame | None:
        now = self._clock()
        target = self._target
        if (target is None or not self.enabled or target.id not in self.allowed_ids
                or latest != target or not self._fresh(observed_at, now)
                or not 0 <= now - self._started < self.DURATION):
            self.cancel()
            return None
        progress = (now - self._started) / self.DURATION
        dx = self.AMPLITUDE * math.sin(progress * 4 * math.pi) * (1 - progress)
        rect = target.rect
        return IconReplicaFrame(
            Rect(rect.x + dx, rect.y, rect.width, rect.height),
            min(1.0, max(0.0, (1 - progress) * 2)),
        )
