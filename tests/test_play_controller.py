"""Controller lifecycle boundaries must always release the deliberate play canvas."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from unittest.mock import patch
import pytest
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QMenu
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter

@pytest.fixture
def c(tmp_path):
    qt=QApplication.instance() or QApplication([])
    controller=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=7)
    e=controller.engine
    e.open_gate();e.invite('amber')
    v=e.visitors['amber'];v.x=250.;v.y=e.floor_y
    e._state(v,'idle');v.next_action=100;v.stay_remaining=100
    v.carrying_gift=None;v.demo_gift_pending=None
    e.cats['amber'].trust=70
    controller.changed()
    yield controller
    controller.shutdown();qt.processEvents()

def test_start_uses_local_wand_and_bounded_session(c):
    assert c.start_play('amber')
    s=c._teaser_session
    assert s.active and s.timer.isActive()
    assert c.engine.active_teaser_id=='amber'
    assert s._canvas.isVisible() and s._canvas.cursor().shape()==Qt.CursorShape.BitmapCursor
    assert QApplication.overrideCursor() is None
    assert not c.start_play('amber')
    # Advance only the injected session clocks; never wait 15 real seconds in unit tests.
    s._started-=15.01;s._wall_started-=15.01
    s._pulse()
    assert c._teaser_session is None and s._canvas is None
    assert not s.timer.isActive() and c.engine.active_teaser_id is None
    assert QApplication.overrideCursor() is None
    v=c.engine.visitors['amber']
    assert v.state=='resting' or v.arrival_state=='resting'

@pytest.mark.parametrize('boundary',['pause','close','toggle','screen','journal','topmost','quit'])
def test_context_boundary_stops_play_without_residual_canvas(c,boundary):
    assert c.start_play('amber');s=c._teaser_session;canvas=s._canvas
    if boundary=='pause':c.set_paused(True)
    elif boundary=='close':c.close_gate()
    elif boundary=='toggle':c.toggle_gate()
    elif boundary=='screen':c._invalidate_desktop_context()
    elif boundary=='journal':c.show_journal()
    elif boundary=='topmost':c.set_overlay_topmost(False)
    else:c.shutdown()
    assert c._teaser_session is None and c.engine.active_teaser_id is None
    assert not canvas.isVisible() and not s.timer.isActive() and s._canvas is None
    assert QApplication.overrideCursor() is None
    c.stop_play('again')

def test_ineligible_cat_never_installs_canvas(c):
    c.engine._state(c.engine.visitors['amber'],'carried')
    assert not c.start_play('amber')
    assert c._teaser_session is None and c.engine.active_teaser_id is None

def test_right_cat_menu_starts_play(c):
    observed=[]
    def choose_play(menu,pos):
        options=[a for a in menu.actions() if a.text()=='和它玩 · 15 秒']
        assert len(options)==1 and options[0].isEnabled()
        observed.extend(options);options[0].trigger()
    with patch.object(QMenu,'exec',choose_play):
        c.cat_windows['amber'].context_menu(QPoint(300,300))
    assert observed and c._teaser_session is not None

def test_scratch_is_click_through_fades_and_is_removed(c):
    v=c.engine.visitors['amber'];c.engine._state(v,'sleeping')
    with patch.object(c.engine.rng,'random',return_value=0.):
        c.pet('amber')
    assert v.state=='swatting' and len(c._scratches)==1
    scratch=c._scratches[0]
    assert scratch.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert scratch.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    scratch.progress=.25
    image=scratch.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    assert any(image.pixelColor(x,y).alpha()>0 for y in range(20,140,4) for x in range(40,180,4))
    scratch._started-=2;scratch.step()
    assert c._scratches==[] and not scratch.timer.isActive() and not scratch.isVisible()

@pytest.mark.parametrize('boundary',['screen','quit'])
def test_scratch_cleanup_at_context_boundary(c,boundary):
    c._show_scratch('amber');scratch=c._scratches[0]
    if boundary=='screen':c._invalidate_desktop_context()
    else:c.shutdown()
    assert c._scratches==[] and not scratch.timer.isActive()

def test_full_trust_belly_touch_never_creates_scratch(c):
    v=c.engine.visitors['amber'];c.engine._state(v,'sleeping');c.engine.cats['amber'].trust=100
    with patch.object(c.engine.rng,'random',return_value=0.):c.pet('amber')
    assert v.state=='purring' and c._scratches==[]


def test_teasing_redraws_on_phase_and_run_frame_without_window_motion(c):
    assert c.start_play('amber')
    v=c.engine.visitors['amber'];v.rise_remaining=v.landing_remaining=v.settle_remaining=v.turn_remaining=0
    c._sync()
    w=c.cat_windows['amber']
    with patch.object(w,'update') as update:
        c.engine._teaser.phase='chase';c._sync()
        assert update.call_count==1
        v.run_phase=.25;c._sync()
        assert update.call_count==2


def test_rest_posture_redraws_until_final_static_endpoint(c):
    v=c.engine.visitors['amber'];c.engine._state(v,'resting')
    c.engine._begin_posture(v,'belly_roll',.55,'swatting','resting')
    c._sync();w=c.cat_windows['amber']
    with patch.object(w,'update') as update:
        v.posture_remaining=.2;c._sync()
        assert update.call_count==1
        c.engine._clear_posture(v);c._sync()
        assert update.call_count==2
        c._sync()
        assert update.call_count==2  # Settled rest no longer redraws continuously.
