"""Wallpaper identity/fit preflight uses a mocked worker, never the real desktop."""
import copy
import json
import subprocess
from unittest.mock import Mock
import pytest
from backyard_visitor.desktop import DesktopAdapter, MacDesktopAdapter


def result(value):
    return subprocess.CompletedProcess([], 0, json.dumps(value), '')


def snapshot():
    return {'path':'/fixture/preset.png', 'options':{'scaling':1, 'clipping':False},
            'primary_size':[1920,1080], 'display_id':'a1234567-1234-4567-890a-123456789abc'}


def test_snapshot_is_read_only_separate_from_backup_and_uses_logical_size():
    value=snapshot();value['options']['fill']=[.1,.2,.3,1.]
    runner=Mock(return_value=result(value));adapter=MacDesktopAdapter(runner=runner)
    backup={'path':'/fixture/old.png','options':{'scaling':3,'clipping':True}}
    adapter.wallpaper_backup=backup
    assert adapter.current_wallpaper_snapshot()==value
    assert adapter.wallpaper_backup is backup
    script=runner.call_args.args[0][4]
    assert 'desktopImageOptionsForScreen' in script and 'primary_size' in script
    assert 'frame.size.width' in script and 'frame.size.height' in script
    assert 'Application(' not in script and 'setDesktopImageURL' not in script
    assert not adapter.icons_enabled and not adapter.recycle_enabled
    assert DesktopAdapter().current_wallpaper_snapshot() is None


@pytest.mark.parametrize('field,bad',[
    ('path',''),('path',12),('options',None),('primary_size',None),
    ('primary_size',[0,1080]),('primary_size',[True,1080]),
    ('primary_size',[1920,float('nan')]),('primary_size',[1920,float('inf')]),
    ('primary_size',[1920]),('primary_size',[1920,1080,2]),
])
def test_incomplete_or_nonfinite_snapshot_is_unknown(field,bad):
    value=snapshot();value[field]=bad
    adapter=MacDesktopAdapter(runner=Mock(return_value=result(value)))
    assert adapter.current_wallpaper_snapshot() is None and adapter.last_error


@pytest.mark.parametrize('options',[
    {}, {'scaling':1,'clipping':None}, {'clipping':False}, {'scaling':True,'clipping':False},
    {'scaling':1,'clipping':0}, {'scaling':1.0,'clipping':False},
    {'scaling':9,'clipping':False}, {'scaling':1,'clipping':False,'fill':[0,0,float('nan'),1]},
])
def test_fit_fields_cannot_be_inferred_from_truthiness_or_missing_metadata(options):
    value=snapshot();value['options']=options
    adapter=MacDesktopAdapter(runner=Mock(return_value=result(value)))
    assert adapter.current_wallpaper_snapshot() is None


@pytest.mark.parametrize('value',[None,[],False,'unexpected'])
def test_no_primary_or_malformed_response_is_unknown(value):
    adapter=MacDesktopAdapter(runner=Mock(return_value=result(value)))
    assert adapter.current_wallpaper_snapshot() is None


def test_other_valid_fit_is_reported_so_controller_can_reject_it():
    value=snapshot();value['options']={'scaling':3,'clipping':True}
    adapter=MacDesktopAdapter(runner=Mock(return_value=result(value)))
    assert adapter.current_wallpaper_snapshot()==value


@pytest.mark.parametrize('error',[OSError('fixture failure'),subprocess.TimeoutExpired('fixture',8)])
def test_worker_failure_does_not_reuse_old_snapshot(error):
    runner=Mock(side_effect=[result(snapshot()),error])
    adapter=MacDesktopAdapter(runner=runner)
    assert adapter.current_wallpaper_snapshot() is not None
    assert adapter.current_wallpaper_snapshot() is None and adapter.last_error


def test_omitted_clipping_uses_documented_appkit_false_default():
    value=snapshot();del value['options']['clipping']
    adapter=MacDesktopAdapter(runner=Mock(return_value=result(value)))
    assert adapter.current_wallpaper_snapshot()==snapshot()
    assert not adapter.last_error
