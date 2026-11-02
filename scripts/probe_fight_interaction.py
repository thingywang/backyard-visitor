"""Bounded source Cocoa fight UI acceptance; only own widgets receive Qt events.

Two fresh demo controllers enter naturally. One fight ends after its normal
five seconds; the second opens the single shared smoke menu with a complete
Qt click, then selects one explicitly named cat with a complete QMenu click. Fur is removed through GiftWindow press/release in both cases.
No injected model ticks, position assignments, OS input, permissions or RSS.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_overlay_visibility import read_json, write_json, stop_owned
from scripts.check_native_lifecycle import watchdog_reason


def hashes():
    files = sorted((ROOT/'backyard_visitor').glob('*.py'))
    files += sorted((ROOT/'backyard_visitor/assets').glob('*.png'))
    files += sorted((ROOT/'backyard_visitor/assets').glob('*.json'))
    files += [Path(__file__), ROOT/'scripts/probe_overlay_visibility.py', ROOT/'scripts/check_native_lifecycle.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def case_checks(case):
    events = case.get('events', [])
    kinds = [e['kind'] for e in events]
    selected = case.get('selected_id')
    before, after = case.get('trust_before', {}), case.get('trust_after', {})
    deltas = {cid: after.get(cid, -999)-value for cid, value in before.items()}
    click, menu_click = case.get('smoke_click', {}), case.get('menu_click', {})
    intervention = [e for e in events if e['kind']=='intervene']
    is_click = case['name']=='click'
    captures = case.get('captures', [])
    smoke = [r for r in captures if r['kind']=='smoke']
    scene = [s['fight_overlay'] for s in case.get('samples', []) if 'fight_overlay' in s]
    ids = set(case.get('fight_ids', []))
    expected = case.get('expected_menu_labels', {})
    menu = case.get('menu_observation', {})
    entries = menu.get('entries', [])
    menu_valid = (len(entries)==2 and set(expected)==ids
        and {e.get('cat_id') for e in entries}==ids
        and all(e.get('enabled') is True and expected.get(e.get('cat_id'), '') in e.get('text', '')
                and bool(expected.get(e.get('cat_id'))) and '护住' in e.get('text', '') for e in entries)
        and menu.get('native', {}).get('qt_visible') is True
        and menu.get('native', {}).get('native_visible') is True)
    complete_click = ['MouseButtonPress','MouseButtonRelease']
    return {
        'two_cats_fought': len(ids)==2 and 'fight_start' in kinds,
        'one_shared_smoke_native_visible': len(smoke)==1 and bool(scene)
            and all(r['native']['native_visible'] and r['native']['qt_visible'] for r in smoke)
            and all(r.get('visible_count')==1 and set(r.get('cat_ids', []))==ids
                    and r.get('native_visible') and r.get('qt_visible') for r in scene),
        'original_cat_windows_hidden_during_fight': bool(scene)
            and all(set(r.get('cats_hidden', {}))==ids and all(r['cats_hidden'].values()) for r in scene),
        # A QMenu can legitimately become key. Its exception never covers the
        # underlying smoke/cat/fur windows, including while the menu is open.
        'captured_scene_windows_not_key': bool(captures)
            and all(not r['native']['key'] for r in captures if r['kind']!='menu')
            and all(not r.get('key', True) for r in scene)
            and (not is_click or bool(menu.get('scene_native'))
                 and all(not r['key'] for r in menu['scene_native'])),
        'explicit_two_cat_menu': menu_valid if is_click else not menu and not menu_click,
        'natural_five_seconds_or_explicit_ui_click': (
            click.get('event_types')==complete_click and click.get('target')=='shared_fight_overlay'
            and menu_click.get('event_types')==complete_click and menu_click.get('target_cat_id')==selected
            and menu_click.get('action_text')==next((e['text'] for e in entries if e.get('cat_id')==selected), None)
            and menu_click.get('inside_action_geometry') is True
            and menu_valid and len(intervention)==1 and intervention[0]['cat_id']==selected
            and 0<case.get('fight_seconds',99)<3) if is_click else (
            not click and not menu_click and not intervention and 4.8<=case.get('fight_seconds',0)<=7
            and 4.8<=case.get('fight_model_seconds',0)<=5.3),
        'trust_changes_only_for_selected_cat': bool(deltas) and all(delta==(4 if is_click and cid==selected else 0) for cid,delta in deltas.items()),
        'both_fleeing_observed': case.get('both_fleeing_seen') is True,
        'fight_overlay_and_menu_cleared': case.get('overlay_cleared_after_fight') is True
            and case.get('menu_hidden_after_fight') is True,
        'cat_windows_restored_after_fight': set(case.get('restored_cat_windows', {}))==ids
            and len(ids)==2 and all(case['restored_cat_windows'].values()),
        'both_original_cats_departed': case.get('original_cats_absent') is True,
        'one_fur_drop': kinds.count('fur')==1 and case.get('fur_count')==1,
        'fur_cleaned_through_qt_click': case.get('fur_click',{}).get('event_types')==complete_click and kinds.count('cleaned')==1,
        'fur_model_and_widget_removed': case.get('fur_removed') is True and case.get('fur_widget_removed') is True,
        'fur_not_added_to_collection': case.get('fur_collection_count')==0,
        'no_time_or_position_fixture': case.get('clock_acceleration_seen') is False and case.get('injected_model_dt') is False,
        'no_global_input_or_native_permissions': case.get('global_override_seen') is False and case.get('unexpected_ui_seen') is False,
        'case_source_fingerprint_unchanged': bool(case.get('source_hashes'))
            and case.get('source_hashes')==case.get('source_hashes_after'),
        'clean_shutdown': case.get('shutdown_verified') is True,
    }


def run_child(options):
    from PyQt6.QtCore import QEvent, QPointF, QTimer, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter
    from scripts.probe_overlay_visibility import Cocoa
    # Controller(demo=True) owns its own TemporaryDirectory; keep that inside
    # this probe's project-local job, without changing product defaults.
    tempfile.tempdir = str(options.child.parent)
    qt=QApplication(['BackyardFightAcceptance']);qt.setQuitOnLastWindowClosed(False)
    cocoa=Cocoa();started=time.monotonic();last_write=0.;running=True
    report={'state':'running','pid':os.getpid(),'cases':[], 'qt_platform':qt.platformName(),
        'fixture':'Two independent seed7 demo_scene(fight) controllers, trust60 for all cats to avoid unrelated cursor startle. Real production ticks and natural edge entry; no cat position or model time assignments.',
        'limitations':'Source Cocoa, deterministic guided fight. QWidget.grab is own rendered content, not final desktop-compositor screenshot. Shared smoke and its explicit two-cat menu receive full Qt press/release pairs. QMenu may become key; cat/smoke/fur windows must remain non-key. Qt sendEvent is not OS hit-testing or physical input.'}
    context={'c':None,'case':None,'stage':'setup','index':0,'case_started':0.,'fight_at':None,'fight_model':None,'menu_widget':None}
    frames=options.output.parent/(options.output.stem+'-frames');frames.mkdir(parents=True,exist_ok=True)

    def trust(c):return {cid:p.trust for cid,p in c.engine.cats.items()}
    def checkpoint(force=False):
        nonlocal last_write
        now=time.monotonic()
        if force or now-last_write>=.5:
            report.update(heartbeat_monotonic=now,elapsed_seconds=now-started,stage=context['stage'])
            write_json(options.child,report);last_write=now
    def visible(widget):
        if widget is None:return False
        try:return widget.isVisible()
        except RuntimeError:return False  # Qt may have deleted the closed popup.
    def cleanup(c):
        c.shutdown()
        ok=(c._quitting and not c._workers and c.pool.activeThreadCount()==0
            and not any(t.isActive() for t in (c.timer,c.native_timer,c.save_timer,c.recycle_timer))
            and not any(w.isVisible() for w in [c.gate,c.bowl,c.bin,*c.cat_windows.values(),*c.gift_windows.values()])
            and c.fight_overlay is None and not visible(context['menu_widget']))
        c.deleteLater()
        return ok
    def finish(error=None):
        nonlocal running
        if not running:return
        running=False;timer.stop()
        if error:report['error']=str(error)
        if context['c'] is not None:
            try:context['case']['shutdown_verified']=cleanup(context['c'])
            except Exception as e:report['cleanup_error']=str(e)
            context['c']=None
        report['state']='checks_complete' if error is None and len(report['cases'])==2 and all(all(x['checks'].values()) for x in report['cases']) else 'failed'
        checkpoint(True);qt.exit(0 if report['state']=='checks_complete' else 1)
    def capture(case, widget, kind, label):
        native=cocoa.describe(widget)
        pixmap=widget.grab();path=frames/(case['name']+'-'+label+'.png')
        if pixmap.isNull() or not pixmap.save(str(path),'PNG'):raise RuntimeError('Own capture failed')
        case['captures'].append({'kind':kind,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                                 'native':native,'elapsed_seconds':time.monotonic()-context['case_started']})
    def click(widget, point=None):
        local=QPointF(widget.rect().center() if point is None else point);global_pos=QPointF(widget.mapToGlobal(local.toPoint()))
        native_id=cocoa.describe(widget)['window_id']
        types=[]
        for typ,buttons in ((QEvent.Type.MouseButtonPress,Qt.MouseButton.LeftButton),(QEvent.Type.MouseButtonRelease,Qt.MouseButton.NoButton)):
            event=QMouseEvent(typ,local,global_pos,Qt.MouseButton.LeftButton,buttons,Qt.KeyboardModifier.NoModifier)
            qt.sendEvent(widget,event);types.append(typ.name)
        return {'event_types':types,'local':[local.x(),local.y()],'native_id':native_id}
    def setup():
        name=('natural','click')[context['index']]
        c=Controller(qt,demo=True,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=7,visit_limit=2)
        case={'name':name,'events':[],'samples':[],'captures':[],'both_fleeing_seen':False,
              'coexisting_cat_snapshot_available':False,'clock_acceleration_seen':False,'injected_model_dt':False,'global_override_seen':False,'unexpected_ui_seen':False}
        context.update(c=c,case=case,stage='entry',case_started=time.monotonic(),fight_at=None,fight_model=None,menu_widget=None)
        case['source_hashes']=hashes()
        report['cases'].append(case)
        original=c.engine.drain_events
        def observe_events():
            rows=original()
            for row in rows:case['events'].append({'kind':row['kind'],'cat_id':row.get('cat_id'),
                'elapsed_seconds':time.monotonic()-context['case_started'],'model_seconds':c.engine._time})
            return rows
        c.engine.drain_events=observe_events
        for cat in c.engine.cats.values():cat.trust=60
        case['trust_before']=trust(c)
        if not c.engine.demo_scene('fight'):raise RuntimeError('Guided fight was rejected')
        case['original_ids']=list(c.engine.visitors)
        c.changed()
        print(json.dumps({'event':'case_started','case':name,'ids':case['original_ids']}),flush=True)
    def pulse():
        if not running:return
        try:
            if context['stage']=='setup':setup()
            c,case=context['c'],context['case'];now=time.monotonic();elapsed=now-context['case_started']
            if elapsed>43:raise RuntimeError('Case deadline exceeded: '+case['name']+' '+context['stage'])
            visitors=c.engine.visitors
            sample={'elapsed_seconds':elapsed,'model_seconds':c.engine._time,
                    'cats':{cid:{'state':v.state,'x':v.x,'y':v.y,'window_visible':cid in c.cat_windows and c.cat_windows[cid].isVisible()} for cid,v in visitors.items()},
                    'gifts':[(g.id,g.kind) for g in c.engine.gifts]}
            case['samples'].append(sample)
            case['clock_acceleration_seen'] |= c.engine._time>elapsed+1
            case['global_override_seen'] |= qt.overrideCursor() is not None
            case['unexpected_ui_seen'] |= c._journal is not None or c._teaser_session is not None or c.native_timer.isActive()
            if case['global_override_seen'] or case['unexpected_ui_seen']:raise RuntimeError('Scope violation')
            fighting=[cid for cid,v in visitors.items() if v.state=='fighting']
            if len(fighting)==2:
                overlay=c.fight_overlay
                if overlay is None:raise RuntimeError('Fighting has no shared overlay')
                native=cocoa.describe(overlay)
                sample['fight_overlay']={**native,'cat_ids':list(overlay.cat_ids),
                    'visible_count':sum(isinstance(w,type(overlay)) and w.isVisible() for w in qt.topLevelWidgets()),
                    'cats_hidden':{cid:cid in c.cat_windows and not c.cat_windows[cid].isVisible() for cid in fighting}}
            # Optional evidence: do not delay or alter the guided encounter to
            # create an appearance of two fully entered, non-fighting cats.
            if not case['coexisting_cat_snapshot_available'] and len(visitors)==2 and not fighting:
                eligible=all(v.state not in {'peeking','entering','fleeing','leaving'} and cid in c.cat_windows
                    and c.cat_windows[cid].isVisible() and c.screen.geometry().contains(c.cat_windows[cid].geometry())
                    for cid,v in visitors.items())
                if eligible:
                    for cid in visitors:capture(case,c.cat_windows[cid],'cat','before-fight-'+cid)
                    case['coexisting_cat_snapshot_available']=True
                    case['coexisting_cat_sample']=sample
            if context['stage']=='entry' and len(fighting)==2:
                context.update(stage='fighting',fight_at=now,fight_model=c.engine._time)
                case['fight_ids']=fighting;case['selected_id']=fighting[-1]
                case['expected_menu_labels']={cid:c.engine.cats[cid].display_name for cid in fighting}
                capture(case,c.fight_overlay,'smoke','shared-smoke')
                print(json.dumps({'event':'fight_visible','case':case['name'],'elapsed':elapsed}),flush=True)
            elif context['stage'] in {'fighting','menu'}:
                if context['stage']=='fighting' and case['name']=='click' and now-context['fight_at']>=.6 and not case.get('smoke_click'):
                    overlay=c.fight_overlay
                    case['smoke_click']=click(overlay);case['smoke_click']['target']='shared_fight_overlay'
                    context['menu_widget']=overlay._menu
                    context['stage']='menu'
                    if context['menu_widget'] is None:raise RuntimeError('Smoke click did not open the explicit menu')
                elif context['stage']=='menu':
                    overlay=c.fight_overlay;menu=context['menu_widget']
                    if overlay is None or not visible(menu):raise RuntimeError('Fight menu disappeared before selection')
                    actions=[a for a in menu.actions() if not a.isSeparator()]
                    entries=[{'cat_id':a.data(),'text':a.text(),'enabled':a.isEnabled()} for a in actions]
                    case['menu_observation']={'native':cocoa.describe(menu),'entries':entries,
                        'scene_native':[cocoa.describe(overlay),*[cocoa.describe(c.cat_windows[cid]) for cid in case['fight_ids']]]}
                    expected=case['expected_menu_labels']
                    if (len(actions)!=2 or {a.data() for a in actions}!=set(expected)
                            or any(not a.isEnabled() or not expected.get(a.data()) or expected[a.data()] not in a.text() or '护住' not in a.text() for a in actions)):
                        raise RuntimeError('Menu does not offer the two correct display names')
                    capture(case,menu,'menu','choice-menu')
                    selected=case['selected_id'];action=next(a for a in actions if a.data()==selected)
                    geometry=menu.actionGeometry(action);point=geometry.center();text=action.text()
                    if geometry.isEmpty():raise RuntimeError('Selected menu action has no clickable geometry')
                    case['menu_click']=click(menu,point)
                    case['menu_click'].update(target_cat_id=selected,action_text=text,
                                             inside_action_geometry=geometry.contains(point))
                    context['stage']='fighting'
                if not c.engine._fight_pair:
                    case['fight_seconds']=now-context['fight_at'];case['fight_model_seconds']=c.engine._time-context['fight_model']
                    case['both_fleeing_seen']=all(cid in visitors and visitors[cid].state=='fleeing' for cid in case['original_ids'])
                    case['overlay_cleared_after_fight']=c.fight_overlay is None
                    case['menu_hidden_after_fight']=not visible(context['menu_widget'])
                    case['restored_cat_windows']={cid:cid in c.cat_windows and c.cat_windows[cid].isVisible() for cid in case['original_ids']}
                    fur=[g for g in c.engine.gifts if g.kind=='fur'];case['fur_count']=len(fur)
                    if len(fur)!=1:raise RuntimeError('Expected exactly one fight fur')
                    case['fur_id']=fur[0].id;capture(case,c.gift_windows[fur[0].id],'fur','fur')
                    context['stage']='departing'
            elif context['stage']=='departing' and not any(cid in visitors for cid in case['original_ids']):
                case['original_cats_absent']=all(cid not in c.cat_windows for cid in case['original_ids'])
                c.close_gate();case['fur_click']=click(c.gift_windows[case['fur_id']]);context['stage']='cleaned'
            elif context['stage']=='cleaned':
                case['fur_removed']=all(g.id!=case['fur_id'] for g in c.engine.gifts)
                case['fur_widget_removed']=case['fur_id'] not in c.gift_windows
                case['fur_collection_count']=c.engine.collected_count('fur');case['trust_after']=trust(c)
                case['shutdown_verified']=cleanup(c);context['c']=None
                case['source_hashes_after']=hashes()
                case['checks']=case_checks(case)
                if not all(case['checks'].values()):raise RuntimeError('Failed checks: '+','.join(k for k,v in case['checks'].items() if not v))
                print(json.dumps({'event':'case_complete','case':case['name'],'seconds':elapsed,'checks':case['checks']}),flush=True)
                context['index']+=1
                if context['index']==2:finish();return
                context['stage']='setup'
            checkpoint()
        except Exception:finish(traceback.format_exc())
    def exception(kind,value,tb):
        finish(''.join(traceback.format_exception(kind,value,tb)))
    sys.excepthook=exception
    timer=QTimer();timer.setInterval(40);timer.timeout.connect(pulse);timer.start();checkpoint(True)
    code=qt.exec()
    if running:finish('Unexpected event loop exit');return 1
    return code


def supervise(options):
    import fcntl
    with options.output.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 2
        started=time.monotonic();before=hashes();process=None
        report={'schema_version':2,'state':'running','started_utc':datetime.now(timezone.utc).isoformat(),
                'supervisor_pid':os.getpid(),'source_hashes':before,'hard_deadline_seconds':90,'termination_reason':None}
        try:
            with tempfile.TemporaryDirectory(prefix='fight-probe-',dir=options.output.parent) as job:
                child=Path(job)/'child.json'
                process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--child',str(child),'--output',str(options.output)],
                    cwd=ROOT,env=dict(os.environ,QT_QPA_PLATFORM='cocoa',PYTHONDONTWRITEBYTECODE='1'))
                report['child_pid']=process.pid
                while process.poll() is None:
                    data=read_json(child);now=time.monotonic();beat=data.get('heartbeat_monotonic')
                    reason=watchdog_reason(now-started,None if beat is None else now-beat,85,heartbeat_timeout=6)
                    report.update(child=data,elapsed_seconds=now-started,heartbeat_age_seconds=None if beat is None else now-beat)
                    write_json(options.output,report)
                    if reason:report['termination_reason']=reason;break
                    time.sleep(.2)
                stop_owned(process);report['child']=read_json(child);report['child_exit_code']=process.returncode
                report['source_hashes_after']=hashes();report['sources_unchanged']=before==report['source_hashes_after']
                report['state']='complete' if report['child'].get('state')=='checks_complete' and process.returncode==0 and not report['termination_reason'] and report['sources_unchanged'] else 'failed'
        except Exception:report.update(state='failed',error=traceback.format_exc())
        finally:
            stop_owned(process);report.update(elapsed_seconds=time.monotonic()-started,finished_utc=datetime.now(timezone.utc).isoformat());write_json(options.output,report)
        print(json.dumps({'state':report['state'],'elapsed_seconds':report['elapsed_seconds']}),flush=True)
        return 0 if report['state']=='complete' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/v09/fight-shared-interaction.json')
    parser.add_argument('--child',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--supervisor',action='store_true',help=argparse.SUPPRESS)
    options=parser.parse_args();options.output=options.output.resolve()
    if sys.platform!='darwin':parser.error('Cocoa requires macOS')
    if not options.child and not options.supervisor and options.output.exists():
        parser.error('Refusing to overwrite an existing report; choose a fresh --output path')
    options.output.parent.mkdir(parents=True,exist_ok=True)
    if options.child:return run_child(options)
    if options.supervisor:return supervise(options)
    log=options.output.with_suffix('.log')
    with log.open('a') as stream:
        process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--supervisor','--output',str(options.output)],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    job={'state':'submitted','supervisor_pid':process.pid,'report':str(options.output),'log':str(log),'submitted_utc':datetime.now(timezone.utc).isoformat()}
    write_json(options.output.with_suffix('.job.json'),job);print(json.dumps(job),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
