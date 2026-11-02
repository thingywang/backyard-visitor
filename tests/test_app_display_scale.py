"""Production Controller, overlays and input share the same display transform."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter, DesktopTarget, Rect
from backyard_visitor.model import Engine, Gift
from backyard_visitor.storage import save_state
from test_app_lifecycle import Desktop, Screen


class ScalingDesktop(Desktop):
    def __getattr__(self,name): return getattr(self.qt,name)


def screen(width=1440,height=900,origin=(0,0),dpr=1):
    value=Screen('scale-display',*origin,width,height)
    value.available=QRect(origin[0],origin[1]+24,width,height-24)
    value.ratio=dpr
    return value


def stop_clocks(c):
    c.timer.stop();c.native_timer.stop();c.wallpaper_timer.stop();c.recycle_timer.stop();c._scale_timer.stop()


@pytest.fixture
def make(tmp_path):
    qt=QApplication.instance() or QApplication([])
    controllers=[]
    def create(display=None,folder=None,saved=None):
        desktop=ScalingDesktop(qt)
        desktop.primary=display or screen();desktop.display_list=[desktop.primary]
        folder=Path(folder or tmp_path/f'save-{len(controllers)}')
        if saved is not None: save_state(folder/'state.json',saved)
        c=Controller(desktop,state_dir=folder,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=7)
        controllers.append(c);stop_clocks(c)
        return c
    yield create
    for c in controllers:c.shutdown()
    qt.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def cat(c):
    e=c.engine;e.calm_mode=True;e.open_gate();e.invite('amber');e._visit_countdown=10000
    v=e.visitors['amber'];v.state='idle';v.x=v.target_x=e.bounds[0]+350
    v.y=v.target_y=e.floor_y;v.state_age=2;v.next_action=1000;v.stay_remaining=1000
    v.takeoff_remaining=v.landing_remaining=v.settle_remaining=v.rise_remaining=v.turn_remaining=0
    v.carrying_gift=None;e.cats['amber'].trust=100
    c._sync();stop_clocks(c)
    return v


def apply(c,value):
    c.set_artwork_scale(value);c._process_scale_request();stop_clocks(c)


def mouse(kind,local,global_pos):
    moving=kind==QEvent.Type.MouseMove;released=kind==QEvent.Type.MouseButtonRelease
    return QMouseEvent(kind,QPointF(*local),QPointF(*global_pos),
        Qt.MouseButton.NoButton if moving else Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton if released else Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)


def drag(window,dx,dy):
    local=(window.width()/3,window.height()/3)
    start=(window.x()+local[0],window.y()+local[1]);end=(start[0]+dx,start[1]+dy)
    window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress,local,start))
    window.mouseMoveEvent(mouse(QEvent.Type.MouseMove,local,end))
    window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease,local,end))


def centre(c,kind):
    if kind=='bowl':return c.to_display(*c.engine.bowl)
    if kind=='bed':return c.to_display(c.engine.bed.x,c.engine.bed_y)
    g=c.engine.gifts[0];return c.to_display(g.x+19,g.y+19)


@pytest.mark.parametrize('dimensions,expected_cat,expected_gate',[
    ((1440,900),(144,112),(30,30)),((1920,1080),(183,142),(38,38)),
    ((1080,1920),(183,142),(38,38)),((720,450),(108,84),(23,23)),
])
def test_resolution_changes_real_overlay_sizes_and_selected_screen_bounds(make,dimensions,expected_cat,expected_gate):
    c=make(screen(*dimensions));v=cat(c);w=c.cat_windows['amber']
    assert (w.width(),w.height())==expected_cat
    assert (c.gate.width(),c.gate.height())==expected_gate
    assert w.isVisible() and not w.mask().isEmpty()
    # Fractional scale uses a ceil-sized canvas; only its clipped input and
    # painted pixels must remain on the selected display.
    assert c.screen.geometry().contains(w.mask().boundingRect().translated(w.pos()))
    assert c.screen.availableGeometry().contains(c.bowl.geometry())
    # Drawing is real, rather than only a QSize calculation.
    image=w.grab().toImage()
    assert (image.width(),image.height())==expected_cat
    opaque=[(x,y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x,y).alpha()>20]
    assert len(opaque)>500
    assert all(w.mask().contains(QPoint(x,y)) for x,y in opaque)
    assert all(c.screen.geometry().contains(w.x()+x,w.y()+y) for x,y in opaque)
    assert c.to_display(v.x,v.y)[0]==pytest.approx(w.x(),abs=1)


def test_retina_dpr_does_not_double_artwork_size(make):
    a=make(screen(1920,1080,dpr=1));b=make(screen(1920,1080,dpr=2))
    cat(a);cat(b)
    assert a.artwork_scale==b.artwork_scale
    assert a.bowl.size()==b.bowl.size() and a.cat_windows['amber'].size()==b.cat_windows['amber'].size()
    assert a.engine.bounds==b.engine.bounds


@pytest.mark.parametrize('factor,cat_size,bowl_size,bed_size',[
    (.75,(108,84),(47,30),(129,33)),(1.5,(216,168),(93,60),(258,66)),
])
def test_slider_resizes_all_existing_foreground_canvases_with_matching_masks(make,factor,cat_size,bowl_size,bed_size):
    c=make();v=cat(c);c.engine.adopt('amber');c.engine.place_bed('amber',900)
    c.engine.gifts.append(Gift('f','feather',500,400,'amber'));c._sync()
    before=v.age,v.state_age,v.stay_remaining,c.engine._time
    old_x=c.to_display(v.x+72,v.y+108)[0]
    apply(c,factor)
    assert c._pending_artwork_scale is None and c.artwork_scale==factor
    assert (c.cat_windows['amber'].width(),c.cat_windows['amber'].height())==cat_size
    assert (c.bowl.width(),c.bowl.height())==bowl_size
    assert (c.bed_window.width(),c.bed_window.height())==bed_size
    assert c.gift_windows['f'].width()==(29 if factor==.75 else 57)
    assert c.bin.width()==(72 if factor==.75 else 144)
    assert c.bed_window.mask().boundingRect().width()<=c.bed_window.width()
    assert c.cat_windows['amber'].mask().boundingRect().width()<=c.cat_windows['amber'].width()
    assert (v.age,v.state_age,v.stay_remaining,c.engine._time)==before
    assert c.to_display(v.x+72,v.y+108)[0]==pytest.approx(old_x)


def test_actual_furniture_and_gift_drag_preserve_display_centres_through_resize_and_restart(make,tmp_path):
    folder=tmp_path/'persistent';c=make(folder=folder);v=cat(c)
    c.engine.adopt('amber');c.engine.place_bed('amber',900)
    c.engine.set_bowl(650,600);c.engine.gifts.append(Gift('f','feather',450,380,'amber'));c._sync()
    apply(c,1.5)
    initial={kind:centre(c,kind) for kind in ('bowl','bed','gift')}
    drag(c.bowl,-120,-60);drag(c.bed_window,-100,0);drag(c.gift_windows['f'],90,-45)
    stop_clocks(c)
    before={kind:centre(c,kind) for kind in ('bowl','bed','gift')}
    assert before['bowl']==pytest.approx((initial['bowl'][0]-120,initial['bowl'][1]-60),abs=1)
    assert before['bed'][0]==pytest.approx(initial['bed'][0]-100,abs=1)
    assert before['gift']==pytest.approx((initial['gift'][0]+90,initial['gift'][1]-45),abs=1)
    assert c.engine.food==0 and len(c.engine.gifts)==1
    apply(c,.75)
    for kind in ('bowl','gift'):assert centre(c,kind)==pytest.approx(before[kind],abs=1)
    assert centre(c,'bed')[0]==pytest.approx(before['bed'][0],abs=1)
    assert c._save();data=json.loads(c.state_path.read_text())
    assert data['display_transform']['scale']==.75
    assert data['settings']['artwork_scale']==.75
    c.shutdown()
    restored=make(folder=folder)
    for kind in ('bowl','gift'):assert centre(restored,kind)==pytest.approx(before[kind],abs=1)
    assert centre(restored,'bed')[0]==pytest.approx(before['bed'][0],abs=1)
    assert restored.settings['artwork_scale']==.75 and not restored.engine.visitors
    old=(*restored.engine.bowl,restored.engine.bed.x,restored.engine.gifts[0].x,restored.engine.gifts[0].y)
    for _ in range(3):
        restored._apply_screen_geometry();restored._sync()
    assert (*restored.engine.bowl,restored.engine.bed.x,restored.engine.gifts[0].x,restored.engine.gifts[0].y)==pytest.approx(old)


def test_legacy_unscaled_save_uses_existing_physical_placements(make):
    e=Engine(seed=2);e.set_bounds(0,24,1440,876);e.set_bowl(600,600)
    e.cats['amber'].trust=100;e.cats['amber'].discovered=True
    e.cats['amber'].neighbor_status='neighbor'
    assert e.adopt('amber');assert e.place_bed('amber',1000)
    e.gifts.append(Gift('old','feather',300,350,'amber'))
    saved=e.export();saved['settings']={'artwork_scale':1.5}
    c=make(saved=saved)
    assert c.artwork_scale==1.5
    assert centre(c,'bowl')==pytest.approx((600,600))
    assert centre(c,'bed')[0]==pytest.approx(1000)
    assert centre(c,'gift')==pytest.approx((319,369))



@pytest.mark.parametrize('origin', [(0, 0), (-1440, 120)])
def test_legacy_save_with_left_dock_preserves_actual_furniture_positions(make, origin):
    ox, oy = origin
    display = screen(origin=origin)
    # A left-hand Dock reserves 80 native points inside the full display;
    # this work-area inset must not be mistaken for a different monitor origin.
    display.available = QRect(ox + 80, oy + 24, 1360, 876)
    e = Engine(seed=2)
    e.set_bounds(ox + 80, oy + 24, 1360, 876)
    e.set_bowl(ox + 400, oy + 500)
    e.cats['amber'].trust = 100
    e.cats['amber'].discovered = True
    e.cats['amber'].neighbor_status = 'neighbor'
    assert e.adopt('amber')
    assert e.place_bed('amber', ox + 1000)
    e.gifts.append(Gift('old-dock', 'feather', ox + 300, oy + 350, 'amber'))
    saved = e.export()
    saved['settings'] = {'artwork_scale': 1.5}
    assert 'display_transform' not in saved
    c = make(display=display, saved=saved)
    assert c.artwork_scale == 1.5
    assert centre(c, 'bowl') == pytest.approx((ox + 400, oy + 500))
    assert centre(c, 'bed')[0] == pytest.approx(ox + 1000)
    assert centre(c, 'gift') == pytest.approx((ox + 319, oy + 369))


def test_negative_screen_maps_inputs_and_furniture_without_cross_screen_shift(make):
    c=make(screen(1920,1080,origin=(-1920,80)));v=cat(c)
    w=c.cat_windows['amber']
    assert c.screen.geometry().contains(w.mask().boundingRect().translated(w.pos())) and w.x()<0
    before=centre(c,'bowl')
    drag(c.bowl,-180,-90);stop_clocks(c)
    assert centre(c,'bowl')==pytest.approx((before[0]-180,before[1]-90),abs=1.5)
    assert c.screen.availableGeometry().contains(c.bowl.geometry())


def test_bowl_mouth_alignment_is_in_actual_desktop_points_after_scale(make):
    c=make();v=cat(c);apply(c,1.5)
    c.engine.set_bowl(420,c.engine.floor_y+97);c.engine.feed()
    v.next_action=0
    for _ in range(1200):
        c.engine.tick(.05);c._sync()
        if v.state=='eating':break
    assert v.state=='eating'
    from backyard_visitor.model import EATING_MUZZLE_LEFT,EATING_MUZZLE_RIGHT
    mouth=(v.x+(EATING_MUZZLE_RIGHT if v.facing>0 else EATING_MUZZLE_LEFT),v.y+97)
    assert c.to_display(*mouth)==pytest.approx(c.to_display(*c.engine.bowl),abs=.01)
    assert c.bowl.geometry().contains(*map(round,c.to_display(*c.engine.bowl)))


def test_real_native_window_coordinates_convert_before_perch_and_scale_waits_for_descent(make):
    c=make();v=cat(c);apply(c,1.5)
    c.settings['window_paths']=True
    c._windows=[DesktopTarget('real-helper','helper',Rect(300,450,800,300))]
    c._windows_checked=time.monotonic();c._rebuild_paths()
    key=next(iter(c.engine.window_edges));edge=c.engine.window_edges[key]
    v.x=v.target_x=edge[0]+80;v.y=v.target_y=edge[2]-112;v.window_edge_id=key
    c._sync();before=v.x,v.y
    assert c.to_display(v.x,v.y+112)[1]==pytest.approx(450)
    c.set_artwork_scale(.75);c._process_scale_request();stop_clocks(c)
    assert c.artwork_scale==1.5 and c._pending_artwork_scale==.75
    assert (v.x,v.y)==before and v.state in {'jumping','walking'}
    for _ in range(1000):
        c.engine.tick(.05);c._sync();c._process_scale_request();stop_clocks(c)
        if c._pending_artwork_scale is None:break
    assert c.artwork_scale==.75 and c._pending_artwork_scale is None
    assert v.y==c.engine.floor_y


def refresh_tree(c):
    full=c.screen.geometry()
    c._depth_snapshot={'path':str(c._wallpaper_path()),'display_id':'scale-test-display',
        'options':{'scaling':1,'clipping':False},'primary_size':[full.width(),full.height()]}
    c._windows_checked=c._wallpaper_checked=time.monotonic()


def test_tree_journey_returns_continuously_before_pending_resize(make,monkeypatch):
    c=make();v=cat(c)
    monkeypatch.setattr(c,'_run_worker',lambda *args:False);c._enable_native=True
    c.settings.update(preset_wallpaper=True,wallpaper_id='backyard-cat-tree-landscape-morning',tree_enabled=True)
    c._mask_data=c._read_mask();refresh_tree(c)
    assert c.send_to_tree('amber')
    for _ in range(1500):
        refresh_tree(c);c.engine.tick(.05);c._sync()
        if v.tree_pose.get('pose')=='sleeping':break
    assert v.tree_pose['pose']=='sleeping'
    before=v.x,v.y,v.tree_scale
    c.set_artwork_scale(1.5);c._process_scale_request();stop_clocks(c)
    assert c.artwork_scale==1 and v.tree_trip is not None and v.tree_pose['returning']
    assert (v.x,v.y,v.tree_scale)==before
    for _ in range(1500):
        refresh_tree(c);c.engine.tick(.05);c._sync();c._process_scale_request();stop_clocks(c)
        if c._pending_artwork_scale is None:break
    assert v.tree_trip is None and c.artwork_scale==1.5
    assert v.y==c.engine.floor_y and v.tree_scale==1


def test_paused_busy_request_does_not_move_cat_or_advance_clock_and_shutdown_stops_queue(make):
    c=make();v=cat(c)
    c.engine._walk(v,v.x+20,v.y-120)
    assert v.state=='jumping'
    c.set_paused(True)
    before=v.x,v.y,v.jump_elapsed,v.state_age,c.engine._time
    c.set_artwork_scale(1.5);c._process_scale_request()
    assert c.paused and c._pending_artwork_scale==1.5 and c.artwork_scale==1
    assert (v.x,v.y,v.jump_elapsed,v.state_age,c.engine._time)==before
    assert not c.timer.isActive()
    c.shutdown();assert not c._scale_timer.isActive()


def test_paused_ground_cat_can_resize_without_waking_or_resuming_animation(make):
    c=make();v=cat(c);v.state='sleeping';v.state_age=8
    c.set_paused(True);before=v.age,v.state_age,c.engine._time
    c.set_artwork_scale(1.5);c._process_scale_request()
    assert c.artwork_scale==1.5 and v.state=='sleeping' and c.paused
    assert (v.age,v.state_age,c.engine._time)==before and not c.timer.isActive()


def test_resident_sleep_survives_controller_resize_with_bed_and_body_aligned(make):
    from backyard_visitor.resident_bed import at_bed
    c=make();v=cat(c)
    assert c.engine.adopt('amber') and c.engine.place_bed('amber',800)
    c.engine.bed.credit=60
    v.next_action=0
    for _ in range(1200):
        c.engine.tick(.05);c._sync()
        if v.bed_nap and v.state=='sleeping':break
    assert v.bed_nap and v.state=='sleeping' and at_bed(c.engine,v)
    before=(v.age,v.state_age,c.engine.bed.credit,c.engine.bed.nap_elapsed,
            c.engine.bed.sleep_elapsed,c.engine._bed_revision)
    apply(c,1.5)
    assert v.bed_nap and v.state=='sleeping' and at_bed(c.engine,v)
    assert (v.age,v.state_age,c.engine.bed.credit,c.engine.bed.nap_elapsed,
            c.engine.bed.sleep_elapsed,c.engine._bed_revision)==before
    assert c.to_display(v.x+72,v.y)[0]==pytest.approx(c.bed_window.x()+c.bed_window.width()/2,abs=1)
    c.engine.tick(.05);c._sync()
    assert v.bed_nap and v.state=='sleeping' and at_bed(c.engine,v)


def test_screen_resolution_signal_resizes_existing_widgets_and_preserves_ground_lifecycle(make):
    c=make();v=cat(c)
    c.engine.set_bowl(650,600);c._sync()
    before=(v.age,v.state_age,v.stay_remaining,c.engine._time)
    bowl=centre(c,'bowl')
    c.screen.full=QRect(0,0,1920,1080)
    c.screen.available=QRect(0,24,1920,1056)
    c.screen.geometryChanged.emit(c.screen.geometry())
    c._process_scale_request();stop_clocks(c)
    assert (c.cat_windows['amber'].width(),c.cat_windows['amber'].height())==(183,142)
    assert (c.bowl.width(),c.bowl.height())==(79,51)
    assert c._pending_artwork_scale is None
    assert (v.age,v.state_age,v.stay_remaining,c.engine._time)==before
    assert centre(c,'bowl')==pytest.approx(bowl)
    assert v.y==c.engine.floor_y


def placed_save_with_scale_metadata():
    e=Engine(seed=2);e.set_bounds(0,24,1440,876);e.set_bowl(600,600)
    e.cats['amber'].trust=100;e.cats['amber'].discovered=True
    e.cats['amber'].neighbor_status='neighbor'
    assert e.adopt('amber');assert e.place_bed('amber',1000)
    e.gifts.append(Gift('saved-feather','feather',300,350,'amber'))
    saved=e.export()
    saved['settings']={'artwork_scale':1.5}
    saved['display_transform']={'scale':1.0,'origin_x':0.0,'origin_y':0.0}
    return saved


def assert_restored_scaled_placements(c):
    assert c.artwork_scale==1.5 and c.settings['artwork_scale']==1.5
    assert centre(c,'bowl')==pytest.approx((600,600))
    assert c.engine.resident_id=='amber' and centre(c,'bed')[0]==pytest.approx(1000)
    assert c.engine.gifts[0].id=='saved-feather'
    assert centre(c,'gift')==pytest.approx((319,369))
    assert not c.engine.gate_open and not c.engine.visitors
    assert c._save()
    persisted=json.loads(c.state_path.read_text())
    assert persisted['display_transform']=={'scale':1.5,'origin_x':0.0,'origin_y':0.0}


@pytest.mark.parametrize('field',['scale','origin_x','origin_y'])
def test_finite_extreme_saved_display_metadata_falls_back_without_losing_placements(make,field):
    saved=placed_save_with_scale_metadata()
    saved['display_transform'][field]=1e308
    c=make(saved=saved)
    assert_restored_scaled_placements(c)


@pytest.mark.parametrize('field',['scale','origin_x','origin_y'])
def test_nonfinite_saved_display_metadata_recovers_last_good_snapshot(make,tmp_path,field):
    folder=tmp_path/'nonfinite-save';folder.mkdir()
    valid=placed_save_with_scale_metadata()
    save_state(folder/'state.json.bak',valid)
    invalid=placed_save_with_scale_metadata()
    invalid['display_transform'][field]=float('nan')
    # Simulate a malformed external file: normal save_state correctly refuses
    # NaN. Controller must let the storage reader recover the valid backup.
    (folder/'state.json').write_text(json.dumps(invalid,allow_nan=True))
    c=make(folder=folder)
    assert_restored_scaled_placements(c)
