"""Resident-only placement, reversible drags, saved geometry and actual poses."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter
from backyard_visitor.art import CatArt

@pytest.fixture
def c(tmp_path):
    qt=QApplication.instance() or QApplication(["bed-ui-tests"])
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=5)
    c.engine.open_gate();c.engine.invite("amber")
    for _ in range(400):
        c.engine.tick(.05)
        if c.engine.visitors["amber"].state=="idle":break
    c.changed()
    yield c
    c.shutdown();qt.processEvents()


def resident(c):
    c.engine.cats["amber"].trust=100
    c.adopt("amber")
    c.place_bed("amber")


def mouse(w,kind,dx=0,dy=0):
    local=QPointF(12,20)
    return QMouseEvent(kind,local,QPointF(w.x()+12+dx,w.y()+20+dy),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton if kind==QMouseEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)


def test_bed_requires_acceptance_and_is_visible_in_roster(c):
    c.place_bed("amber")
    assert c.engine.bed is None and not c.bed_window.isVisible()
    c.journal.refresh();card=c.journal.cards[0]
    assert card.bed_button.isHidden()
    resident(c);card.refresh()
    assert not card.bed_button.isHidden() and card.bed_button.text()=="收起猫窝"
    assert c.bed_window.isVisible()
    assert not c.bed_window.mask().contains(c.bed_window.rect().topLeft())
    card.toggle_bed()
    assert c.engine.bed is None and not c.bed_window.isVisible()


def test_drag_preview_and_release_move_only_bed_horizontally(c):
    resident(c)
    w=c.bed_window;v=c.engine.visitors["amber"]
    before=(v.x,v.y);center=c.engine.bed.x;top=w.y()
    w.mousePressEvent(mouse(w,QMouseEvent.Type.MouseButtonPress))
    w.mouseMoveEvent(mouse(w,QMouseEvent.Type.MouseMove,dx=-80,dy=-200))
    assert c.engine.bed.x==center and w.y()==top
    w.mouseReleaseEvent(mouse(w,QMouseEvent.Type.MouseButtonRelease))
    assert c.engine.bed.x<center
    assert (v.x,v.y)==before
    assert not w.drag_active
    assert c._save()


@pytest.mark.parametrize("cancel",["pause","screen","shutdown"])
def test_interrupted_drag_does_not_commit_or_leave_overlay(c,cancel):
    resident(c)
    w=c.bed_window;center=c.engine.bed.x
    w.mousePressEvent(mouse(w,QMouseEvent.Type.MouseButtonPress))
    w.mouseMoveEvent(mouse(w,QMouseEvent.Type.MouseMove,dx=-80))
    if cancel=="pause":c.set_paused(True)
    elif cancel=="screen":c._suspend_display()
    else:c.shutdown()
    w.mouseReleaseEvent(mouse(w,QMouseEvent.Type.MouseButtonRelease))
    assert c.engine.bed.x==center
    if cancel!="shutdown":assert not w.drag_active
    if cancel in {"screen","shutdown"}:assert not w.isVisible()


def test_floor_bed_and_owner_survive_save_without_starting_visit(c):
    resident(c);c.move_bed(320);assert c._save()
    directory=c.state_dir;c.shutdown()
    other=Controller(c.qt,state_dir=directory,hidden=True,adapter=DesktopAdapter(),enable_native=False)
    try:
        assert other.engine.bed.cat_id=="amber"
        assert other.engine.bed.x==pytest.approx(320)
        assert other.bed_window.isVisible() and not other.engine.visitors
        assert not other.engine.gate_open
    finally:other.shutdown()


@pytest.mark.parametrize("reverse,forward,source,target",[
    ("crouch_lower","crouch_rise","standing","play_crouch"),
    ("belly_lie","belly_roll","play_crouch","sleeping"),
])
def test_entering_sleep_reuses_exact_reverse_frames(reverse,forward,source,target,c):
    art=CatArt();back=art.posture_state(reverse,source,target)
    front=art.posture_state(forward,target,source)
    assert back is not None and front is not None
    assert art.states[back]==list(reversed(art.states[front]))
    frame_count=len(art.frames)
    for _ in range(10):assert art.posture_state(reverse,source,target)==back
    assert len(art.frames)==frame_count
