"""Production tree rendering shares geometry, sprite identity and model clock."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPainterPath, QColor
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt
from backyard_visitor.depth_art import DepthArt
from backyard_visitor.overlays import CatWindow
from backyard_visitor.tree_art import canvas_rect, paint_tree_visit


_QT = None


@pytest.fixture(scope='module')
def qt():
    global _QT
    _QT = QApplication.instance() or QApplication(['tree-render-check'])
    return _QT


def snapshot(**changes):
    result = dict(foot=(340., 330.), scale=.65, pose='idle', phase='rest',
                  facing=1, age=1.2, gait_phase=.3, run_phase=.4)
    result.update(changes)
    return result


def image_of(snap, art=None, depth=None, coat='ginger'):
    image = QImage(144, 112, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    assert paint_tree_visit(painter, snap, art or CatArt(), depth or DepthArt(), coat)
    painter.end()
    return image


def pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return image.constBits().asstring(image.sizeInBytes())


def occupied(image):
    return [(x, y) for y in range(image.height()) for x in range(image.width())
            if image.pixelColor(x, y).alpha()]


def test_scaled_canvas_preserves_actual_global_foot_anchor():
    for scale in (.65, .8, 1.):
        rect = canvas_rect(snapshot(scale=scale))
        assert rect.x()+72*scale == pytest.approx(72)
        assert rect.y()+108*scale == pytest.approx(108)
        assert rect.width() == 144*scale and rect.height() == 112*scale
    for value in (0, -1, 1.01, float('nan'), None, True):
        assert canvas_rect(snapshot(scale=value)).isEmpty()


def test_jump_selector_uses_distance_relative_to_the_scaled_body(qt):
    art = CatArt()
    poses = []
    original = art.frame
    def record(state, *args, **kwargs):
        from backyard_visitor.parkour_art import select_parkour_pose
        poses.append(select_parkour_pose(state, **{key:kwargs.get(key) for key in
            ('jump_vy','jump_pose_phase','jump_vertical_span','climb_phase')}))
        return original(state, *args, **kwargs)
    with patch.object(art, 'frame', record):
        up = image_of(snapshot(pose='jumping', phase='jump', jump_vy=-40,
                     jump_vertical_span=65, jump_pose_phase=.3), art)
        down = image_of(snapshot(pose='jumping', phase='jump', jump_vy=40,
                       jump_vertical_span=65, jump_pose_phase=.3), art)
    assert poses == [('jump_up', 0), ('jump_down', 0)]
    assert pixels(up) != pixels(down)


@pytest.mark.parametrize('coat', ['ginger','gray','tuxedo','black'])
def test_static_scratching_holds_pose_and_fits_scaled_input_canvas(qt, coat):
    art, depth = CatArt(), DepthArt()
    first = image_of(snapshot(pose='scratching', age=0), art, depth, coat)
    later = image_of(snapshot(pose='scratching', age=9, gait_phase=.9), art, depth, coat)
    assert pixels(first) == pixels(later)
    bounds = canvas_rect(snapshot()).toAlignedRect()
    assert occupied(first) and all(bounds.contains(x,y) for x,y in occupied(first))
    # The planted hind paw reaches the platform; no hanging by front paws.
    assert any(68 <= x <= 76 and 106 <= y <= 109 for x,y in occupied(first))
    mirrored = image_of(snapshot(pose='scratching', facing=-1), art, depth, coat)
    assert pixels(first) != pixels(mirrored)


def test_perspective_walking_reuses_authored_depth_views(qt):
    art, depth = CatArt(), DepthArt()
    with patch.object(depth, 'frame', wraps=depth.frame) as spy:
        away = image_of(snapshot(pose='walking', phase='recede', facing=1), art, depth)
        toward = image_of(snapshot(pose='walking', phase='advance', facing=-1), art, depth)
    assert spy.call_args_list[0].args[:2] == ('away',2)
    assert spy.call_args_list[1].args[:2] == ('toward',1)
    assert pixels(away) != pixels(toward)


def test_draw_restores_existing_painter_transform_opacity_and_clip(qt):
    image = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
    p=QPainter(image);p.translate(13,19);p.setOpacity(.42);p.setClipRect(QRect(0,0,120,120))
    transform,clip=p.transform(),p.clipRegion()
    paint_tree_visit(p,snapshot(),CatArt(),DepthArt())
    assert p.transform()==transform and p.clipRegion()==clip and p.opacity()==pytest.approx(.42)
    p.end()


@pytest.mark.parametrize('pose,phase', [('idle','rest'),('sleeping','rest'),('grooming','rest'),
                                       ('scratching','rest'),('walking','recede')])
def test_actual_cat_window_input_bounds_follow_scaled_painted_body(qt, pose, phase):
    snap=snapshot(pose=pose,phase=phase)
    v=SimpleNamespace(tree_trip=object(),tree_pose=snap,state='tree_visiting',
                      x=268.,y=222.,rear_blend=0.,clip_y=None)
    c=SimpleNamespace(settings={'overlay_topmost':True},
        screen=SimpleNamespace(geometry=lambda:QRect(0,0,1000,800)),
        engine=SimpleNamespace(visitors={'sample':v},cats={'sample':SimpleNamespace(coat='black',render_coat='black')}),
        art=CatArt(),depth_art=DepthArt(),cat_clip=lambda *args:None)
    w=CatWindow(c,'sample')
    try:
        w.move(round(v.x),round(v.y));w.show();w.refresh_input_region()
        actual=w.grab().toImage()
        assert w.mask().boundingRect()==canvas_rect(snap).toAlignedRect()
        assert all(w.mask().contains(QPoint(x,y)) for x,y in occupied(actual))
        assert w._has_visible_content
        # A narrower caller clip must never be bypassed by tree drawing.
        path=QPainterPath();path.addRect(QRectF(50,50,30,62))
        c.cat_clip=lambda *args:path
        w.refresh_input_region();clipped=w.grab().toImage()
        assert occupied(clipped) and all(50 <= x < 80 for x,y in occupied(clipped))
    finally:
        w.close();w.deleteLater();qt.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('enabled,active,expected', [(False,False,None),
    (True,False,'去爬架玩'),(True,True,'回到桌面'),(False,True,'回到桌面')])
def test_cat_context_menu_exposes_tree_action_and_return_then_play(qt, monkeypatch, enabled, active, expected):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QMenu
    v=SimpleNamespace(tree_trip=object() if active else None,state='tree_visiting' if active else 'idle',
        carrying_gift=None,demo_gift_pending=None,posture_remaining=0,true_name_pending=False,
        true_name_remaining=0,depth_trip=None)
    c=SimpleNamespace(settings={'overlay_topmost':True,'tree_enabled':enabled},
        engine=SimpleNamespace(visitors={'sample':v},cats={'sample':SimpleNamespace(
            display_name='Test visitor',trust=100,adopted=False,is_neighbor=True)},gate_open=True,active_teaser_id=None),
        paused=False,send_to_tree=Mock(),call_cat=Mock(),rename_cat=Mock(),pet=Mock(),
        start_play=Mock(),show_journal=Mock(),close_gate=Mock())
    recorded=[]
    def inspect(menu, position):
        actions={action.text():action for action in menu.actions()}
        recorded.append(tuple(actions))
        if expected:
            assert expected in actions
            actions[expected].trigger()
        else:
            assert '去爬架玩' not in actions and '回到桌面' not in actions
        assert actions['和它玩 · 15 秒'].isEnabled()
    monkeypatch.setattr(QMenu,'exec',inspect)
    window=CatWindow(c,'sample')
    try:
        window.context_menu(QPoint(50,50))
        assert recorded
        if expected: c.send_to_tree.assert_called_once_with('sample')
        else: c.send_to_tree.assert_not_called()
    finally:
        window.close();window.deleteLater();qt.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('phase,progress_key,expected', [('takeoff','takeoff_progress','takeoff'),
    ('landing','land_progress','landing')])
def test_stationary_platform_transitions_reuse_supported_crouch_art(qt, phase, progress_key, expected):
    art=CatArt()
    with patch.object(art,'frame',wraps=art.frame) as frames:
        image=image_of(snapshot(pose='standing',phase=phase,**{progress_key:.5}),art)
    assert frames.call_args.args[0]==expected
    assert occupied(image)
    bounds=canvas_rect(snapshot()).toAlignedRect()
    assert all(bounds.contains(x,y) for x,y in occupied(image))
