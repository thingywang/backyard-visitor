"""Exercise native extraction through actual route geometry, not geometry alone."""
from unittest.mock import Mock
import pytest
from backyard_visitor.desktop import MacDesktopAdapter, Rect, visible_window_supports, visible_window_climbs

SCREEN=Rect(0,0,1280,800)

def record(number,x,y,width,height):
    return {'kCGWindowNumber':number,'kCGWindowOwnerPID':100,'kCGWindowOwnerName':'Own test',
            'kCGWindowLayer':0,'kCGWindowIsOnscreen':True,'kCGWindowAlpha':1,
            'kCGWindowBounds':dict(X=x,Y=y,Width=width,Height=height)}

def snapshot(records):
    native=Mock();native.window_info.return_value=records;native.display_rects.return_value=[SCREEN]
    return MacDesktopAdapter(native=native,own_pid=999).windows()

@pytest.mark.parametrize('size',[(120,40),(40,120),(200,40)])
def test_native_small_front_window_blocks_side_but_is_never_support(size):
    blocker=record(1,650,320,*size);back=record(2,300,200,400,400)
    windows=snapshot([blocker,back])
    assert [w.id for w in windows]==['mac-window-1','mac-window-2']
    assert not windows[0].supports_cat and windows[1].supports_cat
    sides=visible_window_climbs(windows,SCREEN)
    assert [s[0] for s in sides]==['mac-window-2:side:left']
    tops,_=visible_window_supports(windows,SCREEN)
    assert all(s[0].startswith('mac-window-2:') for s in tops)
    # Native order is authoritative: a rear small surface cannot hide the front.
    sides=visible_window_climbs(snapshot([back,blocker]),SCREEN)
    assert {s[0] for s in sides if s[0].startswith('mac-window-2:')}=={'mac-window-2:side:left','mac-window-2:side:right'}

def test_wide_short_native_window_occludes_top_without_becoming_new_platform():
    windows=snapshot([record(1,400,160,200,40),record(2,300,220,400,400)])
    tops,_=visible_window_supports(windows,SCREEN)
    assert tops==[]  # both remaining top segments are narrower than the cat
    assert visible_window_climbs(windows,SCREEN)==[]

@pytest.mark.parametrize('change',[
    {'kCGWindowIsOnscreen':False},{'kCGWindowAlpha':0},
    {'kCGWindowLayer':20},{'kCGWindowOwnerPID':999},
])
def test_hidden_transparent_nonordinary_and_own_overlays_do_not_obstruct(change):
    blocker=record(1,650,320,120,40);blocker.update(change)
    windows=snapshot([blocker,record(2,300,200,400,400)])
    assert [w.id for w in windows]==['mac-window-2']
    assert len(visible_window_climbs(windows,SCREEN))==2
