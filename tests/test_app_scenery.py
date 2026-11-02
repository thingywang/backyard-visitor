"""Wallpaper selection and mask integration, using no native desktop services."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path
import threading
import time

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImageReader
from PyQt6.QtWidgets import QApplication, QPushButton

from backyard_visitor import app
from backyard_visitor.desktop import DesktopAdapter


class FakeWallpaperAdapter(DesktopAdapter):
    """A controllable worker-side wallpaper service; never touches the OS."""
    def __init__(self):
        super().__init__()
        self.capabilities["wallpaper"] = True
        self.current = "/fake/original.png"
        self.calls = []
        self.read_threads = []
        self.restore_calls = []
        self.succeed = True
        self.report_applied_path = True
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def set_wallpaper(self, path):
        self.calls.append((Path(path), threading.get_ident()))
        self.started.set()
        if not self.release.wait(2):
            raise TimeoutError("test did not release fake wallpaper service")
        if not self.succeed:
            self.last_error = "fake wallpaper rejection"
            return False
        if self.report_applied_path:
            self.current = str(path)
        return True

    def current_wallpaper(self):
        self.read_threads.append(threading.get_ident())
        return self.current

    def restore_wallpaper(self):
        self.restore_calls.append(threading.get_ident())
        self.current = "/fake/original.png"
        return True


@pytest.fixture(scope="module")
def qt():
    instance = QApplication.instance() or QApplication(["backyard-scenery-test"])
    instance.setApplicationName("BackyardVisitorTests")
    return instance


def wait_until(qt, condition, timeout=2):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qt.processEvents()
        QTest.qWait(5)
    qt.processEvents()
    assert condition(), "wallpaper worker did not complete within test timeout"


@pytest.fixture
def make_controller(qt, tmp_path):
    controllers = []

    def create(saved_id=None, *, with_saved_id=False, demo=False, saved_depth=False):
        state_dir = tmp_path / str(len(controllers))
        state_dir.mkdir()
        if with_saved_id:
            (state_dir / "state.json").write_text(json.dumps({
                "version": 1, "settings": {"wallpaper_id": saved_id, "preset_wallpaper": True, "depth_enabled": saved_depth},
            }), encoding="utf-8")
        adapter = FakeWallpaperAdapter()
        controller = app.Controller(qt, state_dir=state_dir, demo=demo, hidden=True,
                                    adapter=adapter, enable_native=False, seed=17)
        controllers.append(controller)
        return controller, adapter, state_dir

    yield create

    for controller in controllers:
        controller.adapter.release.set()
        wait_until(qt, lambda: not controller._workers)
        controller.shutdown()
    qt.processEvents()


def select(controller, scene_id):
    choices = controller.journal.wallpaper_choices
    index = choices.findData(scene_id)
    assert index >= 0
    choices.setCurrentIndex(index)
    return choices


def click_apply(controller):
    buttons = [button for button in controller.journal.findChildren(QPushButton)
               if button.text() == "使用后院预设壁纸"]
    assert len(buttons) == 1
    buttons[0].click()


def finish_apply(controller, qt):
    wait_until(qt, lambda: not controller._workers)


def test_all_presets_can_be_selected_without_system_writes(make_controller, qt):
    c, adapter, _ = make_controller()
    choices = c.journal.wallpaper_choices
    assert {choices.itemData(i) for i in range(choices.count())} >= {
        "backyard-dusk", "backyard-morning", "backyard-cat-tree-portrait",
    }
    original_id, original_mask = c.settings["wallpaper_id"], c._mask_data
    for scene_id in ("backyard-morning", "backyard-dusk", "backyard-cat-tree-portrait"):
        select(c, scene_id)
        qt.processEvents()
        assert c.settings["wallpaper_id"] == original_id
        assert c._mask_data is original_mask
    assert adapter.calls == [] and adapter.read_threads == []
    assert c._workers == {}
    assert not c._wallpaper_match


@pytest.mark.parametrize("scene_id", ["backyard-dusk", "backyard-morning", "backyard-cat-tree-portrait"])
def test_explicit_apply_runs_off_ui_thread_and_matches_saved_mask(make_controller, qt, scene_id, monkeypatch):
    c, adapter, _ = make_controller()
    select(c, scene_id)
    if scene_id == "backyard-cat-tree-portrait":
        set_primary(monkeypatch, qt, portrait=True)
    adapter.release.clear()
    main_thread = threading.get_ident()

    click_apply(c)
    wait_until(qt, adapter.started.is_set)
    assert "wallpaper" in c._workers
    assert not c.settings["preset_wallpaper"]
    assert not c._wallpaper_match
    assert adapter.calls == [(app.ASSETS / c.scenes[scene_id]["file"], adapter.calls[0][1])]
    assert adapter.calls[0][1] != main_thread

    adapter.release.set()
    finish_apply(c, qt)
    assert c.settings["preset_wallpaper"]
    assert c.settings["wallpaper_id"] == scene_id
    assert c._mask_data == c.scenes[scene_id]
    assert c._wallpaper_match
    assert adapter.read_threads and all(thread != main_thread for thread in adapter.read_threads)
    assert c._save()
    saved = json.loads(c.state_path.read_text(encoding="utf-8"))
    assert saved["settings"]["wallpaper_id"] == scene_id


def test_failed_apply_does_not_enable_new_mask(make_controller, qt):
    c, adapter, _ = make_controller()
    select(c, "backyard-dusk")
    click_apply(c)
    finish_apply(c, qt)
    old_mask = c._mask_data
    assert c._wallpaper_match

    select(c, "backyard-morning")
    adapter.succeed = False
    click_apply(c)
    finish_apply(c, qt)
    assert len(adapter.calls) == 2
    assert c.settings["wallpaper_id"] == "backyard-dusk"
    assert c._mask_data is old_mask
    assert not c.settings["preset_wallpaper"]
    assert not c._wallpaper_match


def test_reported_success_requires_current_wallpaper_verification(make_controller, qt):
    c, adapter, _ = make_controller()
    adapter.report_applied_path = False
    select(c, "backyard-morning")
    click_apply(c)
    finish_apply(c, qt)
    assert c.settings["wallpaper_id"] == "backyard-morning"
    assert c._mask_data == c.scenes["backyard-morning"]
    assert not c._wallpaper_match
    c._native_result({"wallpaper": str(app.ASSETS / c.scenes["backyard-dusk"]["file"])})
    assert not c._wallpaper_match


def test_unapplied_choice_does_not_change_active_mask_or_verification(make_controller, qt):
    c, adapter, _ = make_controller()
    select(c, "backyard-dusk")
    click_apply(c)
    finish_apply(c, qt)
    old_mask = c._mask_data

    select(c, "backyard-morning")
    c._native_result({"wallpaper": adapter.current})
    assert len(adapter.calls) == 1
    assert c.settings["wallpaper_id"] == "backyard-dusk"
    assert c._mask_data is old_mask
    assert c._wallpaper_match
    assert c._save()
    assert json.loads(c.state_path.read_text())["settings"]["wallpaper_id"] == "backyard-dusk"


def test_selection_changed_during_apply_keeps_requested_scene_and_mask(make_controller, qt):
    c, adapter, _ = make_controller()
    select(c, "backyard-morning")
    adapter.release.clear()
    click_apply(c)
    wait_until(qt, adapter.started.is_set)
    select(c, "backyard-dusk")
    adapter.release.set()
    finish_apply(c, qt)
    assert c.journal.wallpaper_choices.currentData() == "backyard-dusk"
    assert c.settings["wallpaper_id"] == "backyard-morning"
    assert c._mask_data == c.scenes["backyard-morning"]
    assert c._wallpaper_match
    assert adapter.calls[0][0].name == "backyard-morning.png"


@pytest.mark.parametrize("saved_id", [
    "missing-scene", "../../outside", "/tmp/untrusted.png", "", None,
    ["backyard-morning"], {"id": "backyard-morning"}, 42, True,
], ids=["unknown", "traversal", "absolute-path", "empty", "null", "list", "object", "number", "bool"])
def test_untrusted_saved_wallpaper_id_falls_back_without_system_writes(make_controller, saved_id):
    c, adapter, _ = make_controller(saved_id, with_saved_id=True)
    assert c.settings["wallpaper_id"] == "backyard-dusk"
    assert c._mask_data == c.scenes["backyard-dusk"]
    assert c.journal.wallpaper_choices.currentData() == "backyard-dusk"
    assert c._wallpaper_path().resolve().parent == app.ASSETS.resolve()
    assert not c._wallpaper_match
    assert adapter.calls == []


@pytest.mark.parametrize("scene_id", ["backyard-dusk", "backyard-morning", "backyard-cat-tree-portrait"])
def test_known_saved_preset_restores_choice_but_waits_for_verification(make_controller, scene_id):
    c, adapter, _ = make_controller(scene_id, with_saved_id=True)
    assert c.settings["wallpaper_id"] == scene_id
    assert c._mask_data == c.scenes[scene_id]
    assert c.journal.wallpaper_choices.currentData() == scene_id
    assert not c._wallpaper_match
    assert adapter.calls == []


def test_unknown_ui_choice_does_not_apply_or_change_mask(make_controller, qt):
    c, adapter, _ = make_controller()
    choices = c.journal.wallpaper_choices
    old_mask = c._mask_data
    choices.addItem("Unavailable preset", "../../outside")
    choices.setCurrentIndex(choices.count() - 1)
    click_apply(c)
    qt.processEvents()
    assert adapter.calls == []
    assert not c._workers
    assert c.settings["wallpaper_id"] == "backyard-dusk"
    assert c._mask_data is old_mask
    assert not c._wallpaper_match


def test_demo_cannot_apply_restore_or_change_formal_state(make_controller, qt):
    c, adapter, formal_dir = make_controller("backyard-morning", with_saved_id=True, demo=True)
    formal_state = formal_dir / "state.json"
    original = formal_state.read_bytes()
    old_mask, old_id = c._mask_data, c.settings["wallpaper_id"]
    select(c, "backyard-morning")
    click_apply(c)
    c.restore_wallpaper()
    qt.processEvents()
    assert adapter.calls == [] and adapter.restore_calls == [] and adapter.read_threads == []
    assert not c._workers
    assert c._mask_data is old_mask
    assert c.settings["wallpaper_id"] == old_id
    assert not c._wallpaper_match
    assert c._save()
    assert not c.state_path.exists()
    assert formal_state.read_bytes() == original


def test_unsupported_adapter_cannot_apply_selected_wallpaper(make_controller, qt):
    c, adapter, _ = make_controller()
    adapter.capabilities["wallpaper"] = False
    select(c, "backyard-morning")
    click_apply(c)
    qt.processEvents()
    assert adapter.calls == [] and not c._workers
    assert c.settings["wallpaper_id"] == "backyard-dusk"
    assert not c._wallpaper_match


class GeometryScreen:
    def __init__(self, portrait):
        self.portrait = portrait

    def geometry(self):
        return QRect(0, 0, 900, 1600) if self.portrait else QRect(0, 0, 1600, 900)


def set_primary(monkeypatch, qt, *, portrait):
    primary = GeometryScreen(portrait)
    monkeypatch.setattr(qt, 'primaryScreen', lambda: primary)


def test_portrait_preview_keeps_whole_image_ratio_and_explains_limits(make_controller):
    c, adapter, _ = make_controller()
    select(c, 'backyard-cat-tree-portrait')
    metadata = json.loads((app.ASSETS/'backyard-cat-tree-portrait.json').read_text())
    size = QImageReader(str(app.ASSETS/metadata['file'])).size()
    assert [size.width(), size.height()] == metadata['canvas'] == [941, 1672]
    assert size.width()/size.height() == pytest.approx(9/16, abs=.001)
    preview = c.journal.wallpaper_preview.pixmap()
    assert not preview.isNull()
    assert preview.height() == 180 and preview.width() < preview.height()
    assert preview.width()/preview.height() == pytest.approx(size.width()/size.height(), abs=.006)
    assert '9:16' in c.journal.wallpaper_note.text()
    assert '猫爬架互动' in c.journal.wallpaper_note.text()
    assert not c.settings['tree_enabled']
    assert '不启用纵深' in c.journal.wallpaper_note.text()
    assert not adapter.calls and not adapter.read_threads


def test_portrait_apply_checks_system_primary_not_cat_activity_screen(make_controller, qt, monkeypatch):
    c, adapter, _ = make_controller()
    select(c, 'backyard-cat-tree-portrait')
    set_primary(monkeypatch, qt, portrait=False)
    monkeypatch.setattr(c, 'screen', GeometryScreen(True))
    click_apply(c)
    assert not adapter.calls and not c._workers
    assert c.settings['wallpaper_id'] == 'backyard-dusk'
    assert any('系统主屏幕' in str(note) for note in c._notes)


def test_static_portrait_disables_inherited_depth_and_can_restore_wallpaper(make_controller, qt, monkeypatch):
    c, adapter, _ = make_controller('backyard-dusk', with_saved_id=True, saved_depth=True)
    select(c, 'backyard-cat-tree-portrait')
    set_primary(monkeypatch, qt, portrait=True)
    click_apply(c)
    finish_apply(c, qt)
    assert c.settings['wallpaper_id'] == 'backyard-cat-tree-portrait'
    assert c._mask_data['occluders'] == []
    assert not c.settings['depth_enabled'] and not c._depth_context_ready()
    assert not c.journal.depth_check.isChecked() and not c.journal.depth_check.isEnabled()
    c.set_depth_enabled(True)
    assert not c.settings['depth_enabled']
    c.restore_wallpaper()
    finish_apply(c, qt)
    assert len(adapter.restore_calls) == 1 and adapter.current == '/fake/original.png'
    assert not c.settings['preset_wallpaper'] and not c._wallpaper_match


def test_saved_static_scene_never_reactivates_depth_or_rewrites_system(make_controller):
    c, adapter, _ = make_controller('backyard-cat-tree-portrait', with_saved_id=True, saved_depth=True)
    assert c.settings['wallpaper_id'] == 'backyard-cat-tree-portrait'
    assert not c.settings['depth_enabled'] and not c.journal.depth_check.isEnabled()
    assert not adapter.calls and not adapter.read_threads
