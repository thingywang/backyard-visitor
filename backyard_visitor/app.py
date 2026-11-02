"""Qt application controller. Native desktop work never runs in a paint callback."""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

from PyQt6.QtCore import (QLockFile, QObject, QPointF, QRect, QRectF, QRunnable,
                         QStandardPaths, Qt, QThreadPool, QTimer, pyqtSignal)
from PyQt6.QtGui import QCursor, QIcon, QPainter, QPainterPath, QPixmap, QPixmapCache
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QInputDialog,
                            QLabel, QListWidget, QListWidgetItem, QMenu,
                            QSystemTrayIcon, QVBoxLayout, QMessageBox)

from .art import ASSETS, CatArt, paint_door, paint_gift
from .desktop import DesktopAdapter, DesktopTarget, Rect, make_adapter, visible_window_supports, visible_window_climbs, configure_cocoa_accessory_application
from .model import Engine, SPRITE_HEIGHT, SPRITE_WIDTH, STATE_VERSION
from .overlays import Bowl, CatWindow, CleanupBin, Gate, GiftWindow, IconRipple, IconReplica, Overlay, ScratchMarks, FightOverlay
from .storage import load_state, save_state
from .scenery import load_scenes, same_layout_geometry
from .wallpaper_cycle import CyclePolicy, target_scene
from .icon_motion import IconReplicaPolicy
from .bed_overlay import CatBed
from .display_controller import DisplayScaling
from .display_scale import normalize_user_scale
from .presence_clock import PresenceAccounting


class WorkerSignals(QObject):
    finished = pyqtSignal(str, object, object)


class Worker(QRunnable):
    def __init__(self, key: str, operation: Callable[[], Any]):
        super().__init__()
        self.key, self.operation = key, operation
        self.signals = WorkerSignals()

    def run(self):
        result, error = None, None
        try:
            result = self.operation()
        except Exception as exc:  # Deliver failures to the main thread, never Qt's exception handler.
            error = exc
        self.signals.finished.emit(self.key, result, error)


def default_state_dir() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))


class _ScreenSnapshot:
    """Safe geometry for hidden overlays while Qt has no live screen."""
    def __init__(self, screen):
        self._name = screen.name()
        self._geometry = QRect(screen.geometry())
        self._available = QRect(screen.availableGeometry())

    def name(self):
        return self._name

    def geometry(self):
        return QRect(self._geometry)

    def availableGeometry(self):
        return QRect(self._available)


class Controller(PresenceAccounting, DisplayScaling, QObject):
    def __init__(self, qt: QApplication, *, state_dir: str | Path | None = None,
                 demo: bool = False, hidden: bool = False,
                 adapter: DesktopAdapter | None = None, seed: int | None = None,
                 enable_native: bool = True, visit_limit: int = 3):
        super().__init__()
        self.qt, self.demo = qt, bool(demo)
        self._init_guest_presence()
        self.qt.setQuitOnLastWindowClosed(False)
        self._temporary = tempfile.TemporaryDirectory(prefix="backyard-visitor-demo-") if demo else None
        self.state_dir = Path(self._temporary.name if self._temporary else state_dir or default_state_dir())
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "state.json"
        self.lock = QLockFile(str(self.state_dir / "session.lock"))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            raise RuntimeError("这个存档已有一位后院访客在运行，请从小门或菜单栏打开它。")
        self._quitting = False
        self._software_updater = None
        self._teaser_session = None
        self._scratches = []
        self.fight_overlay = None
        self._journal = None
        self._art = None
        self._rear_art = None
        self._gift_art = None
        self._depth_art = None
        self._depth_snapshot = None
        self._depth_mask_suspended = set()
        self._tree_display_id = None
        self._tree_next_offer = 45.0
        self._tree_play_pending = None
        self._notes = deque(maxlen=120)
        self._cat_paint_keys: dict[str, tuple] = {}
        self._painted_gate = None
        self._painted_food = None
        self._save_error_warned = False
        self.logger = logging.getLogger(f"backyard-visitor.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self._log_handler = RotatingFileHandler(self.state_dir / "visitor.log", maxBytes=65_536, backupCount=1, encoding="utf-8")
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(self._log_handler)
        saved = None if self.demo else load_state(self.state_path)
        self.engine = Engine(saved, seed=seed, demo=demo, visit_limit=visit_limit)
        self.scenes = load_scenes(ASSETS)
        self.wallpaper_cycle = CyclePolicy(self.scenes, (saved or {}).get("wallpaper_cycle")
                                           if isinstance(saved, dict) and not self.demo else None)
        self._wallpaper_generation = 0
        self._wallpaper_pending = None
        self._wallpaper_depth_wait = False
        self._cycle_enable_request = None
        self._cycle_initial_snapshot = None
        self._cycle_poll_pending = False
        self._cycle_notice = None
        self.settings = {"overlay_topmost": True, "gate_visible": True, "window_paths": False, "icons_enabled": False,
                         "icon_ids": [], "icon_replica": False, "realistic_mouse": True, "calm_mode": self.engine.calm_mode,
                         "preset_wallpaper": False, "wallpaper_id": "backyard-dusk", "screen_name": "", "recycle_enabled": False, "depth_enabled": False, "tree_enabled": False, "artwork_scale": 1.0}
        if isinstance(saved, dict) and isinstance(saved.get("settings"), dict):
            values = saved["settings"]
            for key in ("overlay_topmost", "gate_visible", "window_paths", "icons_enabled", "icon_replica", "realistic_mouse", "calm_mode", "preset_wallpaper", "recycle_enabled", "depth_enabled", "tree_enabled"):
                if type(values.get(key)) is bool:
                    self.settings[key] = values[key]
            if isinstance(values.get("icon_ids"), list):
                self.settings["icon_ids"] = [i for i in values["icon_ids"][:500] if isinstance(i, str) and len(i) <= 128]
            if isinstance(values.get("wallpaper_id"), str) and values["wallpaper_id"] in self.scenes:
                self.settings["wallpaper_id"] = values["wallpaper_id"]
            if isinstance(values.get("screen_name"), str):
                self.settings["screen_name"] = values["screen_name"][:128]
        if isinstance(saved, dict) and isinstance(saved.get("settings"), dict):
            self.settings["artwork_scale"] = normalize_user_scale(saved["settings"].get("artwork_scale", 1.))
        self.engine.calm_mode = self.settings["calm_mode"]
        self.adapter = adapter or make_adapter()
        self._enable_native = enable_native
        self.settings["window_paths"] &= self.adapter.capabilities.get("windows", False)
        self.settings["icons_enabled"] &= self.adapter.capabilities.get("desktop_icons", False)
        self.adapter.icons_enabled = self.settings["icons_enabled"]
        self.settings["recycle_enabled"] &= self.adapter.capabilities.get("recycle_rect",False)
        self.adapter.recycle_enabled = self.settings["recycle_enabled"]
        if isinstance(saved, dict):
            backup = saved.get("wallpaper_backup")
            if isinstance(backup, dict) and isinstance(backup.get("path"), str) and isinstance(backup.get("options", {}), dict):
                self.adapter.wallpaper_backup = backup
        self._install_wallpaper_recovery()
        screens = self.qt.screens()
        self.screen = next((s for s in screens if s.name() == self.settings["screen_name"]), self.qt.primaryScreen())
        if self.screen is None:
            raise RuntimeError("没有可用的图形屏幕")
        self.settings["screen_name"] = self.screen.name()
        self._screen_ready = True
        self._screen_connections = {}
        self._screen_signature = None
        self._desktop_epoch = 0
        self._recycle_checked_epoch = 0
        self.paused, self.current_fps = False, 0
        self._last_tick = time.monotonic()
        self._last_wall_tick = time.time()
        self._last_cursor = QCursor.pos()
        self._windows: list[DesktopTarget] = []
        self._icons: list[DesktopTarget] = []
        self._windows_checked = -math.inf
        self._icons_checked = -math.inf
        self._wallpaper_checked = -math.inf
        self._wallpaper_match = False
        self._icon_epoch = 0
        self._replica_epoch = 0
        self._icon_replica = None
        self._replica_policy = IconReplicaPolicy()
        self._replica_attempted = -math.inf
        self._configure_icon_replica()
        self._recycle_epoch = 0
        self.dragging_gift = False
        self._previous_states: dict[str, str] = {}
        self._ripples: list[IconRipple] = []
        self._workers: dict[str, tuple[Worker, Callable, int | None]] = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._mask_data = self._read_mask()
        if not self._mask_data.get("occluders"):
            self.settings["depth_enabled"] = False
        if not self._mask_data.get("layout_geometry", {}).get("supports"):
            self.settings["tree_enabled"] = False
        self._init_display_scaling(saved)
        self._apply_screen_geometry()
        if saved is None and not self.demo:
            # Only a new save starts at the lower-right. Restored placements
            # and later screen changes keep their existing geometry behavior.
            left, top, width, height = self.engine.bounds
            self.engine.set_bowl(left + width - 55, top + height - 38)
        self.gate = Gate(self)
        self.bowl = Bowl(self)
        self.bin = CleanupBin(self)
        self.bed_window = CatBed(self)
        self.cat_windows: dict[str, CatWindow] = {}
        self.gift_windows: dict[str, GiftWindow] = {}
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(650)
        self.save_timer.timeout.connect(self._save)
        self.native_timer = QTimer(self)
        self.native_timer.setInterval(2000)
        self.native_timer.timeout.connect(self._native_poll)
        self.wallpaper_timer = QTimer(self)
        self.wallpaper_timer.setInterval(60_000)
        self.wallpaper_timer.timeout.connect(self._poll_wallpaper_cycle)
        self.recycle_timer = QTimer(self)
        self.recycle_timer.setInterval(400)
        self.recycle_timer.timeout.connect(self._poll_recycle)
        self._position_fixed_overlays()
        self.tray = QSystemTrayIcon(self._icon(), self)
        self.tray.setToolTip("后院访客 · 小门轻轻合着")
        self.tray.activated.connect(self._tray_activated)
        self._tray_menu = self._build_menu()
        self.tray.setContextMenu(self._tray_menu)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.gate.setVisible(self.settings["gate_visible"] and self._screen_ready)
        self.place_bowl()
        self._sync()
        self.note("体验模式：进度独立，关闭后不会改变正式存档。" if demo else "小门关着。准备好后，再迎接下一位访客。")
        # Identity recovery must be visible even when the closed gate has no timer.
        self._events()
        if self._wallpaper_recovery_conflict:
            self.note("壁纸恢复记录存在冲突，已保留两份记录；解决前不会更改系统壁纸。")
        if not hidden or self._needs_journal_entry():
            self.show_journal()
        self.qt.aboutToQuit.connect(self.shutdown)
        self.qt.screenRemoved.connect(self._screen_removed)
        self.qt.screenAdded.connect(self._screen_added)
        self.qt.primaryScreenChanged.connect(self._primary_screen_changed)
        for screen in screens:
            self._bind_screen(screen)
        self.wake()
        if self.wallpaper_cycle.enabled and not self.demo:
            self.wallpaper_timer.start()
            QTimer.singleShot(0, self._poll_wallpaper_cycle)
        readable_save = (saved is None or (isinstance(saved, dict)
                         and type(saved.get("version", 0)) is int
                         and 0 <= saved.get("version", 0) <= STATE_VERSION))
        if (not self.demo and readable_save
                and ((saved or {}).get("cat_relations") != self.engine.relations.export()
                     or (saved or {}).get("cats") != self.engine.export()["cats"])):
            # Persist freshly assigned/migrated identities even if the gate
            # stays closed. Reuse the existing atomic-save timer.
            self.save_timer.start()

    @property
    def art(self):
        if self._art is None:
            self._art = CatArt()
        return self._art

    @property
    def rear_art(self):
        if self._rear_art is None:
            from .rear_art import RearArt
            self._rear_art = RearArt()
        return self._rear_art

    def paint_gift(self, painter, rect, kind, *, facing=-1):
        if kind == "fur" or (kind == "toy_mouse" and not self.settings["realistic_mouse"]):
            paint_gift(painter, rect, kind, facing=facing)
            return
        if self._gift_art is None:
            from .gift_art import GiftArt
            self._gift_art = GiftArt()
        if not self._gift_art.paint(painter, rect, kind,
                                   realistic_mouse=self.settings["realistic_mouse"], facing=facing):
            paint_gift(painter, rect, kind, facing=facing)

    def set_realistic_mouse(self, enabled):
        self.settings["realistic_mouse"] = bool(enabled)
        for window in (*self.cat_windows.values(), *self.gift_windows.values()):
            window.update()
        self.save_timer.start()

    @property
    def journal(self):
        if self._journal is None:
            from .panel import Journal
            self._journal = Journal(self)
            self._journal.pause_check.setChecked(self.paused)
            for entry in self._notes:
                self._journal.note(entry)
        return self._journal

    def _icon(self) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        paint_door(painter, QRectF(1, 1, 30, 30), False)
        painter.end()
        return QIcon(pixmap)

    def _read_mask(self):
        return self.scenes.get(self.settings["wallpaper_id"], {})

    def _wallpaper_path(self, scene_id=None):
        scene = self.scenes.get(scene_id or self.settings["wallpaper_id"])
        return ASSETS / scene["file"] if scene else None

    def _matches_wallpaper(self, current):
        expected = self._wallpaper_path()
        return bool(current and expected and Path(current).resolve() == expected.resolve())

    @staticmethod
    def _geometry_key(screen):
        available, full = screen.availableGeometry(), screen.geometry()
        ratio = screen.devicePixelRatio() if hasattr(screen, "devicePixelRatio") else 1.0
        return (available.x(), available.y(), available.width(), available.height(),
                full.x(), full.y(), full.width(), full.height(), ratio)

    def _apply_screen_geometry(self):
        self._update_scene_geometry()
        self._screen_signature = self._geometry_key(self.screen)
        self._screen_snapshot = _ScreenSnapshot(self.screen)

    def _position_fixed_overlays(self):
        b = self.scene_rect()
        self.gate.move(*(round(v) for v in self.to_display(b.left+18, b.bottom-44)))
        self.bin.move(*(round(v) for v in self.to_display(b.right-116, b.bottom-93)))
        self.place_bowl()

    def place_bowl(self):
        if not getattr(self.bowl, "drag_active", False):
            self.bowl.move(*(round(v) for v in self.to_display(self.engine.bowl[0]-31, self.engine.bowl[1]-20)))
        self.bowl.setVisible(self._screen_ready and (self.engine.gate_open or bool(self.engine.visitors) or self.engine.food > 0))
        if self._painted_food != self.engine.food:
            self._painted_food = self.engine.food
            self.bowl.update()

    def _bind_screen(self, screen):
        if screen in self._screen_connections:
            return
        slot = lambda *_args, source=screen: self._geometry_changed(source)
        connections = []
        for name in ("availableGeometryChanged", "geometryChanged", "logicalDotsPerInchChanged"):
            signal = getattr(screen, name, None)
            if signal is not None:
                signal.connect(slot)
                connections.append((signal, slot))
        self._screen_connections[screen] = connections

    def _unbind_screen(self, screen):
        for signal, slot in self._screen_connections.pop(screen, []):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # Qt may already have destroyed a removed QScreen.

    def _refresh_screen_choices(self):
        if self._journal is not None:
            self._journal.refresh_screens()

    def _reset_clock(self, now=None, wall=None, cursor=None):
        self._last_tick = time.monotonic() if now is None else now
        self._last_wall_tick = time.time() if wall is None else wall
        self._last_cursor = QCursor.pos() if cursor is None else cursor

    def _invalidate_desktop_context(self, depth_reason="desktop_changed", *, preserve_tree=False):
        self._depth_snapshot = None
        if not preserve_tree:
            self.engine.request_tree_return_all(depth_reason)
        for cid, visitor in self.engine.visitors.items():
            if visitor.depth_trip is not None:
                self._depth_mask_suspended.add(cid)
                self.engine.request_depth_return(cid, depth_reason)
        self.stop_play("screen_changed")
        if self.fight_overlay is not None:
            self.fight_overlay.dismiss_menu()
        self._clear_scratches()
        self._desktop_epoch += 1
        self._recycle_checked_epoch = None
        self._windows, self._icons = [], []
        self._windows_checked = self._icons_checked = self._wallpaper_checked = -math.inf
        self._wallpaper_match = False
        self._cancel_icon_replica()
        for ripple in list(self._ripples):
            self._remove_ripple(ripple)
        self.engine.set_window_edges([])
        self.engine.set_paths([])
        self.engine.set_gift_spots([])

    def _cancel_screen_drags(self):
        # A drag's global coordinates belong to its old screen. Keep the model's
        # newly rebased position and end the gesture without collecting a gift.
        self.gate.drag_active = False
        self.gate.dragged = True
        self.bowl.drag_active = False
        self.bowl.dragged = True
        self.bowl.feed_click.stop()
        self.bed_window.drag_active = False
        for window in self.gift_windows.values():
            if window.drag_active:
                self.engine.set_gift_held(window.gift_id, False)
            window.drag_active = False
            window.dragged = True
        for window in self.cat_windows.values():
            window.hold.stop()
            window.pressed = window.stroking = False
            visitor = window.visitor()
            if window.carrying and visitor is not None:
                self.engine.release(window.cat_id, visitor.x, visitor.y)
            window.carrying = False
            window.unsetCursor()
        self.end_gift_drag()
        self.bin.hide()

    def _use_screen(self, screen):
        geometry = screen.availableGeometry()
        if geometry.width() < SPRITE_WIDTH or geometry.height() < SPRITE_HEIGHT:
            return False  # Ignore a transient zero-sized display configuration.
        changed = not self._screen_ready or screen is not self.screen
        self.screen, self._screen_ready = screen, True
        self._bind_screen(screen)
        self.settings["screen_name"] = screen.name()
        if changed or self._geometry_key(screen) != self._screen_signature:
            self._invalidate_desktop_context()
            self._apply_screen_geometry()
            self._cancel_screen_drags()
            self._position_fixed_overlays()
            self._reset_clock()
            self.gate.setVisible(self.settings["gate_visible"])
            # _sync positions/clips cat and gift windows before making them
            # visible; restoring hidden windows here would expose old geometry.
            self.changed()
            self._native_poll()
        self._refresh_screen_choices()
        return True

    def change_screen(self, index: int):
        screens = self.qt.screens()
        if not self._quitting and 0 <= index < len(screens):
            self._use_screen(screens[index])

    def _screen_added(self, screen):
        if self._quitting:
            return
        newly_added = screen not in self._screen_connections
        self._bind_screen(screen)
        if not self._screen_ready:
            self._use_screen(screen)
        else:
            if newly_added:
                self._invalidate_desktop_context()
                self._reset_clock()
                self.wake()
            self._refresh_screen_choices()
        self._poll_wallpaper_cycle()

    def _suspend_display(self):
        self._clear_fight_overlay()
        # Queued paints must not touch a QScreen that Qt has already removed or
        # whose geometry is temporarily invalid during display reconfiguration.
        self.screen = self._screen_snapshot
        self._screen_ready = False
        self._invalidate_desktop_context()
        self._cancel_screen_drags()
        self._reset_clock()
        for window in (self.gate, self.bowl, self.bin, self.bed_window, *self.cat_windows.values(), *self.gift_windows.values()):
            window.hide()
        self.wake()

    def _screen_removed(self, removed):
        self._unbind_screen(removed)
        if self._quitting:
            return
        if removed is self.screen or not self._screen_ready:
            remaining = [s for s in self.qt.screens() if s is not removed]
            primary = self.qt.primaryScreen()
            ordered = ([primary] if primary in remaining else []) + [s for s in remaining if s is not primary]
            if not any(self._use_screen(screen) for screen in ordered):
                self._suspend_display()
        else:
            self._invalidate_desktop_context()
            self.wake()
        self._refresh_screen_choices()

    def _primary_screen_changed(self, screen):
        if self._quitting:
            return
        self._wallpaper_generation += 1
        self._wallpaper_pending = None
        if self.wallpaper_cycle.pending is not None:
            self.wallpaper_cycle.fail(self.wallpaper_cycle.pending)
        self._cycle_poll_pending = self.wallpaper_cycle_enabled
        self._poll_wallpaper_cycle()
        if not self._screen_ready and screen is not None:
            self._use_screen(screen)
        else:
            # A new primary changes wallpaper identity and native coordinates,
            # but should not silently move a user-selected screen.
            self._invalidate_desktop_context()
            self._reset_clock()
            self.wake()

    def _geometry_changed(self, source=None, *_):
        if self._quitting:
            return
        self._poll_wallpaper_cycle()
        if not self._screen_ready:
            if source in self.qt.screens() and source.name() == self.settings["screen_name"]:
                self._use_screen(source)
            return
        if source is not None and source is not self.screen:
            return
        if not self._use_screen(self.screen):
            self._suspend_display()

    def _recover_clock_gap(self, now=None, wall=None, cursor=None):
        if not self.current_fps or not self._screen_ready:
            return False
        now = time.monotonic() if now is None else now
        wall = time.time() if wall is None else wall
        elapsed, wall_elapsed = now - self._last_tick, wall - self._last_wall_tick
        limit = max(0.25, self.timer.interval() / 1000 * 2 + 0.1)
        # macOS monotonic time may exclude machine sleep. Wall time is only a
        # discontinuity detector; it is never used as simulation elapsed time.
        if 0 <= elapsed <= limit and -limit <= wall_elapsed <= limit:
            return False
        self._reset_clock(now, wall, cursor)
        self._invalidate_desktop_context()
        self._cancel_screen_drags()
        self._sync()
        self.wake(reset_clock=False)
        self._poll_wallpaper_cycle()
        return True

    def show_journal(self):
        self.stop_play("journal_opened")
        self.journal.show()
        self.journal.raise_()
        self.journal.activateWindow()

    def _needs_journal_entry(self):
        """Keep one usable entry when neither desktop gate nor tray is present."""
        return (not self._quitting and not self.gate.isVisible()
                and not (QSystemTrayIcon.isSystemTrayAvailable() and self.tray.isVisible()))

    def release_journal(self, window):
        if self._quitting or window is not self._journal or window.isVisible():
            return
        if self._needs_journal_entry():
            self.show_journal()
            return
        self._journal = None
        window.deleteLater()
        # Only ephemeral Qt file-loader cache is cleared; the tiny sprite cache
        # remains owned by CatArt. Hidden retina window backing stores can be large.
        QPixmapCache.clear()
        self._release_unused_art()

    def _release_unused_art(self):
        if self._rear_art is not None and not any(
                visitor.rear_blend > 0 or visitor.state in {"window_rear", "top_entering", "top_leaving"}
                for visitor in self.engine.visitors.values()):
            # Rare upper visits should not retain extra coat pixmaps throughout
            # a later two-cat game. Current ordinary frames keep their owners.
            self._rear_art = None
            QPixmapCache.clear()
        if not self.engine.visitors and self._journal is None and (self._art is not None or self._rear_art is not None):
            self._art = None
            self._rear_art = None
            QPixmapCache.clear()

    def note(self, message: str):
        self.logger.info(message)
        entry = f"{datetime.now():%H:%M}  {message}"
        self._notes.append(entry)
        if self._journal is not None:
            self._journal.note(entry)

    def _warning(self, message: str):
        self.note(message)
        if hasattr(self, "tray") and self.tray.isVisible():
            self.tray.showMessage("后院访客", message, QSystemTrayIcon.MessageIcon.Warning, 5000)

    def toggle_gate(self):
        if self.engine.gate_open:
            self._tree_play_pending = None
            self.stop_play("gate_closed")
        self.engine.open_gate(not self.engine.gate_open)
        self.changed()

    def close_gate(self):
        self._tree_play_pending = None
        self.stop_play("gate_closed")
        self.engine.open_gate(False)
        self.changed()

    def feed(self):
        if not self.engine.feed():
            self.note("碗里已经有三份了，留一点给下一次。")
        self.changed()

    def rename_cat(self, cat_id: str):
        cat = self.engine.cats.get(cat_id)
        if cat is None or not self.engine.is_discovered(cat_id) or not cat.is_neighbor:
            return
        name, accepted = QInputDialog.getText(self._journal, "给访客起个昵称", "你想怎样轻声叫它？", text=cat.nickname)
        if accepted:
            if self.engine.name_cat(cat_id, name) == "invalid":
                self.note("昵称需要至少一个文字或数字。")
            self.changed()

    def call_cat(self, cat_id: str):
        if self.engine.call_cat(cat_id) == "absent":
            self.note(f"{self.engine.cat_reference(cat_id)}现在不在桌面，等下一次来访再叫{self.engine.cat_reference(cat_id)}吧。")
        self.changed()

    def adopt(self, cat_id: str):
        if not self.engine.adopt(cat_id):
            self.note("长期陪伴需要满格信任；目前只邀请一位常住伙伴。")
        self.changed()

    def cancel_residence(self, cat_id: str) -> bool:
        if self._quitting or not self.engine.cancel_residence(cat_id):
            return False
        self.changed()
        return True

    def place_bed(self, cat_id: str):
        if self._quitting:
            return
        if not self.engine.place_bed(cat_id):
            self.note("等访客答应常住后，就可以为它布置猫窝。")
        self.changed()

    def move_bed(self, x):
        if self._quitting:
            return
        self.engine.move_bed(x)
        self.changed()

    def remove_bed(self):
        if self._quitting:
            return
        self.engine.remove_bed()
        self.changed()

    def pet(self, cat_id: str):
        self.engine.pet(cat_id)
        self.changed()

    def intervene(self, cat_id: str):
        self.engine.intervene(cat_id)
        self.changed()

    def start_play(self, cat_id: str):
        visitor = self.engine.visitors.get(cat_id)
        if visitor is not None and visitor.tree_trip is not None:
            if self.paused or self._quitting or not self.engine.gate_open or self._teaser_session is not None:
                return False
            self._tree_play_pending = cat_id
            self.engine.request_tree_return(cat_id, "play")
            self.note("等它从爬架回到桌面，再一起玩。")
            self.changed()
            return True
        if self._quitting or self.paused or not self._screen_ready or self._teaser_session is not None:
            return False
        from .play_session import TeaserSession
        session = TeaserSession(self, cat_id, parent=self)
        self._teaser_session = session
        session.finished.connect(lambda reason, s=session: self._play_finished(s, reason))
        if session.start():
            return True
        if self._teaser_session is session:
            self._teaser_session = None
            session.deleteLater()
        self.note(f"{self.engine.cat_reference(cat_id)}现在正忙，等{self.engine.cat_reference(cat_id)}停下来再一起玩吧。")
        return False

    def stop_play(self, reason="cancelled"):
        self._tree_play_pending = None
        if self._teaser_session is not None:
            self._teaser_session.stop(reason)

    def _play_finished(self, session, reason):
        if self._teaser_session is session:
            self._teaser_session = None
        if session.error and not self._quitting:
            self._warning("逗猫已结束，鼠标恢复了。稍后可以再试一次。")
        self._reset_clock()
        session.deleteLater()

    def set_gate_visible(self, enabled: bool):
        """Hide the desktop control without changing the invitation gate."""
        if type(enabled) is not bool:
            return False
        self.settings["gate_visible"] = enabled
        self.gate.drag_active = False
        self.gate.dragged = True
        self.gate.setVisible(enabled and self._screen_ready and not self._quitting)
        self._set_check("gate_visible_check", enabled)
        self.save_timer.start()
        if self._needs_journal_entry() and (self._journal is None or not self._journal.isVisible()):
            self.show_journal()
        return True

    def set_overlay_topmost(self, enabled: bool):
        self.stop_play("display_changed")
        if self.fight_overlay is not None:
            self.fight_overlay.dismiss_menu()
        enabled = bool(enabled)
        self.settings["overlay_topmost"] = enabled
        for window in self.qt.topLevelWidgets():
            if isinstance(window, Overlay) and window.c is self:
                window.set_topmost(enabled)
        if self.bed_window.isVisible():
            self.bed_window.lower()
        self.queue_furniture_stack()
        self._set_check("topmost_check", enabled)
        # A normal changed()/sync pass may re-show the bowl; this setting must
        # preserve every overlay's current visibility exactly.
        self.save_timer.start()

    def set_calm_mode(self, enabled: bool):
        self.settings["calm_mode"] = bool(enabled)
        self.engine.calm_mode = bool(enabled)
        self.engine.tick(0.001)
        self.changed()

    def set_paused(self, enabled: bool):
        self._reset_guest_presence()
        enabled = bool(enabled)
        if enabled == self.paused:
            return  # Repeated UI values must not cancel a new manual gesture.
        self.paused = enabled
        self._invalidate_desktop_context()
        self._cancel_screen_drags()
        self._reset_clock()
        # Release carry/held states and roll back uncommitted bowl/gift drags
        # immediately, even while paused. This sync never advances model time.
        self._sync()
        self.wake()
        if self._journal is not None:
            self._journal.refresh()

    def set_window_paths(self, enabled: bool):
        enabled = bool(enabled) and self.adapter.capabilities.get("windows", False)
        self.settings["window_paths"] = enabled
        self._desktop_epoch += 1
        self._windows, self._windows_checked = [], -math.inf
        self._rebuild_paths()
        self.changed()
        if enabled:
            self._native_poll()

    def _set_check(self, name: str, value: bool):
        widget = getattr(self._journal, name, None)
        if widget:
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)

    def set_icons_enabled(self, enabled: bool):
        self._icon_epoch += 1
        epoch = self._icon_epoch
        if not enabled:
            self.settings["icons_enabled"] = False
            self.adapter.enable_desktop_icons(False)
            self._configure_icon_replica()
            self._icons = []
            self._rebuild_paths()
            self.changed()
            return
        if not self.adapter.capabilities.get("desktop_icons", False):
            self._set_check("icons_check", False)
            self.note("当前系统还没有可用的桌面图标接口。")
            return
        self.note("正在请求 Finder 图标访问；可以在系统提示中允许或拒绝。")
        def request():
            ok = self.adapter.request_icon_access()
            return ok, self.adapter.last_error
        def received(result):
            if epoch != self._icon_epoch:
                self.adapter.enable_desktop_icons(False)
                return
            ok, error = result
            self.settings["icons_enabled"] = bool(ok)
            self._configure_icon_replica()
            self._set_check("icons_check", bool(ok))
            if not ok:
                self.note("未启用图标互动。" + (f" {error}" if error else ""))
            else:
                self.note("图标访问已启用；请在“选择允许互动的图标”中选择目标。")
            self.changed()
            if ok:
                self._native_poll()
        self._run_worker("icon-permission", request, received)

    def _cancel_icon_replica(self):
        self._replica_epoch += 1
        self._replica_policy.cancel()
        if self._icon_replica is not None:
            window, self._icon_replica = self._icon_replica, None
            window.close()
            window.deleteLater()

    def _configure_icon_replica(self):
        self._cancel_icon_replica()
        requested = (self.settings["icon_replica"] and self.settings["icons_enabled"]
                     and bool(self.settings["icon_ids"]))
        enabled = self.adapter.configure_icon_replica(requested, self.settings["icon_ids"])
        self._replica_policy.configure(enabled, self.settings["icon_ids"])

    def set_icon_replica(self, enabled):
        self.settings["icon_replica"] = bool(enabled) and self.adapter.capabilities.get("icon_replica", False)
        self._configure_icon_replica()
        self.changed()

    def _request_icon_replica(self, target):
        if (self._quitting or not self._screen_ready or not self._replica_policy.enabled or self.paused or not self._enable_native
                or time.monotonic() - self._replica_attempted < 8):
            return
        epoch = self._replica_epoch
        if self._journal is not None and self._journal.isVisible():
            r = self._journal.frameGeometry()
            if target.rect.intersects(Rect(r.x(), r.y(), r.width(), r.height())):
                return
        self._replica_attempted = time.monotonic()
        def query():
            icons = self.adapter.desktop_icons()
            observed = time.monotonic()
            latest = next((i for i in icons if i.id == target.id), None)
            windows = self.adapter.windows()
            windows_ok = not bool(self.adapter.last_error)
            if (not windows_ok or latest != target
                    or any(w.rect.intersects(target.rect) for w in windows)):
                return icons, observed, None
            return icons, observed, self.adapter.icon_replica_png(target.id)
        def received(result):
            self._recover_clock_gap()
            if self._quitting or epoch != self._replica_epoch or self.paused or not self.settings["icons_enabled"]:
                return
            icons, observed, png = result
            if not self.adapter.icons_enabled:
                self.settings["icons_enabled"] = False
                self.settings["icon_replica"] = False
                self._icons = []
                self._set_check("icons_check", False)
                self._set_check("icon_motion_check", False)
                self._configure_icon_replica()
                self._rebuild_paths()
                self.save_timer.start()
                return
            self._icons, self._icons_checked = icons, observed
            self._rebuild_paths()
            if not self.adapter.icon_replica_enabled:
                self.settings["icon_replica"] = False
                self._set_check("icon_motion_check", False)
                self._configure_icon_replica()
                self.save_timer.start()
                return
            if not png:
                return
            latest = next((i for i in icons if i.id == target.id), None)
            if latest != target or target not in self._selected_icons():
                return
            if self._journal is not None and self._journal.isVisible():
                r = self._journal.frameGeometry()
                if target.rect.intersects(Rect(r.x(), r.y(), r.width(), r.height())):
                    return
            if any(w.rect.intersects(target.rect) for w in self._windows):
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(png, "PNG") or max(pixmap.width(), pixmap.height()) > 128:
                return
            if self._replica_policy.begin(latest, observed):
                self._icon_replica = IconReplica(self, latest, pixmap)
                if not self._icon_replica.start():
                    self._cancel_icon_replica()
        self._run_worker("icon-replica", query, received)

    def set_recycle_enabled(self, enabled):
        self._recycle_epoch += 1
        epoch = self._recycle_epoch
        if not enabled:
            self.settings["recycle_enabled"] = False
            self.adapter.enable_recycle(False)
            self.recycle_timer.stop()
            self.changed()
            return
        if not self.adapter.capabilities.get("recycle_rect",False):
            self._set_check("recycle_check",False)
            self.note("当前系统使用应用内清理篮。")
            return
        def request():
            ok=self.adapter.request_recycle_access()
            return ok,self.adapter.last_error
        def received(result):
            if epoch != self._recycle_epoch:
                self.adapter.enable_recycle(False)
                return
            ok,error=result
            self.settings["recycle_enabled"]=bool(ok)
            self._set_check("recycle_check",bool(ok))
            self.note("可以把小礼物拖向可见的 Dock 废纸篓了。" if ok else str(error or "暂未授权，仍可使用清理篮。"))
            self.changed()
        self._run_worker("recycle-permission",request,received)

    def begin_gift_drag(self):
        self.dragging_gift=True
        if self.settings["recycle_enabled"] and self._enable_native:
            self.recycle_timer.start()
            self._poll_recycle()

    def end_gift_drag(self):
        self.dragging_gift=False
        self.recycle_timer.stop()

    def recycle_target(self):
        if (not self._screen_ready or self._quitting or self.paused or not self.settings["recycle_enabled"]
                or self._recycle_checked_epoch != self._desktop_epoch):
            return None
        target = self.adapter.recycle_rect()
        return target if target and self._inside_selected_screen(target, available=False) else None

    def _poll_recycle(self):
        if self._quitting or not self._screen_ready or not self.dragging_gift or not self.settings["recycle_enabled"]:
            self.recycle_timer.stop()
            return
        epoch, requested = self._desktop_epoch, time.monotonic()
        def received(result):
            if (epoch != self._desktop_epoch or self._quitting or self.paused
                    or time.monotonic() - requested > 1):
                return
            self._recycle_checked_epoch = epoch
            if not self.adapter.recycle_enabled:
                self.settings["recycle_enabled"]=False
                self._set_check("recycle_check",False)
                self.recycle_timer.stop()
                self.note("废纸篓定位暂不可用，可继续拖入清理篮。")
                self.save_timer.start()
        self._run_worker("recycle-poll",self.adapter.query_recycle_rect,received)

    def choose_icons(self):
        if not self.adapter.capabilities.get("desktop_icons", False):
            self.note("当前系统还没有可用的桌面图标接口。")
            return
        self._icon_epoch += 1
        epoch = self._icon_epoch
        def query():
            if not self.adapter.icons_enabled and not self.adapter.request_icon_access():
                return False, [], self.adapter.last_error
            icons = self.adapter.desktop_icons()
            return self.adapter.icons_enabled, icons, self.adapter.last_error
        def received(result):
            if epoch != self._icon_epoch:
                self.adapter.enable_desktop_icons(False)
                return
            allowed, icons, error = result
            if not allowed:
                self._set_check("icons_check", False)
                self.settings["icons_enabled"] = False
                self.note("无法读取图标。" + (f" {error}" if error else ""))
                self.changed()
                return
            self._icons = icons
            self._icons_checked = time.monotonic()
            self._show_icon_picker(icons)
        self._run_worker("icon-picker", query, received)

    def _show_icon_picker(self, icons):
        dialog = QDialog(self._journal)
        dialog.setWindowTitle("只邀请它靠近这些图标")
        dialog.resize(410, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("只读取名称与位置，互动不会移动或修改真实文件。"))
        choices = QListWidget()
        selected = set(self.settings["icon_ids"])
        for target in icons:
            item = QListWidgetItem(target.label)
            item.setData(Qt.ItemDataRole.UserRole, target.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if target.id in selected else Qt.CheckState.Unchecked)
            choices.addItem(item)
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings["icon_ids"] = [choices.item(i).data(Qt.ItemDataRole.UserRole) for i in range(choices.count())
                                         if choices.item(i).checkState() == Qt.CheckState.Checked]
            self.settings["icons_enabled"] = True
            self._set_check("icons_check", True)
            self._configure_icon_replica()
            self.note(f"已选择 {len(self.settings['icon_ids'])} 个允许互动的图标。")
            self._rebuild_paths()
            self.changed()

    def _run_worker(self, key: str, operation: Callable, callback: Callable) -> bool:
        if self._quitting or key in self._workers:
            return False
        worker = Worker(key, operation)
        worker.signals.finished.connect(self._worker_finished)
        contextual = key in {"native-poll", "icon-replica", "icon-picker", "recycle-poll", "guest-presence"}
        self._workers[key] = (worker, callback, self._desktop_epoch if contextual else None)
        self.pool.start(worker)
        return True

    def _worker_finished(self, key, result, error):
        record = self._workers.pop(key, None)
        if self._quitting or record is None:
            return
        self._recover_clock_gap()
        if record[2] is not None and record[2] != self._desktop_epoch:
            return
        if error is not None:
            self.logger.warning("Desktop operation %s: %s", key, error)
            self.note("桌面服务暂时没有回应；基础访客功能仍可使用。")
            if key == "native-poll":
                # A failed observation cannot justify keeping a rear-layer mask.
                self._depth_snapshot = None
                self._windows_checked = self._wallpaper_checked = -math.inf
                self._rebuild_paths()
                self._sync()
                self.wake(reset_clock=False)
            if key.startswith("recycle"):
                self._recycle_epoch += 1
                self.settings["recycle_enabled"] = False
                self.adapter.enable_recycle(False)
                self._set_check("recycle_check",False)
                self.recycle_timer.stop()
                self.save_timer.start()
            if key.startswith("icon"):
                self.settings["icons_enabled"] = False
                self.adapter.enable_desktop_icons(False)
                self._configure_icon_replica()
                self._set_check("icons_check", False)
                self.save_timer.start()
            return
        record[1](result)

    def _native_poll(self):
        if self._quitting or not self._screen_ready or self.paused or not self._enable_native:
            return
        has_visitors = bool(self.engine.visitors)
        depth = self.settings.get("depth_enabled", False) or self.settings.get("tree_enabled", False) or any(v.tree_trip is not None for v in self.engine.visitors.values())
        read_windows = bool((self.settings["window_paths"] or depth) and (has_visitors or self.engine.gate_open))
        read_wallpaper = bool(depth and self.settings["preset_wallpaper"])
        read_icons = bool(has_visitors and self.settings["icons_enabled"] and time.monotonic() - self._icons_checked >= 12)
        if not (read_windows or read_icons or read_wallpaper):
            return
        epoch, requested = self._desktop_epoch, time.monotonic()
        def query():
            result = {"context_epoch": epoch, "requested_at": requested}
            if read_windows:
                result["windows"] = self.adapter.windows()
                result["windows_ok"] = not bool(self.adapter.last_error)
                result["windows_time"] = time.monotonic()
            if read_icons:
                result["icons"] = self.adapter.desktop_icons()
                result["icons_allowed"] = self.adapter.icons_enabled
                result["icons_time"] = time.monotonic()
            if read_wallpaper:
                result["wallpaper_snapshot"] = self.adapter.current_wallpaper_snapshot()
                result["wallpaper_time"] = time.monotonic()
            return result
        self._run_worker("native-poll", query, self._native_result)

    def _native_result(self, result):
        now = time.monotonic()
        self._recover_clock_gap(now)
        if (self._quitting or self.paused or not self._screen_ready
                or result.get("context_epoch", self._desktop_epoch) != self._desktop_epoch
                or now - result.get("requested_at", now) > 5):
            return
        if "windows" in result:
            self._windows = result["windows"]
            self._windows_checked = result.get("windows_time", now) if result["windows_ok"] else -math.inf
        if "icons" in result:
            self._icons = result["icons"]
            self._icons_checked = result.get("icons_time", now)
            if not result["icons_allowed"]:
                self.settings["icons_enabled"] = False
                self._configure_icon_replica()
                self._set_check("icons_check", False)
                self.note("图标访问已关闭；需要时可在偏好中重新开启。")
        if "wallpaper_snapshot" in result:
            self._depth_snapshot = result["wallpaper_snapshot"]
            self._wallpaper_checked = result.get("wallpaper_time", now)
        if "wallpaper" in result:
            current = result["wallpaper"]
            self._wallpaper_match = self._matches_wallpaper(current)
            self._wallpaper_checked = result.get("wallpaper_time", now)
        self._rebuild_paths()
        self._sync()
        self.wake(reset_clock=False)
        for window in self.cat_windows.values():
            window.update()

    def _inside_selected_screen(self, rect, *, available=True):
        if not self._screen_ready or not rect.valid:
            return False
        bounds = self.screen.availableGeometry() if available else self.screen.geometry()
        return (bounds.x() <= rect.left and bounds.y() <= rect.top
                and rect.right <= bounds.x() + bounds.width()
                and rect.bottom <= bounds.y() + bounds.height())

    def _selected_icons(self):
        if not self.settings["icons_enabled"] or not self._screen_ready:
            return []
        selected = set(self.settings["icon_ids"])
        return [target for target in self._icons
                if target.id in selected and self._inside_selected_screen(target.rect)]

    def _rebuild_paths(self):
        edges, low_headroom, climb_sides = [], set(), []
        if (self._screen_ready and self.settings["window_paths"]
                and self._pending_artwork_scale is None and time.monotonic()-self._windows_checked < 5):
            bounds, full = self.scene_rect(), self.scene_rect(available=False)
            windows = list(self._windows)
            if self._journal is not None and self._journal.isVisible():
                rect = self._journal.frameGeometry()
                windows.insert(0, DesktopTarget("visitor-journal", "访客手册",
                    Rect(rect.x(), rect.y(), rect.width(), rect.height())))
            windows = [self.scene_target(w) for w in windows]
            edges, low_headroom = visible_window_supports(windows, bounds, SPRITE_WIDTH, SPRITE_HEIGHT,
                                                         screen_rect=full)
            edges = [edge for edge in edges if not edge[0].startswith("visitor-journal:")]
            low_headroom &= {edge[0] for edge in edges}
            climb_sides = visible_window_climbs(windows, bounds, SPRITE_WIDTH, SPRITE_HEIGHT, screen_rect=full)
            climb_sides = [side for side in climb_sides if not side[0].startswith("visitor-journal:")]
        self.engine.set_window_edges(edges, low_headroom_ids=low_headroom,
                                     screen_top=self.scene_rect(available=False).top, climb_sides=climb_sides)
        icons = [self.scene_target(target) for target in self._selected_icons()]
        self.engine.set_paths([(t.rect.center[0]-SPRITE_WIDTH/2, t.rect.bottom-SPRITE_HEIGHT) for t in icons])
        self.engine.set_gift_spots([(t.rect.right+8, t.rect.bottom-38) for t in icons])

    @staticmethod
    def _same_wallpaper_backup(first, second):
        if not isinstance(first, dict) or not isinstance(second, dict):
            return False
        a, b = first.get("options"), second.get("options")
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        owner_a, owner_b = first.get("display_id"), second.get("display_id")
        if any(owner is not None and (not isinstance(owner, str) or not owner) for owner in (owner_a, owner_b)):
            return False
        return (first.get("path") == second.get("path")
                and (not owner_a or not owner_b or owner_a.lower() == owner_b.lower())
                and all(a.get(key, False if key == "clipping" else None)
                        == b.get(key, False if key == "clipping" else None)
                        for key in ("scaling", "clipping", "fill")))

    def _install_wallpaper_recovery(self):
        self._wallpaper_recovery_path = self.state_dir / "wallpaper-recovery.json"
        self._wallpaper_recovery_conflict = False
        self._wallpaper_recovery_source = getattr(self.adapter, "wallpaper_backup", None)
        if self.demo:
            return
        recovery = load_state(self._wallpaper_recovery_path)
        if recovery is not None or self._wallpaper_recovery_path.exists():
            backup = recovery.get("wallpaper_backup") if isinstance(recovery, dict) else None
            valid = (isinstance(recovery, dict) and recovery.get("version") == 1
                     and isinstance(backup, dict) and isinstance(backup.get("path"), str)
                     and isinstance(backup.get("options"), dict) and isinstance(backup.get("display_id"), str))
            if (not valid or (self._wallpaper_recovery_source is not None
                              and not self._same_wallpaper_backup(self._wallpaper_recovery_source, backup))):
                self._wallpaper_recovery_conflict = True
            else:
                self.adapter.wallpaper_backup = backup
                self._wallpaper_recovery_source = backup
        self.adapter.wallpaper_backup_writer = self._write_wallpaper_recovery

    def _write_wallpaper_recovery(self, backup):
        # Called on the adapter worker before any OS write. It never reads
        # Engine or rewrites the main save, including after shutdown started.
        if self.demo or self._wallpaper_recovery_conflict:
            raise ValueError("壁纸恢复记录冲突，已保留原件，尚未更改壁纸。")
        if (not isinstance(backup, dict) or not isinstance(backup.get("path"), str)
                or not isinstance(backup.get("options"), dict) or not isinstance(backup.get("display_id"), str)):
            raise ValueError("壁纸恢复记录不完整，尚未更改壁纸。")
        if (self._wallpaper_recovery_source is not None
                and not self._same_wallpaper_backup(self._wallpaper_recovery_source, backup)):
            raise ValueError("壁纸原件与存档不一致，已保留原件，尚未更改壁纸。")
        stored = load_state(self._wallpaper_recovery_path)
        if ((stored is None and self._wallpaper_recovery_path.exists())
                or (stored is not None and (stored.get("version") != 1
                    or not self._same_wallpaper_backup(stored.get("wallpaper_backup"), backup)))):
            raise ValueError("壁纸恢复文件已改变，已保留原件，尚未更改壁纸。")
        save_state(self._wallpaper_recovery_path, {"version": 1, "wallpaper_backup": backup})

    @property
    def wallpaper_cycle_enabled(self):
        return bool(self.wallpaper_cycle.enabled or self._cycle_enable_request)

    def _refresh_wallpaper_cycle(self):
        if self._journal is not None:
            self._journal.refresh_wallpaper_controls()
        self.save_timer.start()

    def _wallpaper_notice(self, message):
        if message != self._cycle_notice:
            self._cycle_notice = message
            self.note(message)

    def _cancel_wallpaper_requests(self):
        self._wallpaper_generation += 1
        self._wallpaper_pending = None
        self._wallpaper_depth_wait = False
        self._cycle_poll_pending = False

    def _queue_wallpaper(self, operation, finished, *, wait_for_depth=False):
        # There is one worker and one latest pending request across automatic,
        # manual and restore actions. Native operations never run in a timer.
        self._wallpaper_pending = (self._wallpaper_generation, operation, finished)
        if wait_for_depth:
            self._wallpaper_depth_wait = True
            for cid, visitor in self.engine.visitors.items():
                if visitor.depth_trip is not None:
                    self.engine.request_depth_return(cid, "wallpaper_change")
                if visitor.tree_trip is not None:
                    self.engine.request_tree_return(cid, "wallpaper_change")
            self.wake(reset_clock=False)
        self._drain_wallpaper_requests()

    def _drain_wallpaper_requests(self):
        if self._quitting or "wallpaper" in self._workers:
            return
        if self._wallpaper_depth_wait and any(v.depth_trip is not None or v.tree_trip is not None for v in self.engine.visitors.values()):
            return
        pending, self._wallpaper_pending = self._wallpaper_pending, None
        if pending is None:
            if self._cycle_poll_pending:
                self._cycle_poll_pending = False
                self._poll_wallpaper_cycle()
            return
        generation, operation, callback = pending
        def work():
            if generation != self._wallpaper_generation:
                return None, None
            try:
                return operation(), None
            except Exception as exc:
                return None, str(exc)
        def done(result):
            value, error = result
            if generation == self._wallpaper_generation:
                callback(value, error)
            elif isinstance(getattr(self.adapter, "wallpaper_backup", None), dict):
                # A native operation already in flight may finish after cancel.
                # Retain its original backup without publishing stale scenery.
                self.save_timer.start()
            if self._wallpaper_pending is None and not self._cycle_poll_pending:
                self._wallpaper_depth_wait = False
            self._drain_wallpaper_requests()
        self._run_worker("wallpaper", work, done)

    @staticmethod
    def _known_wallpaper_snapshot(snapshot):
        if not isinstance(snapshot, dict):
            return False
        size, options = snapshot.get("primary_size"), snapshot.get("options")
        return (isinstance(snapshot.get("path"), str) and bool(snapshot["path"])
                and isinstance(snapshot.get("display_id"), str) and bool(snapshot["display_id"])
                and isinstance(size, (list, tuple)) and len(size) == 2
                and all(type(n) in (int, float) and math.isfinite(n) and n > 0 for n in size)
                and isinstance(options, dict) and type(options.get("scaling")) is int
                and options["scaling"] in (0, 1, 2, 3)
                and type(options.get("clipping")) is bool)

    def _snapshot_scene(self, snapshot):
        if (not self._known_wallpaper_snapshot(snapshot)
                or snapshot["options"]["scaling"] != 1 or snapshot["options"]["clipping"]):
            return None
        for scene_id in self.scenes:
            if Path(snapshot["path"]).resolve() == self._wallpaper_path(scene_id).resolve():
                return scene_id
        return None

    def _scene_fits_snapshot(self, scene_id, snapshot):
        if not self._known_wallpaper_snapshot(snapshot):
            return False
        width, height = snapshot["primary_size"]
        orientation = self.scenes.get(scene_id, {}).get("orientation", "landscape")
        return (height > width) if orientation == "portrait" else (width >= height)

    def _verified_wallpaper(self, scene_id, before, after):
        return (self._known_wallpaper_snapshot(before) and self._known_wallpaper_snapshot(after)
                and self._snapshot_scene(after) == scene_id
                and before["display_id"] == after["display_id"]
                and list(before["primary_size"]) == list(after["primary_size"])
                and self._scene_fits_snapshot(scene_id, after))

    def _publish_wallpaper(self, scene_id, snapshot=None):
        # Any old depth outing returns continuously with its old geometry;
        # stale masks are suspended before publishing the newly read-back scene.
        preserve_tree = (same_layout_geometry(self._mask_data, self.scenes.get(scene_id))
                         and self._tree_context_ready()
                         and self._known_wallpaper_snapshot(snapshot)
                         and snapshot["display_id"] == self._depth_snapshot.get("display_id")
                         and snapshot["primary_size"] == self._depth_snapshot.get("primary_size"))
        previous_windows, previous_checked = self._windows, self._windows_checked
        self._invalidate_desktop_context(depth_reason="desktop_obscured", preserve_tree=preserve_tree)
        if preserve_tree:
            self._windows, self._windows_checked = previous_windows, previous_checked
        self.settings["preset_wallpaper"] = True
        self.settings["wallpaper_id"] = scene_id
        self._mask_data = self._read_mask()
        if not self._mask_data.get("occluders"):
            self.settings["depth_enabled"] = False
            self._set_check("depth_check", False)
        if not self._mask_data.get("layout_geometry", {}).get("supports"):
            self.settings["tree_enabled"] = False
            self._set_check("tree_check", False)
        self._depth_snapshot = snapshot
        self._wallpaper_match = True
        self._wallpaper_checked = time.monotonic()
        self._cycle_notice = None
        if self._journal is not None:
            self._journal.refresh_wallpaper_controls()
        self.changed()
        self._native_poll()

    def set_wallpaper_cycle(self, enabled):
        self._cancel_wallpaper_requests()
        self._cycle_enable_request = None
        self._cycle_initial_snapshot = None
        self.wallpaper_cycle.disable()
        self.wallpaper_timer.stop()
        if enabled:
            if self.demo or not self.adapter.capabilities.get("wallpaper_guarded", False):
                self._wallpaper_notice("体验模式或当前桌面服务不支持自动切换系统壁纸。")
            else:
                scene_id = (self._journal.wallpaper_choices.currentData() if self._journal is not None
                            else self.settings["wallpaper_id"])
                scene = self.scenes.get(scene_id, {})
                if scene.get("cycle_ready") is not True or not scene.get("theme_id"):
                    self._wallpaper_notice("请先选择一套具有晨间和暮光配对的壁纸。")
                else:
                    self._cycle_enable_request = (scene["theme_id"], scene["orientation"], scene_id)
                    self.wallpaper_timer.start()
                    self._poll_wallpaper_cycle()
        self._refresh_wallpaper_cycle()

    def _poll_wallpaper_cycle(self):
        if self._quitting or self.demo or not self.wallpaper_cycle_enabled:
            return
        if not self.adapter.capabilities.get("wallpaper_guarded", False):
            self.set_wallpaper_cycle(False)
            return
        if "wallpaper" in self._workers or self._wallpaper_pending is not None:
            self._cycle_poll_pending = True
            return
        self._cycle_poll_pending = False
        def observed(snapshot, error):
            if error or not self._known_wallpaper_snapshot(snapshot):
                self._wallpaper_notice("暂时无法确认主屏壁纸；昼夜切换将在桌面恢复后重试。")
                return
            if self._cycle_enable_request:
                theme, orientation, scene_id = self._cycle_enable_request
                if not self._scene_fits_snapshot(scene_id, snapshot):
                    self.set_wallpaper_cycle(False)
                    self._wallpaper_notice("所选壁纸方向与系统主屏幕不一致，尚未启用昼夜切换。")
                    return
                self.wallpaper_cycle.enable(theme, orientation, snapshot["display_id"])
                self._cycle_initial_snapshot = snapshot
                self._cycle_enable_request = None
            width, height = snapshot["primary_size"]
            orientation = "portrait" if height > width else "landscape"
            initial_changed = (self.wallpaper_cycle.confirmed_scene_id is None
                               and self._cycle_initial_snapshot is not None
                               and self._cycle_initial_snapshot != snapshot)
            if orientation != self.wallpaper_cycle.orientation or initial_changed:
                self.wallpaper_cycle.disable("orientation_changed" if not initial_changed else "external_change")
                self.wallpaper_timer.stop()
                self._cycle_poll_pending = False
                self._invalidate_desktop_context()
                self._wallpaper_notice("主屏方向或壁纸已改变，昼夜自动切换已暂停；重新启用后才会继续。")
                self._refresh_wallpaper_cycle()
                return
            state = self.wallpaper_cycle.observe(self._snapshot_scene(snapshot), snapshot["display_id"])
            if state == "paused":
                self._invalidate_desktop_context()
                self.wallpaper_timer.stop()
                self._cycle_poll_pending = False
                self._wallpaper_notice("主屏幕或壁纸已改变，昼夜自动切换已暂停；重新启用后才会继续。")
                self._refresh_wallpaper_cycle()
                return
            if state != "ready":
                return
            request = self.wallpaper_cycle.plan(datetime.now())
            if request is None:
                self._wallpaper_depth_wait = False
                confirmed = self.wallpaper_cycle.confirmed_scene_id
                if confirmed and (self.settings["wallpaper_id"] != confirmed or not self._wallpaper_match):
                    self._publish_wallpaper(confirmed, snapshot)
                self._refresh_wallpaper_cycle()
                return
            if not self._scene_fits_snapshot(request.scene_id, snapshot):
                self.wallpaper_cycle.disable("orientation_changed")
                self.wallpaper_timer.stop()
                self._wallpaper_notice("主屏方向已改变，昼夜切换已暂停。")
                self._refresh_wallpaper_cycle()
                return
            generation = self._wallpaper_generation
            def apply():
                if generation != self._wallpaper_generation or not self.wallpaper_cycle.is_current(request):
                    return False, None, ""
                if target_scene(self.scenes, request.theme_id, request.orientation, datetime.now()) != request.scene_id:
                    return False, None, "stale-time"
                ok = self.adapter.set_wallpaper_guarded(
                    self._wallpaper_path(request.scene_id), request.display_id, snapshot)
                detail = self.adapter.last_error
                after = self.adapter.current_wallpaper_snapshot()
                return ok, after, detail
            def applied(result, error):
                if not self.wallpaper_cycle.is_current(request):
                    return
                ok, after, detail = result if result else (False, None, error or "")
                if (ok and self._verified_wallpaper(request.scene_id, snapshot, after)
                        and self.wallpaper_cycle.confirm(request, self._snapshot_scene(after), after["display_id"])):
                    if target_scene(self.scenes, request.theme_id, request.orientation, datetime.now()) != request.scene_id:
                        # Record our own verified write so the next observation
                        # does not mistake it for an external replacement, but
                        # never publish anchors for a phase that already ended.
                        self._cycle_poll_pending = True
                    else:
                        self._publish_wallpaper(request.scene_id, after)
                else:
                    self.wallpaper_cycle.fail(request)
                    if detail == "stale-time":
                        self._cycle_poll_pending = True
                    else:
                        self._invalidate_desktop_context()
                        if self._known_wallpaper_snapshot(after) and after != snapshot:
                            # The native guard/readback found a change during
                            # this request. Do not retry over an external image,
                            # even if the very first enable has not confirmed yet.
                            self.wallpaper_cycle.disable("changed_during_request")
                            self.wallpaper_timer.stop()
                            self._cycle_poll_pending = False
                        self._wallpaper_notice(f"昼夜壁纸暂未切换，保留上次确认的场景。{detail}")
                self._refresh_wallpaper_cycle()
            same_geometry = same_layout_geometry(self._mask_data, self.scenes[request.scene_id])
            self._queue_wallpaper(apply, applied, wait_for_depth=not same_geometry)
        self._queue_wallpaper(self.adapter.current_wallpaper_snapshot, observed)

    def apply_wallpaper(self):
        if self.demo:
            self.note("体验模式不更改系统壁纸，请在正式模式中选择。")
            return
        if not self.adapter.capabilities.get("wallpaper", False):
            self.note("当前系统尚不支持自动设置壁纸。")
            return
        scene_id = (self._journal.wallpaper_choices.currentData() if self._journal is not None
                    else self.settings["wallpaper_id"])
        selected = self._wallpaper_path(scene_id)
        if selected is None:
            self.note("这套壁纸素材暂时不可用。")
            return
        if self.scenes[scene_id].get("orientation") == "portrait":
            primary = self.qt.primaryScreen()
            if primary is None or primary.geometry().height() <= primary.geometry().width():
                self.note("这张壁纸为 9:16 竖屏设计，请先将竖向显示器设为系统主屏幕。猫咪活动屏的选择不会改变壁纸目标。")
                return
        self.set_wallpaper_cycle(False)
        guarded = self.adapter.capabilities.get("wallpaper_guarded", False)
        def apply():
            if guarded:
                before = self.adapter.current_wallpaper_snapshot()
                if not self._scene_fits_snapshot(scene_id, before):
                    return False, None, before, "无法确认主屏或所选壁纸方向不符。"
                ok = self.adapter.set_wallpaper_guarded(selected, before["display_id"], before)
                detail = self.adapter.last_error
                return ok, self.adapter.current_wallpaper_snapshot(), before, detail
            ok = self.adapter.set_wallpaper(selected)
            detail = self.adapter.last_error
            return ok, self.adapter.current_wallpaper() if ok else None, None, detail
        def finished(result, error):
            ok, current, before, detail = result if result else (False, None, None, error or "")
            verified = self._verified_wallpaper(scene_id, before, current) if guarded else bool(ok)
            if ok and verified:
                self._publish_wallpaper(scene_id, current if guarded else None)
                if not guarded:
                    self._wallpaper_match = self._matches_wallpaper(current)
                self.note(f"主屏幕已换成{self.scenes[scene_id]['title']}；猫咪会在桌面前层活动。")
            else:
                self.settings["preset_wallpaper"] = False
                self._wallpaper_match = False
                self.note(f"壁纸未能确认应用。{detail}")
                self.changed()
        self._queue_wallpaper(apply, finished, wait_for_depth=guarded and not
                              same_layout_geometry(self._mask_data, self.scenes[scene_id]))

    @staticmethod
    def _restored_wallpaper_fit(actual, expected):
        if not isinstance(actual, dict) or not isinstance(expected, dict):
            return False
        return (("scaling" not in expected or actual.get("scaling") == expected["scaling"])
                and actual.get("clipping", False) == expected.get("clipping", False)
                and ("fill" not in expected or actual.get("fill") == expected["fill"]))

    def restore_wallpaper(self):
        self.set_wallpaper_cycle(False)
        if self.demo:
            self.note("体验模式没有修改系统壁纸。")
            return
        if not hasattr(self.adapter, "restore_wallpaper"):
            self.note("当前系统尚不支持自动还原壁纸。")
            return
        guarded = self.adapter.capabilities.get("wallpaper_guarded", False)
        def restore():
            if guarded:
                before = self.adapter.current_wallpaper_snapshot()
                if not self._known_wallpaper_snapshot(before):
                    return False, "暂时无法确认主屏，尚未还原壁纸。"
                ok = self.adapter.restore_wallpaper_guarded(before["display_id"], before)
                detail = self.adapter.last_error
                after, backup = self.adapter.current_wallpaper_snapshot(), self.adapter.wallpaper_backup
                ok = bool(ok and self._known_wallpaper_snapshot(after) and isinstance(backup, dict)
                          and after["display_id"] == before["display_id"]
                          and after["path"] == backup.get("path")
                          and self._restored_wallpaper_fit(after["options"], backup.get("options"))
                          and list(after["primary_size"]) == list(before["primary_size"]))
            else:
                ok = self.adapter.restore_wallpaper()
                detail = self.adapter.last_error
            return ok, detail
        def finished(result, error):
            ok, detail = result if result else (False, error or "")
            if ok:
                self.settings["preset_wallpaper"] = False
                self._invalidate_desktop_context()
            self.note("此前的壁纸已还原。" if ok else f"暂时无法还原壁纸。{detail}")
            self.changed()
        self._queue_wallpaper(restore, finished, wait_for_depth=guarded)

    @property
    def depth_art(self):
        if self._depth_art is None:
            from .depth_art import DepthArt
            self._depth_art = DepthArt()
        return self._depth_art

    def set_tree_enabled(self, enabled):
        from .tree_geometry import build_tree_layout
        enabled = bool(enabled)
        if enabled and (not self.settings["preset_wallpaper"] or self._tree_layout() is None):
            enabled = False
            self.note("先在主屏应用适合当前屏幕的猫爬架壁纸，再允许访客去爬架玩。")
        self.settings["tree_enabled"] = enabled
        self._tree_display_id = None
        self._tree_next_offer = self.engine._time + 45
        self._set_check("tree_check", enabled)
        if not enabled:
            self.engine.request_tree_return_all("tree_disabled")
        self.changed()
        if enabled:
            self._native_poll()

    def _tree_context_ready(self):
        if (not self._screen_ready or not self._enable_native
                or not self.settings.get("tree_enabled", False)
                or not self.settings["preset_wallpaper"]
                or self.screen is not self.qt.primaryScreen()):
            return False
        now, snapshot = time.monotonic(), self._depth_snapshot
        if (not 0 <= now-self._windows_checked < 5 or not 0 <= now-self._wallpaper_checked < 5
                or not self._known_wallpaper_snapshot(snapshot)
                or not self._matches_wallpaper(snapshot["path"])
                or snapshot["options"]["scaling"] != 1 or snapshot["options"]["clipping"] is not False):
            return False
        full = self.screen.geometry()
        if list(snapshot["primary_size"]) != [full.width(), full.height()]:
            return False
        from .tree_geometry import build_tree_layout
        if self._tree_layout() is None:
            return False
        if self._tree_display_id is None:
            self._tree_display_id = snapshot["display_id"]
        return snapshot["display_id"] == self._tree_display_id

    def _tree_layout(self):
        from .tree_geometry import build_tree_layout
        full = self.scene_rect(available=False)
        return build_tree_layout(self._mask_data, (full.x, full.y, full.width, full.height))

    def _guard_tree_visits(self):
        active = [(cid, v) for cid, v in self.engine.visitors.items() if v.tree_trip is not None]
        if not active:
            return
        from .tree_context import tree_route_blocked
        ready = self._tree_context_ready()
        layout = self._tree_layout() if ready else None
        blockers = self._depth_blockers()
        for cid, visitor in active:
            if not ready or layout is None or layout.fingerprint != visitor.tree_trip.layout.fingerprint:
                self.engine.request_tree_return(cid, "layout_changed")
            elif tree_route_blocked(layout, (visitor.x+72, visitor.y+108), blockers,
                                    return_points=(visitor.tree_trip.start, visitor.tree_trip.near)):
                self.engine.request_tree_return(cid, "desktop_obscured")

    def send_to_tree(self, cat_id, *, close_journal=False, quiet=False):
        visitor = self.engine.visitors.get(cat_id)
        if visitor is None:
            return False
        if visitor.tree_trip is not None:
            self.engine.request_tree_return(cat_id, "called")
            self.changed()
            return True
        if self.paused or self._wallpaper_depth_wait or not self._tree_context_ready():
            if not quiet:
                self.note("爬架暂不可用：请在主屏使用匹配的猫爬架壁纸，并在偏好中允许爬架活动。")
                self._native_poll()
            return False
        from .tree_context import tree_route_blocked
        layout = self._tree_layout()
        if self._pending_artwork_scale is not None or layout is None or tree_route_blocked(layout, (visitor.x+72, visitor.y+108),
                                                self._depth_blockers(include_journal=not close_journal)):
            if not quiet:
                self.note("爬架或往返路线上有窗口遮挡；让出这段桌面后再去。")
            return False
        if not self.depth_art.available or not self.engine.begin_tree_visit(cat_id, layout):
            if not quiet:
                self.note("等它在地面站稳、忙完手里的事，并留出空平台后，再去爬架玩。")
            return False
        if close_journal and self._journal is not None:
            self._journal.hide()
        self._tree_next_offer = self.engine._time + 60
        self.changed()
        return True

    def _maybe_tree_visit(self):
        if (self._pending_artwork_scale is not None or not self.settings.get("tree_enabled", False) or not self.engine.gate_open
                or self.paused or self._tree_play_pending is not None
                or self.engine.active_teaser_id is not None
                or self.engine._time < self._tree_next_offer):
            return
        self._tree_next_offer = self.engine._time + self.engine.rng.uniform(45, 90)
        if not self._tree_context_ready():
            return
        for cat_id, visitor in self.engine.visitors.items():
            if (visitor.tree_trip is None and self.engine.can_begin_tree_visit(cat_id)
                    and self.engine.rng.random() < {"mischievous": .65, "graceful": .45, "shy": .30}.get(self.engine.cats[cat_id].personality, .4)
                    and self.send_to_tree(cat_id, quiet=True)):
                break

    def _continue_tree_play(self):
        cat_id = self._tree_play_pending
        if cat_id is None:
            return
        visitor = self.engine.visitors.get(cat_id)
        if (visitor is None or not self.engine.gate_open or self._quitting
                or visitor.tree_pending_action
                or visitor.state in {"leaving", "fleeing", "fighting", "carried"}):
            self._tree_play_pending = None
        elif visitor.tree_trip is None and not self.paused:
            self._tree_play_pending = None
            self.start_play(cat_id)

    def set_depth_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled and not self.settings['preset_wallpaper']:
            enabled = False
            self.note('先应用一张后院预设壁纸，再允许访客往后院走。')
        if enabled and not self._mask_data.get('occluders'):
            enabled = False
            self.note('当前壁纸仅作背景陈设，不启用纵深；访客继续在桌面前层活动。')
        self.settings['depth_enabled'] = enabled
        self._set_check('depth_check', enabled)
        if not enabled:
            for cid, visitor in self.engine.visitors.items():
                if visitor.depth_trip is not None:
                    self._depth_mask_suspended.add(cid)
                    self.engine.request_depth_return(cid, 'depth_disabled')
        self.changed()
        if enabled:self._native_poll()

    def _depth_context_ready(self):
        if (not self._screen_ready or not self._enable_native
                or not self.settings.get('depth_enabled', False)
                or not self._mask_data.get('occluders')
                or not self.settings['preset_wallpaper'] or self.screen is not self.qt.primaryScreen()):
            return False
        now=time.monotonic();snapshot=self._depth_snapshot
        if (not 0 <= now-self._wallpaper_checked < 5 or not 0 <= now-self._windows_checked < 5
                or not isinstance(snapshot,dict) or not isinstance(snapshot.get('path'),str)
                or not self._matches_wallpaper(snapshot.get('path'))):
            return False
        options=snapshot.get('options',{});full=self.screen.geometry()
        return (isinstance(options,dict) and type(options.get('scaling')) in (int,float)
                and options['scaling']==1 and options.get('clipping') is False
                and snapshot.get('primary_size')==[full.width(),full.height()])

    def _depth_blockers(self, *, include_journal=True):
        blockers=[target.rect for target in self._windows]
        if include_journal and self._journal is not None and self._journal.isVisible():
            rect=self._journal.frameGeometry()
            blockers.append(Rect(rect.x(),rect.y(),rect.width(),rect.height()))
        return [self._scene_transform.rect_to_scene(rect) for rect in blockers]

    def _guard_depth_visits(self):
        from .depth_context import route_blocked, escape_blocked
        active={cid for cid,v in self.engine.visitors.items() if v.depth_trip is not None}
        self._depth_mask_suspended.intersection_update(active)
        if not active:return
        ready=self._depth_context_ready();blockers=self._depth_blockers()
        for cid in active:
            v=self.engine.visitors[cid]
            if ready and v.depth_pose.get('escaping'):
                full=self.scene_rect(available=False)
                covered=escape_blocked((v.x+72,v.y+108),v.depth_scale,v.facing,
                                      (full.x,full.y,full.width,full.height),blockers)
            else:
                covered=not ready or route_blocked(v.depth_trip.route,v.depth_screen,blockers)
            if covered:
                self._depth_mask_suspended.add(cid)
                self.engine.request_depth_return(cid, 'desktop_obscured')

    def send_to_backyard(self, cat_id, *, close_journal=False):
        visitor=self.engine.visitors.get(cat_id)
        if visitor is None:return False
        if visitor.depth_trip is not None:
            self.engine.request_depth_return(cat_id, 'called')
            self.changed();return True
        if self._wallpaper_depth_wait:
            self.note('正在等待后院访客回到前层，壁纸切换完成后再去后院转转。')
            return False
        if not self._mask_data.get('occluders'):
            self.note('当前壁纸仅作背景陈设，不启用纵深；访客继续在桌面前层活动。')
            return False
        if self._pending_artwork_scale is not None or self.paused or not self._depth_context_ready():
            self.note('后院路线暂不可用：请在主屏使用匹配的预设壁纸，并在偏好中允许往后院走。')
            self._native_poll();return False
        from .depth_context import build_depth_route, route_blocked
        full=self.scene_rect(available=False);screen=(full.x,full.y,full.width,full.height)
        route=build_depth_route(self._mask_data,screen,(visitor.x+72,visitor.y+108))
        if route is None or route_blocked(route,screen,self._depth_blockers(include_journal=not close_journal)):
            self.note('后院路线上有窗口或空间不足；让出这段桌面后再叫它去。')
            return False
        if not self.depth_art.available:
            self.note('后院动作素材暂时不可用，访客继续留在前层。')
            return False
        if not self.engine.begin_depth_visit(cat_id,route,screen_rect=screen):
            self.note('等它在地面站稳、忙完手里的事，再去后院转转。')
            return False
        self._depth_mask_suspended.discard(cat_id)
        if close_journal and self._journal is not None:self._journal.hide()
        self.note(f'{self.engine.cat_reference(cat_id)}往后院走去了；随时可以叫回来。')
        self.changed();return True

    def cat_clip(self, pos, size, cat_id=None):
        # Ordinary foreground cats never inherit wallpaper masks. Only a
        # deliberate trip behind a verified matching scene may be clipped.
        v=self.engine.visitors.get(cat_id) if cat_id is not None else None
        if (v is None or v.depth_trip is None or v.depth_layer!='rear'
                or cat_id in self._depth_mask_suspended or not self._depth_context_ready()):
            return None
        sx,sy,sw,sh=v.depth_screen
        visible=QPainterPath();visible.addRect(QRectF(0,0,size.width(),size.height()))
        mask=QPainterPath();mask.setFillRule(Qt.FillRule.WindingFill)
        for polygon in v.depth_trip.route.polygons:
            mask.moveTo(sx+polygon[0][0]*sw-pos.x(),sy+polygon[0][1]*sh-pos.y())
            for x,y in polygon[1:]:mask.lineTo(sx+x*sw-pos.x(),sy+y*sh-pos.y())
            mask.closeSubpath()
        return visible.subtracted(mask)

    def _events(self) -> bool:
        # Lazy procedural assets can fail during a paint callback. Report once
        # in the handbook while the renderer keeps the Qt event loop alive.
        import sys
        renderer = sys.modules.get("backyard_visitor.procedural_coat")
        error = getattr(renderer, "pending_error", None) or getattr(renderer, "load_error", None)
        if renderer is not None and getattr(renderer, "pending_error", None):
            renderer.pending_error = None
        newly_reported = bool(error and error != getattr(self, "_coat_error_reported", None))
        if newly_reported:
            self._coat_error_reported = error
            self.note(error)
        events = self.engine.drain_events()
        for event in events:
            self.note(event["message"])
            if event["kind"] == "scratch":
                self._show_scratch(event.get("cat_id"))
            if event["kind"] == "fight_start":
                self._ripple_near_cat(event.get("cat_id"))
        return bool(events) or newly_reported

    def _show_scratch(self, cat_id):
        visitor = self.engine.visitors.get(cat_id)
        if visitor is None or self._quitting or not self._screen_ready:
            return
        # A brief, non-interactive mark at the touched cat; never cover the desktop.
        scratch = ScratchMarks(self)
        bounds = self.screen.geometry()
        sx, sy = self.to_display(visitor.x-20, visitor.y-24)
        x = max(bounds.left(), min(bounds.right()-scratch.width()+1, round(sx)))
        y = max(bounds.top(), min(bounds.bottom()-scratch.height()+1, round(sy)))
        scratch.move(x,y)
        self._scratches.append(scratch)
        scratch.finished.connect(lambda s=scratch: self._remove_scratch(s))
        scratch.show()
        scratch.start()

    def _remove_scratch(self, scratch):
        if scratch not in self._scratches:
            return
        self._scratches.remove(scratch)
        scratch.timer.stop()
        scratch.close()
        scratch.deleteLater()

    def _clear_scratches(self):
        for scratch in list(self._scratches):
            self._remove_scratch(scratch)

    def _ripple_near_cat(self, cat_id):
        visitor = self.engine.visitors.get(cat_id)
        if visitor is None:
            return
        for target in self._selected_icons():
            x, y = self.to_scene(*target.rect.center)
            if math.hypot(x - visitor.x - 72, y - visitor.y - 56) <= 190:
                self._request_icon_replica(target)
                ripple = IconRipple(self, target)
                self._ripples.append(ripple)
                ripple.show()
                QTimer.singleShot(1000, lambda r=ripple: self._remove_ripple(r))
                break

    def _remove_ripple(self, ripple):
        if ripple not in self._ripples:
            return
        self._ripples.remove(ripple)
        ripple.close()
        ripple.deleteLater()

    def _clear_fight_overlay(self):
        overlay, self.fight_overlay = self.fight_overlay, None
        if overlay is not None:
            overlay.close()
            overlay.deleteLater()

    def _sync_fight_overlay(self):
        ids = tuple(cid for cid, v in self.engine.visitors.items() if v.state == "fighting")
        if len(ids) != 2 or not self._screen_ready or self._quitting:
            self._clear_fight_overlay()
            return
        if self.fight_overlay is not None and self.fight_overlay.cat_ids != ids:
            self._clear_fight_overlay()
        if self.fight_overlay is None:
            self.fight_overlay = FightOverlay(self, ids)
        if self.fight_overlay.sync_geometry():
            if not self.fight_overlay.isVisible():
                self.fight_overlay.show()
        else:
            self._clear_fight_overlay()

    def _sync(self):
        self._guard_depth_visits()
        self._guard_tree_visits()
        self.bed_window.sync_geometry()
        if not self._screen_ready:
            self._clear_fight_overlay()
            return
        for cid in list(self.cat_windows):
            if cid not in self.engine.visitors:
                window = self.cat_windows.pop(cid)
                self._cat_paint_keys.pop(cid, None)
                window.close()
                window.deleteLater()
        for cid, visitor in self.engine.visitors.items():
            if cid not in self.cat_windows:
                self.cat_windows[cid] = CatWindow(self, cid)
            window = self.cat_windows[cid]
            height = 168 if visitor.rear_blend > 0 or visitor.state == "window_rear" else 112
            if window.base_size.height() != height:
                window.set_scene_size(144, height)
            if visitor.state == "fighting":
                window.hold.stop()
                window.pressed = window.stroking = window.carrying = False
                window.unsetCursor()
                window.hide()
            x, y = round(visitor.x), round(visitor.y)
            window.sync_native_position(x, y)
            key = (visitor.state, visitor.facing, visitor.carrying_gift, self.engine.cats[cid].render_coat,
                   getattr(visitor, "settle_remaining", 0) > 0,
                   getattr(visitor, "posture_kind", ""), getattr(visitor, "posture_remaining", 0) > 0,
                   (visitor.tree_pose or {}).get("pose"), (visitor.tree_pose or {}).get("phase"))
            animated = (visitor.tree_trip is not None or visitor.depth_trip is not None or visitor.moving or visitor.rear_blend > 0 or visitor.state == "window_rear"
                        or getattr(visitor, "posture_remaining", 0) > 0
                        or getattr(visitor, "landing_remaining", 0) > 0
                        or getattr(visitor, "settle_remaining", 0) > 0
                        or getattr(visitor, "rise_remaining", 0) > 0
                        or getattr(visitor, "turn_remaining", 0) > 0) or visitor.state in {"peeking", "purring", "fighting", "playing", "teasing", "swatting", "eating", "window_peeking"}
            window.refresh_input_region()
            if animated or self._cat_paint_keys.get(cid) != key:
                window.update()
            self._cat_paint_keys[cid] = key
            if (visitor.state != "fighting" and not window.isVisible()
                    and getattr(window, "_has_visible_content", False)):
                # Cocoa does not reliably expose a window first shown wholly
                # offscreen when it later moves in. Show at its first pixels.
                window.show()
            if visitor.state == "playing" and self._previous_states.get(cid) != "playing":
                self._ripple_near_cat(cid)
        self._sync_fight_overlay()
        self._previous_states = {cid: v.state for cid, v in self.engine.visitors.items()}
        self._release_unused_art()
        gifts = {g.id: g for g in self.engine.gifts}
        for gid in list(self.gift_windows):
            if gid not in gifts:
                window = self.gift_windows.pop(gid)
                window.close()
                window.deleteLater()
        for gid, gift in gifts.items():
            if gid not in self.gift_windows:
                self.gift_windows[gid] = GiftWindow(self, gift)
            if not getattr(self.gift_windows[gid], "drag_active", False):
                self.gift_windows[gid].move(*(round(v) for v in self.to_display(gift.x, gift.y)))
            window = self.gift_windows[gid]
            window.refresh_input_region()
            paint_key = (gift.state, gift.escape_direction)
            if gift.state == "escaping" or getattr(window, "paint_key", None) != paint_key:
                window.update()
                window.paint_key = paint_key
            if not window.isVisible():
                window.show()  # Also restore gifts hidden while no screen existed.
        if self._painted_gate != self.engine.gate_open:
            self._painted_gate = self.engine.gate_open
            self.gate.update()
            self.tray.setToolTip("后院访客 · " + ("小门开着" if self.engine.gate_open else "小门轻轻合着"))
        self.place_bowl()
        if self._wallpaper_depth_wait:
            self._drain_wallpaper_requests()

    def _tick(self):
        if self._quitting or self.paused or not self._screen_ready:
            return
        now, wall, cursor = time.monotonic(), time.time(), QCursor.pos()
        if self._recover_clock_gap(now, wall, cursor):
            return
        dt = max(0, now - self._last_tick)
        self._reset_clock(now, wall, cursor=self._last_cursor)
        if dt > 0 and self.engine.visitors:
            speed = math.hypot(cursor.x() - self._last_cursor.x(), cursor.y() - self._last_cursor.y()) / dt
            if self.engine.active_teaser_id is not None and not self.screen.geometry().contains(cursor):
                # Match TeaserSession's full-display check so this faster model
                # tick cannot restore a toy target on a different monitor.
                self.engine.clear_cursor()
            else:
                x, y = self.to_scene(cursor.x(), cursor.y())
                self.engine.cursor_moved(x, y, speed/self.artwork_scale)
        self._last_cursor = cursor
        if self.settings["window_paths"] and now - self._windows_checked > 5:
            self._rebuild_paths()
        self._guard_depth_visits()
        self._guard_tree_visits()
        self._tick_guest_presence(dt, now)
        self.engine.tick(dt)
        self._maybe_tree_visit()
        self._continue_tree_play()
        dirty = self._events()
        self._sync()
        if dirty:
            self.save_timer.start()
        self.wake(reset_clock=False)

    def wake(self, *, reset_clock=True):
        if self._quitting:
            return
        fps = 0 if self.paused or not self._screen_ready else (24 if self.engine.moving else 2 if self.engine.visitors else 1 if self.engine.gate_open or self.engine.has_gift_activity else 0)
        self.current_fps = fps
        if fps:
            interval = round(1000 / fps)
            waits = [g.wait_remaining for g in self.engine.gifts
                     if g.kind == "toy_mouse" and g.state == "idle" and g.wait_remaining > 0]
            if waits:
                # Wake at the start of a dash; a full idle second would skip
                # the first 200px before switching to the movement cadence.
                interval = min(interval, max(1, math.ceil(min(waits) * 1000)))
            previous_interval = self.timer.interval()
            if previous_interval != interval or not self.timer.isActive():
                self.timer.start(interval)
            # A native callback may start a jump between sparse idle ticks.
            # Its new fast cadence needs a fresh baseline: old idle elapsed
            # time is neither a sleep gap nor time spent in the new jump.
            # Unchanged polling cadence must not keep postponing model ticks.
            if reset_clock or interval < previous_interval:
                self._reset_clock()
        else:
            self.timer.stop()
        native = (self._screen_ready and not self.paused and self._enable_native
                  and (((self.settings["window_paths"] or self.settings.get("depth_enabled",False) or self.settings.get("tree_enabled",False) or any(v.tree_trip is not None for v in self.engine.visitors.values())) and (self.engine.gate_open or bool(self.engine.visitors)))
                       or (self.settings["icons_enabled"] and bool(self.engine.visitors))))
        perched = any(v.window_edge_id or v.window_target_id or v.climb_side_id
                      for v in self.engine.visitors.values())
        native_interval = 500 if perched or any(v.depth_trip is not None or v.tree_trip is not None for v in self.engine.visitors.values()) else 2000
        if self.native_timer.interval() != native_interval:
            self.native_timer.setInterval(native_interval)
        if native and not self.native_timer.isActive():
            self.native_timer.start()
            self._native_poll()
        elif not native:
            self.native_timer.stop()

    def changed(self):
        if self._quitting:
            return
        self._events()
        self._sync()
        self.wake()
        self.save_timer.start()
        if self._journal is not None and self._journal.isVisible():
            self._journal.refresh()

    def _save(self):
        if self.demo:
            return True
        state = self.engine.export()
        state["settings"] = self.settings.copy()
        state["display_transform"] = self.display_transform_state()
        state["wallpaper_cycle"] = self.wallpaper_cycle.export()
        backup = getattr(self.adapter, "wallpaper_backup", None)
        if isinstance(backup, dict):
            state["wallpaper_backup"] = backup
        try:
            save_state(self.state_path, state)
            self._save_error_warned = False
            return True
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("Could not save: %s", exc)
            if not self._save_error_warned:
                self._save_error_warned = True
                self._warning("进度暂时无法保存，请检查存档目录的空间和访问权限。")
            return False

    def check_for_updates(self):
        from .updates import NativeUpdater, UpdateUnavailable
        if self.demo:
            QMessageBox.information(self._journal, "软件更新", "请回到正式模式检查更新，体验模式不会替换应用。")
            return
        try:
            if self._software_updater is None:
                self._software_updater = NativeUpdater(self._save, self.shutdown)
            if not self._software_updater.check():
                self.note("更新窗口已经打开，请在该窗口继续。")
        except UpdateUnavailable as exc:
            QMessageBox.information(self._journal, "软件更新", str(exc))

    def _build_menu(self):
        menu = QMenu()
        menu.addAction("打开访客手册", self.show_journal)
        menu.addAction("开门 / 关门", self.toggle_gate)
        menu.addAction("添一份小鱼干", self.feed)
        pause = menu.addAction("暂停 / 继续桌面活动", lambda: self.set_paused(not self.paused))
        if self.demo:
            scenes = menu.addMenu("体验一段故事")
            for label, scene in (("投喂", "feeding"), ("抚摸与信任", "trust"), ("收到礼物", "gift"), ("小老鼠逃跑", "mouse"), ("两位访客争地盘", "fight")):
                scenes.addAction(label, lambda checked=False, s=scene: self.demo_scene(s))
        else:
            menu.addAction("打开独立体验模式", self.start_demo)
        menu.addSeparator()
        menu.addAction("检查更新…", self.check_for_updates)
        menu.addAction("退出后院访客", self.quit)
        return menu

    def menu(self, pos):
        self._build_menu().exec(pos)

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_journal()

    def demo_scene(self, scene: str):
        if self.engine.demo_scene(scene):
            self.changed()
        elif self.demo:
            self.note("安静陪伴模式下不会演示打架。")

    def start_demo(self):
        if self.demo:
            self.demo_scene("feeding")
            self.note("体验菜单在小门右键中，可试试信任、送礼与双猫事件。")
            return
        command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "backyard_visitor"]
        command.append("--demo")
        try:
            subprocess.Popen(command, cwd=str(Path(__file__).resolve().parent.parent),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.note("独立体验窗口正在打开，正式进度不会改变。")
        except OSError as exc:
            self.logger.warning("Could not start demo: %s", exc)
            self._warning("体验窗口暂时无法启动。")

    def quit(self):
        self.shutdown()
        self.qt.quit()

    def shutdown(self):
        if self._quitting:
            return
        self._quitting = True
        self._wallpaper_generation += 1
        self._wallpaper_pending = None
        self._cycle_enable_request = None
        self._cycle_poll_pending = False
        self.wallpaper_timer.stop()
        self._scale_timer.stop()
        self.bed_window.drag_active = False
        self.stop_play("quit")
        self._clear_fight_overlay()
        self._clear_scratches()
        for screen in list(self._screen_connections):
            self._unbind_screen(screen)
        for signal, slot in ((self.qt.screenAdded, self._screen_added),
                             (self.qt.screenRemoved, self._screen_removed),
                             (self.qt.primaryScreenChanged, self._primary_screen_changed),
                             (self.qt.aboutToQuit, self.shutdown)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._cancel_icon_replica()
        self.adapter.configure_icon_replica(False)
        self.timer.stop()
        self.native_timer.stop()
        self.recycle_timer.stop()
        self.save_timer.stop()
        self.bowl.feed_click.stop()
        if self._journal is not None:
            self._journal.refresh_timer.stop()
        self._save()
        self.pool.clear()
        for window in [self.gate, self.bowl, self.bin, self.bed_window, *self.cat_windows.values(), *self.gift_windows.values(), *self._ripples]:
            window.hide()
            window.close()
        if self._journal is not None:
            self._journal.hide()
        self.tray.hide()
        self.lock.unlock()
        self.logger.removeHandler(self._log_handler)
        self._log_handler.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="后院访客 · 一位有自己生活的桌面邻居")
    parser.add_argument("--hidden", action="store_true", help="只显示小门，不打开手册")
    parser.add_argument("--demo", action="store_true", help="使用独立、退出即丢弃的体验进度")
    parser.add_argument("--demo-scene", choices=("feeding", "trust", "gift", "mouse", "fight", "teaser"), help="选择独立体验场景")
    parser.add_argument("--demo-cat-limit", type=int, choices=(1, 2, 3), help="限制体验模式同时出现的猫数")
    parser.add_argument("--demo-teaser-report", type=Path, help="双猫逗猫诊断的全新报告路径；仅用于 --demo --demo-scene teaser")
    parser.add_argument("--state-dir", type=Path, help="自定义正式存档目录")
    parser.add_argument("--smoke-seconds", type=float, default=0, help="自动退出，用于图形启动验证")
    parser.add_argument("--render-preview", type=Path, help="将手册窗口保存为 PNG")
    options = parser.parse_args(argv)
    if (options.demo_scene is not None or options.demo_cat_limit is not None) and not options.demo:
        parser.error("--demo-scene 和 --demo-cat-limit 仅用于 --demo 独立体验模式")
    if options.demo_cat_limit == 1 and options.demo_scene == "fight":
        parser.error("双猫争执场景需要 --demo-cat-limit 2")
    if options.demo_teaser_report is not None and (not options.demo or options.demo_scene != "teaser"):
        parser.error("--demo-teaser-report 仅用于 --demo --demo-scene teaser")
    if options.demo_scene == "teaser" and (options.demo_cat_limit == 1 or options.demo_teaser_report is None or not options.hidden or options.render_preview):
        parser.error("逗猫诊断需要 --demo-cat-limit 2、--hidden 和全新的 --demo-teaser-report，不能同时预览手册")
    if options.smoke_seconds < 0 or not math.isfinite(options.smoke_seconds):
        parser.error("--smoke-seconds 必须是非负的有限秒数")
    report_writer = None
    if options.demo_scene == "teaser":
        from .demo_teaser import DemoReport
        try:
            report_writer = DemoReport(options.demo_teaser_report)
        except OSError as exc:
            parser.error(f"不能创建诊断报告：{exc}")
    qt = QApplication.instance() or QApplication([sys.argv[0]])
    qt.setApplicationName("Backyard Visitor")
    qt.setOrganizationName("BackyardVisitor")
    try:
        if qt.platformName() == "cocoa":
            # Match the packaged LSUIElement application when launched from
            # Python/launch.command, before creating any companion windows.
            configure_cocoa_accessory_application()
        controller = Controller(qt, state_dir=options.state_dir, demo=options.demo, hidden=options.hidden,
                                visit_limit=options.demo_cat_limit or 3,
                                seed=7 if options.demo_scene == "teaser" else None)
        if options.demo_scene == "teaser":
            from .demo_teaser import DemoTeaser
            controller._demo_teaser = DemoTeaser(controller, report_writer)
            QTimer.singleShot(0, controller._demo_teaser.start)
        elif options.demo:
            controller.demo_scene(options.demo_scene or "feeding")
    except (RuntimeError, OSError) as exc:
        if report_writer is not None:
            try:
                report_writer.write({"state": "failed", "pid": os.getpid(), "error": str(exc), "shutdown_verified": False})
            except OSError as report_error:
                print(f"诊断报告写入失败：{report_error}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2
    if options.render_preview:
        controller.show_journal()
        def capture():
            try:
                options.render_preview.parent.mkdir(parents=True, exist_ok=True)
                if not controller.journal.grab().save(str(options.render_preview), "PNG"):
                    raise OSError("无法写入预览图片")
            except OSError as exc:
                controller._warning(str(exc))
        QTimer.singleShot(300, capture)
    if options.smoke_seconds:
        QTimer.singleShot(max(500 if options.render_preview else 1, round(options.smoke_seconds * 1000)), controller.quit)
    return qt.exec()
