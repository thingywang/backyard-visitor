"""Pure full-screen stretch mappings and conservative route-window exclusion."""
from pathlib import Path
import math
import pytest
from backyard_visitor.depth_context import (map_foot,unmap_foot,build_depth_route,
                                            route_swept_rect,route_blocked)
from backyard_visitor.depth_visit import DepthVisit
from backyard_visitor.desktop import Rect
from backyard_visitor.scenery import load_scenes

SCENES=load_scenes(Path(__file__).resolve().parents[1]/'backyard_visitor/assets')


@pytest.mark.parametrize('screen',[(0,0,1920,1080),(-720,-461,720,1280),
                                  (100,50,800,600),(-1920,30,1440,900)])
def test_mapping_roundtrip_and_native_cat_size_are_independent_of_origin_and_ratio(screen):
    for point in ((0.,0.),(.25,.8),(1.,1.)):
        assert unmap_foot(map_foot(point,screen),screen)==pytest.approx(point)
    foot=map_foot((.52,.94),screen)
    route=build_depth_route(SCENES['backyard-dusk'],screen,foot)
    assert route is not None
    assert map_foot(route.start,screen)==pytest.approx(foot)
    assert route.near[1]==route.start[1] and route.near[0]==.60
    corners=[map_foot(p,screen) for p in route.footprint(route.start,1.)]
    assert corners[1][0]-corners[0][0]==pytest.approx(144.)
    assert corners[3][1]-corners[0][1]==pytest.approx(112.)
    assert foot[1]-corners[0][1]==pytest.approx(108.)


def test_work_area_floor_does_not_move_wallpaper_mask_origin():
    full=(-1920,-200,1920,1080)
    available=(-1920,-170,1920,970)
    foot=(full[0]+.52*full[2],available[1]+available[3]-4)
    route=build_depth_route(SCENES['backyard-dusk'],full,foot)
    assert route is not None
    assert map_foot(route.start,full)==pytest.approx(foot)
    assert map_foot((0,0),full)==(-1920.,-200.)
    assert route.start[1]!=(foot[1]-available[1])/available[3]


@pytest.mark.parametrize('screen,foot',[
    ((0,0,143,900),(70,800)),((0,0,800,111),(400,100)),
    ((0,0,0,900),(0,0)),((0,0,float('nan'),900),(500,800)),
    ((0,0,1920,1080),(-50,950)),((0,0,1920,1080),(500,700)),
])
def test_invalid_or_unreachable_current_foot_never_creates_route(screen,foot):
    assert build_depth_route(SCENES['backyard-dusk'],screen,foot) is None


def test_unsupported_fit_and_obstructed_corridor_are_rejected():
    screen=(0,0,1920,1080);foot=(998.4,996.)
    scene=dict(SCENES['backyard-dusk'],fit='aspect_fill')
    assert build_depth_route(scene,screen,foot) is None
    scene={'fit':'stretch','occluders':[{'polygon':[[.55,.7],[1,.7],[1,1],[.55,1]]}]}
    assert build_depth_route(scene,screen,foot) is None
    # The same art may not have a clear 144px corridor on a narrower screen.
    assert build_depth_route(SCENES['backyard-morning'],(-720,-461,720,1280),(-345.6,735.)) is None


def test_entire_forward_return_canvas_sweep_is_inside_conservative_bounds():
    screen=(-1920,-200,1920,1080)
    route=build_depth_route(SCENES['backyard-dusk'],screen,map_foot((.52,.94),screen))
    sweep=route_swept_rect(route,screen);visit=DepthVisit(route)
    for _ in range(2000):
        s=visit.step(.037)
        for p in route.footprint(s['foot'],s['scale']):
            x,y=map_foot(p,screen)
            assert sweep.left-1e-9<=x<=sweep.right+1e-9
            assert sweep.top-1e-9<=y<=sweep.bottom+1e-9
        if s['done']:break
    assert s['done']


def test_windows_and_journal_rectangles_touching_any_swept_area_block():
    screen=(0,0,1920,1080)
    route=build_depth_route(SCENES['backyard-dusk'],screen,(998.4,996.))
    sweep=route_swept_rect(route,screen)
    assert not route_blocked(route,screen,[])
    assert not route_blocked(route,screen,[Rect(0,0,300,300)])
    assert route_blocked(route,screen,[(sweep.left,sweep.top,1,1)])
    assert route_blocked(route,screen,[Rect(sweep.left-10,sweep.top,10,20)])
    assert route_blocked(route,screen,[Rect(0,0,1920,1080)])


@pytest.mark.parametrize('blockers',[None,[None],[(0,0,float('nan'),100)],[(0,0,-1,100)]])
def test_unknown_blocker_geometry_fails_closed(blockers):
    screen=(0,0,1920,1080)
    route=build_depth_route(SCENES['backyard-dusk'],screen,(998.4,996.))
    assert route_blocked(route,screen,blockers)



def test_portrait_scenery_never_produces_a_depth_route():
    scene = SCENES['backyard-cat-tree-portrait']
    assert scene['occluders'] == []
    for screen in ((0, 0, 900, 1600), (0, 0, 1920, 1080)):
        assert build_depth_route(scene, screen, map_foot((.52, .94), screen)) is None
