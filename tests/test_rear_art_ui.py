"""The partial cat has real visible pixels, a matching tail hit area and bounded caches."""
import os
import math
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication
from backyard_visitor.rear_art import RearArt, source_frame
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter


@pytest.fixture
def qt():
    return QApplication.instance() or QApplication(['rear-art-test'])


def alpha(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return image.constBits().asstring(image.sizeInBytes())[3::4]


def test_small_rendition_matches_source_render_and_coats_keep_alpha(qt):
    art = RearArt(); original = art.frame('ginger')
    assert (original.width(), original.height(), original.devicePixelRatio()) == (288,336,2)
    expected = source_frame().toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    actual = original.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    assert expected == actual
    original_alpha = alpha(actual)
    for coat in ['gray','tuxedo']:
        assert alpha(art.frame(coat).toImage()) == original_alpha
        assert art.frame(coat) is art.frame(coat)
    assert set(art.frames) == {'ginger','gray','tuxedo'}


def test_resting_tail_mask_moves_with_painted_transform_and_leaves_blank_corners(qt):
    art = RearArt()
    first = art.input_region(0., 1.)
    later = art.input_region(2., 1.)
    assert first != later
    for region in [first,later]:
        assert not region.contains(QPoint(0,140))
        assert not region.contains(QPoint(143,140))
        assert any(region.contains(QPoint(x,140)) for x in range(144))


class Screen:
    def __init__(self,left,top): self.left,self.top=left,top
    def name(self): return "rear-art-test-screen"
    def geometry(self): return QRect(self.left,self.top,1280,720)
    def availableGeometry(self): return QRect(self.left,self.top+24,1280,696)
    def devicePixelRatio(self): return 1.


def displayed_local(c, window, visitor, x, y):
    dx,dy=c.to_display(round(visitor.x)+x,round(visitor.y)+y)
    return QPoint(round(dx-window.x()),round(dy-window.y()))


@pytest.mark.parametrize('origin',[(0,0),(-1280,-400)])
def test_real_cat_window_clips_upper_body_but_keeps_tail_visible_clickable(qt,tmp_path,origin):
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=1)
    try:
        assert c._rear_art is None
        c.screen=Screen(*origin);c._apply_screen_geometry()
        bounds=c.scene_rect()
        c.engine.open_gate();c.engine.invite('amber');v=c.engine.visitors['amber']
        top=bounds.y;c.engine.set_window_edges([('max:top:0',bounds.x,bounds.right,top)],low_headroom_ids={'max:top:0'})
        v.x=bounds.x+500;v.y=top-112;v.state='window_rear';v.window_edge_id='max:top:0';v.window_target_id=None
        v.route_edge_id=None;v.route_stage='';v.clip_y=None;v.rear_blend=1.
        c._sync();w=c.cat_windows['amber'];image=w.grab().toImage()
        scale=c.artwork_scale
        assert w.base_size.height()==168 and w.height()==math.ceil(168*scale)
        assert w.y()==round(c.to_display(round(v.x),round(v.y))[1])
        ratio=image.devicePixelRatio();start=round((origin[1]-w.y())*ratio)
        assert all(image.pixelColor(x,y).alpha()==0 for y in range(start) for x in range(image.width()))
        assert any(image.pixelColor(x,y).alpha()>100 for y in range(round(125*scale*ratio),round(155*scale*ratio)) for x in range(image.width()))
        assert not w.mask().contains(displayed_local(c,w,v,0,140))
        tail_y=displayed_local(c,w,v,72,140).y()
        assert any(w.mask().contains(QPoint(x,tail_y)) for x in range(w.width()))
        assert not w.mask().contains(displayed_local(c,w,v,72,20))
        # Returning to an ordinary floor pose removes the extended hit area.
        v.rear_blend=0;v.state='idle';v.window_edge_id=None;v.y=c.engine.floor_y
        c._sync();assert w.height()==math.ceil(112*scale) and not w.mask().contains(displayed_local(c,w,v,72,140))
        assert c._rear_art is None
        c.engine.visitors.clear();c._sync();assert c._rear_art is None
    finally:c.shutdown();qt.processEvents()


def test_native_clamp_fallback_translates_visible_tail_and_drag_anchor(qt,tmp_path):
    from PyQt6.QtCore import QPointF, QEvent, Qt
    from PyQt6.QtGui import QMouseEvent
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=1)
    try:
        c.screen=Screen(0,0);c._apply_screen_geometry();c.engine.open_gate();c.engine.invite('amber')
        v=c.engine.visitors['amber'];v.x=500;v.y=c.scene_rect().top-112;v.state='window_rear';v.rear_blend=1.;v.window_target_id=None;v.clip_y=None
        c._sync();w=c.cat_windows['amber']
        # Emulate Cocoa's placement constraint when the user turns topmost OFF.
        constrained=(round(c.to_display(round(v.x),round(v.y))[0]),c.screen.availableGeometry().top())
        w.move(*constrained);w.refresh_input_region();im=w.grab().toImage();ratio=im.devicePixelRatio()
        tail_y=displayed_local(c,w,v,72,140).y()
        assert any(w.mask().contains(QPoint(x,tail_y)) for x in range(w.width()))
        assert not any(w.mask().contains(QPoint(x,round(140*c.artwork_scale))) for x in range(w.width()))
        start=displayed_local(c,w,v,72,125).y();end=displayed_local(c,w,v,72,155).y()
        assert any(im.pixelColor(x,y).alpha()>100 for y in range(round(start*ratio),round(end*ratio)) for x in range(im.width()))
        global_pos=QPointF(*c.to_display(v.x+72,v.y+140));local=global_pos-QPointF(w.pos())
        event=QMouseEvent(QEvent.Type.MouseButtonPress,local,global_pos,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        w.mousePressEvent(event)
        assert (w.offset.x(),w.offset.y())==pytest.approx((72,140),abs=1/c.artwork_scale)
        w.hold.stop();w.pressed=False
        w.set_topmost(False)
        w.move(*constrained);w.refresh_input_region()
        w.set_topmost(True)
        assert w.y()==round(c.to_display(round(v.x),round(v.y))[1])
        mask=w.mask();w.refresh_input_region();assert w.mask()==mask
    finally:c.shutdown();qt.processEvents()


def test_upper_entry_waits_for_visible_pixels_before_first_show(qt,tmp_path):
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False)
    try:
        c.screen=Screen(0,0);c._apply_screen_geometry()
        c.engine.open_gate();c.engine.invite('amber');v=c.engine.visitors['amber']
        v.x=500;v.y=-170;v.state='top_entering';v.rear_blend=1.;v.clip_y=None
        c._sync();w=c.cat_windows['amber']
        assert not w.isVisible() and not w._has_visible_content
        v.y=c.scene_rect().top-112;c._sync()
        assert w.isVisible() and w._has_visible_content
    finally:c.shutdown();qt.processEvents()
