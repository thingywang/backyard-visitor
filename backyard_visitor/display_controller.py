"""Controller boundary between a fixed-size animal world and the real desktop."""
from __future__ import annotations

import math
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication

from .desktop import Rect, DesktopTarget
from .display_scale import SceneTransform, artwork_scale, normalize_user_scale


class DisplayScaling:
    @property
    def artwork_scale(self):
        return self._scene_transform.scale

    def to_display(self, x, y):
        return self._scene_transform.to_display(x, y)

    def to_scene(self, x, y):
        return self._scene_transform.to_scene(x, y)

    def scene_rect(self, *, available=True):
        r = self.screen.availableGeometry() if available else self.screen.geometry()
        return self._scene_transform.rect_to_scene(Rect(r.x(), r.y(), r.width(), r.height()))

    def scene_target(self, target):
        return DesktopTarget(target.id, target.label,
                             self._scene_transform.rect_to_scene(target.rect), target.supports_cat)

    def _init_display_scaling(self, saved):
        full, available = self.screen.geometry(), self.screen.availableGeometry()
        # Legacy save coordinates are real Qt points. Recover its full-screen
        # origin from its saved work area without shifting the menu-bar gap twice.
        ox = self.engine.bounds[0] - (available.x()-full.x())
        oy = self.engine.bounds[1] - (available.y()-full.y())
        old = SceneTransform(1., ox, oy)
        value = saved.get('display_transform') if isinstance(saved, dict) else None
        if isinstance(value, dict):
            try:
                candidate = SceneTransform(value['scale'], value['origin_x'], value['origin_y'])
                if (.6 <= candidate.scale <= 2.5
                        and abs(candidate.origin_x) <= 1_000_000
                        and abs(candidate.origin_y) <= 1_000_000):
                    old = candidate
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        self._scene_transform = old
        self._scene_initialized = False
        self._pending_artwork_scale = None
        self._scale_notice = False
        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.setInterval(180)
        self._scale_timer.timeout.connect(self._process_scale_request)
        self._furniture_stack_queued = False
        self._furniture_stack = None

    def _desired_artwork_scale(self):
        r = self.screen.geometry()
        return artwork_scale(r.width(), r.height(), self.settings.get('artwork_scale', 1.))

    def _update_scene_geometry(self):
        from .display_rebase import rebase_scene, is_scale_safe
        full = self.screen.geometry()
        old = self._scene_transform
        desired = self._desired_artwork_scale()
        initial = not self._scene_initialized
        apply_scale = initial or (is_scale_safe(self.engine)
                      and (not hasattr(self, "gate") or not self._scale_drag_active())
                      and not math.isclose(desired, old.scale, abs_tol=1e-9))
        target = SceneTransform(desired if apply_scale else old.scale, full.x(), full.y())
        self._scene_transform = target
        r = self.scene_rect()
        bounds = (r.x, r.y, r.width, r.height)
        if apply_scale:
            rebase_scene(self.engine, old, target, bounds)
            if hasattr(self, "gate"):
                self._rescale_overlays()
        elif tuple(self.engine.bounds) != bounds:
            # During screen replacement keep the current scale until normal
            # route invalidation has brought airborne/far cats safely home.
            self.engine.set_bounds(*bounds)
        full_scene = self.scene_rect(available=False)
        self.engine.set_depth_screen_rect((full_scene.x, full_scene.y, full_scene.width, full_scene.height))
        self._scene_initialized = True
        if not math.isclose(desired, target.scale, abs_tol=1e-9):
            self._pending_artwork_scale = desired
            self._scale_timer.start()
        elif self._pending_artwork_scale is not None:
            self._pending_artwork_scale = None
            self._scale_timer.stop()

    def set_artwork_scale(self, value):
        value = normalize_user_scale(value)
        if value == self.settings.get('artwork_scale', 1.):
            return
        self.settings['artwork_scale'] = value
        self._pending_artwork_scale = self._desired_artwork_scale()
        self._scale_timer.start()  # Coalesce the slider while it is moving.
        self.save_timer.start()

    def _scale_drag_active(self):
        fixed = [self.gate, self.bowl, self.bed_window]
        return (any(getattr(w, 'drag_active', False) for w in fixed)
                or any(getattr(w, 'drag_active', False) for w in self.gift_windows.values())
                or any(w.pressed or w.carrying for w in self.cat_windows.values()))

    def _process_scale_request(self):
        from .display_rebase import is_scale_safe, rebase_scene
        if self._quitting or self._pending_artwork_scale is None:
            return
        if not self._screen_ready:
            return  # Screen restoration restarts the request.
        desired = self._desired_artwork_scale()
        if math.isclose(desired, self.artwork_scale, abs_tol=1e-9):
            self._pending_artwork_scale = None
            self._scale_notice = False
            return
        if self._scale_drag_active() or self._teaser_session is not None:
            self._scale_timer.start(250)
            return
        if not is_scale_safe(self.engine):
            if not self.paused:
                self.engine.request_tree_return_all('called')
                for cid, visitor in self.engine.visitors.items():
                    if visitor.depth_trip is not None:
                        self.engine.request_depth_return(cid, 'called')
                # Existing edge-loss handling provides a continuous descent;
                # keep new native paths disabled until the resize has applied.
                self._rebuild_paths()
                self._sync()
                self.wake(reset_clock=False)
            if not self._scale_notice:
                self.note('大小已记下，等猫咪回到地面、结束当前互动后调整。')
                self._scale_notice = True
            self._scale_timer.start(250)
            return
        full = self.screen.geometry()
        old, target = self._scene_transform, SceneTransform(desired, full.x(), full.y())
        actual = self.screen.availableGeometry()
        r = target.rect_to_scene(Rect(actual.x(), actual.y(), actual.width(), actual.height()))
        rebase_scene(self.engine, old, target, (r.x, r.y, r.width, r.height))
        self._scene_transform = target
        r = self.scene_rect(available=False)
        self.engine.set_depth_screen_rect((r.x, r.y, r.width, r.height))
        self._pending_artwork_scale = None
        self._scale_notice = False
        self._cat_paint_keys.clear()
        self._rescale_overlays()
        # Keep the user's gate placement; only clamp its enlarged canvas.
        self.gate.move(max(actual.left(), min(self.gate.x(), actual.x()+actual.width()-self.gate.width())),
                       max(actual.top(), min(self.gate.y(), actual.y()+actual.height()-self.gate.height())))
        b = self.scene_rect()
        self.bin.move(*(round(v) for v in self.to_display(b.right-115, b.bottom-92)))
        self._rebuild_paths()
        self._reset_clock()
        self.changed()
        self.queue_furniture_stack()

    def _rescale_overlays(self):
        from .overlays import Overlay
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, Overlay) and widget.c is self:
                widget.rescale()
        self._cat_paint_keys.clear()

    def display_transform_state(self):
        t = self._scene_transform
        return dict(scale=t.scale, origin_x=t.origin_x, origin_y=t.origin_y)

    def queue_furniture_stack(self):
        if getattr(self, '_quitting', False) or getattr(self, '_furniture_stack_queued', False):
            return
        self._furniture_stack_queued = True
        QTimer.singleShot(0, self._reconcile_furniture_stack)

    def _reconcile_furniture_stack(self):
        self._furniture_stack_queued = False
        if self._quitting:
            return
        furniture = [w for w in (getattr(self, 'bowl', None), getattr(self, 'bed_window', None))
                     if w is not None and w.isVisible()]
        cats = [w for w in self.cat_windows.values() if w.isVisible()]
        if self.fight_overlay is not None and self.fight_overlay.isVisible():
            cats.append(self.fight_overlay)
        if not furniture or not cats:
            return
        if QApplication.platformName() == 'cocoa':
            try:
                from .cat_native import CocoaFurnitureStack, CocoaCatLevel
                if self._furniture_stack is None:
                    self._furniture_stack = CocoaFurnitureStack()
                if self.fight_overlay in cats:
                    w = self.fight_overlay
                    if not hasattr(w, '_cat_level'):
                        w._cat_level = CocoaCatLevel()
                    topmost = bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                    w._cat_level.configure(int(w.winId()), False, topmost=topmost)
                self._furniture_stack.configure([int(w.winId()) for w in furniture],
                                                [int(w.winId()) for w in cats])
            except (RuntimeError, OSError) as error:
                if not getattr(self, '_stack_warning_reported', False):
                    self._stack_warning_reported = True
                    self.note(f'家具与猫咪的显示层级暂未确认：{error}')
        else:
            for widget in furniture:
                for cat in cats:
                    widget.stackUnder(cat)
