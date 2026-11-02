"""An authored coat must not be clipped by another cat's tail silhouette."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication
from backyard_visitor.rear_art import RearArt
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter


def authored_tail(art):
    frame = QPixmap(288, 336)
    frame.fill(Qt.GlobalColor.transparent)
    painter = QPainter(frame)
    painter.fillRect(200, 256, 60, 40, QColor(30, 31, 32))
    painter.end()
    frame.setDevicePixelRatio(2)
    art.frames['black'] = frame


def test_authored_coat_tail_keeps_its_own_visible_hit_region():
    qt = QApplication.instance() or QApplication(['black-tail-input'])
    art = RearArt()
    ginger = art.input_region(0., 1.)
    authored_tail(art)
    black = art.input_region(0., 1., 'black')
    assert black.contains(QPoint(115, 138))
    assert not ginger.contains(QPoint(115, 138))
    assert not black.contains(QPoint(72, 138))
    assert art.input_region(0., 1.) == ginger
    assert art.input_region(0., 1., 'gray') == ginger
    assert art.input_region(0., 1., 'black') == black


def test_cat_window_selects_black_tail_mask(tmp_path):
    qt = QApplication.instance() or QApplication(['black-tail-window'])
    controller = Controller(qt, state_dir=tmp_path, hidden=True,
                            adapter=DesktopAdapter(), enable_native=False, seed=1)
    try:
        controller.engine.open_gate()
        assert controller.engine.invite('onyx') == 'onyx'
        visitor = controller.engine.visitors['onyx']
        screen = controller.scene_rect(available=False)
        visitor.x = screen.x + 20
        visitor.y = screen.y + 40
        visitor.age = 0.
        visitor.state = 'window_rear'
        visitor.rear_blend = 1.
        visitor.clip_y = None
        visitor.window_target_id = None
        authored_tail(controller.rear_art)
        controller._sync()
        window = controller.cat_windows['onyx']
        def displayed_local(x, y):
            dx,dy=controller.to_display(round(visitor.x)+x,round(visitor.y)+y)
            return QPoint(round(dx-window.x()),round(dy-window.y()))
        assert window.mask().contains(displayed_local(115, 138))
        assert not window.mask().contains(displayed_local(72, 138))
    finally:
        controller.shutdown()
        qt.processEvents()
