"""Render real Engine jump trajectories with production CatArt, without OS screenshots.

Synthetic edges are selected by _move_to_window after natural edge entry and,
for interwindow cases, an actual first jump. Positions and timers are never
assigned by this probe. Fixed-dt model ticks are logical acceleration, not a
native event-loop or real-time endurance test.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DT = .005
FRAME_NAMES = ('approach', 'charge_start', 'charge_deep', 'air_quarter', 'air_middle',
               'contact', 'compressed', 'recover', 'standing_again', 'finish_walk', 'arrival')


def render_offset(row):
    if row['takeoff_remaining'] > 0:
        return 6 if row['takeoff_edge_id'] else 0
    return 6 if row['window_edge_id'] or row['window_target_id'] else 0


def sample_record(v, elapsed):
    from backyard_visitor.model import LANDING_SECONDS, TAKEOFF_SECONDS
    flight = min(1., v.jump_elapsed/v.jump_duration)
    charge = 1-v.takeoff_remaining/TAKEOFF_SECONDS if v.takeoff_remaining > 0 else None
    landing = 1-v.landing_remaining/LANDING_SECONDS if v.landing_remaining > 0 else None
    phase = ('charge' if charge is not None else 'air' if v.state == 'jumping'
             else 'landing' if landing is not None else 'approach' if v.route_stage == 'approach'
             else 'finish' if v.route_stage == 'finish' else 'rest')
    linear_y = v.jump_start_y+(v.target_y-v.jump_start_y)*flight
    row = {name: getattr(v, name) for name in (
        'state', 'x', 'y', 'age', 'state_age', 'facing', 'jump_elapsed', 'jump_duration',
        'takeoff_remaining', 'landing_remaining', 'takeoff_edge_id',
        'window_edge_id', 'window_target_id', 'gait_phase', 'settle_remaining',
        'rise_remaining', 'turn_remaining', 'ground_speed', 'run_phase',
        'jump_start_x', 'jump_start_y', 'jump_arc', 'target_x', 'target_y',
        'route_stage', 'route_final_x', 'route_final_y', 'route_edge_id')}
    row.update(elapsed_seconds=round(elapsed, 6), phase=phase,
               z_arc_lift=max(0., linear_y-v.y) if phase == 'air' else 0.,
               flight_progress=flight, takeoff_progress=charge, land_progress=landing)
    row['render_offset_y'] = render_offset(row)
    return row


def choose_frames(path):
    approach = [r for r in path if r['phase'] == 'approach']
    approach_half = (approach[0]['x']+approach[-1]['x'])/2 if approach else None
    direction = 1 if path[-1]['x'] >= path[0]['x'] else -1
    rules = {
        'approach': lambda r: r['phase'] == 'approach' and r['ground_speed'] > 0
            and (r['x']-approach_half)*direction >= 0,
        'charge_start': lambda r: r['phase'] == 'charge',
        'charge_deep': lambda r: r['phase'] == 'charge' and r['takeoff_progress'] >= .9,
        'air_quarter': lambda r: r['phase'] == 'air' and r['flight_progress'] >= .25,
        'air_middle': lambda r: r['phase'] == 'air' and r['flight_progress'] >= .5,
        'contact': lambda r: r['phase'] == 'landing',
        'compressed': lambda r: r['phase'] == 'landing' and r['land_progress'] >= .375,
        'recover': lambda r: r['phase'] == 'landing' and r['land_progress'] >= .625,
        'standing_again': lambda r: r['phase'] == 'landing' and r['land_progress'] >= .875,
        'finish_walk': lambda r: r['phase'] == 'finish' and r['ground_speed'] > 0,
        'arrival': lambda r: r['phase'] == 'rest' and r['state'] == 'idle',
    }
    selected = {}
    for i, row in enumerate(path):
        for label, rule in rules.items():
            if label not in selected and rule(row):
                selected[label] = {'label': label, 'trajectory_index': i, **row}
    if set(selected) != set(FRAME_NAMES):
        raise RuntimeError(f'Missing actual model phases: {set(FRAME_NAMES)-set(selected)}')
    return [selected[name] for name in FRAME_NAMES]


def _tick_until(engine, visitor, condition, step=DT, limit=45):
    elapsed = 0.
    while not condition(visitor):
        engine.tick(step)
        elapsed += step
        if visitor.cat_id not in engine.visitors or elapsed > limit:
            raise RuntimeError('Natural setup did not complete within its model-time limit')
    return elapsed


def stage_case(kind, facing):
    from backyard_visitor.model import Engine, SPRITE_WIDTH, WINDOW_JUMP_X
    if kind not in ('floor_to_window', 'window_to_window') or facing not in (-1, 1):
        raise ValueError('Need a supported route and left/right facing')
    engine = Engine(seed=71, demo=True, visit_limit=1)
    engine.set_bounds(0, 0, 1600, 1000)
    engine.open_gate()
    engine.invite('amber')
    v = engine.visitors['amber']
    entry_seconds = _tick_until(engine, v, lambda c: c.state == 'idle', step=.02)
    setup_seconds, source, edges = entry_seconds, None, []
    if kind == 'window_to_window':
        source = 'source'
        # The source ledge must support a real horizontal approach. A 40px
        # standing interval cannot reach a target 300px away under the new
        # short-hop rule. Widen only the synthetic ledge, never move the cat.
        edges.append((source, max(0, v.x-360), min(1600, v.x+360+SPRITE_WIDTH), 650))
        engine.set_window_edges(edges)
        if not engine._move_to_window(v, source, v.x):
            raise RuntimeError('Could not stage first window route')
        setup_seconds += _tick_until(engine, v, lambda c: c.state == 'idle' and c.landing_remaining <= 0)
        if v.window_edge_id != source:
            raise RuntimeError('Source window was not reached by real ticks')
    target_x = min(1436., max(20., v.x+facing*300))
    if (target_x-v.x)*facing <= 0:
        raise RuntimeError('Requested direction is unavailable')
    top = 450 if source else 600
    edges.append(('target', max(0, target_x-20), min(1600, target_x+164), top))
    engine.set_window_edges(edges)
    origin = (v.x, v.y)
    if not engine._move_to_window(v, 'target', target_x):
        raise RuntimeError('Could not stage target window route')
    if (v.x, v.y) != origin or v.route_stage != 'approach':
        raise RuntimeError('Route must begin with a supported approach without teleporting')
    path = [sample_record(v, 0)]
    elapsed = 0.
    while v.route_stage or v.state == 'jumping' or v.landing_remaining > 0:
        engine.tick(DT)
        elapsed += DT
        path.append(sample_record(v, elapsed))
        if (v.cat_id not in engine.visitors or elapsed > 20
                or not all(math.isfinite(n) for n in (v.x, v.y))):
            raise RuntimeError('Approach/jump/finish route did not finish with finite coordinates')
    charge = next((r for r in path if r['phase'] == 'charge'), None)
    contact = next((r for r in path if r['phase'] == 'landing'), None)
    if charge is None or contact is None:
        raise RuntimeError('Route did not produce actual supported takeoff and landing')
    if charge['takeoff_edge_id'] != source:
        raise RuntimeError('Charge lost the real source support')
    if abs(contact['x']-charge['x']) > WINDOW_JUMP_X+1e-6:
        raise RuntimeError('Route violated the maximum horizontal hop distance')
    if (v.window_edge_id != 'target' or abs(v.y+112-top) > 1e-6
            or abs(v.x-target_x) > 1e-6):
        raise RuntimeError('Route missed its final supported destination')
    return {'id': f"{kind}_{'left' if facing < 0 else 'right'}", 'kind': kind,
            'requested_facing': facing, 'bounds': list(engine.bounds), 'edges': edges,
            'source_edge_id': source, 'source_position': list(origin),
            'takeoff_position': [charge['x'],charge['y']],
            'landing_position': [contact['x'],contact['y']],
            'horizontal_jump_distance': abs(contact['x']-charge['x']),
            'maximum_horizontal_jump': WINDOW_JUMP_X,
            'target_position': [target_x,top-112],
            'setup_model_seconds': round(setup_seconds, 6), 'natural_entry_model_seconds': round(entry_seconds, 6),
            'route_model_seconds': round(elapsed, 6), 'tick_seconds': DT,
            'trajectory': path, 'frames': choose_frames(path),
            'setup': 'Public Engine construction/bounds/open/invite then real ticks; _move_to_window selects synthetic routes only. No coordinate/timer writes.'}


def render_pose(art, row):
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QImage, QPainter
    from backyard_visitor.model import RISE_SECONDS, SETTLE_SECONDS
    image = QImage(288, 224, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        art.paint(p, QRectF(0, row['render_offset_y'], 144, 112), row['state'], row['age'], row['facing'], 'ginger',
                  gait_phase=row['gait_phase'], run_phase=row['run_phase'], land_progress=row['land_progress'], takeoff_progress=row['takeoff_progress'],
                  settling=row['settle_remaining'] > 0, turning=row['turn_remaining'] > 0,
                  settle_progress=1-row['settle_remaining']/SETTLE_SECONDS if row['settle_remaining'] > 0 else None,
                  rise_progress=1-row['rise_remaining']/RISE_SECONDS if row['rise_remaining'] > 0 else None)
    finally:
        p.end()
    return image


def fingerprints():
    paths = sorted((ROOT/'backyard_visitor').glob('*.py'))+[Path(__file__).resolve()]
    paths += sorted(p for p in (ROOT/'backyard_visitor/assets').iterdir() if p.suffix in ('.png', '.json'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def render_sheet(cases):
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
    from backyard_visitor.art import CatArt
    art = CatArt()
    # The real staged visitor walked into the desktop first; warm the same
    # production walking frames before the renderer resolves its airborne alias.
    art.frame('walking', 0, -1, gait_phase=0)
    art._load_jumps()
    if len(art.states.get('takeoff', [])) != 3 or len(art.states.get('landing', [])) != 4:
        raise RuntimeError('Accepted jump artwork is not ready; refusing fallback-pose output')
    width, height, sidebar, header = 300, 330, 280, 60
    sheet = QImage(sidebar+width*len(FRAME_NAMES), header+height*len(cases), QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor('#f5f5ee'))
    p = QPainter(sheet)
    try:
        p.setPen(QColor('#30483c')); p.setFont(QFont('Helvetica', 18))
        p.drawText(16, 26, 'ENGINE TRAJECTORY + PRODUCTION CATART | OFFSCREEN QA, NOT AN OS SCREENSHOT')
        p.setFont(QFont('Helvetica', 12))
        p.drawText(16, 47, 'Manual 5ms model ticks; unmodified positions/timers. z = derived arc lift. Synthetic window edges.')
        for ri, case in enumerate(cases):
            y = header+ri*height
            if ri % 2: p.fillRect(QRectF(0,y,sheet.width(),height), QColor('#e9eee6'))
            p.setPen(QColor('#30483c')); p.setFont(QFont('Helvetica',13))
            p.drawText(QRectF(12,y+10,sidebar-24,45), Qt.TextFlag.TextWordWrap, case['id'].replace('_',' '))
            p.setFont(QFont('Helvetica',11))
            p.drawText(12,y+70,f"route {case['route_model_seconds']:.3f}s; dt={DT:g}s")
            p.drawText(12,y+88,f"source: {case['source_edge_id'] or 'floor'}")
            p.drawText(12,y+106,f"hop dx={case['horizontal_jump_distance']:.1f}px; approach / finish walk")
            def point(x, yy): return QPointF(12+x*.16, y+128+yy*.16)
            p.setPen(QPen(QColor('#93a28b'),1)); p.drawRect(QRectF(12,y+128,256,160))
            for _,left,right,top in case['edges']: p.drawLine(point(left,top),point(right,top))
            p.setPen(QPen(QColor('#66857b'),2))
            for before,after in zip(case['trajectory'],case['trajectory'][1:]):
                p.drawLine(point(before['x']+72,before['y']+112),point(after['x']+72,after['y']+112))
            for col,frame in enumerate(case['frames']):
                x=sidebar+col*width
                p.setPen(QColor('#30483c')); p.setFont(QFont('Helvetica',13))
                p.drawText(QRectF(x,y+8,width,23),Qt.AlignmentFlag.AlignCenter,frame['label'])
                p.setFont(QFont('Helvetica',10))
                p.drawText(x+8,y+47,f"t={frame['elapsed_seconds']:.3f}s | {frame['phase']} | facing={frame['facing']:+d}")
                p.drawImage(QRectF(x+6,y+60,288,224),render_pose(art,frame))
                if frame['phase'] != 'air':
                    p.setPen(QPen(QColor('#a2b69b'),1)); ground=y+60+2*(106+frame['render_offset_y'])
                    p.drawLine(QPointF(x+6,ground),QPointF(x+294,ground))
                p.setPen(QColor('#30483c'))
                p.drawText(x+8,y+303,f"x={frame['x']:.1f}  y={frame['y']:.1f}  z={frame['z_arc_lift']:.1f}")
                p.drawText(x+8,y+320,f"jump={frame['jump_elapsed']:.3f}s | offset={frame['render_offset_y']}px")
    finally:
        p.end()
    return sheet


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'output/v07')
    options=parser.parse_args(argv)
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.model import TAKEOFF_SECONDS, LANDING_SECONDS
    qt=QApplication.instance() or QApplication(['jump-preview'])
    started=time.monotonic(); before=fingerprints()
    cases=[stage_case(kind,facing) for kind in ('floor_to_window','window_to_window') for facing in (-1,1)]
    sheet=render_sheet(cases); after=fingerprints()
    if before != after: raise RuntimeError('Sources/assets changed during preview; retry after they settle')
    options.output_dir.mkdir(parents=True,exist_ok=True)
    target=options.output_dir/'jump-preview.png'
    if not sheet.save(str(target)): raise RuntimeError(f'Could not save {target}')
    report={'schema_version':2,'state':'complete','created_at':datetime.now(timezone.utc).isoformat(),
            'scope':'Offscreen production CatArt.paint on actual Engine trajectories; synthetic edges, no OS screenshot/native window access.',
            'timing':{'logical_acceleration':True,'real_seconds':round(time.monotonic()-started,6),
                      'model_tick_seconds':DT,'native_event_loop_endurance':False},
            'coordinate_contract':{'x_y':'Actual Engine logical desktop top-left coordinates.',
                                   'z_arc_lift':'Derived 2D arc height above source-to-target interpolation; not independent Engine z.',
                                   'sprite_offset':'Charge uses source takeoff_edge_id; air uses existing window edge/target +6px rule.',
                                   'source_vs_takeoff':'source_position starts the route; takeoff_position is observed after the horizontal approach.',
                                   'route':'Supported horizontal approach, hop <=64px horizontally, contact pause, horizontal finish.'},
            'takeoff_seconds':TAKEOFF_SECONDS,'landing_seconds':LANDING_SECONDS,
            'renderer':'backyard_visitor.art.CatArt.paint','qt_platform':qt.platformName(),
            'source_and_assets_sha256':after,'cases':cases}
    (options.output_dir/'jump-preview.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'state':'complete','image':str(target),'cases':len(cases),
                      'keyframes':sum(len(c['frames']) for c in cases),'real_seconds':report['timing']['real_seconds']}))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
