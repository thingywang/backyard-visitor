"""Scaled input stays on its monitor; native level caches follow Qt flags."""
import math
from types import SimpleNamespace
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QRegion
from backyard_visitor.desktop import Rect
from backyard_visitor.display_scale import SceneTransform
from backyard_visitor.model import Engine, Visitor
from backyard_visitor.overlays import CatWindow, FightOverlay
from test_display_overlays import scene


def test_fight_native_baseline_is_invalidated_only_when_window_flags_change(scene):
    c, factor = scene
    w = c.make(FightOverlay, ("amber", "mist"))
    original = object()
    w._cat_level = original
    w.set_topmost(True)
    assert w._cat_level is original
    w.set_topmost(False)
    assert not hasattr(w, "_cat_level")
    normal = object()
    w._cat_level = normal
    w.set_topmost(False)
    assert w._cat_level is normal
    w.set_topmost(True)
    assert not hasattr(w, "_cat_level")


@pytest.mark.parametrize("factor", [.6, .75, .9, 1.1, 1.25, 1.75, 2.5])
def test_scaled_cat_input_never_crosses_selected_real_screen_edge(factor):
    qt = QApplication.instance() or QApplication([])
    # Seven scales and 25 edge/corner positions reproduce the 175-case audit.
    real = QRect(-1441, 37, 1441, 901)
    transform = SceneTransform(factor, -1441, 37)
    engine = Engine(seed=5)
    engine.visitors["amber"] = Visitor("amber", -1400, 80, state="idle")
    c = SimpleNamespace(engine=engine, artwork_scale=factor,
        settings={"overlay_topmost": True}, cat_clip=lambda *args: None,
        screen=SimpleNamespace(geometry=lambda: QRect(real)),
        to_display=transform.to_display, to_scene=transform.to_scene,
        scene_rect=lambda available=True: Rect(-1441, 37, 1441/factor, 901/factor))
    w = CatWindow(c, "amber")
    try:
        v = engine.visitors["amber"]
        bounds = c.scene_rect()
        points = [(x,y) for x in (bounds.left-143,bounds.left-20,bounds.left,bounds.right-40,bounds.right-1)
                  for y in (bounds.top-110,bounds.top-20,bounds.top,bounds.bottom-40,bounds.bottom-1)]
        for x,y in points:
            v.x,v.y = round(x),round(y)
            w.sync_native_position(v.x,v.y)
            w.refresh_input_region()
            if not w._has_visible_content:
                continue
            actual_screen = QRegion(real.translated(-w.x(),-w.y()))
            assert w.mask().subtracted(actual_screen).isEmpty()
            assert w.mask().subtracted(QRegion(w.rect())).isEmpty()
    finally:
        w.close()
        w.deleteLater()
