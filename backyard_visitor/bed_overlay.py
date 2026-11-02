"""A small floor-supported bed; only its horizontal placement is draggable."""
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QMenu
from .overlays import Overlay, to_display, to_scene, scene_screen, queue_furniture_stack
from .resident_bed import BED_WIDTH, BED_HEIGHT
from .bed_art import BedArt


class CatBed(Overlay):
    def __init__(self, controller):
        super().__init__(controller, BED_WIDTH, BED_HEIGHT, "常住猫窝")
        self.drag_active = False
        self.setToolTip("拖动窝沿，沿桌面底部移动 · 右键收起猫窝")
        self.art = BedArt()
        self.set_scene_mask(self.art.input_region())

    def sync_geometry(self):
        bed = self.c.engine.bed
        if bed is None or not self.c._screen_ready or self.c._quitting:
            self.drag_active = False
            self.hide()
            return
        if not self.drag_active:
            x, y = to_display(self.c, bed.x-BED_WIDTH/2, self.c.engine.bed_y-BED_HEIGHT/2)
            self.move(round(x), round(y))
        if not self.isVisible():
            self.show()
            self.lower()  # Keep the mattress below this app's cat windows.

    def paintEvent(self, event):
        p = self.scene_painter()
        self.art.paint(p, self.scene_canvas_rect())
        p.end()

    def mousePressEvent(self, event):
        queue_furniture_stack(self.c)
        if event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            menu.addAction("收起猫窝", self.c.remove_bed)
            menu.exec(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton or self.c.paused:
            return
        self.drag_active = True
        self._offset_x = event.globalPosition().x()-self.x()

    def mouseMoveEvent(self, event):
        queue_furniture_stack(self.c)
        if not self.drag_active or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        bounds = scene_screen(self.c, available=True)
        display_x = event.globalPosition().x()-self._offset_x
        x, _ = to_scene(self.c, display_x, event.globalPosition().y())
        x = max(bounds.x(), min(bounds.x()+bounds.width()-BED_WIDTH, x))
        display_x, display_y = to_display(self.c, x, self.c.engine.bed_y-BED_HEIGHT/2)
        self.move(round(display_x), round(display_y))

    def mouseReleaseEvent(self, event):
        queue_furniture_stack(self.c)
        if event.button() != Qt.MouseButton.LeftButton or not self.drag_active:
            return
        self.drag_active = False
        x, _ = to_scene(self.c, self.x(), self.y())
        self.c.move_bed(x+BED_WIDTH/2)
