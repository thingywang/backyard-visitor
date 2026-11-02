"""Conservative visible time between two affirmative display observations.

Unknown, sleeping or stale observations discard the pending interval. Time
comes from the existing monotonic animation clock, never the system calendar.
"""
from __future__ import annotations
import math
import time
from .desktop import Rect


class VisibleTimeAccumulator:
    def __init__(self):
        self.reset()

    def reset(self):
        self.awake = False
        self.sampled_at = None
        self.pending = {}

    def add(self, dt, visible_ids):
        if not self.awake or type(dt) not in (int, float) or not math.isfinite(dt) or not 0 < dt <= 5:
            return
        for cid in set(visible_ids):
            if isinstance(cid, str):
                self.pending[cid] = min(5., self.pending.get(cid, 0.) + dt)

    def observe(self, awake, now):
        if type(now) not in (int, float) or not math.isfinite(now):
            self.reset(); return {}
        valid = (awake is True and self.awake and self.sampled_at is not None
                 and 0 <= now-self.sampled_at <= 4.)
        accounted = self.pending.copy() if valid else {}
        self.pending.clear()
        self.awake = awake is True
        self.sampled_at = float(now)
        return accounted


class PresenceAccounting:
    """Controller mixin: Qt visibility plus worker-only read-only power query."""
    def _init_guest_presence(self):
        self._guest_clock = VisibleTimeAccumulator()
        self._guest_next_poll = -math.inf
        self._guest_clock_epoch = None
        self._guest_unsaved = 0.

    def _reset_guest_presence(self):
        self._guest_clock.reset()
        self._guest_next_poll = -math.inf
        self._guest_clock_epoch = None

    @staticmethod
    def _presence_window_visible(window):
        if window is None or not window.isVisible():
            return False
        get_handle = getattr(window, "windowHandle", None)
        handle = get_handle() if callable(get_handle) else None
        return handle is None or handle.isExposed()

    def _visible_guest_ids(self):
        bounds = self.screen.geometry()
        result = []
        for cid, visitor in self.engine.visitors.items():
            cat = self.engine.cats[cid]
            if cat.is_neighbor or cat.is_departed:
                continue
            window = self.cat_windows.get(cid)
            if (PresenceAccounting._presence_window_visible(window)
                    and getattr(window, '_has_visible_content', False)
                    and bounds.intersects(window.geometry())):
                result.append(cid)
            elif (visitor.state == 'fighting' and self.fight_overlay is not None
                    and PresenceAccounting._presence_window_visible(self.fight_overlay) and cid in self.fight_overlay.cat_ids
                    and bounds.intersects(self.fight_overlay.geometry())):
                result.append(cid)
        return result

    def _tick_guest_presence(self, dt, now):
        if (self.demo or self.paused or not self._screen_ready or not self._enable_native
                or not any(not self.engine.cats[cid].is_neighbor and not self.engine.cats[cid].is_departed
                           for cid in self.engine.visitors)):
            self._reset_guest_presence()
            return
        epoch = self._desktop_epoch
        if self._guest_clock_epoch != epoch:
            self._reset_guest_presence(); self._guest_clock_epoch = epoch
        self._guest_clock.add(dt, self._visible_guest_ids())
        if now < self._guest_next_poll or 'guest-presence' in self._workers:
            return
        geometry = self.screen.geometry()
        rect = Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())
        self._guest_next_poll = now+2.
        def query():
            # An unavailable platform query cannot consume a visitor's chance.
            try: awake = self.adapter.display_awake(rect)
            except (OSError, ValueError, TypeError, AttributeError): awake = None
            return epoch, time.monotonic(), awake
        self._run_worker('guest-presence', query, self._guest_presence_observed)

    def _guest_presence_observed(self, record):
        epoch, sampled_at, awake = record
        if (self._quitting or self.paused or not self._screen_ready
                or epoch != self._desktop_epoch or self._guest_clock_epoch != epoch):
            self._reset_guest_presence(); return
        intervals = self._guest_clock.observe(awake, sampled_at)
        changed = False
        for cid, duration in intervals.items():
            if self.engine.account_guest_exposure(duration, [cid], display_awake=True):
                changed = True
                self._guest_unsaved += duration
        events = self._events()
        if events:
            self._sync(); self.wake(reset_clock=False)
        if events or (changed and self._guest_unsaved >= 30.):
            self._guest_unsaved = 0.
            self.save_timer.start()
