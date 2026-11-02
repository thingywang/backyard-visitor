"""Q20 owner/compare-before-write contracts; never access the real wallpaper.

The JavaScript cases shadow every ObjC symbol with in-memory fakes. osascript
is only a JS interpreter there: no AppKit import or native setter is executed.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from backyard_visitor.desktop import DesktopAdapter, MacDesktopAdapter, _SET_WALLPAPER

A = 'a1234567-1234-4567-890a-123456789abc'
B = 'b1234567-1234-4567-890a-123456789abc'


def completed(value):
    return subprocess.CompletedProcess([], 0, value, '')


def snapshot(path, identity=A):
    return {'path':str(path), 'options':{'scaling':1, 'clipping':False},
            'primary_size':[1920,1080], 'display_id':identity}


@pytest.fixture
def files(tmp_path):
    old, new = tmp_path/'original.png', tmp_path/'preset.png'
    old.write_bytes(b'original fixture');new.write_bytes(b'preset fixture')
    return old,new


def test_base_adapter_guard_never_delegates_to_unprotected_write():
    adapter=DesktopAdapter();adapter.set_wallpaper=Mock(return_value=True)
    adapter.restore_wallpaper=Mock(return_value=True)
    assert adapter.capabilities['wallpaper_guarded'] is False
    assert not adapter.set_wallpaper_guarded('/fake',A,snapshot('/old'))
    assert not adapter.restore_wallpaper_guarded(A)
    adapter.set_wallpaper.assert_not_called();adapter.restore_wallpaper.assert_not_called()
    assert MacDesktopAdapter(runner=Mock()).capabilities['wallpaper_guarded'] is True


def test_guard_retains_owner_and_passes_snapshot_as_data(files):
    old,new=files;expected=snapshot(old)
    runner=Mock(side_effect=[completed(json.dumps(expected)),completed('true')])
    adapter=MacDesktopAdapter(runner=runner)
    assert adapter.set_wallpaper_guarded(new,A,expected)
    assert adapter.wallpaper_backup==expected
    argv=runner.call_args.args[0]
    assert argv[5]==A and json.loads(argv[6])==expected
    assert argv[7]==str(new) and json.loads(argv[8])=={'scaling':1,'clipping':False}
    assert str(old) not in argv[4] and str(new) not in argv[4]
    assert 'Application(' not in argv[4]
    assert expected==snapshot(old)  # No caller-owned data mutated.


@pytest.mark.parametrize('change',[
    {'display_id':None}, {'display_id':'123'}, {'display_id':B}, {'path':''},
    {'primary_size':[True,1080]}, {'primary_size':[1920,0]},
    {'primary_size':[1920,float('nan')]}, {'options':{}},
    {'options':{'scaling':True}}, {'options':{'scaling':1,'clipping':0}},
    {'options':{'scaling':1,'fill':[0,0,float('inf'),1]}},
])
def test_invalid_or_wrong_display_guard_never_reads_or_writes(files,change):
    old,new=files;expected={**snapshot(old),**change};runner=Mock()
    adapter=MacDesktopAdapter(runner=runner)
    assert not adapter.set_wallpaper_guarded(new,A,expected)
    assert adapter.wallpaper_backup is None
    runner.assert_not_called()


@pytest.mark.parametrize('operation',['set','restore','guarded_restore'])
def test_retained_owner_cannot_be_rebound_to_equal_size_other_primary(files,operation):
    old,new=files;backup=snapshot(old);runner=Mock(return_value=completed('false'))
    adapter=MacDesktopAdapter(runner=runner);adapter.wallpaper_backup=backup
    if operation=='set':
        assert not adapter.set_wallpaper_guarded(new,B,snapshot(old,B))
        runner.assert_not_called()
    elif operation=='restore':
        assert not adapter.restore_wallpaper()
        assert runner.call_args.args[0][5]==A
    else:
        assert not adapter.restore_wallpaper_guarded(B)
        runner.assert_not_called()
    assert adapter.wallpaper_backup is backup


def test_guarded_restore_forwards_both_owner_and_current_snapshot(files):
    old,new=files;expected=snapshot(new);runner=Mock(return_value=completed('true'))
    adapter=MacDesktopAdapter(runner=runner);adapter.wallpaper_backup=snapshot(old)
    assert adapter.restore_wallpaper_guarded(A,expected)
    argv=runner.call_args.args[0]
    assert argv[5]==A and json.loads(argv[6])==expected and argv[-2]==str(old)


@pytest.mark.parametrize('difference',['path','scaling','clipping','fill'])
def test_legacy_owner_is_not_guessed_from_current_preset_or_different_fit(files,difference):
    old,new=files;legacy={'path':str(old),'options':{'scaling':1,'clipping':False}}
    current=snapshot(old)
    if difference=='path': current['path']=str(new)
    elif difference=='fill': current['options']['fill']=[0,0,0,1]
    elif difference=='scaling': current['options']['scaling']=3
    else: current['options']['clipping']=True
    runner=Mock(return_value=completed(json.dumps(current)))
    adapter=MacDesktopAdapter(runner=runner);adapter.wallpaper_backup=legacy
    assert not adapter.set_wallpaper(new) and not adapter.restore_wallpaper()
    assert adapter.wallpaper_backup is legacy
    assert '手动恢复' in adapter.last_error and '屏幕归属' in adapter.last_error
    assert all('setDesktopImageURL' not in call.args[0][4] for call in runner.call_args_list)


def test_legacy_can_gain_owner_only_after_original_image_and_fit_are_current(files):
    old,new=files;legacy={'path':str(old),'options':{'scaling':1}}
    current=snapshot(old)
    runner=Mock(side_effect=[completed(json.dumps(current)),completed('true')])
    adapter=MacDesktopAdapter(runner=runner);adapter.wallpaper_backup=legacy
    assert adapter.set_wallpaper_guarded(new,A,current)
    assert adapter.wallpaper_backup=={**legacy,'display_id':A}
    assert 'display_id' not in legacy
    assert json.loads(runner.call_args.args[0][6])==current


def test_missing_owner_on_first_snapshot_fails_without_saving_unowned_backup(files):
    old,new=files;value=snapshot(old);value['display_id']=None
    runner=Mock(return_value=completed(json.dumps(value)));adapter=MacDesktopAdapter(runner=runner)
    assert not adapter.set_wallpaper(new)
    assert adapter.wallpaper_backup is None and runner.call_count==1


@pytest.mark.parametrize('identity',[None,'',42,'main-display','a'*36])
def test_unknown_or_malformed_snapshot_identity_is_never_guard_eligible(identity):
    value=snapshot('/fake');value['display_id']=identity
    adapter=MacDesktopAdapter(runner=Mock(return_value=completed(json.dumps(value))))
    current=adapter.current_wallpaper_snapshot()
    if identity is None:
        assert current['display_id'] is None
    else:
        assert current is None
    assert MacDesktopAdapter._guard_snapshot(current,identity) is None


# Execute the exact production setter with native symbols replaced, including
# a primary change after the first read. No real Objective-C operation is called.
JS_FAKE = r'''
(function(config, source) {
    var writes = [], reads = 0;
    function box(value) { return {value:value,isNil:function(){return value===null;}}; }
    function color(values) { return {
        isNil:function(){return false;}, colorUsingColorSpace:function(){return this;},
        redComponent:values[0],greenComponent:values[1],blueComponent:values[2],alphaComponent:values[3]
    }; }
    var a = JSON.parse(JSON.stringify(config.before));
    var b = JSON.parse(JSON.stringify(config.before)); b.display_id=config.other;
    function screen(state, number) { return {state:state,
        deviceDescription:{objectForKey:function(){return box(number);}},
        frame:{size:{width:state.primary_size[0],height:state.primary_size[1]}}}; }
    var sa=screen(a,1), sb=screen(b,2);
    var primary=sa;
    var $=function(v){return box(v);};
    var ObjC={import:function(){},unwrap:function(v){return v && v.value!==undefined?v.value:v;},
        castRefToObject:function(ref){return box(ref.native);}};
    $.NSScreen={get screens(){
        reads++;
        if (config.switchAt===reads) { primary=sb; }
        return {count:1,objectAtIndex:function(){return primary;}};
    }};
    $.CGDisplayCreateUUIDFromDisplayID=function(n){return {native:n===1?a.display_id:b.display_id};};
    $.CFUUIDCreateString=function(_,v){return {native:v.native};};
    $.CFRelease=function(){};
    $.NSWorkspaceDesktopImageScalingKey='scaling';
    $.NSWorkspaceDesktopImageAllowClippingKey='clipping';
    $.NSWorkspaceDesktopImageFillColorKey='fill';
    $.NSColorSpace={deviceRGBColorSpace:{}};
    $.NSColor={colorWithDeviceRedGreenBlueAlpha:function(r,g,b,a){return color([r,g,b,a]);}};
    $.NSMutableDictionary={get dictionary(){return {values:{},setObjectForKey:function(v,k){
        this.values[k]=k==='fill'?[v.redComponent,v.greenComponent,v.blueComponent,v.alphaComponent]:ObjC.unwrap(v);
    }};}};
    $.NSURL={fileURLWithPath:function(path){return {path:path};}};
    $.NSWorkspace={sharedWorkspace:{
        desktopImageURLForScreen:function(s){return {path:s.state.path,isNil:function(){return false;}};},
        desktopImageOptionsForScreen:function(s){return {isNil:function(){return false;},objectForKey:function(k){
            var v=s.state.options[k];return k==='fill'&&v?color(v):box(v===undefined?null:v);
        }};},
        setDesktopImageURLForScreenOptionsError:function(url,s,options){
            writes.push({display_id:s.state.display_id,path:url.path,options:options.values});
            if(config.setterFails){return false;}
            s.state.path=url.path;s.state.options=options.values;
            if(config.badReadback){s.state.options.scaling=3;}
            return true;
        }
    }};
    var fn=new Function('ObjC','$','Ref','argv', source+'\nreturn run(argv);');
    var value=fn(ObjC,$,function(){return {};},
        [config.expectedDisplay,JSON.stringify(config.expected),'/fixture/new.png','{"scaling":1,"clipping":false}']);
    return JSON.stringify({ok:value==='true',writes:writes,reads:reads});
})(CONFIG, SOURCE)
'''


def run_fake_script(config):
    if sys.platform!='darwin':
        pytest.skip('JXA interpreter unavailable; native symbols are mocked even on macOS')
    code=JS_FAKE.replace('CONFIG',json.dumps(config,allow_nan=False)).replace('SOURCE',json.dumps(_SET_WALLPAPER))
    result=subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',code],
                          capture_output=True,text=True,timeout=5,check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('case',[
    'success','wrong_owner','changed_path','changed_options','changed_size',
    'primary_before_write','primary_after_write','bad_readback','setter_fails',
])
def test_same_invocation_native_guard_and_readback(case):
    before=snapshot('/fixture/old.png');expected=copy.deepcopy(before)
    config={'before':before,'expected':expected,'expectedDisplay':A,'other':B}
    if case=='wrong_owner': config['expectedDisplay']=B
    elif case=='changed_path': expected['path']='/fixture/stale.png'
    elif case=='changed_options': expected['options']['clipping']=True
    elif case=='changed_size': expected['primary_size']=[1080,1920]
    elif case=='primary_before_write': config['switchAt']=2
    elif case=='primary_after_write': config['switchAt']=3
    elif case=='bad_readback': config['badReadback']=True
    elif case=='setter_fails': config['setterFails']=True
    result=run_fake_script(config)
    assert result['ok'] is (case=='success')
    if case in ('success','primary_after_write','bad_readback','setter_fails'):
        assert len(result['writes'])==1 and result['writes'][0]['display_id']==A
    else:
        assert result['writes']==[]


def test_corefoundation_ref_contract_without_reading_any_display():
    """A fixed in-memory UUID proves Ref conversion; no NSScreen/NSWorkspace."""
    if sys.platform != 'darwin':
        pytest.skip('CoreFoundation JXA bridge is macOS-only')
    code = r"""
    ObjC.import('ColorSync');ObjC.import('CoreFoundation');
    var uuid=$.CFUUIDCreateFromString(null,$('a1234567-1234-4567-890a-123456789abc'));
    var text=$.CFUUIDCreateString(null,uuid);
    var result={binding:typeof $.CGDisplayCreateUUIDFromDisplayID,
                rawIsNil:typeof uuid.isNil, nil:ObjC.castRefToObject(uuid).isNil(),
                text:ObjC.unwrap(ObjC.castRefToObject(text)).toLowerCase()};
    JSON.stringify(result);
    """
    result=subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',code],
                          capture_output=True,text=True,timeout=5,check=True)
    assert json.loads(result.stdout)=={'binding':'function','rawIsNil':'undefined','nil':False,'text':A}


def test_legacy_binding_rejects_malformed_current_options_even_if_python_values_compare_equal(files):
    old,new=files;legacy={'path':str(old),'options':{'scaling':1,'clipping':False}}
    current=snapshot(old);current['options']['scaling']=True
    runner=Mock(return_value=completed(json.dumps(current)))
    adapter=MacDesktopAdapter(runner=runner);adapter.wallpaper_backup=legacy
    assert not adapter.set_wallpaper(new)
    assert adapter.wallpaper_backup is legacy and runner.call_count==1


@pytest.mark.parametrize('operation',['manual','guarded','restore','guarded_restore'])
def test_durable_original_is_written_before_every_native_setter(files,tmp_path,operation):
    old,new=files;original=snapshot(old);events=[];durable=tmp_path/'wallpaper-recovery.json'
    def writer(backup):
        events.append('persist')
        durable.write_text(json.dumps(backup))
        # The callback receives a deep copy, not the adapter's retained object.
        backup['options']['scaling']=3
        backup['display_id']=B
    def runner(argv,**kwargs):
        if 'setDesktopImageURL' not in argv[4]:
            events.append('read')
            return completed(json.dumps(original))
        events.append('native-set')
        assert json.loads(durable.read_text())==original
        assert adapter.wallpaper_backup==original
        return completed('true')
    adapter=MacDesktopAdapter(runner=runner,wallpaper_backup_writer=writer)
    if operation in ('restore','guarded_restore'):
        adapter.wallpaper_backup=copy.deepcopy(original)
    if operation=='manual':ok=adapter.set_wallpaper(new)
    elif operation=='guarded':ok=adapter.set_wallpaper_guarded(new,A,original)
    elif operation=='restore':ok=adapter.restore_wallpaper()
    else:ok=adapter.restore_wallpaper_guarded(A,snapshot(new))
    assert ok and events[-2:]==['persist','native-set']
    assert adapter.wallpaper_backup==original


@pytest.mark.parametrize('operation',['manual','guarded','restore','guarded_restore'])
@pytest.mark.parametrize('error',[OSError('read-only fixture disk'),RuntimeError('fixture writer unavailable')])
def test_failed_backup_persistence_refuses_native_write_and_retains_original(files,operation,error):
    old,new=files;original=snapshot(old)
    runner=Mock(return_value=completed(json.dumps(original)))
    writer=Mock(side_effect=error)
    adapter=MacDesktopAdapter(runner=runner)
    adapter.wallpaper_backup=original;adapter.wallpaper_backup_writer=writer
    if operation=='manual':ok=adapter.set_wallpaper(new)
    elif operation=='guarded':ok=adapter.set_wallpaper_guarded(new,A,original)
    elif operation=='restore':ok=adapter.restore_wallpaper()
    else:ok=adapter.restore_wallpaper_guarded(A,snapshot(new))
    assert not ok and adapter.wallpaper_backup is original
    assert '原壁纸备份未能持久保存，尚未更改壁纸' in adapter.last_error
    writer.assert_called_once_with(original);runner.assert_not_called()


def test_unvalidated_backup_never_reaches_writer(files):
    old,new=files;writer=Mock();runner=Mock()
    adapter=MacDesktopAdapter(runner=runner,wallpaper_backup_writer=writer)
    adapter.wallpaper_backup=snapshot(old,B)
    assert not adapter.set_wallpaper_guarded(new,A,snapshot(old))
    writer.assert_not_called();runner.assert_not_called()
