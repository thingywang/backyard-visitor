"""Natural entry survives simulated screen geometry changes without popping in."""
import os
import math
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtWidgets import QApplication
from backyard_visitor import app
from backyard_visitor.desktop import DesktopAdapter
from backyard_visitor.model import Engine, SPRITE_WIDTH
from test_app_lifecycle import Desktop, Screen

BOUNDS = (0, 24, 900, 652)


def natural_entry(seed, *, fully_inside=False):
    engine = Engine(seed=seed, visit_limit=1)
    engine.set_bounds(*BOUNDS)
    engine.open_gate()
    assert engine.invite('amber')
    visitor = engine.visitors['amber']
    for _ in range(600):
        engine.tick(.02)
        left, _, width, _ = engine.bounds
        inside = left <= visitor.x and visitor.x+SPRITE_WIDTH <= left+width
        if visitor.state == 'entering' and (inside if fully_inside else not inside):
            return engine, visitor
    pytest.fail('Natural invitation did not reach the requested entry phase')


def visible_width(engine, visitor):
    left, _, width, _ = engine.bounds
    return max(0, min(visitor.x+SPRITE_WIDTH, left+width)-max(visitor.x,left))


@pytest.mark.parametrize('seed', [0, 3], ids=['left', 'right'])
@pytest.mark.parametrize('advance', [0, .8], ids=['first-step', 'partly-entered'])
@pytest.mark.parametrize('bounds', [(0,24,900,650), (0,24,1100,652), (0,24,600,652),
                                   (-1200,-226,1000,752), (200,-100,144,300)])
def test_partial_entry_preserves_exposed_width_and_continues_naturally(seed, advance, bounds):
    e,v = natural_entry(seed)
    for _ in range(round(advance/.02)):
        e.tick(.02)
    exposed = visible_width(e,v)
    assert 0 < exposed < SPRITE_WIDTH
    age, facing, state_age = v.age, v.facing, v.state_age
    e.set_bounds(*bounds)
    assert v.state == 'entering'
    assert (v.age,v.facing,v.state_age) == (age,facing,state_age)
    assert visible_width(e,v) == pytest.approx(exposed)
    left,_,width,_ = bounds
    assert left <= v.target_x <= left+width-SPRITE_WIDTH
    assert v.x < left if facing>0 else v.x+SPRITE_WIDTH>left+width
    previous = v.x
    for _ in range(1000):
        e.tick(.02)
        assert abs(v.x-previous) <= 66*.02+1e-7
        previous = v.x
        if v.state != 'entering':
            break
    assert v.state == 'idle'
    assert visible_width(e,v) == SPRITE_WIDTH


@pytest.mark.parametrize('seed', [0, 3], ids=['left', 'right'])
def test_same_bounds_repeated_during_entry_preserves_all_entry_geometry(seed):
    e,v = natural_entry(seed)
    before = (v.x,v.y,v.target_x,v.target_y,v.state,v.age,v.state_age,v.facing)
    for _ in range(3):
        e.set_bounds(*BOUNDS)
    assert (v.x,v.y,v.target_x,v.target_y,v.state,v.age,v.state_age,v.facing)==before


@pytest.mark.parametrize('seed', [0, 3], ids=['left', 'right'])
@pytest.mark.parametrize('bounds', [(0,24,1100,650), (-1200,-226,1100,752)])
def test_fully_entered_cat_is_not_reanchored_to_new_edge(seed,bounds):
    e,v = natural_entry(seed,fully_inside=True)
    before_x, before_age = v.x,v.age
    assert v.state=='entering' and visible_width(e,v)==SPRITE_WIDTH
    e.set_bounds(*bounds)
    assert v.x == pytest.approx(before_x+bounds[0]-BOUNDS[0])
    assert v.age == before_age
    assert visible_width(e,v)==SPRITE_WIDTH


@pytest.mark.parametrize('seed', [0,3], ids=['left','right'])
@pytest.mark.parametrize('operation', ['height-change','screen-switch'])
def test_controller_rebases_partial_entry_before_show_without_revealing_full_cat(tmp_path,seed,operation):
    qt=QApplication.instance() or QApplication(['entry-rebase-tests'])
    desktop=Desktop(qt)
    c=app.Controller(desktop,state_dir=tmp_path,hidden=True,adapter=DesktopAdapter(),
                     enable_native=False,seed=seed,visit_limit=1)
    try:
        c.timer.stop();c.native_timer.stop()
        c.engine.open_gate();c.engine.invite('amber')
        v=c.engine.visitors['amber']
        for _ in range(600):
            c.engine.tick(.02)
            if v.state=='entering':break
        assert v.state=='entering'
        c.changed();c.timer.stop()
        exposed,age=visible_width(c.engine,v),v.age
        if operation=='height-change':
            c.screen.available.setHeight(c.screen.available.height()-2)
            c.screen.availableGeometryChanged.emit(c.screen.availableGeometry())
        else:
            desktop.add(Screen('left',-1200,-250,1000,800))
            c.change_screen(1)
        assert visible_width(c.engine,v)==pytest.approx(exposed)
        assert v.age==age and v.state=='entering'
        window=c.cat_windows['amber']
        dx,dy=c.to_display(round(v.x),round(v.y))
        assert window.isVisible() and (window.x(),window.y())==(round(dx),round(dy))
        screen=c.screen.geometry()
        assert not window.mask().isEmpty()
        exposed_pixels=screen.intersected(window.geometry()).width()
        assert 0 < exposed_pixels < math.ceil(SPRITE_WIDTH*c.artwork_scale)
        assert exposed_pixels==pytest.approx(exposed*c.artwork_scale,abs=2)
    finally:
        c.shutdown();qt.processEvents()
