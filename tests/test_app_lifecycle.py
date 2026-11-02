"""Simulated display/sleep boundaries; no system permissions or display changes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
import time
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QPoint, QRect, QRectF
from PyQt6.QtWidgets import QApplication

from backyard_visitor import app
from backyard_visitor.desktop import DesktopAdapter, DesktopTarget, Rect
from backyard_visitor.model import GIFT_SIZE, SPRITE_HEIGHT, SPRITE_WIDTH


class Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in tuple(self.slots):
            slot(*args)


class Screen:
    def __init__(self, name, x=0, y=0, width=900, height=700):
        self.title = name
        self.full = QRect(x, y, width, height)
        self.available = QRect(x, y + 24, width, height - 48)
        self.availableGeometryChanged = Signal()
        self.geometryChanged = Signal()
        self.logicalDotsPerInchChanged = Signal()
        self.ratio = 2.0
        self.deleted = False

    def name(self):
        assert not self.deleted, "Accessed a removed screen"
        return self.title

    def geometry(self):
        assert not self.deleted, "Accessed a removed screen"
        return QRect(self.full)

    def availableGeometry(self):
        assert not self.deleted, "Accessed a removed screen"
        return QRect(self.available)

    def devicePixelRatio(self):
        return self.ratio


class Desktop:
    def __init__(self, qt):
        self.qt = qt
        self.display_list = [Screen("main")]
        self.primary = self.display_list[0]
        self.screenAdded = Signal()
        self.screenRemoved = Signal()
        self.primaryScreenChanged = Signal()
        self.aboutToQuit = Signal()

    def setQuitOnLastWindowClosed(self, value):
        self.qt.setQuitOnLastWindowClosed(value)

    def screens(self):
        return list(self.display_list)

    def primaryScreen(self):
        return self.primary

    def add(self, screen):
        self.display_list.append(screen)
        if self.primary is None:
            self.primary = screen
        self.screenAdded.emit(screen)

    def remove(self, screen):
        self.display_list.remove(screen)
        if self.primary is screen:
            self.primary = self.display_list[0] if self.display_list else None
        self.screenRemoved.emit(screen)
        screen.deleted = True


@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication(["lifecycle-tests"])
    desktop = Desktop(qt)
    controller = app.Controller(desktop, state_dir=tmp_path, hidden=True,
                                adapter=DesktopAdapter(), enable_native=False, seed=9)
    yield controller
    controller.shutdown()
    qt.processEvents()


def visitor(c):
    c.engine.open_gate()
    c.engine.invite("amber")
    v = c.engine.visitors["amber"]
    v.state = "walking"
    v.x, v.y = 220., c.engine.floor_y
    v.target_x, v.target_y = 680., c.engine.floor_y
    v.facing = 1
    v.turn_remaining = v.landing_remaining = 0
    c.changed()
    return v


def clock(monkeypatch, c, *, mono=100., wall=1000., cursor=QPoint(292, 600)):
    values = {"mono": mono, "wall": wall, "cursor": cursor}
    monkeypatch.setattr(app.time, "monotonic", lambda: values["mono"])
    monkeypatch.setattr(app.time, "time", lambda: values["wall"])
    monkeypatch.setattr(app.QCursor, "pos", lambda: QPoint(values["cursor"]))
    c._reset_clock()
    return values


@pytest.mark.parametrize("elapsed", [3., 60., 3600.])
def test_long_event_loop_gap_never_catches_up_simulation(c, monkeypatch, elapsed):
    v = visitor(c)
    c.engine._drop("toy_mouse", 300, 300, "amber")
    gift = c.engine.gifts[-1]
    gift.wait_remaining = 9.25
    c.changed()
    values = clock(monkeypatch, c)
    original = v.x, v.y, v.age, gift.x, gift.wait_remaining
    tick = Mock(wraps=c.engine.tick)
    cursor = Mock(wraps=c.engine.cursor_moved)
    monkeypatch.setattr(c.engine, "tick", tick)
    monkeypatch.setattr(c.engine, "cursor_moved", cursor)
    values["mono"] += elapsed
    values["wall"] += elapsed
    c._tick()
    assert (v.x, v.y, v.age, gift.x, gift.wait_remaining) == original
    tick.assert_not_called()
    cursor.assert_not_called()
    assert c.current_fps == 24
    assert not c._windows and not c._icons


def test_sleep_detected_when_monotonic_excludes_sleep(c, monkeypatch):
    v = visitor(c)
    values = clock(monkeypatch, c, cursor=QPoint(-800, -600))
    values.update(mono=100.042, wall=1600., cursor=QPoint(*(round(n) for n in c.to_display(v.x + 72, v.y + 56))))
    x, trust = v.x, c.engine.cats["amber"].trust
    c._tick()
    assert v.x == x and v.state == "walking"
    assert c.engine.cats["amber"].trust == trust
    assert c._last_cursor == values["cursor"]
    # The first real movement interval uses the new baseline, not the old warp.
    values["mono"] += .042
    values["wall"] += .042
    c._tick()
    assert v.x > x and v.state == "walking"
    assert c.engine.cats["amber"].trust == trust


@pytest.mark.parametrize("wall", [900., 1100.])
def test_wall_clock_jump_only_resynchronizes_once(c, monkeypatch, wall):
    v = visitor(c)
    values = clock(monkeypatch, c)
    values.update(mono=100.042, wall=wall)
    before = v.x
    c._tick()
    assert v.x == before
    values["mono"] += .042
    values["wall"] += .042
    c._tick()
    assert v.x > before


@pytest.mark.parametrize("wait,elapsed", [(1., 1.), (.03, .04)])
def test_normal_sparse_mouse_deadline_is_not_treated_as_sleep(c, monkeypatch, wait, elapsed):
    c.engine._drop("toy_mouse", 300, 300, "amber")
    gift = c.engine.gifts[-1]
    gift.wait_remaining = wait
    c.changed()
    values = clock(monkeypatch, c)
    epoch = c._desktop_epoch
    values["mono"] += elapsed
    values["wall"] += elapsed
    c._tick()
    assert gift.state == "escaping"
    assert abs(gift.x - 300) == pytest.approx(max(0, elapsed - wait) * 220)
    assert c._desktop_epoch == epoch and c.current_fps == 24
    assert c.timer.interval() == 42


def test_new_screen_hooks_are_connected_once_and_removed_on_shutdown(c):
    extra = Screen("left", -1200, -200, 1000, 800)
    c.qt.add(extra)
    for _ in range(4):
        c._screen_added(extra)
        c.change_screen(1)
    for signal in (extra.availableGeometryChanged, extra.geometryChanged, extra.logicalDotsPerInchChanged):
        assert len(signal.slots) == 1
    c.shutdown()
    for screen in c.qt.display_list:
        assert not screen.availableGeometryChanged.slots
        assert not screen.geometryChanged.slots
    assert not c.qt.screenAdded.slots and not c.qt.screenRemoved.slots


def test_other_screen_or_duplicate_notification_preserves_mouse_wait(c, monkeypatch):
    extra = Screen("other", 900, 0)
    c.qt.add(extra)
    c.engine._drop("toy_mouse", 300, 300, "amber")
    gift = c.engine.gifts[-1]
    gift.wait_remaining = 6.5
    bounds = Mock(wraps=c.engine.set_bounds)
    monkeypatch.setattr(c.engine, "set_bounds", bounds)
    extra.geometryChanged.emit(extra.geometry())
    c.screen.geometryChanged.emit(c.screen.geometry())
    c.screen.availableGeometryChanged.emit(c.screen.availableGeometry())
    bounds.assert_not_called()
    assert gift.wait_remaining == 6.5
    main = c.screen
    main.available.setWidth(760)
    main.availableGeometryChanged.emit(main.availableGeometry())
    main.geometryChanged.emit(main.geometry())
    assert bounds.call_count == 1
    assert c.engine.bounds[2] == pytest.approx(c.scene_rect().width)
    assert c.engine.bounds[2] * c.artwork_scale == pytest.approx(760)


def test_screen_change_rebases_negative_coordinates_and_drops_old_targets(c):
    v = visitor(c)
    c.engine._drop("feather", 300, 300, "amber")
    c._windows = [DesktopTarget("old", "old", Rect(100, 300, 500, 200))]
    c._icons = [DesktopTarget("icon", "old", Rect(200, 350, 64, 64))]
    c._windows_checked = c._icons_checked = time.monotonic()
    c.settings.update(window_paths=True, icons_enabled=True, icon_ids=["icon"])
    c._wallpaper_match = True
    c._rebuild_paths()
    assert c.engine.window_edges and c.engine.paths
    left = Screen("left", -1200, -250, 1000, 800)
    c.qt.add(left)
    c.change_screen(1)
    assert c.screen is left and c.settings["screen_name"] == "left"
    rect = c.scene_rect()
    assert c.engine.bounds == pytest.approx((rect.x, rect.y, rect.width, rect.height))
    assert c.to_display(rect.left, rect.top) == pytest.approx((-1200., -226.))
    assert c.to_display(rect.right, rect.bottom) == pytest.approx((-200., 526.))
    assert not c._windows and not c._icons and not c.engine.window_edges and not c.engine.paths
    assert not c.engine.gift_spots and not c._wallpaper_match
    assert math.isinf(c._windows_checked) and c._windows_checked < 0
    x, y, width, height = c.engine.bounds
    assert x <= v.x <= x + width - SPRITE_WIDTH
    assert y <= v.y <= y + height - SPRITE_HEIGHT
    assert x <= v.target_x <= x + width - SPRITE_WIDTH
    assert y <= v.target_y <= y + height - SPRITE_HEIGHT
    for gift in c.engine.gifts:
        assert x <= gift.x <= x + width - GIFT_SIZE
        assert y <= gift.y <= y + height - GIFT_SIZE
    assert left.availableGeometry().contains(c.gate.geometry())
    assert left.availableGeometry().contains(c.bin.geometry())


@pytest.mark.parametrize("paused", [False, True])
def test_no_screen_stops_display_and_reconnect_preserves_user_pause(c, paused):
    visitor(c)
    c.set_paused(paused)
    old = c.screen
    c.qt.remove(old)
    assert not c._screen_ready and c.paused is paused
    assert c.current_fps == 0 and not c.timer.isActive() and not c.native_timer.isActive()
    assert not c.gate.isVisible() and not any(w.isVisible() for w in c.cat_windows.values())
    # The former QScreen is now invalid, but queued paint/clip reads remain safe.
    assert c.screen.geometry().width() == 900
    c._sync()
    new = Screen("replacement", -900, 100)
    c.qt.add(new)
    assert c._screen_ready and c.screen is new and c.paused is paused
    assert c.gate.isVisible()
    assert c.timer.isActive() is (not paused)


def test_removed_selected_screen_falls_back_without_reconnecting_remaining(c):
    left = Screen("left", -900, 0)
    c.qt.add(left)
    c.change_screen(1)
    c.qt.remove(left)
    assert c.screen is c.qt.primaryScreen()
    assert c.settings["screen_name"] == "main"
    assert len(c.screen.geometryChanged.slots) == 1
    assert not left.geometryChanged.slots


def test_primary_change_keeps_explicit_selection_but_invalidates_wallpaper(c):
    left = Screen("left", -900, 0)
    c.qt.add(left)
    c.change_screen(1)
    c._wallpaper_match = True
    epoch = c._desktop_epoch
    c.qt.primary = left
    c.qt.primaryScreenChanged.emit(left)
    assert c.screen is left
    assert c._desktop_epoch == epoch + 1 and not c._wallpaper_match


def test_only_current_screen_icons_and_window_segments_become_targets(c):
    left = Screen("left", -900, -100)
    c.qt.add(left)
    c.change_screen(1)
    c.settings.update(icons_enabled=True, icon_ids=["yes", "other", "cross"], window_paths=True)
    c._icons = [DesktopTarget("yes", "allowed", Rect(-550, 250, 64, 64)),
                DesktopTarget("other", "other monitor", Rect(150, 250, 64, 64)),
                DesktopTarget("cross", "straddles edge", Rect(-20, 250, 64, 64))]
    c._windows = [DesktopTarget("local", "local", Rect(-650, 200, 500, 200)),
                  DesktopTarget("other", "other monitor", Rect(150, 250, 500, 200))]
    c._windows_checked = time.monotonic()
    c._rebuild_paths()
    assert [i.id for i in c._selected_icons()] == ["yes"]
    assert len(c.engine.paths) == len(c.engine.gift_spots) == 1
    assert list(c.engine.window_edges) == ["local:top:0"]
    x, y, width, height = c.engine.bounds
    assert all(x <= a <= b <= x + width and y + SPRITE_HEIGHT <= top <= y + height
               for a, b, top in c.engine.window_edges.values())
    assert all(x <= px <= x + width - SPRITE_WIDTH and y <= py <= c.engine.floor_y
               for px, py in c.engine.paths)


def native_result(c, **overrides):
    now = time.monotonic()
    result = {"context_epoch": c._desktop_epoch, "requested_at": now,
              "windows": [DesktopTarget("old", "test", Rect(100, 300, 500, 200))],
              "windows_ok": True, "windows_time": now}
    result.update(overrides)
    return result


@pytest.mark.parametrize("change", ["screen", "pause", "age", "shutdown"])
def test_native_snapshot_cannot_restore_old_coordinates(c, change):
    result = native_result(c)
    if change == "screen":
        c.qt.add(Screen("left", -900, 0))
        c.change_screen(1)
    elif change == "pause":
        c.set_paused(True)
        c.set_paused(False)
    elif change == "age":
        result["requested_at"] -= 10
    else:
        c.shutdown()
    c._native_result(result)
    assert c._windows == [] and not c.engine.window_edges


def test_queued_native_result_before_first_resume_tick_is_discarded(c, monkeypatch):
    visitor(c)
    values = clock(monkeypatch, c)
    result = native_result(c)
    values.update(mono=100.042, wall=1600.)
    c._native_result(result)
    assert not c._windows and c._desktop_epoch != result["context_epoch"]


def test_stale_worker_error_does_not_revoke_current_permissions(c):
    class Pool:
        def start(self, worker):
            pass
        def clear(self):
            pass
    c.pool = Pool()
    c.settings["icons_enabled"] = c.adapter.icons_enabled = True
    callback = Mock()
    assert c._run_worker("icon-replica", lambda: None, callback)
    c._invalidate_desktop_context()
    c._worker_finished("icon-replica", None, RuntimeError("old operation"))
    callback.assert_not_called()
    assert c.settings["icons_enabled"] and c.adapter.icons_enabled
    assert not c._workers


def test_old_recycle_cache_cannot_survive_screen_context(c, monkeypatch):
    c.settings["recycle_enabled"] = c.adapter.recycle_enabled = True
    target = Rect(100, 100, 50, 50)
    monkeypatch.setattr(c.adapter, "recycle_rect", lambda: target)
    assert c.recycle_target() == target
    c._invalidate_desktop_context()
    assert c.recycle_target() is None
    c._recycle_checked_epoch = c._desktop_epoch
    c.qt.add(Screen("left", -900, 0))
    c.change_screen(1)
    c._recycle_checked_epoch = c._desktop_epoch
    assert c.recycle_target() is None  # Another display's Dock is not a target.


def test_late_ripple_expiry_is_harmless_after_lifecycle_cleanup(c):
    ripple = Mock()
    c._ripples.append(ripple)
    c._invalidate_desktop_context()
    c._remove_ripple(ripple)
    ripple.close.assert_called_once()
    ripple.deleteLater.assert_called_once()


def test_landing_turn_and_settle_end_each_refresh_visible_pose(c, monkeypatch):
    v = visitor(c)
    v.state = "idle"
    v.landing_remaining = v.turn_remaining = v.settle_remaining = 0
    c._sync()
    update = Mock()
    monkeypatch.setattr(c.cat_windows["amber"], "update", update)
    v.landing_remaining = .2
    c._sync()
    c._sync()
    assert update.call_count == 2
    v.landing_remaining, v.turn_remaining = 0, .1
    c._sync()
    assert update.call_count == 3
    v.turn_remaining, v.settle_remaining = 0, .7
    c._sync()
    v.settle_remaining = .4
    c._sync()
    # Sitting down now has distinct key poses and refreshes during its timer.
    assert update.call_count == 5
    v.settle_remaining = 0
    c._sync()
    assert update.call_count == 6
    c._sync()
    assert update.call_count == 6  # Fully seated returns to unchanged-pose skipping.


@pytest.mark.parametrize("paused", [False, True])
def test_transient_zero_sized_screen_suspends_then_restores(c, paused):
    visitor(c)
    c.set_paused(paused)
    screen = c.screen
    original = QRect(screen.available)
    old_bounds = c.engine.bounds
    screen.available = QRect(0, 0, 0, 0)
    screen.availableGeometryChanged.emit(screen.availableGeometry())
    assert not c._screen_ready and not c.timer.isActive()
    assert c.engine.bounds == old_bounds
    assert c.screen.availableGeometry() == original
    screen.available = original
    screen.availableGeometryChanged.emit(original)
    assert c._screen_ready and c.screen is screen
    assert c.paused is paused and c.timer.isActive() is (not paused)


def test_delayed_recycle_callback_cannot_validate_cache_after_screen_change(c, monkeypatch):
    c.settings["recycle_enabled"] = c.adapter.recycle_enabled = True
    c.dragging_gift = True
    requests = []
    monkeypatch.setattr(c, "_run_worker", lambda *args: requests.append(args))
    c._poll_recycle()
    assert len(requests) == 1
    c.qt.add(Screen("left", -900, 0))
    c.change_screen(1)
    target = Rect(-800, 100, 50, 50)
    monkeypatch.setattr(c.adapter, "recycle_rect", lambda: target)
    requests[0][2](target)
    assert c.recycle_target() is None
    c.dragging_gift = True
    c._poll_recycle()
    requests[-1][2](target)
    assert c.recycle_target() == target


def test_display_change_commits_model_positions_and_cancels_old_drags(c):
    c.engine._drop("toy_mouse", 300, 300, "amber")
    c.changed()
    gift = c.engine.gifts[-1]
    window = c.gift_windows[gift.id]
    c.engine.set_gift_held(gift.id, True)
    window.drag_active = True
    window.move(1700, 1000)
    c.bowl.drag_active = True
    c.bowl.move(1600, 900)
    c.qt.add(Screen("left", -900, 0))
    c.change_screen(1)
    assert not window.drag_active and not c.bowl.drag_active
    assert (window.x(), window.y()) == tuple(round(n) for n in c.to_display(gift.x, gift.y))
    assert gift.state == "idle" and 8 <= gift.wait_remaining <= 22
    assert c.screen.availableGeometry().contains(window.geometry())
    assert not c.bin.isVisible() and not c.dragging_gift


class ShowSnapshot:
    """Collect synchronous Qt ShowEvent state, not OS compositor pixels."""
    def __init__(self, qt, c, windows=None):
        from PyQt6.QtCore import QEvent, QObject
        from PyQt6.QtGui import QRegion
        from backyard_visitor.overlays import CatWindow, GiftWindow
        self.records=[]
        records=self.records
        class Filter(QObject):
            def eventFilter(self,obj,event):
                if event.type()!=QEvent.Type.Show or not isinstance(obj,(CatWindow,GiftWindow)):
                    return False
                if windows is not None and obj not in windows:return False
                model=obj.visitor() if isinstance(obj,CatWindow) else obj.gift()
                expected=QRegion(obj.rect()) & QRegion(c.screen.geometry().translated(-obj.x(),-obj.y()))
                if isinstance(obj,CatWindow):
                    # The scene anchor can fall between Qt window pixels. The
                    # painter retains that fraction, and the input mask must
                    # follow the painted canvas rather than invent a border.
                    dx,dy=c.to_display(round(model.x),round(model.y))
                    canvas=QRectF(dx-obj.x(),dy-obj.y(),
                                  obj.base_size.width()*c.artwork_scale,
                                  obj.base_size.height()*c.artwork_scale)
                    expected &= QRegion(canvas.toRect())
                if isinstance(obj,CatWindow) and model.clip_y is not None:
                    expected &= QRegion(QRect(0,0,obj.width(),max(0,min(obj.height(),math.ceil(c.to_display(model.x, model.clip_y)[1]-obj.y())))))
                if expected.isEmpty():expected=QRegion(QRect(-2,-2,1,1))
                records.append({'kind':type(obj).__name__,'position':[obj.x(),obj.y()],
                    'expected_position':[round(n) for n in c.to_display(
                        round(model.x) if isinstance(obj,CatWindow) else model.x,
                        round(model.y) if isinstance(obj,CatWindow) else model.y)],
                    'mask_matches_selected_screen':obj.mask()==expected})
                return False
        self.qt=qt;self.filter=Filter();qt.installEventFilter(self.filter)
    def close(self):self.qt.removeEventFilter(self.filter)


def test_new_cat_first_show_already_has_model_position_and_screen_clip(c):
    watch=ShowSnapshot(c.qt.qt,c)
    try:
        c.engine.open_gate();c.engine.invite('amber');c.changed()
    finally:watch.close()
    assert len(watch.records)==1
    assert watch.records[0]['position']==watch.records[0]['expected_position']
    assert watch.records[0]['mask_matches_selected_screen']


@pytest.mark.parametrize('paused',[False,True])
def test_restored_cat_and_gift_show_only_after_reposition_and_reclip(c,paused):
    visitor(c);c.engine._drop('feather',300,300,'amber');c.changed();c.set_paused(paused)
    windows=[c.cat_windows['amber'],c.gift_windows[c.engine.gifts[-1].id]]
    c.qt.remove(c.screen)
    assert all(not w.isVisible() for w in windows)
    watch=ShowSnapshot(c.qt.qt,c,windows)
    try:c.qt.add(Screen('new',-900,100))
    finally:watch.close()
    assert len(watch.records)==2
    assert {r['kind'] for r in watch.records}=={'CatWindow','GiftWindow'}
    assert all(r['position']==r['expected_position'] and r['mask_matches_selected_screen'] for r in watch.records)
    assert all(w.isVisible() for w in windows)
    assert c.paused is paused
