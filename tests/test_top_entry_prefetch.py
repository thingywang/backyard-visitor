import os,time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter,DesktopTarget,Rect

@pytest.fixture
def fixture(tmp_path,monkeypatch):
 q=QApplication.instance() or QApplication(['top-entry-prefetch'])
 c=Controller(q,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False)
 calls=[];monkeypatch.setattr(c,'_run_worker',lambda *args:calls.append(args))
 c._enable_native=True
 yield c,calls
 c.shutdown();q.processEvents()

def test_gate_open_prefetches_windows_without_visitors_but_never_icons(fixture):
 c,calls=fixture;c.settings.update(window_paths=True,icons_enabled=True)
 c.engine.open_gate();c.wake();assert not c.engine.visitors
 assert c.native_timer.isActive() and c.native_timer.interval()==2000
 assert len(calls)==1
 result=calls[0][1]();assert 'windows' in result and 'icons' not in result
 c.engine.open_gate(False);c.wake();assert not c.native_timer.isActive()
 calls.clear();c._native_poll();assert not calls

def test_closed_gate_or_icon_only_does_not_prefetch(fixture):
 c,calls=fixture;c.settings.update(window_paths=True,icons_enabled=True);c.wake();c._native_poll();assert not calls
 c.engine.open_gate();c.settings['window_paths']=False;c.wake();c._native_poll();assert not calls and not c.native_timer.isActive()

def test_rebuild_provides_actual_screen_top_not_menu_bar_bottom(fixture):
 c,calls=fixture;c.settings['window_paths']=True
 a=c.screen.availableGeometry();c._windows=[DesktopTarget('own','own',Rect(a.x(),a.y(),a.width(),a.height()))]
 c._windows_checked=time.monotonic();c._rebuild_paths()
 assert c.engine.screen_top==c.screen.geometry().top()
 assert c.engine.low_headroom_edges
