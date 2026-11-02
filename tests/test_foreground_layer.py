"""Front-layer cats never inherit wallpaper masks; window peeking stays separate."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import time
import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter

@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication(["foreground-tests"])
    (tmp_path / "state.json").write_text(json.dumps({"settings": {"preset_wallpaper": True}}))
    controller = Controller(qt, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=5)
    controller.engine.open_gate(); controller.engine.invite("amber")
    visitor = controller.engine.visitors["amber"]
    visitor.x, visitor.y = 200, 250
    visitor.state = "sleeping"
    controller._sync()
    yield controller
    controller.shutdown(); qt.processEvents()

def mask_entire_desktop(c):
    c._wallpaper_match = True
    c._wallpaper_checked = c._windows_checked = time.monotonic()
    c._mask_data = {"occluders": [{"polygon": [[0,0],[1,0],[1,1],[0,1]]}]}


def test_saved_wallpaper_does_not_cut_render_or_input(c):
    window = c.cat_windows["amber"]
    before = window.grab().toImage()
    mask_entire_desktop(c)
    c._sync()
    assert c.cat_clip(window.pos(), window.size()) is None
    assert window.mask().contains(QPoint(72,60))
    assert window.grab().toImage() == before


def test_wallpaper_alone_does_not_start_native_poll(c, monkeypatch):
    c._enable_native = True
    calls = []
    monkeypatch.setattr(c, "_run_worker", lambda *args: calls.append(args))
    c._native_poll()
    c.wake()
    assert not calls
    assert not c.native_timer.isActive()


def test_window_peek_clip_is_not_wallpaper_depth(c):
    mask_entire_desktop(c)
    visitor = c.engine.visitors["amber"]
    visitor.clip_y = visitor.y + 34
    c._sync()
    window = c.cat_windows["amber"]
    assert window.mask().contains(QPoint(72,20))
    assert not window.mask().contains(QPoint(72,60))
