"""Shared smoke rendering, explicit selection and controller lifecycle boundaries."""
import os
import math
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from unittest.mock import patch
import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication,QEvent,QPoint,QPointF,QTimer,Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication,QMenu
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter
from backyard_visitor.overlays import FightOverlay
from test_app_lifecycle import Desktop,Screen


@pytest.fixture
def c(tmp_path):
    qt=QApplication.instance() or QApplication([])
    controller=Controller(Desktop(qt),state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=7)
    controller.qt.topLevelWidgets=qt.topLevelWidgets
    controller.engine.open_gate()
    for cid,x in [('amber',551),('mist',708)]:
        controller.engine.invite(cid);v=controller.engine.visitors[cid]
        v.x=v.target_x=x;v.y=v.target_y=controller.engine.floor_y
        controller.engine._state(v,'idle');controller.engine.cats[cid].trust=10
    assert controller.engine.trigger_fight()
    controller.changed()
    yield controller
    controller.shutdown();qt.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def click(widget,button=Qt.MouseButton.LeftButton,point=None):
    local=QPointF(point or widget.rect().center());glob=QPointF(widget.mapToGlobal(local.toPoint()))
    for typ,buttons in [(QEvent.Type.MouseButtonPress,button),(QEvent.Type.MouseButtonRelease,Qt.MouseButton.NoButton)]:
        QApplication.sendEvent(widget,QMouseEvent(typ,local,glob,button,buttons,Qt.KeyboardModifier.NoModifier))


def test_shared_cloud_connects_previously_separated_smoke_and_owns_input(c):
    overlay=c.fight_overlay
    assert isinstance(overlay,FightOverlay) and overlay.isVisible()
    # Cats retain model-space body sizes; the shared canvas and hit mask use
    # the monitor's actual display scale once, including its local smoke gap.
    scale=c.artwork_scale
    expected_origin=c.to_display(533,round(c.engine.floor_y)-28)
    assert (overlay.x(),overlay.y())==tuple(round(n) for n in expected_origin)
    assert overlay.base_size.width()==337 and overlay.base_size.height()==140
    assert overlay.width()==math.ceil(337*scale) and overlay.height()==math.ceil(140*scale)
    assert all(not w.isVisible() for w in c.cat_windows.values())
    image=overlay.grab().toImage();y=round(140*.55*scale)
    # Every actual pixel column spanning the former model-space 162..176 gap
    # has smoke and accepts input; this must not merely test an unrelated lobe.
    gap=range(math.floor(162*scale),math.ceil(176*scale))
    assert all(image.pixelColor(x,y).alpha()>0 for x in gap)
    assert all(overlay.mask().contains(QPoint(x,y)) for x in gap)
    assert not overlay.mask().contains(QPoint(0,0))
    assert overlay.findChildren(QTimer)==[]
    assert c.current_fps==24


@pytest.mark.parametrize('button',[Qt.MouseButton.LeftButton,Qt.MouseButton.RightButton])
def test_both_buttons_open_explicit_nonblocking_choice_without_immediate_reward(c,button):
    overlay=c.fight_overlay
    with patch.object(QMenu,'exec',side_effect=AssertionError('Blocking menu forbidden')):
        click(overlay,button)
    menu=overlay._menu
    assert menu is not None and menu.isVisible()
    assert {a.data() for a in menu.actions()}=={'amber','mist'}
    assert all(a.text()=='护住'+c.engine.cats[a.data()].display_name for a in menu.actions())
    assert all(c.engine.cats[cid].true_name not in a.text() for a in menu.actions() for cid in c.engine.cats)
    assert [c.engine.cats[cid].trust for cid in ('amber','mist')]==[10,10]
    assert c.engine._fight_pair and c.timer.isActive()


def test_menu_qt_click_rewards_only_selected_then_restores_cats_and_fur(c):
    overlay=c.fight_overlay;click(overlay);menu=overlay._menu
    selected=next(a for a in menu.actions() if a.data()=='mist')
    click(menu,point=menu.actionGeometry(selected).center())
    assert c.engine.cats['mist'].trust==14 and c.engine.cats['amber'].trust==10
    assert c.fight_overlay is None and not overlay.isVisible() and not menu.isVisible()
    assert all(w.isVisible() for w in c.cat_windows.values())
    assert all(v.state=='fleeing' for v in c.engine.visitors.values())
    assert [g.kind for g in c.engine.gifts]==['fur']
    # Hidden former individual smoke windows cannot provide an alternate reward.
    assert not c.engine.intervene('amber')


def test_natural_five_seconds_finish_even_with_choice_open(c):
    overlay=c.fight_overlay;click(overlay);menu=overlay._menu
    c.engine.tick(5);c.changed()
    assert c.fight_overlay is None and not overlay.isVisible() and not menu.isVisible()
    assert all(w.isVisible() for w in c.cat_windows.values())
    assert all(c.engine.cats[cid].trust==10 for cid in ('amber','mist'))
    assert len(c.engine.gifts)==1


def test_old_choice_cannot_reward_a_later_fight(c):
    first=c.fight_overlay;click(first);action=first._menu.actions()[0]
    c.engine.tick(5);c.changed()
    for cid,x in [('amber',551),('mist',708)]:
        v=c.engine.visitors[cid];v.x=v.target_x=x
        c.engine._state(v,'idle')
    assert c.engine.trigger_fight();c.changed()
    assert c.fight_overlay is not first
    action.trigger()
    assert c.engine._fight_pair and all(c.engine.cats[cid].trust==10 for cid in ('amber','mist'))


@pytest.mark.parametrize('boundary',['close','quit','no_screen'])
def test_terminal_boundaries_close_overlay_and_menu(c,boundary):
    overlay=c.fight_overlay;click(overlay);menu=overlay._menu
    if boundary=='close':c.close_gate()
    elif boundary=='quit':c.shutdown()
    else:c._suspend_display()
    assert c.fight_overlay is None and not overlay.isVisible() and not menu.isVisible()


def test_pause_closes_choice_preserves_frozen_cloud_and_rejects_stale_action(c):
    overlay=c.fight_overlay;click(overlay);menu=overlay._menu;action=menu.actions()[0]
    c.set_paused(True)
    assert c.fight_overlay is overlay and overlay.isVisible() and not menu.isVisible()
    assert c.current_fps==0
    c.set_paused(False);action.trigger()
    assert c.engine._fight_pair and c.engine.cats['amber'].trust==10


def test_screen_change_repositions_local_cloud_and_closes_old_menu(c):
    overlay=c.fight_overlay;click(overlay);menu=overlay._menu
    other=Screen('left',-1000,-100,900,700);c.qt.add(other);c.change_screen(1)
    assert not menu.isVisible()
    assert c.fight_overlay is not None and c.fight_overlay.isVisible()
    assert other.geometry().contains(c.fight_overlay.geometry())
    assert all(not w.isVisible() for w in c.cat_windows.values())


@pytest.mark.parametrize('topmost',[False,True])
def test_topmost_switch_includes_cloud_and_preserves_hidden_cats(c,topmost):
    overlay=c.fight_overlay;click(overlay);menu=overlay._menu;position=overlay.pos()
    c.set_overlay_topmost(topmost)
    assert overlay.isVisible() and overlay.pos()==position and not menu.isVisible()
    assert bool(overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)==topmost
    assert all(not w.isVisible() for w in c.cat_windows.values())
    assert not c.bin.isVisible()


def test_first_geometry_already_matching_still_has_input_mask(c):
    old=c.fight_overlay;c._clear_fight_overlay()
    for v in c.engine.visitors.values():v.x=551
    overlay=FightOverlay(c,tuple(c.engine.visitors));c.fight_overlay=overlay
    overlay.set_scene_size(180,140)
    overlay.move(*(round(n) for n in c.to_display(533,round(c.engine.floor_y)-28)))
    before=overlay.geometry()
    assert overlay.mask().isEmpty()
    assert overlay.sync_geometry()
    assert overlay.geometry()==before  # Exercise the unchanged-geometry branch.
    assert not overlay.mask().isEmpty()
    assert not overlay.mask().contains(QPoint(0,0))


def test_clip_keeps_cloud_on_selected_screen_without_changing_model_positions(c):
    c.engine.visitors['amber'].x=-90;c.engine.visitors['mist'].x=50
    positions={cid:(v.x,v.y) for cid,v in c.engine.visitors.items()}
    c._sync()
    assert c.screen.geometry().contains(c.fight_overlay.geometry())
    assert {cid:(v.x,v.y) for cid,v in c.engine.visitors.items()}==positions


def test_finished_overlay_and_popup_are_deleted(c):
    overlay=c.fight_overlay;click(overlay);menu=overlay._menu
    c.close_gate()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert sip.isdeleted(overlay) and sip.isdeleted(menu)


def test_hidden_individual_window_cannot_directly_favor_a_cat(c):
    click(c.cat_windows['amber'])
    assert c.engine._fight_pair and c.engine.cats['amber'].trust==10
    assert c.fight_overlay._menu is None
    c.cat_windows['amber'].context_menu(QPoint(600,600))
    assert c.fight_overlay._menu is not None
    assert {a.data() for a in c.fight_overlay._menu.actions()}=={'amber','mist'}


def test_tail_coats_follow_actual_left_right_cat_and_use_fight_clock(c):
    overlay=c.fight_overlay
    expected=tuple(c.engine.cats[cid].coat for cid in ('amber','mist'))
    assert overlay.visual_state()==(expected,0.0)
    c.engine.tick(.4);c.changed()
    coats,age=overlay.visual_state()
    assert coats==expected and age==pytest.approx(.4)
    c.engine.visitors['amber'].x=900
    c.engine.visitors['mist'].x=800
    assert overlay.visual_state()[0]==expected[::-1]


def test_paused_tail_and_effect_frames_do_not_animate_from_wall_clock(c):
    overlay=c.fight_overlay
    c.engine.tick(.5);c.changed();c.set_paused(True)
    before=overlay.grab().toImage()
    QApplication.processEvents()
    assert overlay.grab().toImage()==before
    assert overlay.findChildren(QTimer)==[]
