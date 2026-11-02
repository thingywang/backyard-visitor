"""Local gesture eligibility cannot survive pause or clock-discontinuity boundaries.

Offscreen only. Fixture states and simulated clock offsets do not claim OS sleep.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time

import pytest
from PyQt6.QtCore import QEvent,QPointF,Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter


@pytest.fixture
def c(tmp_path):
    qt=QApplication.instance() or QApplication(['gesture-recovery'])
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=31)
    c.engine.open_gate();c.engine.invite('amber')
    v=c.engine.visitors['amber'];v.x=v.target_x=220.;v.y=v.target_y=c.engine.floor_y
    c.engine._state(v,'idle');v.next_action=100;v.stay_remaining=100
    v.carrying_gift=None;v.demo_gift_pending=None;c.engine.cats['amber'].trust=60
    c.changed()
    yield c
    c.shutdown();qt.processEvents()


def send(w,kind,dx=0,dy=0):
    local=QPointF(w.width()/2,w.height()/2)
    point=QPointF(w.x()+local.x()+dx,w.y()+local.y()+dy)
    move=kind==QEvent.Type.MouseMove;release=kind==QEvent.Type.MouseButtonRelease
    event=QMouseEvent(kind,local,point,Qt.MouseButton.NoButton if move else Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w,event)


def prepare(c,boundary):
    if boundary=='resume':c.set_paused(True)


def boundary(c,kind):
    before=c.engine._time
    if kind=='pause':c.set_paused(True)
    elif kind=='resume':c.set_paused(False)
    else:
        c._last_tick-=60;c._last_wall_tick-=60;c._tick()
    assert c.engine._time==before  # Cancel/sync is not a simulation tick.


@pytest.mark.parametrize('kind',['pause','resume','gap'])
def test_pending_cat_hold_is_stopped_and_old_timeout_cannot_carry(c,kind):
    prepare(c,kind);w=c.cat_windows['amber'];v=w.visitor()
    send(w,QEvent.Type.MouseButtonPress)
    assert w.hold.isActive() and w.pressed
    boundary(c,kind)
    assert not w.hold.isActive() and not w.pressed
    w.begin_carry()  # An already queued callback also loses its eligibility.
    assert v.state!='carried' and not w.carrying
    assert w.cursor().shape()!=Qt.CursorShape.ClosedHandCursor


@pytest.mark.parametrize('kind',['pause','resume','gap'])
def test_carried_cat_releases_pointer_and_refreshes_pose_without_advancing(c,kind):
    prepare(c,kind);w=c.cat_windows['amber'];v=w.visitor()
    send(w,QEvent.Type.MouseButtonPress);w.begin_carry();c._sync()
    assert v.state=='carried' and c._cat_paint_keys['amber'][0]=='carried'
    position=(v.x,v.y)
    boundary(c,kind)
    assert not w.carrying and not w.pressed and v.state!='carried'
    assert (v.x,v.y)==position
    assert (w.x(),w.y())==pytest.approx(c.to_display(v.x,v.y),abs=1)
    assert c._cat_paint_keys['amber'][0]==v.state
    assert w.cursor().shape()!=Qt.CursorShape.ClosedHandCursor
    send(w,QEvent.Type.MouseMove,200,-100);send(w,QEvent.Type.MouseButtonRelease)
    assert (v.x,v.y)==position


@pytest.mark.parametrize('kind',['pause','resume','gap'])
def test_uncommitted_bowl_drag_rolls_back_and_old_release_cannot_feed(c,kind):
    prepare(c,kind);w=c.bowl;original=c.engine.bowl
    send(w,QEvent.Type.MouseButtonPress);send(w,QEvent.Type.MouseMove,90,-40)
    assert w.drag_active and w.dragged
    boundary(c,kind)
    assert not w.drag_active and c.engine.bowl==original
    # Model anchors are scene coordinates; native widget geometry is display.
    assert (w.x(),w.y())==pytest.approx(c.to_display(original[0]-31,original[1]-20),abs=1)
    send(w,QEvent.Type.MouseMove,200,-100);send(w,QEvent.Type.MouseButtonRelease)
    assert c.engine.bowl==original and c.engine.food==0 and not w.feed_click.isActive()


@pytest.mark.parametrize('kind',['pause','resume','gap'])
def test_pending_bowl_single_click_timer_does_not_survive_boundary(c,kind):
    prepare(c,kind);w=c.bowl
    send(w,QEvent.Type.MouseButtonPress);send(w,QEvent.Type.MouseButtonRelease)
    assert w.feed_click.isActive()
    boundary(c,kind)
    assert not w.feed_click.isActive() and c.engine.food==0


@pytest.mark.parametrize('kind',['pause','resume','gap'])
def test_held_mouse_and_uncommitted_gift_drag_restore_model_position(c,kind):
    c.engine._drop('toy_mouse',300,300,'amber');c.changed()
    gift=c.engine.gifts[-1];w=c.gift_windows[gift.id];original=(gift.x,gift.y)
    prepare(c,kind)
    send(w,QEvent.Type.MouseButtonPress);send(w,QEvent.Type.MouseMove,100,-50)
    assert gift.state=='held' and w.drag_active and c.dragging_gift and c.bin.isVisible()
    boundary(c,kind)
    assert gift.state=='idle' and 8<=gift.wait_remaining<=22
    assert not w.drag_active and not c.dragging_gift and not c.bin.isVisible()
    assert (gift.x,gift.y)==original
    assert (w.x(),w.y())==pytest.approx(c.to_display(gift.x,gift.y),abs=1)
    send(w,QEvent.Type.MouseMove,300,100);send(w,QEvent.Type.MouseButtonRelease)
    assert gift in c.engine.gifts and (gift.x,gift.y)==original


@pytest.mark.parametrize('kind',['pause','resume','gap'])
def test_gate_position_is_preserved_but_old_move_release_is_disarmed(c,kind):
    prepare(c,kind);w=c.gate
    send(w,QEvent.Type.MouseButtonPress);send(w,QEvent.Type.MouseMove,70,-20);position=w.pos()
    boundary(c,kind)
    assert not w.drag_active and w.pos()==position
    send(w,QEvent.Type.MouseMove,200,100);send(w,QEvent.Type.MouseButtonRelease)
    assert w.pos()==position and c.engine.gate_open


@pytest.mark.parametrize('paused',[False,True])
def test_repeating_same_pause_value_does_not_cancel_new_manual_gesture(c,paused):
    c.set_paused(paused);w=c.cat_windows['amber'];send(w,QEvent.Type.MouseButtonPress)
    epoch=c._desktop_epoch;c.set_paused(paused)
    assert c._desktop_epoch==epoch
    assert w.hold.isActive() and w.pressed


def test_normal_tick_does_not_cancel_an_active_carry(c):
    w=c.cat_windows['amber'];send(w,QEvent.Type.MouseButtonPress);w.begin_carry()
    c._last_tick=time.monotonic()-.042;c._last_wall_tick=time.time()-.042
    c._tick()
    assert w.pressed and w.carrying and w.visitor().state=='carried'
    assert w.cursor().shape()==Qt.CursorShape.ClosedHandCursor
