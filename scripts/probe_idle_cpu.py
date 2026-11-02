"""External main-PID CPU observation for a fixture-held native idle cat.

No memory counters are read. Entry and settling use production real-time ticks.
Only next_action is extended after a verified idle arrival, so this measures a
stationary workload, not the probability of a naturally long stationary visit.
The parent samples CPU; one child heartbeat/state observation per second and a
light tick observer remain included in the measured CPU overhead.
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
from scripts.probe_overlay_visibility import read_json,write_json,stop_owned
from scripts.check_native_lifecycle import watchdog_reason


def hashes():
    files=sorted((ROOT/'backyard_visitor').glob('*.py'))+[Path(__file__),ROOT/'scripts/probe_overlay_visibility.py',ROOT/'scripts/check_native_lifecycle.py']
    files+=sorted((ROOT/'backyard_visitor/assets').glob('*.png'))+sorted((ROOT/'backyard_visitor/assets').glob('*.json'))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def run_child(options):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter
    from scripts.probe_overlay_visibility import Cocoa
    tempfile.tempdir=str(options.child.parent)
    qt=QApplication(['BackyardIdleCPU']);qt.setQuitOnLastWindowClosed(False)
    c=Controller(qt,demo=True,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=71,visit_limit=1)
    cocoa=Cocoa();started=time.monotonic();running=True
    report={'state':'running','stage':'entry','pid':os.getpid(),'qt_platform':qt.platformName(),'idle_samples':[], 'tick_observations':[],
        'fixture':'Normal entry/settling, trust60, visit_limit1. Only next_action is extended after verified idle arrival. No cat positions, states, or dt assigned.',
        'probe_overhead':'One idle state/JSON heartbeat per second and a light observer around real Engine.tick; no psutil or CPU/memory queries in child.'}
    transitions=('takeoff_remaining','landing_remaining','settle_remaining','rise_remaining','turn_remaining','posture_remaining','true_name_remaining','gift_response_remaining')
    def snapshot():
        v=c.engine.visitors.get('amber');w=c.cat_windows.get('amber')
        if v is None or w is None:return None
        return {'monotonic':time.monotonic(),'state':v.state,'x':v.x,'y':v.y,'facing':v.facing,
            'gait_phase':v.gait_phase,'run_phase':v.run_phase,'paint_key':list(c._cat_paint_keys.get('amber',())),
            'transitions':{key:getattr(v,key) for key in transitions},'route_stage':v.route_stage,
            'fps':c.current_fps,'timer_interval_ms':c.timer.interval(),'model_time':c.engine._time,
            'inside_screen':c.screen.availableGeometry().contains(w.geometry()),'visible':w.isVisible(),
            'global_override_none':qt.overrideCursor() is None,'journal_absent':c._journal is None}
    def checkpoint():
        now=time.monotonic();report.update(heartbeat_monotonic=now,elapsed_seconds=now-started);write_json(options.child,report)
    original=c.engine.tick
    def observed_tick(dt):
        if report['stage']=='idle':report['tick_observations'].append({'monotonic':time.monotonic(),'dt':dt})
        return original(dt)
    c.engine.tick=observed_tick
    def finish(error=None):
        nonlocal running
        if not running:return
        running=False;timer.stop()
        if error:report['error']=str(error)
        c.shutdown()
        report['shutdown_verified']=c._quitting and not c._workers and c.pool.activeThreadCount()==0 and not any(t.isActive() for t in (c.timer,c.native_timer,c.recycle_timer,c.save_timer)) and not any(w.isVisible() for w in [c.gate,c.bowl,c.bin,*c.cat_windows.values()])
        report['state']='checks_complete' if error is None and all(report.get('checks',{}).values()) and report['shutdown_verified'] else 'failed'
        checkpoint();qt.exit(0 if report['state']=='checks_complete' else 1)
    def pulse():
        if not running:return
        try:
            now=time.monotonic();row=snapshot()
            if now-started>51:raise RuntimeError('Native entry/idle deadline exceeded')
            if report['stage']=='entry':
                if row and row['state']=='idle' and all(value<=0 for value in row['transitions'].values()) and not row['route_stage'] and row['inside_screen'] and row['visible'] and row['fps']==2:
                    v=c.engine.visitors['amber'];report['old_next_action']=v.next_action
                    v.next_action=max(v.next_action,v.state_age+45)
                    report['fixture_next_action']=v.next_action
                    report['native_at_idle']=cocoa.describe(c.cat_windows['amber'])
                    report['idle_started_monotonic']=time.monotonic();report['stage']='idle'
                    report['idle_samples'].append(snapshot());timer.setInterval(1000)
                    print(json.dumps({'event':'idle_started','fps':c.current_fps,'position':[v.x,v.y]}),flush=True)
            elif report['stage']=='idle':
                if row is None:raise RuntimeError('Idle visitor disappeared')
                report['idle_samples'].append(row)
                if now-report['idle_started_monotonic']>=14:
                    report['idle_ended_monotonic']=now
                    rows=report['idle_samples'];first=rows[0]
                    stable_keys=('state','x','y','facing','gait_phase','run_phase','paint_key','transitions','route_stage')
                    ticks=report['tick_observations']
                    report['observed_tick_hz']=(len(ticks)-1)/(ticks[-1]['monotonic']-ticks[0]['monotonic']) if len(ticks)>1 else None
                    report['checks']={'idle_at_2_fps':len(rows)>=12 and all(r['state']=='idle' and r['fps']==2 and r['timer_interval_ms']==500 for r in rows),
                        'position_and_pose_stable':all(all(r[k]==first[k] for k in stable_keys) for r in rows),
                        'visible_inside_screen':all(r['inside_screen'] and r['visible'] for r in rows),
                        'no_journal_or_global_cursor_override':all(r['journal_absent'] and r['global_override_none'] for r in rows),
                        'real_controller_ticks_observed':len(ticks)>=24 and 1.8<=report['observed_tick_hz']<=2.2,
                        'real_14_second_interval':14<=now-report['idle_started_monotonic']<=16}
                    finish();return
            checkpoint()
        except Exception:finish(traceback.format_exc())
    timer=QTimer();timer.setInterval(100);timer.timeout.connect(pulse)
    c.engine.cats['amber'].trust=60;c.engine.open_gate();c.engine.invite('amber');c.changed()
    timer.start();checkpoint();code=qt.exec()
    if running:finish('Unexpected event loop exit');return 1
    return code


def supervise(options):
    import fcntl
    import psutil
    with options.output.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 2
        start=time.monotonic();before=hashes();process=None
        report={'schema_version':1,'state':'running','supervisor_pid':os.getpid(),'started_utc':datetime.now(timezone.utc).isoformat(),
                'source_hashes':before,'samples':[],'termination_reason':None,'hard_deadline_seconds':58,
                'metric':'100 * delta(main PID user+system CPU seconds) / elapsed real seconds; one full CPU core=100%. No RSS/USS/footprint collected.',
                'scope':'Source Cocoa with native integrations disabled and fixture-held idle. Parent sampling overhead excluded; child 1Hz heartbeat and tick observer overhead included. Not a frozen-build guarantee or natural idle duration probability.'}
        try:
            with tempfile.TemporaryDirectory(prefix='idle-cpu-',dir=options.output.parent) as job:
                child=Path(job)/'child.json'
                process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--child',str(child),'--output',str(options.output)],cwd=ROOT,env=dict(os.environ,QT_QPA_PLATFORM='cocoa',PYTHONDONTWRITEBYTECODE='1'))
                report['child_pid']=process.pid;observed=psutil.Process(process.pid)
                report['child_create_time']=observed.create_time()
                cpu=observed.cpu_times();previous_cpu=cpu.user+cpu.system;previous_time=time.monotonic()
                while process.poll() is None:
                    now=time.monotonic();data=read_json(child);beat=data.get('heartbeat_monotonic')
                    reason=watchdog_reason(now-start,None if beat is None else now-beat,54,heartbeat_timeout=4)
                    if now-previous_time>=1:
                        try:
                            cpu=observed.cpu_times();total=cpu.user+cpu.system;sample_end=time.monotonic()
                            report['samples'].append({'start_monotonic':previous_time,'end_monotonic':sample_end,'elapsed_seconds':sample_end-previous_time,
                                'cpu_seconds':total-previous_cpu,'cpu_percent_one_core':100*(total-previous_cpu)/(sample_end-previous_time)})
                            previous_time=sample_end;previous_cpu=total
                        except psutil.NoSuchProcess:break
                    report.update(child=data,elapsed_seconds=now-start,heartbeat_age_seconds=None if beat is None else now-beat)
                    write_json(options.output,report)
                    if reason:report['termination_reason']=reason;break
                    time.sleep(.2)
                stop_owned(process);report['child']=read_json(child);report['child_exit_code']=process.returncode
                ch=report['child'];lo=ch.get('idle_started_monotonic');hi=ch.get('idle_ended_monotonic')
                valid=[s for s in report['samples'] if lo is not None and hi is not None and lo<=s['start_monotonic']<s['end_monotonic']<=hi]
                report['valid_idle_samples']=valid
                duration=sum(s['elapsed_seconds'] for s in valid)
                report['idle_cpu_summary']={'valid_sample_count':len(valid),'valid_duration_seconds':duration,
                    'mean_cpu_percent_one_core':100*sum(s['cpu_seconds'] for s in valid)/duration if duration else None,
                    'max_cpu_percent_one_core':max((s['cpu_percent_one_core'] for s in valid),default=None)}
                report['source_hashes_after']=hashes();report['sources_unchanged']=before==report['source_hashes_after']
                report['state']='complete' if ch.get('state')=='checks_complete' and process.returncode==0 and len(valid)>=10 and duration>=10 and report['sources_unchanged'] and not report['termination_reason'] else 'failed'
        except Exception:report.update(state='failed',error=traceback.format_exc())
        finally:
            stop_owned(process);report.update(elapsed_seconds=time.monotonic()-start,finished_utc=datetime.now(timezone.utc).isoformat());write_json(options.output,report)
        print(json.dumps({'state':report['state'],'summary':report.get('idle_cpu_summary')}),flush=True)
        return 0 if report['state']=='complete' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/v09/idle-cpu.json')
    parser.add_argument('--child',type=Path,help=argparse.SUPPRESS);parser.add_argument('--supervisor',action='store_true',help=argparse.SUPPRESS)
    options=parser.parse_args();options.output=options.output.resolve()
    if sys.platform!='darwin':parser.error('Cocoa requires macOS')
    options.output.parent.mkdir(parents=True,exist_ok=True)
    if options.child:return run_child(options)
    if options.supervisor:return supervise(options)
    log=options.output.with_suffix('.log')
    with log.open('a') as stream:
        process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--supervisor','--output',str(options.output)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    job={'state':'submitted','supervisor_pid':process.pid,'report':str(options.output),'log':str(log),'submitted_utc':datetime.now(timezone.utc).isoformat()}
    write_json(options.output.with_suffix('.job.json'),job);print(json.dumps(job),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
