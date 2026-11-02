"""First-use bowl placement must never overwrite a user's saved layout."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter
from test_app_lifecycle import Desktop, Screen


def open_controller(desktop, folder, **kwargs):
    return Controller(desktop, state_dir=folder, hidden=True,
                      adapter=DesktopAdapter(), enable_native=False, seed=7, **kwargs)


@pytest.fixture
def desktop():
    qt = QApplication.instance() or QApplication(["bowl-first-use-tests"])
    return Desktop(qt)


@pytest.mark.parametrize("screen", [Screen("wide", 0, 0, 1440, 900),
                                   Screen("portrait", -1080, 100, 1080, 1920)])
def test_new_save_places_whole_bowl_inside_lower_right(desktop, tmp_path, screen):
    desktop.display_list = [screen]
    desktop.primary = screen
    c = open_controller(desktop, tmp_path)
    try:
        available, bowl = screen.availableGeometry(), c.bowl.geometry()
        assert available.contains(bowl)
        assert 16 <= available.right() - bowl.right() <= 32
        assert 12 <= available.bottom() - bowl.bottom() <= 24
        assert c.engine.bed is None
        assert c.save_timer.isActive()  # New placement is saved even with a closed gate.
        c._save()
        before = c.engine.bowl
        c.shutdown()
        c = open_controller(desktop, tmp_path)
        assert c.engine.bowl == before
    finally:
        c.shutdown()
        desktop.qt.processEvents()


def test_dragged_bowl_and_bed_survive_restart_and_geometry_refresh(desktop, tmp_path):
    c = open_controller(desktop, tmp_path)
    try:
        w = c.bowl
        local = QPointF(15, 10)
        press = QPointF(w.x()+15, w.y()+10)
        target = press + QPointF(-260, -105)
        def mouse(kind, point):
            move = kind == QMouseEvent.Type.MouseMove
            release = kind == QMouseEvent.Type.MouseButtonRelease
            return QMouseEvent(kind, local, point,
                Qt.MouseButton.NoButton if move else Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier)
        w.mousePressEvent(mouse(QMouseEvent.Type.MouseButtonPress, press))
        w.mouseMoveEvent(mouse(QMouseEvent.Type.MouseMove, target))
        w.mouseReleaseEvent(mouse(QMouseEvent.Type.MouseButtonRelease, target))
        cat = c.engine.cats["amber"]
        cat.trust = 100
        cat.discovered = True
        cat.neighbor_status = "neighbor"  # This fixture has an established resident.
        c.adopt("amber")
        c.place_bed("amber")
        c.move_bed(300)
        bowl, bed = c.engine.bowl, c.engine.bed.x
        assert c._save()
        c.shutdown()
        c = open_controller(desktop, tmp_path)
        assert c.engine.bowl == bowl
        assert c.engine.bed.x == bed
        screen = desktop.primary
        screen.available.setWidth(screen.available.width()+100)
        screen.full.setWidth(screen.full.width()+100)
        c._geometry_changed(screen)
        assert c.engine.bowl == bowl
        assert c.engine.bed.x == bed
    finally:
        c.shutdown()
        desktop.qt.processEvents()


def test_demo_retains_its_existing_default_placement(desktop, tmp_path):
    c = open_controller(desktop, tmp_path, demo=True)
    try:
        available = desktop.primary.availableGeometry()
        # Demo keeps the old 640-point horizontal anchor, with only the scaled
        # bottom clearance applied to fit this smaller display. It must not
        # receive the new-save lower-right bowl placement.
        native_bowl = c.to_display(*c.engine.bowl)
        assert native_bowl == pytest.approx((640, available.y() + available.height() - 24 * c.artwork_scale))
        assert native_bowl[0] < available.x() + available.width() - 100
        assert available.contains(c.bowl.geometry())
        assert not c.state_path.exists()
    finally:
        c.shutdown()
        desktop.qt.processEvents()
