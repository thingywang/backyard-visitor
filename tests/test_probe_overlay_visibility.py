"""Pure checks: no Cocoa, permissions, or real app activation."""
import copy
import ctypes
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/probe_overlay_visibility.py'
spec = importlib.util.spec_from_file_location('overlay_visibility_probe', SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def sample(enabled=True):
    bounds = dict(X=100, Y=100, Width=800, Height=600)
    helper = dict(level=0, key=True, cg=dict(layer=0, order=10, bounds=bounds, alpha=1))
    windows = {}
    for index, name in enumerate(('gate', 'bowl', 'cat', 'journal')):
        top = enabled and name != 'journal'
        windows[name] = dict(position=[120 + index * 60, 130], level=3 if top else 0,
                             key=False, qt_visible=True, native_visible=True,
                             cg=dict(layer=3 if top else 0, order=index if top else 11 + index,
                                     bounds=dict(X=120 + index * 60, Y=130, Width=40, Height=40), alpha=1))
    return dict(windows=windows, helper=helper, enabled=enabled, controller_active=False,
                helper_frontmost=True, bin_visible=False), {k:v['position'][:] for k,v in windows.items()}


@pytest.mark.parametrize('enabled', [True, False])
def test_expected_stacking_passes(enabled):
    data, baseline = sample(enabled)
    assert all(probe.evaluate_sample(data, baseline).values())


@pytest.mark.parametrize('changed, expected', [
    ('missing', 'cat_on_screen'), ('raised_off', 'cat_expected_stack'),
    ('position', 'positions_preserved'), ('bin', 'hidden_bin_preserved'),
    ('focus', 'controller_inactive'), ('journal', 'journal_normal_below_helper'),
])
def test_actual_failures_cannot_be_reported_as_pass(changed, expected):
    data, baseline = sample(False)
    if changed == 'missing': data['windows']['cat']['cg'] = {}
    elif changed == 'raised_off': data['windows']['cat']['cg']['order'] = 1
    elif changed == 'position': data['windows']['cat']['position'][0] += 1
    elif changed == 'bin': data['bin_visible'] = True
    elif changed == 'focus': data['controller_active'] = True
    elif changed == 'journal': data['windows']['journal']['level'] = 3
    assert probe.evaluate_sample(data, baseline)[expected] is False


def test_records_drop_other_pids_and_all_titles():
    records = [dict(kCGWindowOwnerPID=20, kCGWindowNumber=2, kCGWindowName='private'),
               dict(kCGWindowOwnerPID=10, kCGWindowNumber=1, kCGWindowName='own title',
                    kCGWindowLayer=3, kCGWindowBounds={'X':20, 'Y':30, 'Width':40, 'Height':50}, kCGWindowAlpha=1)]
    clean = probe.private_records(records, {10: {1}})
    assert set(clean) == {1} and clean[1]['order'] == 1
    assert 'title' not in repr(clean) and 'private' not in repr(clean)


def test_qt_win_id_is_nsview_not_window_number():
    cocoa = object.__new__(probe.Cocoa)
    calls = []
    def send(obj, selector, *args):
        calls.append((obj, selector))
        return {'window': 900, 'windowNumber': 123, 'level': 3, 'isVisible': True, 'isKeyWindow': False}[selector]
    cocoa.send = send
    class Widget:
        def winId(self): return 800
        def isVisible(self): return True
        def x(self): return 12
        def y(self): return 34
    result = cocoa.describe(Widget())
    assert result['window_id'] == 123
    assert calls[:2] == [(800, 'window'), (900, 'windowNumber')]
