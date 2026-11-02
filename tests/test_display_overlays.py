"""Real overlay drawing and gestures cross one explicit scene/display boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
from unittest.mock import Mock
import math
import pytest
from PyQt6.QtCore import QEvent,QPoint,QPointF,QRect,Qt
from PyQt6.QtGui import QMouseEvent,QPixmap,QColor
from PyQt6.QtWidgets import QApplication
from backyard_visitor.model import Engine,Visitor
from backyard_visitor.desktop import Rect,DesktopTarget
from backyard_visitor.art import CatArt,paint_gift
from backyard_visitor.rear_art import RearArt
from backyard_visitor.overlays import CatWindow,Bowl,GiftWindow,Gate,CleanupBin,ScratchMarks,FightOverlay,IconReplica
from backyard_visitor.bed_overlay import CatBed


@pytest.fixture(params=[.75,1.,1.5,2.])
def scene(request):
    qt=QApplication.instance() or QApplication([])
    factor=request.param; origin=(-1200.,40.); real=QRect(-1200,40,1200,800)
    e=Engine(seed=5); e.set_bounds(*origin,1200/factor,800/factor); e.open_gate()
    e.visitors['amber']=Visitor('amber',origin[0]+100,origin[1]+100,state='idle')
    e.cats['amber'].trust=100;e.cats['amber'].discovered=True
    e.cats['amber'].neighbor_status='neighbor'  # Settled actor; guest qualification is tested separately.
    c=SimpleNamespace(engine=e,artwork_scale=factor,screen=SimpleNamespace(geometry=lambda:QRect(real),availableGeometry=lambda:QRect(real)),
        to_display=lambda x,y:(origin[0]+(x-origin[0])*factor,origin[1]+(y-origin[1])*factor),
        to_scene=lambda x,y:(origin[0]+(x-origin[0])/factor,origin[1]+(y-origin[1])/factor),
        scene_rect=lambda available=True:Rect(*origin,1200/factor,800/factor),
        settings={'overlay_topmost':True},_screen_ready=True,_quitting=False,paused=False,art=CatArt(),rear_art=RearArt(),
        cat_clip=lambda *a:None,paint_gift=paint_gift,queue_furniture_stack=Mock(),feed=Mock(),place_bowl=Mock(),changed=Mock(),wake=Mock(),note=Mock(),
        begin_gift_drag=Mock(),end_gift_drag=Mock(),recycle_target=lambda:None,remove_bed=Mock(),move_bed=Mock(),_desktop_epoch=0)
    windows=[]
    def make(cls,*args):
        window=cls(c,*args);windows.append(window);return window
    c.make=make;c.bin=make(CleanupBin);c.bin.move(-10,-10)
    yield c,factor
    for w in reversed(windows):w.close();w.deleteLater()
    QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);qt.processEvents()


def pointer(window,kind,x,y):
    global_point=QPointF(x,y);local=global_point-QPointF(window.pos())
    move=kind==QEvent.Type.MouseMove;release=kind==QEvent.Type.MouseButtonRelease
    return QMouseEvent(kind,local,global_point,Qt.MouseButton.NoButton if move else Qt.MouseButton.LeftButton,
                       Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)


def test_every_local_art_canvas_scales_once_and_native_widget_geometry_stays_real(scene):
    c,s=scene;e=c.engine;e._drop('fur',-900,240,'amber');gift=e.gifts[-1]
    assert e.adopt('amber');assert e.place_bed('amber')
    windows=[(c.make(Gate),30,30),(c.make(Bowl),62,40),(c.make(GiftWindow,gift),38,38),
             (c.make(CatBed),172,44),(c.make(ScratchMarks),184,150),(c.bin,96,74),(c.make(CatWindow,'amber'),144,112)]
    for window,w,h in windows:
        assert window.width()==math.ceil(w*s) and window.height()==math.ceil(h*s)
        assert window.base_size.width()==w and window.base_size.height()==h
        assert window.scene_canvas_rect().width()==w
        if isinstance(window,CatWindow):window.sync_native_position(e.visitors['amber'].x,e.visitors['amber'].y);window.refresh_input_region()
        if isinstance(window,ScratchMarks):window.progress=.3
        image=window.grab().toImage()
        assert image.width()==window.width() and image.height()==window.height()
        assert any(image.pixelColor(x,y).alpha()>0 for x in range(image.width()) for y in range(image.height()))


def test_cat_visual_and_input_mask_share_scaled_anchor_and_cocoa_constraint_offset(scene):
    c,s=scene;v=c.engine.visitors['amber'];w=c.make(CatWindow,'amber');w.sync_native_position(v.x,v.y);w.refresh_input_region()
    expected=c.to_display(v.x,v.y);assert (w.x(),w.y())==tuple(round(x) for x in expected)
    normal=w.grab().toImage();visible=[]
    for y in range(normal.height()):
        for x in range(normal.width()):
            if normal.pixelColor(x,y).alpha()>0:visible.append((x,y))
    assert visible and all(w.mask().contains(QPoint(x,y)) for x,y in visible)
    # Simulate Cocoa moving the actual native window, without changing its scene anchor.
    w.move(w.x(),w.y()+round(10*s));w.refresh_input_region();shifted=w.grab().toImage()
    assert not w.mask().contains(QPoint(2,w.height()-2))
    assert any(shifted.pixelColor(x,y).alpha()>0 for x,y in visible if y<shifted.height()-round(10*s))


def test_cat_drag_inverse_maps_screen_motion_without_scaling_model_twice(scene):
    c,s=scene;v=c.engine.visitors['amber'];w=c.make(CatWindow,'amber');w.sync_native_position(v.x,v.y)
    before=v.x,v.y;x=w.x()+40*s;y=w.y()+30*s
    w.mousePressEvent(pointer(w,QEvent.Type.MouseButtonPress,x,y));w.begin_carry();assert w.carrying
    w.mouseMoveEvent(pointer(w,QEvent.Type.MouseMove,x+60*s,y+25*s))
    assert (v.x,v.y)==pytest.approx((before[0]+60,before[1]+25),abs=1/s)
    assert (w.x(),w.y())==pytest.approx(c.to_display(v.x,v.y),abs=1)


def test_bowl_and_bed_drag_write_scene_centers_and_request_layer_repair(scene):
    c,s=scene;w=c.make(Bowl);dx,dy=c.to_display(-950,300);w.move(round(dx),round(dy));x=w.x()+31*s;y=w.y()+20*s
    w.mousePressEvent(pointer(w,QEvent.Type.MouseButtonPress,x,y));w.mouseMoveEvent(pointer(w,QEvent.Type.MouseMove,x+40*s,y+15*s));w.mouseReleaseEvent(pointer(w,QEvent.Type.MouseButtonRelease,x+40*s,y+15*s))
    assert c.engine.bowl==pytest.approx((-950+40+31,300+15+20),abs=1/s)
    assert c.queue_furniture_stack.call_count>=3
    assert c.engine.adopt('amber');assert c.engine.place_bed('amber',-950)
    bed=c.make(CatBed);bed.sync_geometry()
    assert bed.mask().boundingRect().width()<=bed.width()
    x=bed.x()+86*s;y=bed.y()+22*s
    bed.mousePressEvent(pointer(bed,QEvent.Type.MouseButtonPress,x,y));bed.mouseMoveEvent(pointer(bed,QEvent.Type.MouseMove,x+30*s,y));bed.mouseReleaseEvent(pointer(bed,QEvent.Type.MouseButtonRelease,x+30*s,y))
    assert c.move_bed.call_args.args[0]==pytest.approx(-920,abs=1/s)


def test_gift_drag_preserves_scene_position_and_native_bin_uses_display_point(scene):
    c,s=scene;e=c.engine;e._drop('feather',-950,300,'amber');g=e.gifts[-1];w=c.make(GiftWindow,g)
    x=w.x()+19*s;y=w.y()+19*s
    w.mousePressEvent(pointer(w,QEvent.Type.MouseButtonPress,x,y));w.mouseMoveEvent(pointer(w,QEvent.Type.MouseMove,x+40*s,y+15*s));w.mouseReleaseEvent(pointer(w,QEvent.Type.MouseButtonRelease,x+40*s,y+15*s))
    assert (g.x,g.y)==pytest.approx((-910,315),abs=1/s)
    native=Rect(-1000,500,100,100);c.recycle_target=lambda:native
    x=w.x()+19*s;y=w.y()+19*s
    w.mousePressEvent(pointer(w,QEvent.Type.MouseButtonPress,x,y));w.mouseMoveEvent(pointer(w,QEvent.Type.MouseMove,-950,550));w.mouseReleaseEvent(pointer(w,QEvent.Type.MouseButtonRelease,-950,550))
    assert g not in e.gifts


def test_shared_fight_canvas_mask_and_cat_relative_geometry_scale_together(scene):
    c,s=scene;e=c.engine;e.visitors['mist']=Visitor('mist',-900,140,state='fighting');e.visitors['amber'].state='fighting'
    e.visitors['amber'].x=-1050;e.visitors['amber'].y=140
    w=c.make(FightOverlay,('amber','mist'));c.fight_overlay=w
    assert w.sync_geometry();expected=c.to_display(-1068,112)
    assert (w.x(),w.y())==tuple(round(x) for x in expected)
    assert w.width()==math.ceil((144+150+36)*s) and w.height()==math.ceil(140*s)
    assert not w.mask().contains(QPoint(0,0))
    before=w.mask().boundingRect();c.artwork_scale=s*.8;w.rescale()
    assert w.mask().boundingRect().width()<before.width()


def test_icon_replica_keeps_native_icon_extent_instead_of_rescaling_source(scene):
    c,s=scene;target=DesktopTarget('icon','own',Rect(-950,200,48,52));pix=QPixmap(48,52);pix.fill(QColor('red'))
    window=c.make(IconReplica,target,pix)
    assert window.size().width()==48 and window.size().height()==52
    c.artwork_scale=s*1.25;window.rescale()
    assert window.size().width()==48 and window.size().height()==52
