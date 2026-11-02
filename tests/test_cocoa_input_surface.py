"""Native configuration contracts; no windows, permissions or input injection."""
import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backyard_visitor.desktop import COCOA_INPUT_ALPHA, _CocoaInputSurface


class Bridge(_CocoaInputSurface):
    def __init__(self):
        self.calls = []
        self.main = True
        self.window, self.layer = 20, 30
        self.ignore, self.opaque = False, False
        self.alpha = COCOA_INPUT_ALPHA
        self.fail = None
        self.objc = SimpleNamespace(sel_registerName=lambda name: 100)
        self.cg = SimpleNamespace(CGColorCreateGenericRGB=Mock(return_value=40),
            CGColorRelease=Mock(), CGColorGetAlpha=Mock(side_effect=lambda color:self.alpha))
    def cls(self, name):return name
    def send(self, obj, selector, result=ctypes.c_void_p, args=(), values=()):
        self.calls.append((obj, selector, values))
        if selector == self.fail:raise RuntimeError('native setter failure')
        return {'isMainThread':self.main,'window':self.window,'layer':self.layer,
                'colorWithCGColor:':50,'respondsToSelector:':True,'backgroundColor':40,
                'ignoresMouseEvents':self.ignore,'isOpaque':self.opaque,'contents':None}.get(selector)


def test_native_color_has_nonzero_alpha_and_no_bitmap_or_activation():
    b=Bridge();info=b.configure(10)
    b.cg.CGColorCreateGenericRGB.assert_called_once_with(0.0,0.0,0.0,COCOA_INPUT_ALPHA)
    assert 0 < info['layer_background_alpha'] < .01
    assert info['routing_verified'] is False and not info['layer_has_bitmap_contents']
    assert (30,'setBackgroundColor:',(40,)) in b.calls
    assert (20,'setBackgroundColor:',(50,)) in b.calls
    assert (20,'setOpaque:',(False,)) in b.calls
    assert (20,'setIgnoresMouseEvents:',(False,)) in b.calls
    assert (20,'setHidesOnDeactivate:',(False,)) in b.calls
    assert not any(name in {'setContents:','setLayer:','makeKeyAndOrderFront:',
                           'activateIgnoringOtherApps:','setAlphaValue:'} for _,name,_ in b.calls)
    b.cg.CGColorRelease.assert_called_once_with(40)


@pytest.mark.parametrize('invalid',['thread','view','window','layer'])
def test_missing_native_resources_fail_before_allocating_color(invalid):
    b=Bridge();view=10
    if invalid=='thread':b.main=False
    if invalid=='view':view=0
    if invalid=='window':b.window=0
    if invalid=='layer':b.layer=0
    with pytest.raises(RuntimeError):b.configure(view)
    b.cg.CGColorCreateGenericRGB.assert_not_called()


def test_setter_failure_commits_transaction_and_releases_color():
    b=Bridge();b.fail='setBackgroundColor:'
    with pytest.raises(RuntimeError,match='setter'):b.configure(10)
    assert ('CATransaction','commit',()) in b.calls
    b.cg.CGColorRelease.assert_called_once_with(40)


@pytest.mark.parametrize('invalid',['alpha_zero','alpha_opaque','ignores','opaque'])
def test_unretained_or_opaque_configuration_rejected(invalid):
    b=Bridge()
    if invalid=='alpha_zero':b.alpha=0
    elif invalid=='alpha_opaque':b.alpha=1
    elif invalid=='ignores':b.ignore=True
    else:b.opaque=True
    with pytest.raises(RuntimeError,match='not retained'):b.configure(10)
    b.cg.CGColorRelease.assert_called_once_with(40)
