"""Guest deadlines only use visible intervals bracketed by awake readings."""
from types import SimpleNamespace
from unittest.mock import Mock
import math
import pytest
from PyQt6.QtCore import QRect
from backyard_visitor.presence_clock import VisibleTimeAccumulator, PresenceAccounting
from backyard_visitor.desktop import DesktopAdapter,MacDesktopAdapter,Rect


def test_only_two_affirmative_reads_account_visible_ids():
    c=VisibleTimeAccumulator();c.add(1,['a']);assert c.observe(True,1)=={}
    c.add(.7,['a','a']);c.add(.3,['a','b'])
    assert c.observe(True,2)=={'a':1.,'b':.3}
    assert c.observe(True,3)=={}


@pytest.mark.parametrize('awake',[False,None,0,1,'true'])
def test_unknown_sleep_or_non_boolean_discards_interval(awake):
    c=VisibleTimeAccumulator();c.observe(True,1);c.add(1,['a'])
    assert c.observe(awake,2)=={}
    c.add(1,['a']);assert c.observe(True,3)=={}
    c.add(.5,['a']);assert c.observe(True,4)=={'a':.5}


@pytest.mark.parametrize('elapsed',[5,100000,-1,float('nan'),float('inf')])
def test_stale_backward_or_invalid_observation_never_spends_offline_time(elapsed):
    c=VisibleTimeAccumulator();c.observe(True,10);c.add(1,['a'])
    assert c.observe(True,10+elapsed)=={}


def test_reset_discards_paused_or_old_screen_interval():
    c=VisibleTimeAccumulator();c.observe(True,1);c.add(1,['a']);c.reset()
    assert c.observe(True,2)=={}


def test_bad_elapsed_does_not_create_time():
    c=VisibleTimeAccumulator();c.observe(True,1)
    for dt in (-1,0,True,6,float('nan'),float('inf'),'1'):
        c.add(dt,['a'])
    assert c.observe(True,2)=={}


def test_unknown_platform_and_native_failure_never_assume_awake():
    rect=Rect(0,0,100,100)
    assert DesktopAdapter().display_awake(rect) is None
    native=SimpleNamespace(display_awake=Mock(side_effect=OSError('unavailable')))
    adapter=MacDesktopAdapter(native=native)
    assert adapter.display_awake(rect) is None
    native.display_awake.side_effect=None;native.display_awake.return_value=False
    assert adapter.display_awake(rect) is False
    native.display_awake.return_value=True
    assert adapter.display_awake(rect) is True


def test_visibility_excludes_offscreen_hidden_blank_and_formal_includes_shared_fight():
    def win(rect,visible=True,content=True):
        return SimpleNamespace(isVisible=lambda:visible,_has_visible_content=content,geometry=lambda:rect)
    ids=['visible','offscreen','hidden','blank','formal','departed','fight-a','fight-b']
    c=SimpleNamespace(screen=SimpleNamespace(geometry=lambda:QRect(0,0,100,100)),
        engine=SimpleNamespace(cats={cid:SimpleNamespace(is_neighbor=cid=='formal',is_departed=cid=='departed') for cid in ids},
            visitors={cid:SimpleNamespace(state='fighting' if cid.startswith('fight') else 'idle') for cid in ids}),
        cat_windows={cid:win(QRect(10,10,20,20)) for cid in ids},fight_overlay=win(QRect(20,20,30,30)))
    c.cat_windows['offscreen']=win(QRect(150,150,20,20));c.cat_windows['hidden']=win(QRect(0,0,10,10),False)
    c.cat_windows['blank']=win(QRect(0,0,10,10),content=False)
    c.cat_windows['fight-a'].isVisible=lambda:False;c.cat_windows['fight-b'].isVisible=lambda:False
    c.fight_overlay.cat_ids=('fight-a','fight-b')
    assert set(PresenceAccounting._visible_guest_ids(c))=={'visible','fight-a','fight-b'}


def test_periodic_save_does_not_restart_debounce_on_every_frame():
    class C(PresenceAccounting):pass
    c=C();c._init_guest_presence();c._quitting=c.paused=False;c._screen_ready=True;c._desktop_epoch=3;c._guest_clock_epoch=3
    c.engine=SimpleNamespace(account_guest_exposure=Mock(return_value=True));c._events=lambda:False;c.save_timer=Mock()
    c._guest_clock.observe(True,0)
    for i in range(1,30):
        c._guest_clock.add(1,['a']);c._guest_presence_observed((3,i,True))
    c.save_timer.start.assert_not_called()
    c._guest_clock.add(1,['a']);c._guest_presence_observed((3,30,True))
    c.save_timer.start.assert_called_once()
    assert c.engine.account_guest_exposure.call_count==30
    c._guest_clock.add(1,['a']);c._guest_presence_observed((3,31,True))
    c.save_timer.start.assert_called_once()


def test_late_display_callback_cannot_charge_a_new_screen():
    class C(PresenceAccounting):pass
    c=C();c._init_guest_presence();c._quitting=c.paused=False;c._screen_ready=True;c._desktop_epoch=3;c._guest_clock_epoch=3
    c.engine=SimpleNamespace(account_guest_exposure=Mock());c._guest_clock.observe(True,1);c._guest_clock.add(1,['a'])
    c._guest_presence_observed((2,2,True));c.engine.account_guest_exposure.assert_not_called()
    assert c._guest_clock.pending=={} and not c._guest_clock.awake


@pytest.mark.parametrize('flags,asleep,expected',[
    ({'kCGSessionLoginDoneKey':True},False,True),
    ({'kCGSessionLoginDoneKey':True},True,False),
    ({'kCGSessionLoginDoneKey':True,'CGSSessionScreenIsLocked':True},False,False),
    ({'kCGSessionLoginDoneKey':True,'kCGSessionOnConsoleKey':False},False,False),
    ({'kCGSessionOnConsoleKey':True},False,True),
    ({},False,None),
])
def test_native_optional_console_flag_and_actual_sleep_remain_separate(flags,asleep,expected):
    import ctypes,plistlib
    from backyard_visitor.desktop import _CoreGraphics
    payload=plistlib.dumps(flags);buffer=ctypes.create_string_buffer(payload)
    def active(limit,displays,count):
        displays[0]=1;ctypes.cast(count,ctypes.POINTER(ctypes.c_uint32))[0]=1;return 0
    g=_CoreGraphics.__new__(_CoreGraphics)
    g.cg=SimpleNamespace(CGSessionCopyCurrentDictionary=Mock(return_value=1),
        CGDisplayIsAsleep=Mock(return_value=int(asleep)),CGGetActiveDisplayList=Mock(side_effect=active),
        CGDisplayBounds=Mock(return_value=SimpleNamespace(origin=SimpleNamespace(x=0,y=0),size=SimpleNamespace(width=100,height=100))))
    g.cf=SimpleNamespace(CFPropertyListCreateData=Mock(return_value=2),CFDataGetLength=Mock(return_value=len(payload)),
        CFDataGetBytePtr=Mock(return_value=ctypes.addressof(buffer)),CFRelease=Mock())
    assert g.display_awake(Rect(0,0,100,100)) is expected
    assert [call.args for call in g.cf.CFRelease.call_args_list]==[(2,),(1,)]


def test_hidden_native_space_does_not_consume_visible_time():
    window=SimpleNamespace(isVisible=lambda:True,windowHandle=lambda:SimpleNamespace(isExposed=lambda:False))
    assert not PresenceAccounting._presence_window_visible(window)
    window.windowHandle=lambda:SimpleNamespace(isExposed=lambda:True)
    assert PresenceAccounting._presence_window_visible(window)
