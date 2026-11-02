import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import pytest
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter,DesktopTarget,Rect


@pytest.fixture
def controller(tmp_path):
    qt=QApplication.instance() or QApplication(['climb-controller-tests'])
    adapter=DesktopAdapter();adapter.capabilities['windows']=True
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=adapter,enable_native=False,seed=3)
    yield c
    c.shutdown();qt.processEvents()


def start(c):
    bounds=c.screen.availableGeometry()
    # The offscreen test display is small; a tall enough route uses both its
    # real available geometry and the same window snapshot as production.
    x,y,w,h=bounds.x(),bounds.y(),bounds.width(),bounds.height()
    top=y+120.;left=x+max(80.,w*.25);right=min(x+w-80.,left+300.)
    c.settings['window_paths']=True
    c._windows=[DesktopTarget('own-test','test',Rect(left,top,right-left,h-125.))]
    c._windows_checked=time.monotonic();c._rebuild_paths()
    assert c.engine.window_climb_sides
    side=next(iter(c.engine.window_climb_sides.values()))
    c.engine.open_gate();c.engine.invite('amber');v=c.engine.visitors['amber']
    v.state='idle';v.x=v.target_x=side.canvas_x;v.y=v.target_y=c.engine.floor_y
    v.stay_remaining=v.next_action=v.parkour_cooldown=1000
    c.engine._visit_countdown=1000
    assert c.engine._move_to_window(v,side.edge_id)
    for _ in range(200):
        if v.state=='climbing':break
        c.engine.tick(.025)
    assert v.state=='climbing'
    return v


def test_sides_only_arrive_when_window_feature_and_snapshot_are_valid(controller):
    c=controller;start(c)
    c._windows_checked=-float('inf');c._rebuild_paths()
    assert not c.engine.window_climb_sides
    c._windows_checked=time.monotonic();c.settings['window_paths']=False;c._rebuild_paths()
    assert not c.engine.window_climb_sides


def test_side_climb_uses_active_window_polling_cadence(controller):
    c=controller;v=start(c)
    c.wake()
    assert c.current_fps==24 and c.native_timer.interval()==500


@pytest.mark.parametrize('interrupt',('disable','pause'))
def test_feature_off_or_pause_clears_side_support_without_teleport(controller,interrupt):
    c=controller;v=start(c);c.engine.tick(.1);before=v.x,v.y
    if interrupt=='disable':c.set_window_paths(False)
    else:c.set_paused(True)
    assert not c.engine.window_climb_sides and v.climb_side_id is None
    assert (v.x,v.y)==before
    assert v.state=='jumping' and v.target_y==c.engine.floor_y
    if interrupt=='pause':
        assert c.paused and c.current_fps==0
        c.set_paused(False)
    for _ in range(300):
        if v.state=='idle':break
        c.engine.tick(.025)
    assert v.state=='idle' and v.y==c.engine.floor_y
