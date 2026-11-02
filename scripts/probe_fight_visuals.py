"""Bounded Q12 source Cocoa visual and fight UI acceptance with two coat pairs.

Two fresh demo controllers invite amber+mist then onyx+amber and enter naturally.
Actual production FightOverlay grabs sample both exposed tails across time slices.
No production timer is added: only the probe observer has a separate 40ms timer.
The shared overlay must use the Controller's existing 24FPS clock.

Two fresh demo controllers enter naturally. One fight ends after its normal
five seconds; the second opens the single shared smoke menu with a complete
Qt click, then selects one explicitly named cat with a complete QMenu click. Fur is captured and immediately removed through GiftWindow press/release in both cases; cats then finish departing normally.
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
import statistics
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
    files += sorted(p for p in (ROOT/'backyard_visitor/assets').rglob('*')
                    if p.is_file() and p.suffix in {'.png','.json'})
    files += [Path(__file__), ROOT/'scripts/probe_overlay_visibility.py', ROOT/'scripts/check_native_lifecycle.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def interaction_checks(case):
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
        'fur_cleaned_through_qt_click': case.get('fur_click',{}).get('event_types')==complete_click and kinds.count('cleaned')==1
            and all(e.get('probe_fur_click_active') is True for e in events if e['kind']=='cleaned'),
        'fur_model_and_widget_removed': case.get('fur_removed') is True and case.get('fur_widget_removed') is True,
        'fur_not_added_to_collection': case.get('fur_collection_count')==0,
        'no_time_or_position_fixture': case.get('clock_acceleration_seen') is False and case.get('injected_model_dt') is False,
        'no_global_input_or_native_permissions': case.get('global_override_seen') is False and case.get('unexpected_ui_seen') is False,
        'case_source_fingerprint_unchanged': bool(case.get('source_hashes'))
            and case.get('source_hashes')==case.get('source_hashes_after'),
        'clean_shutdown': case.get('shutdown_verified') is True,
    }



def case_checks(case):
    checks=interaction_checks(case)
    frames=[r for r in case.get('captures',[]) if r['kind'] in {'smoke','visual'}]
    counts=3 if case['name']=='click' else 6
    ticks=case.get('production_ticks',[])
    scenes=[s['fight_overlay'] for s in case.get('samples',[]) if 'fight_overlay' in s]
    expected={'ginger','black'} if case['name']=='click' else {'ginger','gray'}
    checks.update({
        'requested_coat_pair_fought':set(case.get('fight_coats',{}).values())==expected,
        'real_render_time_slices':len(frames)>=counts
            and len({r['sha256'] for r in frames})>=counts
            and all(r.get('fight_age',-1)>=0 for r in frames),
        'both_exposed_tails_have_pixels':bool(frames) and all(
            len(r.get('tails',[]))==2 and all(t['visible'] for t in r['tails']) for r in frames),
        'rendered_tail_coat_pixels_match':bool(frames) and all(
            not r.get('art_load_error') and all(t['coat_pixels_match'] for t in r.get('tails',[])) for r in frames),
        'visual_coats_match_participants':bool(frames) and all(set(r.get('coats',[]))==expected for r in frames),
        'same_production_24fps_timer':len(ticks)>=8 and bool(scenes)
            and all(t['fps']==24 and t['interval_ms']==round(1000/24) for t in ticks)
            and all(s.get('fps')==24 and s.get('timer_interval_ms')==round(1000/24) for s in scenes),
        'no_overlay_animation_timer':bool(scenes) and all(s.get('overlay_qtimer_count')==0 for s in scenes),
    })
    return checks


def visible_tail_pixels(pixmap, widget, age, coats):
    """Match real grab pixels against transformed production tail alpha cores.

    exposed_path includes the sprite canvas and its transparent margins. Map
    its pixels back to the existing source tail frame; only opaque source fur
    is expected in the actual overlay grab. No substitute scene is rendered.
    """
    import math
    from PyQt6.QtCore import QPointF
    from backyard_visitor.fight_art import tail_slots
    picture=pixmap.toImage();dpr=float(pixmap.devicePixelRatio());rows=[]
    for slot in tail_slots(widget.rect(),age):
        source=widget._fight_art.tail_frame(coats[slot.index]).toImage()
        inverse,invertible=slot.transform.inverted()
        if source.isNull() or not invertible:raise RuntimeError('Tail source/transform unavailable')
        path=slot.exposed_path;bounds=path.boundingRect();target=slot.target
        samples=opaque=0;rgb=[0.,0.,0.];reference=[0.,0.,0.];error=0.
        for py in range(max(0,math.floor(bounds.top()*dpr)),min(picture.height(),math.ceil(bounds.bottom()*dpr))):
            for px in range(max(0,math.floor(bounds.left()*dpr)),min(picture.width(),math.ceil(bounds.right()*dpr))):
                point=QPointF((px+.5)/dpr,(py+.5)/dpr)
                if not path.contains(point):continue
                local=inverse.map(point)
                sx=math.floor((local.x()-target.left())/target.width()*source.width())
                sy=math.floor((local.y()-target.top())/target.height()*source.height())
                if not (0<=sx<source.width() and 0<=sy<source.height()):continue
                expected=source.pixelColor(sx,sy)
                if expected.alpha()<192:continue
                samples+=1;pixel=picture.pixelColor(px,py)
                if pixel.alpha()>=128:
                    opaque+=1
                    actual=(pixel.red(),pixel.green(),pixel.blue())
                    desired=(expected.red(),expected.green(),expected.blue())
                    for i in range(3):rgb[i]+=actual[i];reference[i]+=desired[i]
                    error+=sum(abs(a-b) for a,b in zip(actual,desired))/3
        fraction=opaque/max(1,samples);mae=error/max(1,opaque)
        rows.append({'index':slot.index,'coat':coats[slot.index],
            'logical_exposed_bounds':[bounds.x(),bounds.y(),bounds.width(),bounds.height()],
            'expected_opaque_core_pixels':samples,'rendered_opaque_core_pixels':opaque,
            'opaque_core_fraction':fraction,'core_rgb_mean_absolute_error':mae,
            'mean_rgb':None if not opaque else [round(n/opaque,2) for n in rgb],
            'reference_mean_rgb':None if not opaque else [round(n/opaque,2) for n in reference],
            'visible':samples>=24 and opaque>=24 and fraction>=.8,
            'coat_pixels_match':opaque>=24 and mae<=35})
    return dpr,rows


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
        'fixture':'Two independent seed7 demo controllers; requested amber+mist, then onyx+amber via open_gate/invite before demo_scene(fight). Trust60 prevents unrelated cursor startle. Normal production ticks and natural entry; no visitor position/state or model time assignment.',
        'limitations':'Source Cocoa, deterministic guided fight with two explicit coat pairs. Tail visibility comes from actual grab pixels at the transformed opaque cores of production tail source frames, with DPR mapping and coat-color comparison, not path count. Controller tick cadence is observed; the probe never adds a product timer. QWidget.grab is own rendered content, not final desktop-compositor screenshot. Shared smoke and its explicit two-cat menu receive full Qt press/release pairs. QMenu may become key; cat/smoke/fur windows must remain non-key. Qt sendEvent is not OS hit-testing or physical input.'}
    context={'c':None,'case':None,'stage':'setup','index':0,'case_started':0.,'fight_at':None,'fight_model':None,'menu_widget':None,'last_production_tick':None,'own_fur_click':False}
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
        report['state']='checks_complete' if error is None and len(report['cases'])==2 and all(all(x['checks'].values()) for x in report['cases']) else 'inconclusive' if report.get('inconclusive_reason') else 'failed'
        checkpoint(True);qt.exit(0 if report['state']=='checks_complete' else 1)
    def capture(case, widget, kind, label):
        native=cocoa.describe(widget)
        pixmap=widget.grab();path=frames/(case['name']+'-'+label+'.png')
        if pixmap.isNull() or not pixmap.save(str(path),'PNG'):raise RuntimeError('Own capture failed')
        row={'kind':kind,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'native':native,'elapsed_seconds':time.monotonic()-context['case_started']}
        if kind in {'smoke','visual'}:
            coats,age=widget.visual_state()
            dpr,tails=visible_tail_pixels(pixmap,widget,age,coats)
            row.update(coats=list(coats),fight_age=age,dpr=dpr,tails=tails,art_load_error=widget._fight_art.load_error)
        case['captures'].append(row)
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
              'production_ticks':[],'visual_slices':[],'coexisting_cat_snapshot_available':False,'clock_acceleration_seen':False,'injected_model_dt':False,'global_override_seen':False,'unexpected_ui_seen':False}
        context.update(c=c,case=case,stage='entry',case_started=time.monotonic(),fight_at=None,fight_model=None,menu_widget=None,last_production_tick=None,own_fur_click=False)
        case['source_hashes']=hashes()
        report['cases'].append(case)
        original=c.engine.drain_events
        def observe_events():
            rows=original()
            for row in rows:case['events'].append({'kind':row['kind'],'cat_id':row.get('cat_id'),
                'elapsed_seconds':time.monotonic()-context['case_started'],'model_seconds':c.engine._time,
                'probe_fur_click_active':context['own_fur_click']})
            return rows
        c.engine.drain_events=observe_events
        for cat in c.engine.cats.values():cat.trust=60
        case['trust_before']=trust(c)
        requested=('amber','mist') if name=='natural' else ('onyx','amber')
        c.engine.open_gate()
        for cid in requested:
            if c.engine.invite(cid)!=cid:raise RuntimeError('Requested public visitor id could not enter: '+cid)
        if not c.engine.demo_scene('fight'):raise RuntimeError('Guided fight was rejected')
        case['original_ids']=list(c.engine.visitors)
        if set(case['original_ids'])!=set(requested):raise RuntimeError('Unexpected participants')
        def observe_production_tick():
            now=time.monotonic();previous=context['last_production_tick'];context['last_production_tick']=now
            if c.engine._fight_pair:
                case['production_ticks'].append({'real_seconds':now-context['case_started'],
                    'model_seconds':c.engine._time,'fps':c.current_fps,'interval_ms':c.timer.interval(),
                    'elapsed_since_tick':None if previous is None else now-previous})
        c.timer.timeout.connect(observe_production_tick)
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
                    'fps':c.current_fps,'timer_interval_ms':c.timer.interval(),
                    'overlay_qtimer_count':len(overlay.findChildren(QTimer)),
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
                case['fight_coats']={cid:c.engine.cats[cid].coat for cid in fighting}
                case['expected_menu_labels']={cid:c.engine.cats[cid].display_name for cid in fighting}
                capture(case,c.fight_overlay,'smoke','shared-smoke')
                case['visual_slices'].append(0.)
                print(json.dumps({'event':'fight_visible','case':case['name'],'elapsed':elapsed}),flush=True)
            elif context['stage'] in {'fighting','menu'}:
                schedule=(.6,1.2) if case['name']=='click' else (.6,1.2,2.4,3.6,4.5)
                for threshold in schedule:
                    if (c.fight_overlay is not None and c.engine._fight_pair
                            and now-context['fight_at']>=threshold and threshold not in case['visual_slices']):
                        capture(case,c.fight_overlay,'visual','slice-'+str(threshold).replace('.','_'))
                        case['visual_slices'].append(threshold)
                if context['stage']=='fighting' and case['name']=='click' and now-context['fight_at']>=1.6 and not case.get('smoke_click'):
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
                    case['fur_id']=fur[0].id
                    fur_window=c.gift_windows.get(case['fur_id'])
                    if fur_window is None:
                        report['inconclusive_reason']='Fur disappeared before the probe could capture and click it'
                        raise RuntimeError(report['inconclusive_reason'])
                    capture(case,fur_window,'fur','fur')
                    if (case['fur_id'] not in c.gift_windows
                            or not any(g.id==case['fur_id'] for g in c.engine.gifts)
                            or any(e['kind']=='cleaned' for e in case['events'])):
                        report['inconclusive_reason']='Fur was cleaned before the probe-owned click; external cleaning is not accepted'
                        raise RuntimeError(report['inconclusive_reason'])
                    # Full Qt press/release immediately after the evidence grab.
                    # The event observer tags only this call's synchronous
                    # cleaned event; a prior external click cannot pass.
                    context['own_fur_click']=True
                    try:case['fur_click']=click(fur_window)
                    finally:context['own_fur_click']=False
                    context['stage']='departing'
            elif context['stage']=='departing' and not any(cid in visitors for cid in case['original_ids']):
                case['original_cats_absent']=all(cid not in c.cat_windows for cid in case['original_ids'])
                c.close_gate();context['stage']='cleaned'
            elif context['stage']=='cleaned':
                case['fur_removed']=all(g.id!=case['fur_id'] for g in c.engine.gifts)
                case['fur_widget_removed']=case['fur_id'] not in c.gift_windows
                case['fur_collection_count']=c.engine.collected_count('fur');case['trust_after']=trust(c)
                case['shutdown_verified']=cleanup(c);context['c']=None
                case['source_hashes_after']=hashes()
                intervals=[x['elapsed_since_tick'] for x in case['production_ticks'] if x['elapsed_since_tick'] is not None]
                case['observed_tick_seconds']={'count':len(intervals),'median':statistics.median(intervals) if intervals else None,'maximum':max(intervals) if intervals else None}
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
        report={'schema_version':3,'state':'running','started_utc':datetime.now(timezone.utc).isoformat(),
                'supervisor_pid':os.getpid(),'source_hashes':before,'hard_deadline_seconds':90,'termination_reason':None}
        try:
            from backyard_visitor.desktop import _CoreGraphics
            native=_CoreGraphics()
            report['active_display_count']=len(native.display_rects())
            if not report['active_display_count']:raise RuntimeError('No active display; no GUI child started')
            with tempfile.TemporaryDirectory(prefix='fight-visuals-',dir=options.output.parent) as job:
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
                remaining=[]
                for _ in range(5):
                    remaining=[{'window_id':r.get('kCGWindowNumber'),'layer':r.get('kCGWindowLayer')} for r in native.window_info() if r.get('kCGWindowOwnerPID')==process.pid]
                    if not remaining:break
                    time.sleep(.2)
                report['remaining_own_windows']=remaining
                report['state']='complete' if report['child'].get('state')=='checks_complete' and process.returncode==0 and not report['termination_reason'] and report['sources_unchanged'] and not remaining else 'inconclusive' if report['child'].get('state')=='inconclusive' else 'failed'
        except Exception:report.update(state='failed',error=traceback.format_exc())
        finally:
            stop_owned(process);report.update(elapsed_seconds=time.monotonic()-started,finished_utc=datetime.now(timezone.utc).isoformat());write_json(options.output,report)
        print(json.dumps({'state':report['state'],'elapsed_seconds':report['elapsed_seconds']}),flush=True)
        return 0 if report['state']=='complete' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/v09/fight-visuals/native/result.json')
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
