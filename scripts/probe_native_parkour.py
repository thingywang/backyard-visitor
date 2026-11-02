"""Bounded Cocoa route acceptance using only this probe's native helper windows.

The Controller's production timers, native worker, geometry converter, Engine,
and CatWindow run unchanged. A narrow adapter white-lists this probe's helper
IDs; deterministic route selection calls _move_to_window, never assigns cat
positions/clocks or injects ticks. No global mouse operations or permissions.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_native_lifecycle import read_json, write_json, watchdog_reason


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def hashes():
    files = sorted((ROOT/'backyard_visitor').glob('*.py'))
    files += sorted((ROOT/'backyard_visitor/assets').glob('*.png'))
    files += sorted((ROOT/'backyard_visitor/assets').glob('*.json'))
    files += [Path(__file__), ROOT/'scripts/check_native_lifecycle.py', ROOT/'scripts/probe_overlay_visibility.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def helper_layout(bounds, cat_x):
    left, top, width, height = bounds
    if width < 700 or height < 540:
        raise ValueError('Selected screen is too small for this fixed two-window acceptance layout')
    ax = min(left+width-652, max(left+32, cat_x-40))
    ay = top+height-200
    return {'A': [round(ax), round(ay), 480, 70],
            'B': [round(ax+220), round(ay-190), 400, 70]}


def client_rect_for_frame(frame, client, target):
    """Position a decorated helper by its desired outer frame, not its content."""
    fx, fy, fw, fh = frame
    cx, cy, cw, ch = client
    tx, ty, tw, th = target
    result = [tx + cx - fx, ty + cy - fy, tw - (fw - cw), th - (fh - ch)]
    if result[2] <= 0 or result[3] <= 0:
        raise ValueError('Helper frame is smaller than its native decorations')
    return [round(value) for value in result]


def dialog_helper_checks(window):
    """Require observed native metadata for the nonmodal, decorated QDialog."""
    return bool(window.get('qt_class') == 'QDialog' and window.get('qt_dialog') is True
        and window.get('modal') is False and window.get('native_level') == 0
        and window.get('cg_layer') == 0 and window.get('native_visible') is True
        and isinstance(window.get('native_style_mask'), int)
        and window['native_style_mask'] & 1)  # NSWindowStyleMaskTitled


def route_checks(rows, destination, target_x):
    charge = [r for r in rows if r['state']=='jumping' and r['takeoff_remaining']>0]
    air = [r for r in rows if r['state']=='jumping' and r['takeoff_remaining']<=0 and r['jump_elapsed']>0]
    landing = [r for r in rows if r['landing_remaining']>0]
    return {'charge_observed': bool(charge), 'air_observed': bool(air),
            'landing_observed': bool(landing),
            'short_horizontal_air_route': bool(air) and all(abs(r['target_x']-r['jump_start_x'])<=64+1e-6 for r in air),
            'vertical_movement_observed': bool(air) and any(abs(r['y']-r['jump_start_y'])>5 for r in air),
            'arrived_at_native_edge': bool(rows) and rows[-1]['window_edge_id']==destination
                and rows[-1]['state']=='idle' and abs(rows[-1]['x']-target_x)<=1,
            'positions_finite': bool(rows) and all(math.isfinite(r[k]) for r in rows for k in ('x','y'))}


def record_transition(report, phase, snapshot):
    if phase in ('follow_moved_B', 'B_closed', 'A_closed') and snapshot is not None:
        report.setdefault('transition_samples', {}).setdefault(phase, []).append(snapshot)


def final_checks(report):
    routes = report.get('routes', {})
    both = all(name in routes and all(routes[name].get('checks',{}).values()) and bool(routes[name].get('checks')) for name in ('A','B'))
    second = routes.get('B',{}).get('samples',[])
    approach = [r for r in second if r.get('route_stage')=='approach']
    replans = report.get('native_replans',[])
    return {'both_native_routes_completed': both,
            'decorated_nonmodal_dialog_observed': dialog_helper_checks(report.get('dialog_helper', {})),
            'second_route_approach_walk_observed': len(approach)>1 and abs(approach[-1]['x']-approach[0]['x'])>10,
            'only_helper_window_targets': report.get('foreign_target_seen') is False and report.get('native_poll_count',0)>0,
            'helper_move_seen_by_native_worker': report.get('moved_rect_matches') is True,
            'moved_support_reached': report.get('moved_support_reached') is True,
            'native_replans_do_not_teleport': bool(replans) and all(r['position_unchanged'] for r in replans),
            'closed_B_absent_and_left_safely': report.get('B_closed_absent') is True and report.get('returned_to_A') is True,
            'closed_A_absent_and_returned_to_floor': report.get('A_closed_absent') is True and report.get('returned_to_floor') is True,
            'cat_widget_tracks_engine': report.get('widget_mismatch_seen') is False,
            'settled_cat_native_geometry_verified': report.get('settled_native_geometry_count',0)>=4,
            'normal_tick_only': report.get('model_dt_injected') is False and report.get('clock_acceleration_seen') is False,
            'no_input_capture_or_journal': report.get('input_capture_seen') is False and report.get('journal_seen') is False,
            'own_windows_not_key_in_samples': report.get('key_window_seen') is False,
            'clean_controller_shutdown': report.get('shutdown_verified') is True}


def helper(job):
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtWidgets import QApplication, QWidget, QDialog, QLabel
    from backyard_visitor.desktop import _CoreGraphics
    import ctypes
    from scripts.probe_overlay_visibility import Cocoa
    qt=QApplication(['Backyard native route helper']); qt.setQuitOnLastWindowClosed(False)
    cocoa=Cocoa(); graphics=_CoreGraphics(); windows={}; ids={}; native={}; seq=0; started=time.monotonic()
    state={'state':'ready','pid':os.getpid(),'windows':{},'command_ack':0,'key_seen':False}
    for name in ('A','B'):
        if name == 'B':
            window=QDialog(None, Qt.WindowType.Dialog|Qt.WindowType.WindowDoesNotAcceptFocus)
            window.setModal(False)
        else:
            window=QWidget(None, Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowDoesNotAcceptFocus)
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.setWindowTitle('后院访客 · 自有跑酷测试窗 '+name)
        window.setStyleSheet('background:#eee6d4;color:#4a5144;')
        label=QLabel('后院访客 · 路径验收 '+name+'（自动移动及关闭）', window)
        label.setGeometry(12,4 if name == 'B' else 15,370,32)
        windows[name]=window
    def set_frame(window, rect):
        frame=window.frameGeometry(); client=window.geometry()
        window.setGeometry(*client_rect_for_frame(
            [frame.x(),frame.y(),frame.width(),frame.height()],
            [client.x(),client.y(),client.width(),client.height()],rect))
    def pulse():
        nonlocal seq
        command=read_json(job/'command.json') or {}
        if command.get('seq',0)>seq:
            seq=command['seq']; kind=command['kind']
            if kind=='show':
                for name,rect in command['layout'].items():
                    windows[name].setGeometry(*rect); windows[name].show()
                    set_frame(windows[name], rect)
            elif kind=='move_B': set_frame(windows['B'], command['rect'])
            elif kind=='close_B': windows['B'].close()
            elif kind=='close_A': windows['A'].close()
            elif kind=='stop':
                for window in windows.values(): window.close()
                state['state']='complete'
            state['command_ack']=seq
        records=graphics.window_info()
        own_records={row.get('kCGWindowNumber'):row for row in records if row.get('kCGWindowOwnerPID')==os.getpid()}
        for name,window in windows.items():
            if window.isVisible():
                details=cocoa.describe(window); ids[name]=details['window_id']
                state['key_seen'] |= details['key']
                row=own_records.get(ids[name],{})
                native[name]={'native_level':details['level'],'native_visible':details['native_visible'],
                    'native_style_mask':int(cocoa.send(cocoa.window(window),'styleMask',ctypes.c_ulong)),
                    'cg_layer':row.get('kCGWindowLayer'),'cg_bounds':row.get('kCGWindowBounds')}
            rect=window.frameGeometry()
            state['windows'][name]={'id':ids.get(name),'visible':window.isVisible(),
                'qt_class':window.metaObject().className(),
                'qt_dialog':isinstance(window,QDialog) and window.windowType()==Qt.WindowType.Dialog,
                'modal':window.isModal() if isinstance(window,QDialog) else False,
                'rect':[rect.x(),rect.y(),rect.width(),rect.height()],**native.get(name,{})}
        state['heartbeat_monotonic']=time.monotonic(); write_json(job/'helper.json',state)
        if state['state']=='complete': qt.quit()
        if time.monotonic()-started>85:
            state['state']='expired'; write_json(job/'helper.json',state); qt.quit()
    timer=QTimer(); timer.setInterval(50); timer.timeout.connect(pulse); timer.start(); pulse()
    return qt.exec()


def controller(job, output):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import MacDesktopAdapter
    from scripts.probe_overlay_visibility import Cocoa
    class OwnAdapter(MacDesktopAdapter):
        def __init__(self):
            super().__init__()
            self.capabilities={key:key=='windows' for key in self.capabilities}
        def windows(self):
            metadata=read_json(job/'helper.json') or {}
            allowed={f"mac-window-{w['id']}" for w in metadata.get('windows',{}).values() if w.get('id')}
            return [w for w in super().windows() if w.id in allowed]
    qt=QApplication(['Backyard native route controller']); qt.setQuitOnLastWindowClosed(False)
    cocoa=Cocoa(); adapter=OwnAdapter()
    c=Controller(qt,demo=True,hidden=True,adapter=adapter,enable_native=True,seed=71,visit_limit=1)
    c.engine.calm_mode=c.settings['calm_mode']=True
    started=time.monotonic(); phase='entry'; phase_at=started; running=True; seq=0; last_write=0.
    layout={}; edge_ids={}; targets={}; frames=output.parent/(output.stem+'-frames'); frames.mkdir(parents=True,exist_ok=True)
    report={'state':'running','pid':os.getpid(),'phase':phase,'events':[],'routes':{},'native_replans':[],
        'transition_samples':{},'native_poll_count':0,'foreign_target_seen':False,'widget_mismatch_seen':False,
        'settled_native_geometry_count':0,'model_dt_injected':False,'clock_acceleration_seen':False,
        'input_capture_seen':False,'journal_seen':False,'key_window_seen':False,'captures':[],
        'fixture':'Natural entry, trust100/adoption, calm mode; helper A is an ordinary QWidget and B a decorated nonmodal QDialog; route requests select only real helper edges. No injected ticks, cat position/clock assignments, or synthetic engine edges.',
        'limitations':'Source Cocoa run with one decorated nonmodal dialog, not every application dialog or modal sheet. Other application windows excluded from selectable targets and occlusion inputs. QWidget.grab is own-window rendering, not screenshot proof of final desktop compositing. No random parkour frequency, frozen app, Space, or permission coverage.'}
    timer=QTimer(); timer.setInterval(40)
    def snap():
        v=c.engine.visitors.get('amber')
        if v is None:return None
        names=('state','x','y','target_x','target_y','jump_start_x','jump_start_y','jump_elapsed','takeoff_remaining',
               'landing_remaining','settle_remaining','route_stage','window_edge_id','window_target_id','takeoff_edge_id')
        return {**{name:getattr(v,name) for name in names},'elapsed_seconds':time.monotonic()-started,'model_seconds':c.engine._time}
    def event(name, **data):
        row={'event':name,'elapsed_seconds':round(time.monotonic()-started,4),**data}
        report['events'].append(row); print(json.dumps(row,ensure_ascii=False),flush=True)
    def change(name):
        nonlocal phase,phase_at
        phase=name;phase_at=time.monotonic();report['phase']=name;event(name)
    def command(kind,**data):
        nonlocal seq
        seq+=1;write_json(job/'command.json',{'seq':seq,'kind':kind,**data});event('helper_command',kind=kind,seq=seq)
    def checkpoint(force=False):
        nonlocal last_write
        now=time.monotonic()
        if force or now-last_write>=1:
            report.update(heartbeat_monotonic=now,elapsed_seconds=now-started,live=snap(),helper=read_json(job/'helper.json'))
            write_json(job/'controller.json',report);last_write=now
    original_result=c._native_result
    def observe_result(result):
        before=snap();old=dict(c.engine.window_edges)
        original_result(result)
        after=snap();report['native_poll_count']+=1
        if old!=c.engine.window_edges and before and after:
            report['native_replans'].append({'elapsed_seconds':time.monotonic()-started,
                'before_position':[before['x'],before['y']],'after_position':[after['x'],after['y']],
                'position_unchanged':abs(before['x']-after['x'])<1e-6 and abs(before['y']-after['y'])<1e-6,
                'old_edges':old,'new_edges':dict(c.engine.window_edges)})
    c._native_result=observe_result
    def capture(label, settled=False):
        v=c.engine.visitors.get('amber');window=c.cat_windows.get('amber')
        if v is None or window is None:return
        native=cocoa.describe(window);report['key_window_seen'] |= native['key']
        records=adapter._graphics().window_info()
        own=next((r for r in records if r.get('kCGWindowOwnerPID')==os.getpid() and r.get('kCGWindowNumber')==native['window_id']),None)
        bounds=own.get('kCGWindowBounds') if own else None
        match=bool(bounds and abs(bounds['X']-round(v.x))<=2 and abs(bounds['Y']-round(v.y))<=2)
        if settled:
            if not match:raise RuntimeError('Settled native CatWindow does not match the Engine position')
            report['settled_native_geometry_count']+=1
        path=frames/(label+'.png');image=window.grab()
        if image.isNull() or not image.save(str(path),'PNG'):raise RuntimeError('Own CatWindow frame capture failed')
        report['captures'].append({'label':label,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'model':snap(),'cat_window_id':native['window_id'],'cg_bounds':bounds,'settled_geometry_match':match})
    def settled(v,edge):
        return v.state=='idle' and v.window_edge_id==edge and v.landing_remaining<=0 and v.settle_remaining<=0 and not v.route_stage
    def request_route(name):
        v=c.engine.visitors['amber'];before=(v.x,v.y)
        if not c.engine._move_to_window(v,edge_ids[name],targets[name]):raise RuntimeError('Native edge route unavailable: '+name)
        if (v.x,v.y)!=before:raise RuntimeError('Route selection changed position')
        report['routes'][name]={'edge_id':edge_ids[name],'target_x':targets[name],'samples':[]}
        c.changed();change('route_'+name)
    def finish(error=None):
        nonlocal running
        if not running:return
        running=False;timer.stop()
        if error:report['error']=str(error)
        try:
            c.shutdown()
            report['shutdown_verified']=c._quitting and not c._workers and c.pool.activeThreadCount()==0 and not any(t.isActive() for t in (c.timer,c.native_timer,c.save_timer,c.recycle_timer)) and not any(w.isVisible() for w in [c.gate,c.bowl,c.bin,*c.cat_windows.values()])
        except Exception as failure:report['shutdown_error']=str(failure)
        command('stop');report['checks']=final_checks(report)
        report['state']='checks_complete' if error is None and all(report['checks'].values()) else 'failed'
        checkpoint(True);qt.exit(0 if report['state']=='checks_complete' else 1)
    def advance():
        nonlocal layout
        if not running:return
        try:
            now=time.monotonic();v=c.engine.visitors.get('amber');h=read_json(job/'helper.json') or {}
            report['input_capture_seen'] |= c._teaser_session is not None or qt.overrideCursor() is not None
            report['journal_seen'] |= c._journal is not None
            report['key_window_seen'] |= h.get('key_seen',False)
            dialog=h.get('windows',{}).get('B',{})
            if dialog.get('visible') and dialog_helper_checks(dialog):
                report['dialog_helper']=dict(dialog)
            report['clock_acceleration_seen'] |= c.engine._time>now-started+1
            allowed={f"mac-window-{w['id']}:top:" for w in h.get('windows',{}).values() if w.get('id')}
            report['foreign_target_seen'] |= any(not any(edge.startswith(p) for p in allowed) for edge in c.engine.window_edges)
            if report['input_capture_seen'] or report['journal_seen'] or report['foreign_target_seen']:raise RuntimeError('Probe scope violation')
            if v:
                w=c.cat_windows.get('amber')
                if w:report['widget_mismatch_seen'] |= abs(w.x()-round(v.x))>1 or abs(w.y()-round(v.y))>1
            record_transition(report, phase, snap())
            if now-phase_at > (35 if phase=='entry' else 18):raise RuntimeError('Stage deadline exceeded: '+phase)
            if phase=='entry' and v and v.state=='idle':
                layout=helper_layout(c.engine.bounds,v.x);report['initial_layout']=layout
                targets.update(A=layout['A'][0]+40,B=layout['B'][0]+160)
                command('show',layout=layout);c.set_window_paths(True);change('wait_edges')
            elif phase=='wait_edges':
                for name in ('A','B'):
                    wid=h.get('windows',{}).get(name,{}).get('id')
                    found=[e for e in c.engine.window_edges if wid and e.startswith(f'mac-window-{wid}:top:')]
                    if len(found)==1:edge_ids[name]=found[0]
                if len(edge_ids)==2:request_route('A')
            elif phase in ('route_A','route_B') and v:
                name=phase[-1];route=report['routes'][name];route['samples'].append(snap())
                if v.state=='jumping' and v.takeoff_remaining<=0 and v.jump_elapsed>0 and not route.get('air_capture'):
                    capture(name+'-air');route['air_capture']=True
                if settled(v,edge_ids[name]):
                    route['checks']=route_checks(route['samples'],edge_ids[name],targets[name]);capture(name+'-landed',True)
                    if not all(route['checks'].values()):raise RuntimeError('Route evidence incomplete: '+name)
                    if name=='A':request_route('B')
                    else:
                        rect=list(layout['B']);rect[0]+=24;rect[1]+=20;report['requested_moved_B_rect']=rect
                        command('move_B',rect=rect);change('follow_moved_B')
            elif phase=='follow_moved_B' and v:
                rect=report['requested_moved_B_rect'];base=edge_ids['B'].split(':top:')[0]
                native=next((w for w in c._windows if w.id==base),None)
                matches=bool(native and max(abs(a-b) for a,b in zip((native.rect.x,native.rect.y,native.rect.width,native.rect.height),rect))<=2)
                report['moved_rect_matches']=matches
                if matches and settled(v,edge_ids['B']) and abs(v.y+112-rect[1])<=2:
                    report['moved_support_reached']=True;capture('B-moved-landed',True)
                    command('close_B');change('B_closed')
            elif phase=='B_closed' and v:
                base=edge_ids['B'].split(':top:')[0]
                absent=all(w.id!=base for w in c._windows) and edge_ids['B'] not in c.engine.window_edges
                report['B_closed_absent']=absent
                if absent and settled(v,edge_ids['A']):
                    report['returned_to_A']=True;capture('A-fallback-landed',True)
                    command('close_A');change('A_closed')
            elif phase=='A_closed' and v:
                absent=not c._windows and not c.engine.window_edges;report['A_closed_absent']=absent
                if absent and settled(v,None) and abs(v.y-c.engine.floor_y)<=1:
                    report['returned_to_floor']=True;capture('floor-return',True);c.close_gate();change('drain')
            elif phase=='drain' and v is None and not c.cat_windows and not c._workers and now-phase_at>1:
                finish();return
            checkpoint()
        except Exception as error:
            traceback.print_exc();finish(error)
    def exception(kind,value,tb):
        traceback.print_exception(kind,value,tb);finish(value)
    sys.excepthook=exception
    c.engine.open_gate();c.engine.invite('amber');c.engine.cats['amber'].trust=100
    if not c.engine.adopt('amber'):raise RuntimeError('Residence fixture rejected')
    c.changed();timer.timeout.connect(advance);timer.start();checkpoint(True)
    code=qt.exec()
    if running:finish(RuntimeError('Unexpected Qt exit'));code=1
    return code


def supervise(options):
    import fcntl
    with options.output.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 2
        start=time.monotonic();processes={};before=hashes()
        report={'schema_version':1,'state':'running','started_utc':utc_now(),'supervisor_pid':os.getpid(),
                'source_hashes':before,'hard_deadline_seconds':90,'termination_reason':None}
        with tempfile.TemporaryDirectory(prefix='backyard-native-parkour-', dir=options.output.parent) as directory:
            job=Path(directory)
            try:
                env=dict(os.environ,QT_QPA_PLATFORM='cocoa',PYTHONDONTWRITEBYTECODE='1')
                for role in ('helper','controller'):
                    processes[role]=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--'+role,str(job),'--output',str(options.output)],cwd=ROOT,env=env)
                    report[role+'_pid']=processes[role].pid
                while processes['controller'].poll() is None or processes['helper'].poll() is None:
                    now=time.monotonic()
                    for role in processes:report[role]=read_json(job/(role+'.json')) or {}
                    heartbeat=report['controller'].get('heartbeat_monotonic')
                    age=now-heartbeat if heartbeat else None
                    reason=watchdog_reason(now-start,age,84,heartbeat_timeout=8)
                    report.update(elapsed_seconds=now-start,checked_utc=utc_now(),heartbeat_age_seconds=age)
                    write_json(options.output,report)
                    if reason:report['termination_reason']=reason;break
                    if processes['controller'].poll() is not None and report['controller'].get('state')!='checks_complete':break
                    if processes['helper'].poll() is not None and report['helper'].get('state')!='complete':break
                    time.sleep(.2)
            except Exception as error:report['error']=str(error)
            finally:
                for process in processes.values():
                    if process.poll() is None:process.terminate()
                cleanup_deadline=time.monotonic()+3
                for process in processes.values():
                    try:process.wait(timeout=max(.01,cleanup_deadline-time.monotonic()))
                    except subprocess.TimeoutExpired:process.kill()
                for role,process in processes.items():
                    report[role+'_exit_code']=process.wait(timeout=2)
                    report[role]=read_json(job/(role+'.json')) or report.get(role,{})
                report['source_hashes_after']=hashes();report['sources_unchanged']=before==report['source_hashes_after']
                report['state']='complete' if (report.get('controller',{}).get('state')=='checks_complete'
                    and report.get('helper',{}).get('state')=='complete' and not report['termination_reason']
                    and report.get('controller_exit_code')==0 and report.get('helper_exit_code')==0 and report['sources_unchanged']) else 'failed'
                report.update(finished_utc=utc_now(),elapsed_seconds=time.monotonic()-start);write_json(options.output,report)
        print(json.dumps({'state':report['state'],'output':str(options.output),'elapsed_seconds':report['elapsed_seconds']}),flush=True)
        return 0 if report['state']=='complete' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/v09/native-parkour.json')
    parser.add_argument('--supervisor',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--helper',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--controller',type=Path,help=argparse.SUPPRESS)
    options=parser.parse_args();options.output=options.output.resolve()
    if sys.platform!='darwin':parser.error('Native Cocoa is required')
    options.output.parent.mkdir(parents=True,exist_ok=True)
    if options.helper:return helper(options.helper)
    if options.controller:return controller(options.controller,options.output)
    if options.supervisor:return supervise(options)
    log=options.output.with_suffix('.log')
    with log.open('a') as stream:
        process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--supervisor','--output',str(options.output)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    job={'state':'submitted','supervisor_pid':process.pid,'report':str(options.output),'log':str(log),'submitted_utc':utc_now()}
    write_json(options.output.with_suffix('.job.json'),job);print(json.dumps(job),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
