"""Production depth opt-in, native identity guards, rendering and interruption."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json,time
from pathlib import Path
import pytest
from PyQt6.QtCore import QRect,QPoint
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter,DesktopTarget,Rect

class Screen:
    def geometry(self):return QRect(0,0,1440,900)
    def availableGeometry(self):return QRect(0,24,1440,876)
    def devicePixelRatio(self):return 1.
    def name(self):return 'test-depth-screen'

@pytest.fixture
def c(tmp_path,monkeypatch):
    qt=QApplication.instance() or QApplication([])
    c=Controller(qt,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=5)
    c.timer.stop();c.native_timer.stop();c.screen=Screen();c._apply_screen_geometry()
    monkeypatch.setattr(qt,'primaryScreen',lambda:c.screen)
    c._enable_native=True
    monkeypatch.setattr(c,'_run_worker',lambda *args:False)
    c.engine.open_gate();c.engine.invite('amber');v=c.engine.visitors['amber']
    v.state='idle';v.x=720;v.y=c.engine.floor_y;v.facing=1;v.next_action=500;v.stay_remaining=500
    c.engine.cats['amber'].trust=100
    c.settings.update(preset_wallpaper=True,wallpaper_id='backyard-morning',depth_enabled=True)
    c._mask_data=c._read_mask();refresh(c);c._sync()
    yield c
    c.shutdown();qt.processEvents()

def refresh(c):
    c._depth_snapshot={'path':str(c._wallpaper_path()),'options':{'scaling':1,'clipping':False},'primary_size':[1440,900]}
    c._windows_checked=c._wallpaper_checked=time.monotonic()

def run_to(c,predicate,limit=50):
    for _ in range(int(limit/.025)):
        refresh(c);c.engine.tick(.025);c._sync()
        if predicate():return
    pytest.fail('Depth journey condition not reached')

def test_complete_journey_is_explicit_scaled_and_only_rear_is_masked(c):
    v=c.engine.visitors['amber'];start=(v.x,v.y)
    assert c.send_to_backyard('amber') and (v.x,v.y)==pytest.approx(start,abs=1e-9)
    window=c.cat_windows['amber'];assert c.cat_clip(window.scene_position(),window.size(),'amber') is None
    run_to(c,lambda:v.depth_layer=='rear' and v.depth_pose['phase']=='rear_traverse')
    assert .63 < v.depth_scale < .65
    clip=c.cat_clip(window.scene_position(),window.size(),'amber');assert clip is not None
    assert not window.mask().contains(QPoint(5,5))
    run_to(c,lambda:v.depth_trip is None)
    assert (v.x,v.y)==pytest.approx(start) and v.depth_scale==1 and v.depth_layer=='front'
    assert c.cat_clip(window.scene_position(),window.size(),'amber') is None

@pytest.mark.parametrize('failure',['unmatched','fit','clipping','stale','secondary','disabled'])
def test_unverified_context_cannot_enter_or_mask(c,monkeypatch,failure):
    if failure=='unmatched':c._depth_snapshot['path']='/unmatched.png'
    elif failure=='fit':c._depth_snapshot['options']['scaling']=3
    elif failure=='clipping':c._depth_snapshot['options']['clipping']=True
    elif failure=='stale':c._wallpaper_checked-=10
    elif failure=='secondary':monkeypatch.setattr(c.qt,'primaryScreen',lambda:object())
    else:c.settings['depth_enabled']=False
    assert not c.send_to_backyard('amber')
    assert c.engine.visitors['amber'].depth_trip is None

def test_new_cover_requests_continuous_return_and_disables_mask_for_whole_trip(c):
    assert c.send_to_backyard('amber');v=c.engine.visitors['amber']
    run_to(c,lambda:v.depth_layer=='rear')
    before=(v.x,v.y,v.depth_scale)
    c._windows=[DesktopTarget('cover','fixture',Rect(950,650,450,250))];c._sync()
    assert (v.x,v.y,v.depth_scale)==before
    assert v.depth_pose['return_reason']=='desktop_obscured'
    w=c.cat_windows['amber'];assert c.cat_clip(w.scene_position(),w.size(),'amber') is None
    c._windows=[];refresh(c);c._sync()
    assert c.cat_clip(w.scene_position(),w.size(),'amber') is None
    run_to(c,lambda:v.depth_trip is None)

def test_pause_holds_current_foot_then_resumes_safe_return(c):
    assert c.send_to_backyard('amber');v=c.engine.visitors['amber'];run_to(c,lambda:v.depth_pose['phase']=='recede')
    before=(v.x,v.y,v.depth_scale,v.gait_phase)
    c.set_paused(True);c._tick()
    assert (v.x,v.y,v.depth_scale,v.gait_phase)==before and not c.timer.isActive()
    c.set_paused(False);refresh(c);run_to(c,lambda:v.depth_trip is None)

def test_gate_close_returns_first_then_exits(c):
    assert c.send_to_backyard('amber');v=c.engine.visitors['amber'];run_to(c,lambda:v.depth_layer=='rear')
    c.close_gate();assert v.depth_trip is not None
    run_to(c,lambda:'amber' not in c.engine.visitors,limit=80)

def test_handbook_button_hides_own_window_and_starts_trip(c):
    book=c.journal;book.show();book.show_page(1);c.qt.processEvents();book.refresh()
    card=book.cards[0];assert card.depth_button.isVisible()
    card.depth_button.click()
    assert c.engine.visitors['amber'].depth_trip is not None and not book.isVisible()

def test_option_persists_but_trip_does_not(c):
    assert c.send_to_backyard('amber');assert c._save()
    state=json.loads(c.state_path.read_text())
    assert state['settings']['depth_enabled'] is True
    assert 'depth_trip' not in json.dumps(state) and 'depth_visiting' not in json.dumps(state)

def test_depth_poll_queries_windows_and_full_wallpaper_state_without_window_paths(c,monkeypatch):
    seen=[];c.settings['window_paths']=False
    monkeypatch.setattr(c.adapter,'windows',lambda:seen.append('windows') or [])
    monkeypatch.setattr(c.adapter,'current_wallpaper_snapshot',lambda:seen.append('snapshot') or c._depth_snapshot)
    operations=[];monkeypatch.setattr(c,'_run_worker',lambda key,op,callback:operations.append((op,callback)))
    c._native_poll();assert len(operations)==1
    op,callback=operations[0];result=op();callback(result)
    assert seen==['windows','snapshot'] and c._depth_context_ready()


def test_rear_mask_reduces_actual_painted_alpha_and_foreground_stays_whole(c):
    from PyQt6.QtGui import QImage
    assert c.send_to_backyard('amber');v=c.engine.visitors['amber']
    run_to(c,lambda:v.depth_pose['phase']=='hold')
    w=c.cat_windows['amber']
    def pixels():
        image=w.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        return sum(alpha>0 for alpha in image.bits().asstring(image.sizeInBytes())[3::4])
    clipped=pixels();c._depth_mask_suspended.add('amber');whole=pixels()
    assert 0<=clipped<whole and whole>100
    assert c.cat_clip(w.scene_position(),w.size()) is None


def test_native_worker_failure_immediately_unmasks_and_returns_without_teleport(c):
    assert c.send_to_backyard('amber')
    v=c.engine.visitors['amber']
    run_to(c,lambda:v.depth_layer=='rear' and v.depth_pose['phase']=='rear_traverse')
    window=c.cat_windows['amber'];before=(v.x,v.y,v.depth_scale)
    assert c.cat_clip(window.scene_position(),window.size(),'amber') is not None
    c._reset_clock()
    c._workers['native-poll']=(object(),c._native_result,c._desktop_epoch)
    c._worker_finished('native-poll',None,RuntimeError('isolated worker failure'))
    assert not c._depth_context_ready() and c._depth_snapshot is None
    assert (v.x,v.y,v.depth_scale)==before
    assert v.depth_pose['return_reason']=='desktop_obscured'
    assert c.cat_clip(window.scene_position(),window.size(),'amber') is None
    refresh(c);c._sync()
    assert c.cat_clip(window.scene_position(),window.size(),'amber') is None
    run_to(c,lambda:v.depth_trip is None)


def test_depth_uses_existing_active_cadence_and_restores_idle_rate(c):
    assert c.send_to_backyard('amber')
    c.wake(reset_clock=False)
    assert c.current_fps==24 and c.timer.interval()==42
    run_to(c,lambda:c.engine.visitors['amber'].depth_trip is None)
    c.wake(reset_clock=False)
    assert c.current_fps==2 and c.timer.interval()==500


def test_startled_depth_cat_paints_running_immediately_with_distance_phase(c,monkeypatch):
    assert c.send_to_backyard('amber');v=c.engine.visitors['amber']
    run_to(c,lambda:v.depth_pose['phase']=='rear_traverse')
    c.engine.cats['amber'].trust=10
    before=(v.x,v.y,v.depth_scale,v.depth_layer)
    assert c.engine.startle('amber')
    assert (v.x,v.y,v.depth_scale,v.depth_layer)==before
    assert v.depth_pose['pose'] in {'running','fleeing'}
    draws=[];paint=c.art.paint
    def observed(*args,**kwargs):
        draws.append((args[2],kwargs.get('run_phase')))
        return paint(*args,**kwargs)
    monkeypatch.setattr(c.art,'paint',observed)
    c.cat_windows['amber'].grab()
    assert draws and draws[-1][0] in {'running','fleeing'}
    assert draws[-1][1]==pytest.approx(v.depth_pose['run_phase'])
    phase=draws[-1][1]
    c.engine.tick(.1);refresh(c);c._sync();c.cat_windows['amber'].grab()
    assert draws[-1][1]!=phase
    c.engine.request_depth_return('amber','called')
    assert v.depth_pose['pose'] in {'running','fleeing'}
    run_to(c,lambda:'amber' not in c.engine.visitors,limit=8)
    assert 'amber' not in c.cat_windows


def test_depth_escape_pause_context_failure_and_gate_close_do_not_restart_return(c):
    assert c.send_to_backyard('amber');v=c.engine.visitors['amber']
    run_to(c,lambda:v.depth_pose['phase']=='recede')
    c.engine.cats['amber'].trust=10;assert c.engine.startle('amber')
    before=(v.x,v.y,v.depth_scale)
    c.set_paused(True);c._tick();assert (v.x,v.y,v.depth_scale)==before
    c.set_paused(False);c.close_gate();c._depth_snapshot=None;c._sync()
    assert v.depth_pose['pose'] in {'running','fleeing'}
    window=c.cat_windows['amber']
    assert c.cat_clip(window.scene_position(),window.size(),'amber') is None
    run_to(c,lambda:'amber' not in c.engine.visitors,limit=8)


def test_new_window_on_escape_extension_unmasks_without_reversing_the_run(c):
    assert c.send_to_backyard('amber');v=c.engine.visitors['amber']
    run_to(c,lambda:v.depth_pose['phase']=='rear_traverse')
    c.engine.cats['amber'].trust=10;assert c.engine.startle('amber')
    window=c.cat_windows['amber'];before=(v.x,v.y,v.depth_scale,v.facing)
    assert c.cat_clip(window.scene_position(),window.size(),'amber') is not None
    # Beyond the old round-trip sweep, but inside the new run-to-edge corridor.
    c._windows=[DesktopTarget('edge-cover','fixture',Rect(1400,700,40,150))]
    c._sync()
    assert (v.x,v.y,v.depth_scale,v.facing)==before
    assert c.cat_clip(window.scene_position(),window.size(),'amber') is None
    assert v.depth_pose['pose'] in {'running','fleeing'}
    assert v.depth_pose['return_reason']=='startle'



def test_switch_to_static_portrait_returns_existing_depth_trip_without_teleport(c, monkeypatch):
    assert c.send_to_backyard('amber')
    visitor = c.engine.visitors['amber']
    run_to(c, lambda: visitor.depth_layer == 'rear')
    before = (visitor.x, visitor.y, visitor.depth_scale)
    book = c.journal
    book.wallpaper_choices.setCurrentIndex(book.wallpaper_choices.findData('backyard-cat-tree-portrait'))
    class PortraitPrimary:
        def geometry(self):
            return QRect(0, 0, 900, 1600)
    monkeypatch.setattr(c.qt, 'primaryScreen', lambda: PortraitPrimary())
    c.adapter.capabilities['wallpaper'] = True
    applied = []
    monkeypatch.setattr(c.adapter, 'set_wallpaper', lambda path: applied.append(str(path)) or True)
    monkeypatch.setattr(c.adapter, 'current_wallpaper', lambda: applied[-1])
    monkeypatch.setattr(c, '_run_worker', lambda key, operation, callback:
                        callback(operation()) if key == 'wallpaper' else False)
    c.apply_wallpaper()
    assert len(applied) == 1 and applied[0].endswith('backyard-cat-tree-portrait.png')
    assert (visitor.x, visitor.y, visitor.depth_scale) == before
    assert visitor.depth_trip is not None and visitor.depth_pose['return_reason'] == 'desktop_obscured'
    assert 'amber' in c._depth_mask_suspended
    assert not c.settings['depth_enabled'] and not c._depth_context_ready()
    window = c.cat_windows['amber']
    assert c.cat_clip(window.scene_position(), window.size(), 'amber') is None
    run_to(c, lambda: visitor.depth_trip is None)
    assert visitor.depth_scale == 1 and visitor.depth_layer == 'front'
    assert not c.send_to_backyard('amber')
