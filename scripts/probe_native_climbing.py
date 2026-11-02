"""Bounded real Cocoa side-climb check with one self-owned helper window.

The production Controller timers and MacDesktopAdapter run normally. Only route
selection is scripted. No model ticks, visitor positions, states, or animation
clocks are injected. Only this probe's window IDs are selectable; system metadata
reports omit titles and non-probe identities. Default invocation launches an
independent supervisor with a hard deadline and returns immediately.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.probe_overlay_visibility import Cocoa,read_json,write_json
from scripts.probe_native_parkour import client_rect_for_frame
PUBLIC=('state','x','y','facing','target_x','target_y','route_stage','window_edge_id',
        'window_target_id','climb_side_id','climb_phase','jump_start_x','jump_start_y',
        'jump_elapsed','jump_vy','jump_pose_phase','jump_vertical_span',
        'takeoff_remaining','landing_remaining','settle_remaining')


def utc():return datetime.now(timezone.utc).isoformat()


def hashes():
    paths=list((ROOT/'backyard_visitor').glob('*.py'))
    paths.extend(p for p in (ROOT/'backyard_visitor/assets').rglob('*') if p.is_file() and p.suffix in {'.png','.json'})
    paths.append(Path(__file__))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def layout_for(bounds,cat_x):
    left,top,width,height=bounds
    if width<800 or height<600:raise RuntimeError('Available native display is too small for the bounded helper layout')
    width_helper=320.
    if cat_x+72 < left+width/2:
        x=min(left+width-400,max(left+80,cat_x+56))
        desired='left'
    else:
        wall=min(left+width-80,max(left+400,cat_x+88))
        x=wall-width_helper;desired='right'
    return [round(x),round(top+140),round(width_helper),round(height-180)],desired


def helper(job):
    from PyQt6.QtCore import QTimer,Qt
    from PyQt6.QtWidgets import QApplication,QWidget,QLabel
    from backyard_visitor.desktop import _CoreGraphics
    qt=QApplication(['BackyardClimbHelper']);qt.setQuitOnLastWindowClosed(False)
    cocoa=Cocoa();graphics=_CoreGraphics();started=time.monotonic();sequence=0
    window=QWidget(None,Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowDoesNotAcceptFocus)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window.setWindowTitle('后院访客 · 自有窗口攀爬验证')
    window.setStyleSheet('background:#eee9dc;color:#3e5144;')
    label=QLabel('后院访客 · 窗口攀爬验证\n这扇自有测试窗会自动关闭。',window)
    label.setGeometry(18,14,285,64)
    occluder=QWidget(None,Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowDoesNotAcceptFocus)
    occluder.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    occluder.setWindowTitle('后院访客 · 自有遮挡小窗')
    occluder.setStyleSheet('background:#869b88;')
    state={'state':'ready','pid':os.getpid(),'window_id':None,'occluder_id':None,'visible':False,'command_ack':0}
    def pulse():
        nonlocal sequence
        command=read_json(job/'command.json') or {}
        if command.get('seq',0)>sequence:
            sequence=command['seq'];kind=command['kind'];state['command_ack']=sequence
            if kind=='show':
                requested=command['rect'];window.setGeometry(*requested);window.show()
                frame,client=window.frameGeometry(),window.geometry()
                window.setGeometry(*client_rect_for_frame([frame.x(),frame.y(),frame.width(),frame.height()],
                    [client.x(),client.y(),client.width(),client.height()],requested))
                window.raise_()
            elif kind=='occlude':
                occluder.setGeometry(*command['rect']);occluder.show();occluder.raise_()
            elif kind=='close':window.close()
            elif kind=='stop':window.close();occluder.close();state['state']='complete'
        if window.isVisible():
            native=cocoa.describe(window);state['window_id']=native['window_id'];state['native']=native
        records=graphics.window_info()
        if occluder.isVisible():
            state['occluder_id']=cocoa.describe(occluder)['window_id']
        blocker=next((r for r in records if r.get('kCGWindowOwnerPID')==os.getpid() and r.get('kCGWindowNumber')==state['occluder_id']),None)
        state['occluder_visible']=occluder.isVisible()
        state['occluder_bounds']=blocker.get('kCGWindowBounds') if blocker else None
        state['occluder_layer']=blocker.get('kCGWindowLayer') if blocker else None
        state['occluder_onscreen']=bool(blocker and blocker.get('kCGWindowIsOnscreen'))
        ordered=[r.get('kCGWindowNumber') for r in records]
        state['occluder_above_helper']=bool(state['occluder_id'] in ordered and state['window_id'] in ordered and ordered.index(state['occluder_id'])<ordered.index(state['window_id']))
        wid=state['window_id'];own=next((r for r in records if r.get('kCGWindowOwnerPID')==os.getpid() and r.get('kCGWindowNumber')==wid),None)
        frame=window.frameGeometry()
        state.update(visible=window.isVisible(),qt_frame=[frame.x(),frame.y(),frame.width(),frame.height()],
            cg_bounds=own.get('kCGWindowBounds') if own else None,
            cg_layer=own.get('kCGWindowLayer') if own else None,
            cg_onscreen=bool(own and own.get('kCGWindowIsOnscreen')),
            heartbeat=time.monotonic())
        if time.monotonic()-started>68:window.close();occluder.close();state['state']='expired'
        write_json(job/'helper.json',state)
        if state['state'] in {'complete','expired'}:qt.quit()
    timer=QTimer();timer.setInterval(50);timer.timeout.connect(pulse);timer.start();pulse()
    return qt.exec()


def controller(job,output,interrupt="close"):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import MacDesktopAdapter
    from backyard_visitor.parkour_art import select_parkour_pose
    class OwnAdapter(MacDesktopAdapter):
        def __init__(self):
            super().__init__();self.capabilities={name:name=='windows' for name in self.capabilities}
        def windows(self):
            h=read_json(job/'helper.json') or {}
            ids={f'mac-window-{h.get(key)}' for key in ('window_id','occluder_id')}
            return [target for target in super().windows() if target.id in ids]
    qt=QApplication(['BackyardClimbController']);qt.setQuitOnLastWindowClosed(False)
    cocoa=Cocoa();adapter=OwnAdapter()
    c=Controller(qt,demo=True,hidden=True,adapter=adapter,enable_native=True,seed=71,visit_limit=1,state_dir=job/'state')
    # Explicit calm preference suppresses unrelated random decisions. It does
    # not change any visitor coordinates, clocks, or the chosen climbing route.
    c.engine.calm_mode=c.settings['calm_mode']=True
    started=time.monotonic();phase='entry';phase_at=started;sequence=0;running=True
    edge_id=None;side_id=None;target_x=None;wanted_side=None;last_checkpoint=0.;last_capture=set()
    frames=output.parent/'native-climb-frames';frames.mkdir(parents=True,exist_ok=True)
    report={'state':'running','pid':os.getpid(),'started_at':utc(),'phase':phase,'interrupt_kind':interrupt,'events':[],
        'primary_samples':[],'interrupt_samples':[],'native_replans':[],'captures':[],
        'capture_sync_retries':[],
        'capture_sync_policy':'Require current model, QWidget position and CGWindow position within 2 px in the same observer callback. Retain unsynchronized candidates and retry on a later normal timer callback; never advance the model or move a widget for capture.',
        'native_poll_count':0,'widget_position_mismatch':False,'outside_display_seen':False,
        'foreign_selectable_window_seen':False,'clock_acceleration_seen':False,
        'model_dt_injected':False,'visitor_position_or_state_assigned':False,
        'synthetic_geometry':False,'memory_measurement':False,'formal_save_changed':False,
        'fixture':'Normal demo-mode edge arrival, calm preference, one visible real own helper. Routes requested only after production MacDesktopAdapter polling returns its actual top and side.',
        'limitations':'Source Cocoa, own ordinary helper and optional own 120x40 foreground occluder only; adapter retains actual native ordering for these own windows. No unrelated-window acceptance, arbitrary third-party/full-screen, physical input, screenshot compositing or memory acceptance.'}
    def event(name,**fields):
        row={'event':name,'elapsed_seconds':round(time.monotonic()-started,4),**fields};report['events'].append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    def transition(name):
        nonlocal phase,phase_at
        phase=name;phase_at=time.monotonic();report['phase']=phase;event(name)
    def command(kind,**fields):
        nonlocal sequence
        sequence+=1;write_json(job/'command.json',{'seq':sequence,'kind':kind,**fields});event('command',kind=kind,seq=sequence)
    def snap():
        v=c.engine.visitors.get('amber')
        if v is None:return None
        return {**{name:getattr(v,name) for name in PUBLIC},'phase':phase,
            'real_seconds':round(time.monotonic()-started,5),'model_seconds':c.engine._time}
    def geometry_sides():
        return {key:{'edge_id':s.edge_id,'wall_x':s.wall_x,'top':s.top,'bottom':s.bottom,'facing':s.facing}
                for key,s in c.engine.window_climb_sides.items()}
    original_result=c._native_result
    def observed_result(result):
        before=snap();old=geometry_sides();original_result(result);after=snap()
        report['native_poll_count']+=1;new=geometry_sides()
        if before and after and old!=new:
            report['native_replans'].append({'before':before,'after':after,'old_sides':old,'new_sides':new,
                'position_unchanged':abs(before['x']-after['x'])<1e-6 and abs(before['y']-after['y'])<1e-6})
    c._native_result=observed_result
    def checkpoint(force=False):
        nonlocal last_checkpoint
        now=time.monotonic()
        if force or now-last_checkpoint>.4:
            report.update(heartbeat=now,elapsed_seconds=now-started,live=snap(),helper=read_json(job/'helper.json'))
            write_json(job/'controller.json',report);last_checkpoint=now
    def settled(v,edge):
        return (v is not None and v.state=='idle' and v.window_edge_id==edge and not v.route_stage
                and v.landing_remaining<=0 and v.settle_remaining<=0)
    def capture(label):
        window=c.cat_windows.get('amber');v=c.engine.visitors.get('amber')
        if window is None or v is None:return False
        current=snap()
        native=cocoa.describe(window);records=adapter._graphics().window_info()
        own=next((r for r in records if r.get('kCGWindowOwnerPID')==os.getpid() and r.get('kCGWindowNumber')==native['window_id']),None)
        bounds=own.get('kCGWindowBounds') if own else None
        matches=bool(bounds and abs(bounds['X']-round(current['x']))<=2 and abs(bounds['Y']-round(current['y']))<=2)
        qt_matches=abs(window.x()-round(current['x']))<=2 and abs(window.y()-round(current['y']))<=2
        if not matches or not qt_matches:
            report['capture_sync_retries'].append({'label':label,'model':current,
                'qt_position':[window.x(),window.y()],'cg_bounds':bounds,
                'cg_matches':matches,'qt_matches':qt_matches})
            return False
        full=c.screen.geometry()
        inside=bool(bounds and full.left()<=bounds['X'] and full.top()<=bounds['Y']
            and bounds['X']+bounds['Width']<=full.x()+full.width()+1
            and bounds['Y']+bounds['Height']<=full.y()+full.height()+1)
        helper_id=(read_json(job/'helper.json') or {}).get('window_id')
        cat_order=next((i for i,r in enumerate(records) if r.get('kCGWindowNumber')==native['window_id']),None)
        helper_order=next((i for i,r in enumerate(records) if r.get('kCGWindowNumber')==helper_id),None)
        path=frames/(label+'.png');image=window.grab()
        if image.isNull() or not image.save(str(path),'PNG'):raise RuntimeError('Own CatWindow capture failed')
        report['captures'].append({'label':label,'path':str(path),'model':current,
            'native':native,'cg_bounds':bounds,'geometry_matches':matches,'qt_geometry_matches':qt_matches,'inside_display':inside,
            'above_helper':cat_order is not None and helper_order is not None and cat_order<helper_order})
        return True
    def request_route(name):
        v=c.engine.visitors['amber'];before=v.x,v.y
        if not c.engine._move_to_window(v,edge_id,target_x):raise RuntimeError('Actual native side route was refused')
        if (v.x,v.y)!=before:raise RuntimeError('Route request moved the cat instantly')
        c.changed();transition(name)
    def checks():
        rows=report['primary_samples'];climbs=[r for r in rows if r['state']=='climbing']
        slots={min(1,int(r['climb_phase']*2)) for r in climbs}
        hops=[r for r in rows if r['state']=='jumping' and r['window_target_id']==edge_id]
        accepted=report.get('accepted_side',{})
        contact=88 if accepted.get('facing')==-1 else 56
        snapshots=[r for r in report['captures'] if r['label'].startswith('primary-')]
        return {'native_side_from_actual_helper':bool(accepted) and report.get('helper_geometry_match',False),
            'ordinary_helper_layer':report.get('helper_layer_zero',False),
            'normal_entry_observed':report.get('entry_observed',False),
            'climb_two_phases':slots=={0,1},
            'climb_wall_contact':bool(climbs) and all(abs(r['x']+contact-accepted.get('wall_x',float('inf')))<1e-6 for r in climbs),
            'short_mantle':bool(hops) and all(abs(r['target_x']-r['jump_start_x'])<=64+1e-6 for r in hops),
            'reached_actual_window_top':report.get('primary_landed',False),
            'native_pose_capture_coverage':{r['label'] for r in snapshots}=={'primary-climb-0','primary-climb-1','primary-jump_up-0','primary-jump_up-1','primary-jump_down-0','primary-jump_down-1','primary-landed'},
            'own_native_cat_visible_aligned_and_in_bounds':bool(snapshots) and all(r['native']['native_visible'] and r['geometry_matches'] and r['qt_geometry_matches'] and r['inside_display'] and r['above_helper'] for r in snapshots),
            'interrupt_native_condition_verified':report.get('interrupt_native_verified',False),
            'interrupted_side_replan_continuous':bool(report.get('closure_replan')) and report['closure_replan']['position_unchanged'],
            'returned_to_floor_after_interrupt':report.get('returned_to_floor',False),
            'only_helper_selectable':not report['foreign_selectable_window_seen'],
            'normal_model_clock':not report['clock_acceleration_seen'] and not report['model_dt_injected'],
            'native_polls_observed':report['native_poll_count']>=3,
            'no_missing_art':not c.art.parkour_load_error,
            'controller_clean_shutdown':report.get('shutdown_verified',False)}
    def finish(error=None):
        nonlocal running
        if not running:return
        running=False;timer.stop()
        if error:report['error']=str(error)
        try:
            c.shutdown()
            report['shutdown_verified']=c._quitting and not c._workers and c.pool.activeThreadCount()==0 and not any(t.isActive() for t in (c.timer,c.native_timer,c.save_timer,c.recycle_timer)) and not any(w.isVisible() for w in [c.gate,c.bowl,c.bin,*c.cat_windows.values()])
        except Exception as e:report['shutdown_error']=str(e)
        command('stop');report['checks']=checks()
        report['state']='checks_complete' if error is None and all(report['checks'].values()) else 'inconclusive'
        checkpoint(True);qt.exit(0 if report['state']=='checks_complete' else 1)
    timer=QTimer();timer.setInterval(40)
    def advance():
        nonlocal edge_id,side_id,target_x,wanted_side
        if not running:return
        try:
            now=time.monotonic();v=c.engine.visitors.get('amber');h=read_json(job/'helper.json') or {}
            if now-started>63 or now-phase_at>(30 if phase=='entry' else 18):raise RuntimeError('Native stage deadline: '+phase)
            report['clock_acceleration_seen'] |= c.engine._time>now-started+1
            if v:
                if v.state in {'peeking','entering'}:report['entry_observed']=True
                window=c.cat_windows.get('amber')
                if window:report['widget_position_mismatch'] |= abs(window.x()-round(v.x))>2 or abs(window.y()-round(v.y))>2
            wid=h.get('window_id');base=f'mac-window-{wid}'
            own_bases=[f'mac-window-{h.get(key)}' for key in ('window_id','occluder_id')]
            report['foreign_selectable_window_seen'] |= any(not any(key.startswith(prefix+':top:') for prefix in own_bases) for key in c.engine.window_edges)
            if phase=='entry' and settled(v,None):
                rect,wanted_side=layout_for(c.engine.bounds,v.x);report['requested_helper_rect']=rect
                command('show',rect=rect);c.set_window_paths(True);transition('wait_native_side')
            elif phase=='wait_native_side':
                side=next((s for key,s in c.engine.window_climb_sides.items() if key==base+':side:'+wanted_side),None)
                native=next((w for w in c._windows if w.id==base),None)
                if side and native and h.get('cg_onscreen'):
                    bound=h['cg_bounds'];qt_rect=h['qt_frame']
                    native_rect=(native.rect.x,native.rect.y,native.rect.width,native.rect.height)
                    cg_rect=tuple(bound[k] for k in ('X','Y','Width','Height'))
                    report['helper_geometry_match']=all(abs(a-b)<=2 for a,b in zip(native_rect,cg_rect)) and all(abs(a-b)<=2 for a,b in zip(native_rect,qt_rect))
                    report['helper_layer_zero']=h.get('cg_layer')==0
                    if not report['helper_geometry_match']:raise RuntimeError('Native adapter geometry differs from own observed helper frame')
                    edge_id,side_id=side.edge_id,side.id;target_x=side.landing_x
                    report['accepted_side']={'id':side.id,'edge_id':side.edge_id,'wall_x':side.wall_x,'top':side.top,'bottom':side.bottom,'facing':side.facing}
                    request_route('primary_climb')
            elif phase=='primary_climb' and v:
                report['primary_samples'].append(snap())
                pose=select_parkour_pose(v.state,jump_vy=v.jump_vy,jump_pose_phase=v.jump_pose_phase,jump_vertical_span=v.jump_vertical_span,climb_phase=v.climb_phase)
                if pose and v.takeoff_remaining<=0 and pose not in last_capture:
                    if capture('primary-'+pose[0]+'-'+str(pose[1])):last_capture.add(pose)
                if settled(v,edge_id):
                    report['primary_landed']=abs(v.y+112-report['accepted_side']['top'])<=1
                    if capture('primary-landed'):
                        c.engine._walk(v,v.x,c.engine.floor_y);c.changed();transition('descend_for_interrupt')
            elif phase=='descend_for_interrupt' and settled(v,None):
                request_route('interrupt_climb')
            elif phase=='interrupt_climb' and v:
                report['interrupt_samples'].append(snap())
                if v.state=='climbing' and v.state_age>.25:
                    report['interrupt_requested_at']=snap()
                    if interrupt=='close':command('close')
                    elif interrupt=='move':
                        rect=list(report['requested_helper_rect'])
                        rect[0]+=(-100 if wanted_side=='right' else 100)
                        report['moved_rect']=rect;command('show',rect=rect)
                    else:
                        side=report['accepted_side']
                        rect=[round(side['wall_x']-60),round(side['top']+120),120,40]
                        report['occluder_requested_rect']=rect;command('occlude',rect=rect)
                    transition('wait_closed_side')
            elif phase=='wait_closed_side' and v:
                report['interrupt_samples'].append(snap())
                native=next((w for w in c._windows if w.id==base),None)
                current_side=c.engine.window_climb_sides.get(side_id)
                if interrupt=='close':
                    absent=not h.get('visible',True) and not h.get('cg_onscreen',True) and not c._windows and current_side is None
                elif interrupt=='move':
                    bounds=h.get('cg_bounds') or {}
                    moved=report['moved_rect']
                    absent=bool(native and current_side and bounds and abs(bounds['X']-moved[0])<=2 and abs(native.rect.x-moved[0])<=2 and current_side.wall_x!=report['accepted_side']['wall_x'])
                else:
                    bounds=h.get('occluder_bounds') or {}
                    blocker=next((w for w in c._windows if w.id==f'mac-window-{h.get("occluder_id")}'),None)
                    report['occlusion_evidence']={'helper':h,'adapter_saw_blocker':blocker is not None,'side_still_available':current_side is not None,'cat':snap()}
                    absent=bool(h.get('occluder_visible') and h.get('occluder_onscreen') and h.get('occluder_above_helper') and h.get('occluder_layer')==0 and bounds.get('Height')==40 and blocker and current_side is None)
                if absent:
                    report['interrupt_native_verified']=True
                    replan=next((r for r in reversed(report['native_replans']) if r['old_sides']!=r['new_sides'] and r['before']['phase']=='wait_closed_side' and r['before']['climb_side_id']==side_id),None)
                    if replan:report['closure_replan']=replan
                if absent and settled(v,None) and abs(v.y-c.engine.floor_y)<=1:
                    report['returned_to_floor']=True
                    if capture('floor-after-'+interrupt):finish();return
            checkpoint()
        except Exception as error:traceback.print_exc();finish(error)
    def unhandled(kind,value,tb):traceback.print_exception(kind,value,tb);finish(value)
    sys.excepthook=unhandled
    if not adapter._graphics().display_rects():finish(RuntimeError('No active native display'));return 1
    c.engine.open_gate();c.engine.invite('amber');c.changed()
    timer.timeout.connect(advance);timer.start();checkpoint(True)
    code=qt.exec()
    if running:finish(RuntimeError('Unexpected native event-loop exit'));code=1
    return code


def supervise(options):
    from backyard_visitor.desktop import _CoreGraphics
    import fcntl
    with options.output.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 2
        started=time.monotonic();processes={};before=hashes();graphics=_CoreGraphics()
        displays=graphics.display_rects()
        report={'state':'running','started_at':utc(),'supervisor_pid':os.getpid(),'source_hashes':before,
                'hard_deadline_seconds':72,'active_display_count':len(displays),'termination_reason':None}
        if not displays:
            report.update(state='inconclusive',error='No active native displays; no child GUI processes started')
            write_json(options.output,report);return 1
        with tempfile.TemporaryDirectory(prefix='native-climb-',dir=options.output.parent) as folder:
            job=Path(folder)
            try:
                env=dict(os.environ,QT_QPA_PLATFORM='cocoa',PYTHONDONTWRITEBYTECODE='1')
                for role in ('helper','controller'):
                    processes[role]=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--'+role,str(job),'--output',str(options.output),'--interrupt',options.interrupt],cwd=ROOT,env=env)
                    report[role+'_pid']=processes[role].pid
                while any(p.poll() is None for p in processes.values()):
                    now=time.monotonic()
                    for role in processes:report[role]=read_json(job/(role+'.json')) or {}
                    heartbeat=report.get('controller',{}).get('heartbeat')
                    if now-started>68:report['termination_reason']='hard_deadline';break
                    if heartbeat and now-heartbeat>8:report['termination_reason']='controller_heartbeat_stale';break
                    if processes['controller'].poll() is not None and report['controller'].get('state')!='checks_complete':break
                    if processes['helper'].poll() is not None and report['helper'].get('state')!='complete':break
                    report.update(elapsed_seconds=now-started,checked_at=utc());write_json(options.output,report);time.sleep(.2)
            except Exception as error:report['error']=str(error)
            finally:
                for p in processes.values():
                    if p.poll() is None:p.terminate()
                for role,p in processes.items():
                    try:p.wait(timeout=2)
                    except subprocess.TimeoutExpired:p.kill();p.wait(timeout=1)
                    report[role+'_exit_code']=p.returncode;report[role]=read_json(job/(role+'.json')) or report.get(role,{})
                pids={p.pid for p in processes.values()};remaining=[]
                for _ in range(5):
                    remaining=[{'pid':r.get('kCGWindowOwnerPID'),'window_id':r.get('kCGWindowNumber'),'layer':r.get('kCGWindowLayer')} for r in graphics.window_info() if r.get('kCGWindowOwnerPID') in pids]
                    if not remaining:break
                    time.sleep(.2)
                report['remaining_own_windows']=remaining;report['source_hashes_after']=hashes()
                report['sources_unchanged']=report['source_hashes']==report['source_hashes_after']
                report['state']='complete' if (report.get('controller',{}).get('state')=='checks_complete' and report.get('helper',{}).get('state')=='complete' and all(report.get(role+'_exit_code')==0 for role in processes) and not report['termination_reason'] and report['sources_unchanged'] and not remaining) else 'inconclusive'
                report.update(finished_at=utc(),elapsed_seconds=time.monotonic()-started);write_json(options.output,report)
        print(json.dumps({'state':report['state'],'output':str(options.output),'elapsed_seconds':report['elapsed_seconds']}),flush=True)
        return 0 if report['state']=='complete' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/v09/parkour-q11/native/result.json')
    parser.add_argument('--interrupt',choices=('close','move','occlude'),default='close')
    parser.add_argument('--supervisor',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--helper',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--controller',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args();args.output=args.output.resolve();args.output.parent.mkdir(parents=True,exist_ok=True)
    if sys.platform!='darwin':parser.error('Cocoa native environment required')
    if args.helper:return helper(args.helper)
    if args.controller:return controller(args.controller,args.output,args.interrupt)
    if args.supervisor:return supervise(args)
    with args.output.with_suffix('.log').open('a') as log:
        process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--supervisor','--output',str(args.output),'--interrupt',args.interrupt],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    job={'state':'submitted','supervisor_pid':process.pid,'report':str(args.output),'log':str(args.output.with_suffix('.log')),'submitted_at':utc()}
    write_json(args.output.with_suffix('.job.json'),job);print(json.dumps(job),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
