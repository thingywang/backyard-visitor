"""Bounded external Space/stack observer of the unchanged normal frozen app.

Only own helper and demo PIDs are observed. No input, screenshot, or permissions.
Default invocation detaches; its supervisor owns a 55s deadline and all children.
"""
from __future__ import annotations
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.probe_overlay_visibility import Cocoa, read_json, write_json, stop_owned, intersects
from scripts.probe_background_teaser import fullscreen_checks, owned_helper_identity, frontmost_role
from scripts.profile_bundle_teaser import fingerprints, command


def own_windows(records,pid):
    result=[]
    for order,record in enumerate(records):
        if record.get('kCGWindowOwnerPID')!=pid:continue
        b=record.get('kCGWindowBounds',{})
        result.append({'window_id':record.get('kCGWindowNumber'),'order':order,
            'layer':record.get('kCGWindowLayer'),'alpha':record.get('kCGWindowAlpha'),
            'onscreen':record.get('kCGWindowIsOnscreen'),
            'bounds':{k:b.get(k) for k in ('X','Y','Width','Height')}})
    return result


def size_matches(window,size):
    try:return all(abs(float(window['bounds'][k])-v)<=1 for k,v in zip(('Width','Height'),size))
    except (KeyError,TypeError,ValueError):return False


def checks(row):
    helper=row.get('helper',{});hc=helper.get('cg',{})
    result=fullscreen_checks(helper,row.get('screen',[]))
    result.update(helper_frontmost=row.get('frontmost_role')=='helper',
        helper_active_key=row.get('helper_active') is True and helper.get('key') is True,
        helper_heartbeat_fresh=0<=row.get('helper_age',99)<1,
        helper_normal_level=helper.get('level')==0 and hc.get('layer')==0)
    sizes={'gate':(30,30),'bowl':(62,40)}
    if row.get('pair_ready') or row.get('phase') in {'play','cleanup'}:sizes['cats']=(144,112)
    if row.get('phase')=='play':
        sizes.update(canvas=tuple(row['screen'][2:]),hint=(min(344,max(1,row['screen'][2]-24)),min(56,row['screen'][3])))
    for name,size in sizes.items():
        matching=[w for w in row.get('app_windows',[]) if size_matches(w,size)
            and w.get('onscreen') is True and (w.get('alpha') or 0)>0
            and (w.get('layer') or 0)>0 and w.get('order',999)<hc.get('order',-1)
            and intersects(w.get('bounds',{}),hc.get('bounds',{}))]
        result[name+'_onscreen_above_fullscreen']=len(matching)>=(2 if name=='cats' else 1)
    return result


def outcome(report):
    child=report.get('app',{}); rows=report.get('samples',[])
    play=[r for r in rows if r.get('phase')=='play'];entry=[r for r in rows if r.get('phase')=='entry'];cleanup=[r for r in rows if r.get('phase')=='cleanup']
    if report.get('termination_reason') or report.get('error'):return 'inconclusive'
    if not all(report.get(k) is True for k in ('frozen_identity_verified','helper_identity_verified','bundle_unchanged','sources_unchanged','helper_fullscreen_exit')):return 'inconclusive'
    if report.get('app_exit_code')!=0 or report.get('helper_exit_code')!=0 or child.get('shutdown_verified') is not True:return 'inconclusive'
    if not rows:return 'inconclusive'
    # A valid, sustained native Space baseline with missing companions is a failure.
    environment=('helper_native_fullscreen','helper_on_active_space','helper_covers_selected_screen','helper_frontmost','helper_active_key','helper_heartbeat_fresh','helper_normal_level')
    if not all(all(checks(r)[k] for k in environment) for r in rows):return 'inconclusive'
    if any(not all(checks(r).values()) for r in rows):return 'failed'
    start=child.get('play_started_monotonic');end=child.get('play_ended_monotonic')
    if not isinstance(start,(int,float)) or not isinstance(end,(int,float)):return 'inconclusive'
    times=[start,*[r['monotonic'] for r in play],end]
    return 'complete' if (child.get('state')=='checks_complete' and all(child.get('checks',{}).values())
        and len(play)>=50 and len(entry)>=3 and len(cleanup)>=8
        and max(b-a for a,b in zip(times,times[1:]))<=.8) else 'inconclusive'


class WorkspaceRunLoop:
    """Refresh AppKit workspace notifications in this non-Qt observer process.

    NSRunningApplication's mutable state is cached until the main run loop
    advances. A sleep-only supervisor can otherwise keep reporting the helper
    as frontmost after that helper has already received resign-active events.
    This drains notifications only; it never activates or sends input to apps.
    """
    def __init__(self):
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.mode = ctypes.c_void_p.in_dll(self.cf, 'kCFRunLoopDefaultMode').value
        self.cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
        self.cf.CFRunLoopRunInMode.restype = ctypes.c_int32

    def refresh(self):
        self.cf.CFRunLoopRunInMode(self.mode, .001, False)


def source_hashes():
    paths=list((ROOT/'backyard_visitor').glob('*.py'))+[Path(__file__),ROOT/'scripts/probe_background_teaser.py',ROOT/'scripts/probe_overlay_visibility.py']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def supervise(options):
    from backyard_visitor.desktop import _CoreGraphics
    ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    cocoa=Cocoa();workspace_loop=WorkspaceRunLoop();graphics=_CoreGraphics();app=helper=activation=None
    started=time.monotonic();ready_since=None;activation_at=None;helper_started=None
    app_report=options.output.with_suffix('.app.json')
    report={'state':'running','supervisor_pid':os.getpid(),'started_utc':datetime.now(timezone.utc).isoformat(),
        'samples':[],'preparation':[],'hard_seconds':55,'bundle':str(options.bundle),
        'scope':'Unmodified frozen normal demo entry; native full-screen own helper; external own-PID CG on-screen stacking. Size-based role recognition; no pixels/physical input/NSWindow key state of product claim.'}
    executable=options.bundle/'Contents/MacOS/BackyardVisitor'
    helper_executable=options.helper_app/'Contents/MacOS/BackyardFullscreenHelper'
    before=fingerprints(options.bundle);sources=source_hashes()
    report['bundle_hashes']=before;report['source_hashes']=sources
    report['bundle_LSUIElement']=plistlib.loads((options.bundle/'Contents/Info.plist').read_bytes()).get('LSUIElement')
    try:
        with tempfile.TemporaryDirectory(prefix='frozen-space-',dir=ROOT/'output/v09') as tmp:
            job=Path(tmp);write_json(job/'config.json',{'fullscreen':True})
            environment=dict(os.environ,QT_QPA_PLATFORM='cocoa',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(job))
            app=subprocess.Popen(command(executable,app_report),cwd=ROOT,env=environment)
            report['app_pid']=app.pid
            while app.poll() is None:
                data=read_json(app_report);h=read_json(job/'helper.json');now=time.monotonic()
                if data.get('screen') and helper is None:
                    write_json(job/'controller.json',{'state':'ready','helper_geometry':data['screen']['availableGeometry']})
                    helper=subprocess.Popen([str(helper_executable),str(job)],cwd=ROOT,env=environment)
                    helper_started=now;report['helper_pid']=helper.pid
                if helper is not None and helper.poll() is not None:
                    report['termination_reason']='helper_exited_early';break
                if helper is not None and owned_helper_identity(h,helper.pid,helper_executable):
                    workspace_loop.refresh()
                    role=frontmost_role(cocoa.frontmost_pid(),app.pid,helper.pid)
                    if activation is None and ready_since is None and h.get('fullscreen_requested_at') is None and not h.get('active') and now-helper_started>=.5:
                        activation=subprocess.Popen(['/usr/bin/open','-a',str(options.helper_app)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        activation_at=now;report['activation_request']={'seconds':now-started,'owned_helper_pid':helper.pid}
                    if activation is not None and activation.poll() is None and now-activation_at>3:stop_owned(activation)
                    records=graphics.window_info();hw=dict(h['window']);hw['cg']=next((w for w in own_windows(records,helper.pid) if w['window_id']==hw['window_id']),{})
                    screen=data['screen']['geometry'];valid=all(fullscreen_checks(hw,screen).values()) and role=='helper' and h.get('active') and hw.get('key')
                    row={'monotonic':time.monotonic(),'phase':data.get('phase'),'screen':screen,'frontmost_role':role,
                        'helper_active':h.get('active'),'helper_age':now-h['heartbeat_monotonic'],'helper':hw,
                        'app_windows':own_windows(records,app.pid),
                        'pair_ready':bool(data.get('samples') and data['samples'][-1].get('two_fully_visible'))}
                    if valid and ready_since is None:ready_since=now;report['fullscreen_confirmed_seconds']=now-started
                    if ready_since is not None and now-ready_since>=1:
                        report['samples'].append(row)
                    elif len(report['preparation'])<40:report['preparation'].append(row)
                    if ready_since is not None and role!='helper':report['termination_reason']='foreground_changed_after_confirmation';break
                report.update(app=data,helper=h,elapsed_seconds=now-started)
                write_json(options.output,report)
                if now-started>=50:report['termination_reason']='deadline';break
                if ready_since is None and now-started>12:report['termination_reason']='no_native_fullscreen_baseline';break
                if data.get('heartbeat_monotonic') and now-data['heartbeat_monotonic']>4:report['termination_reason']='app_heartbeat_stale';break
                if h.get('heartbeat_monotonic') and now-h['heartbeat_monotonic']>3:report['termination_reason']='helper_heartbeat_stale';break
                time.sleep(.25)
            stop_owned(app);report['app']=data=read_json(app_report);report['app_exit_code']=app.returncode
            write_json(job/'controller.json',{'state':'checks_complete'})
            if helper is not None:
                try:helper.wait(timeout=2)
                except subprocess.TimeoutExpired:stop_owned(helper)
            h=read_json(job/'helper.json');report['helper']=h;report['helper_exit_code']=helper.returncode if helper else None
            report['frozen_identity_verified']=(data.get('frozen') is True and data.get('pid')==app.pid and Path(data.get('executable','')).resolve()==executable.resolve())
            report['helper_identity_verified']=helper is not None and owned_helper_identity(h,helper.pid,helper_executable)
            report['helper_fullscreen_exit']=h.get('fullscreen_exit_observed') is True
    except Exception:report['error']=traceback.format_exc()
    finally:
        stop_owned(app);stop_owned(helper);stop_owned(activation)
        report.update(bundle_unchanged=before==fingerprints(options.bundle),sources_unchanged=sources==source_hashes(),
            app_handle_exited=app is None or app.poll() is not None,helper_handle_exited=helper is None or helper.poll() is not None,
            elapsed_seconds=time.monotonic()-started,finished_utc=datetime.now(timezone.utc).isoformat())
        # Use the app's final actual lifecycle timestamps, not a stale file phase
        # observed just before a native query crossing the cleanup boundary.
        for row in report['samples']:
            phases=[p for p in report.get('app',{}).get('phase_events',[]) if p['monotonic']<=row['monotonic']]
            if phases:
                actual=phases[-1]['phase'];row['reported_phase']=row['phase']
                # A start timestamp precedes construction/show. Until the app
                # commits its play checkpoint, input-layer exposure is setup.
                # Keep those raw observations, without demanding an uncreated UI.
                row['phase']='play_setup' if row['phase']=='entry' and actual=='play' else actual
        report['state']=outcome(report);report['failed_checks']=sorted({k for r in report['samples'] for k,v in checks(r).items() if not v})
        write_json(options.output,report)
    print(json.dumps({'state':report['state'],'failed_checks':report['failed_checks']}),flush=True)
    return 1 if report['state']=='failed' else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--bundle',type=Path,default=ROOT/'dist/BackyardVisitor.app')
    parser.add_argument('--helper-app',type=Path,default=ROOT/'output/v09/fullscreen-helper/dist/BackyardFullscreenHelper.app')
    parser.add_argument('--supervisor',action='store_true')
    options=parser.parse_args()
    for name in ('output','bundle','helper_app'):setattr(options,name,getattr(options,name).resolve())
    if not options.output.is_relative_to(ROOT/'output') or not options.bundle.is_relative_to(ROOT) or not options.helper_app.is_relative_to(ROOT/'output'):parser.error('Only project-owned paths are allowed')
    if options.supervisor:return supervise(options)
    if any(p.exists() for p in (options.output,options.output.with_suffix('.app.json'),options.output.with_suffix('.log'),options.output.with_suffix('.job.json'))):raise SystemExit('Refusing to overwrite evidence')
    with options.output.with_suffix('.log').open('x') as log:
        child=subprocess.Popen([sys.executable,'-B','-u',__file__,'--supervisor','--output',str(options.output),'--bundle',str(options.bundle),'--helper-app',str(options.helper_app)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    meta={'supervisor_pid':child.pid,'output':str(options.output),'state':'submitted'};write_json(options.output.with_suffix('.job.json'),meta);print(json.dumps(meta))
    return 0

if __name__=='__main__':raise SystemExit(main())
