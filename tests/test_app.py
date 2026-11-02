"""Offscreen integration checks for the desktop controller and its boundaries."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from PyQt6.QtCore import QPoint, QSize
from PyQt6.QtWidgets import QApplication

from backyard_visitor import app
from backyard_visitor.desktop import DesktopAdapter, DesktopTarget, Rect
from backyard_visitor.model import Gift, SPRITE_HEIGHT, SPRITE_WIDTH


@pytest.fixture(scope="module")
def qt():
    instance = QApplication.instance() or QApplication(["backyard-test"])
    instance.setApplicationName("BackyardVisitorTests")
    return instance


@pytest.fixture
def controller(qt, tmp_path):
    c = app.Controller(qt, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=5)
    yield c
    c.shutdown()
    qt.processEvents()


def test_gate_idle_moving_pause_and_closed_timers(controller):
    c = controller
    assert not c.engine.gate_open
    assert c.current_fps == 0 and not c.timer.isActive()
    c.toggle_gate()
    assert c.current_fps == 1
    c.engine.invite("amber")
    c.changed()
    assert c.current_fps == 2
    visitor = c.engine.visitors["amber"]
    visitor.state = "walking"
    c.wake()
    assert c.current_fps == 24
    c.set_paused(True)
    assert c.current_fps == 0 and not c.timer.isActive() and not c.native_timer.isActive()
    c.set_paused(False)
    assert c.current_fps == 24
    c.engine.visitors.clear()
    c.close_gate()
    assert c.current_fps == 0 and not c.timer.isActive()


def test_relationship_actions_save_settings_and_close_hides(controller, monkeypatch):
    c = controller
    monkeypatch.setattr(app.QInputDialog, "getText", lambda *args, **kwargs: ("团子", True))
    c.engine.open_gate(); c.engine.invite("amber")
    c.rename_cat("amber")
    c.engine.cats["amber"].trust = 100
    c.adopt("amber")
    c.feed()
    c.set_calm_mode(True)
    assert c._save()
    state = json.loads(c.state_path.read_text())
    assert state["cats"]["amber"]["nickname"] == "团子"
    assert state["cats"]["amber"]["adopted"] is True
    assert state["food"] == 1
    assert state["settings"]["calm_mode"] is True
    c.show_journal()
    c.journal.close()
    assert not c.journal.isVisible()
    assert not c._quitting
    assert not c.journal.refresh_timer.isActive()


def test_state_lock_prevents_two_controllers(controller, qt):
    with pytest.raises(RuntimeError, match="已有"):
        app.Controller(qt, state_dir=controller.state_dir, hidden=True, adapter=DesktopAdapter(), enable_native=False)


def test_demo_ignores_real_state_and_never_saves(qt, tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"version":1,"food":3}', encoding="utf-8")
    original = target.read_bytes()
    c = app.Controller(qt, state_dir=tmp_path, demo=True, hidden=True, adapter=DesktopAdapter(), enable_native=False)
    try:
        assert c.engine.food == 0
        c.demo_scene("trust")
        assert c.engine.visitors
        assert c._save()
        assert not c.state_path.exists()
    finally:
        c.shutdown()
    assert target.read_bytes() == original


def test_only_explicitly_selected_icons_become_paths(controller):
    c = controller
    approved = DesktopTarget("yes", "approved", Rect(300, 350, 50, 50))
    excluded = DesktopTarget("no", "private", Rect(450, 350, 50, 50))
    c._icons = [approved, excluded]
    c.settings.update(icons_enabled=True, icon_ids=["yes"])
    c._rebuild_paths()
    assert len(c.engine.paths) == 1
    x, y = c.engine.paths[0]
    # The model path is in scene units; the cat's bottom centre must still
    # meet the approved icon's real bottom centre, regardless of artwork size.
    assert c.to_display(x + SPRITE_WIDTH / 2, y + SPRITE_HEIGHT) == pytest.approx((325, 400))
    c.settings["icons_enabled"] = False
    c._rebuild_paths()
    assert c.engine.paths == []
    c.settings["window_paths"] = True
    c._windows = [DesktopTarget("w", "Test", Rect(100, 300, 400, 300))]
    c._windows_checked = time.monotonic()
    c._rebuild_paths()
    assert c.engine.window_edges
    c._windows_checked = time.monotonic() - 10
    c._rebuild_paths()
    assert c.engine.paths == []
    assert not c.engine.window_edges


def test_wallpaper_never_cuts_foreground_visitors(controller):
    c = controller
    c.journal.hide()
    geometry = c.screen.geometry()
    point = QPoint(geometry.left(), geometry.top() + round(geometry.height() * 0.75))
    size = QSize(144, 112)
    assert c.cat_clip(point, size) is None
    c._wallpaper_match = True
    c._wallpaper_checked = c._windows_checked = time.monotonic()
    assert c.cat_clip(point, size) is None
    c._windows = [DesktopTarget("cover", "Application", Rect(point.x(), point.y(), 200, 200))]
    assert c.cat_clip(point, size) is None
    c._windows = []
    c._wallpaper_checked = time.monotonic() - 60
    assert c.cat_clip(point, size) is None


def test_native_permission_work_is_off_main_thread(controller, qt):
    c = controller
    main_thread = threading.get_ident()
    seen = []
    c.adapter.capabilities["desktop_icons"] = True
    def request():
        seen.append(threading.get_ident())
        time.sleep(0.1)
        c.adapter.icons_enabled = True
        return True
    c.adapter.request_icon_access = request
    start = time.monotonic()
    c.set_icons_enabled(True)
    assert time.monotonic() - start < 0.08
    deadline = time.monotonic() + 2
    while c._workers and time.monotonic() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    assert not c._workers
    assert seen and seen[0] != main_thread
    assert c.settings["icons_enabled"] is True


def test_failed_save_notifies_once_and_preserves_original(controller, monkeypatch):
    c = controller
    c._save()
    original = c.state_path.read_bytes()
    warnings = []
    monkeypatch.setattr(c, "_warning", warnings.append)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(app, "save_state", fail)
    assert c._save() is False
    assert c._save() is False
    assert len(warnings) == 1
    assert c.state_path.read_bytes() == original


def test_gift_windows_follow_persistent_model(controller):
    c = controller
    c.engine.gifts = [Gift("one", "feather", 20, 30, "amber")]
    c.changed()
    assert "one" in c.gift_windows
    c.engine.collect_gift("one")
    c.changed()
    assert "one" not in c.gift_windows


def test_cli_offscreen_smoke_and_preview(tmp_path):
    target = tmp_path / "preview.png"
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-m", "backyard_visitor", "--demo", "--smoke-seconds", "0.8", "--render-preview", str(target)],
                            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_dragged_gift_and_bowl_are_not_reset_by_frame_sync(controller):
    c = controller
    c.engine.gifts = [Gift("drag", "feather", 20, 30, "amber")]
    c.changed()
    gift = c.gift_windows["drag"]
    gift.drag_active = True
    gift.move(222, 230)
    c.bowl.drag_active = True
    c.bowl.move(333, 340)
    c._sync()
    assert (gift.x(), gift.y()) == (222, 230)
    assert (c.bowl.x(), c.bowl.y()) == (333, 340)
    gift.drag_active = False
    c.bowl.drag_active = False
    c._sync()
    assert (gift.x(), gift.y()) == tuple(round(value) for value in c.to_display(20, 30))
    assert (c.engine.gifts[0].x, c.engine.gifts[0].y) == (20, 30)


def test_hidden_passive_operations_do_not_create_journal_or_art(controller):
    c = controller
    assert c._journal is None and c._art is None
    c.note("静静记录")
    c.feed()
    c.set_calm_mode(True)
    c.set_paused(True)
    c.set_paused(False)
    c.set_icons_enabled(False)
    c.call_cat("amber")
    c.changed()
    c._save()
    assert c._journal is None and c._art is None
    c.shutdown()
    assert c._journal is None and c._art is None


def test_lazy_journal_replays_bounded_notes_and_preserves_pause(controller):
    c = controller
    for index in range(125):
        c.note(f"小事 {index}")
    c.set_paused(True)
    assert c._journal is None and c._art is None
    journal = c.journal
    assert journal.logs.count() == 120
    assert journal.logs.item(0).text().endswith("小事 124")
    assert journal.logs.item(119).text().endswith("小事 5")
    assert journal.pause_check.isChecked()
    assert c.journal is journal
    c.note("后来又发生一件")
    assert journal.logs.count() == 120
    assert journal.logs.item(0).text().endswith("后来又发生一件")


def test_static_sync_avoids_redundant_repaints(controller, monkeypatch):
    c = controller
    c.engine.open_gate()
    c.engine.invite("amber")
    visitor = c.engine.visitors["amber"]
    visitor.state = "idle"
    visitor.x, visitor.y = 200, 300
    c.changed()
    calls = {"cat": 0, "gate": 0, "bowl": 0}
    def counter(key):
        def update():
            calls[key] += 1
        return update
    monkeypatch.setattr(c.cat_windows["amber"], "update", counter("cat"))
    monkeypatch.setattr(c.gate, "update", counter("gate"))
    monkeypatch.setattr(c.bowl, "update", counter("bowl"))
    c._sync()
    c._sync()
    assert calls == {"cat": 0, "gate": 0, "bowl": 0}
    visitor.facing *= -1
    c._sync()
    assert calls["cat"] == 1
    visitor.state = "walking"
    c._sync()
    c._sync()
    assert calls["cat"] == 3
    c.engine.food += 1
    c._sync()
    assert calls["bowl"] == 1
    c.engine.open_gate(False)
    c._sync()
    assert calls["gate"] == 1


def test_hiding_journal_releases_window_and_keeps_story(controller, qt):
    from PyQt6.QtTest import QTest
    c = controller
    c.note('这是一条需要保留的来访手记。')
    c.show_journal()
    old = c._journal
    old.hide()
    QTest.qWait(220)
    qt.processEvents()
    assert c._journal is None
    c.show_journal()
    assert c._journal is not old
    assert any('需要保留' in c._journal.logs.item(i).text() for i in range(c._journal.logs.count()))


def test_journal_navigation_checked_state_switches_page_exclusively(controller):
    journal = controller.journal
    clicked = []
    for index, button in enumerate(journal.nav):
        button.clicked.connect(lambda checked, index=index: clicked.append(index))
    assert journal.pages.currentIndex() == 0
    for selected in (1, 3, 2, 0):
        # Reproduce the macOS AX path: checked changes without a clicked signal.
        journal.nav[selected].setChecked(True)
        assert journal.pages.currentIndex() == selected
        assert [button.isChecked() for button in journal.nav] == [
            index == selected for index in range(len(journal.nav))
        ]
        assert journal.nav_group.checkedId() == selected
    assert clicked == []


def test_journal_navigation_repeat_activation_keeps_one_selection(controller):
    journal = controller.journal
    journal.show_page(3)
    assert journal.nav_group.checkedId() == 3
    journal.nav[3].click()
    journal.nav[3].setChecked(False)
    assert journal.pages.currentIndex() == 3
    assert journal.nav[3].isChecked()
    assert sum(button.isChecked() for button in journal.nav) == 1
    journal.show_page(1)
    assert journal.pages.currentIndex() == 1
    assert journal.nav_group.checkedId() == 1
    assert [button.isChecked() for button in journal.nav] == [False, True, False, False, False]


def test_sprite_cache_released_after_final_visitor_and_journal_leave(controller):
    c = controller
    c.engine.open_gate(True)
    c.engine.invite("amber")
    c.changed()
    loaded = c.art
    c._release_unused_art()
    assert c._art is loaded
    c.show_journal()
    c.engine.visitors.clear()
    c.changed()
    assert c._art is loaded
    window = c._journal
    window.hide()
    c.release_journal(window)
    assert c._art is None
    assert c._journal is None
    c.engine.invite("mist")
    c.changed()
    assert c.art is not loaded
