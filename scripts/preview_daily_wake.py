"""Natural ordinary waking through production CatArt. Offscreen, not OS capture.

No visitor positions, states, timers or animation phases are assigned. The
fixture sets only public scene bounds, calm mode and relationship trust before
natural invitation. It feeds after natural sleep, then records ordinary ticks.
"""
from __future__ import annotations
import argparse,hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PyQt6.QtCore import QRectF,Qt
from PyQt6.QtGui import QImage,QPainter,QColor,QFont
from PyQt6.QtWidgets import QApplication
from backyard_visitor.art import CatArt
from backyard_visitor.model import Engine
from backyard_visitor.interaction_model import BELLY_ROLL_SECONDS,CROUCH_RISE_SECONDS
from scripts.preview_posture_timeline import snapshot
DT=.005

def fingerprints():
    names=["art.py","model.py","interaction_model.py","overlays.py","posture_art.py"]
    names += ["assets/"+name for name in ("activity.png","activity.json","standing.png","standing.json","posture.png","posture-belly.png","posture.json","jump_transitions.png","jump_transitions.json","extra.png","extra.json","compiled-sprites/manifest.json")]
    return {name:hashlib.sha256((ROOT/"backyard_visitor"/name).read_bytes()).hexdigest() for name in names}

def replay():
    engine=Engine(seed=3,demo=True,visit_limit=1)
    engine.set_bounds(0,0,1600,1000)
    engine.calm_mode=True
    engine.cats["mist"].trust=60
    engine.open_gate();engine.invite("mist")
    v=engine.visitors["mist"];setup=0.
    while setup<60 and v.state!="sleeping":
        engine.tick(.05);setup+=.05
        if v.cat_id not in engine.visitors:raise RuntimeError("Natural visitor departed before sleep")
    if v.state!="sleeping":raise RuntimeError("Natural sleep not reached")
    before=snapshot(engine,v,0.)
    assert engine.feed()
    rows=[];selected={};elapsed=0.;wake_start=None;wake_end=None
    saw_wake=False
    while elapsed<40:
        row=snapshot(engine,v,elapsed)
        row.update(food=engine.food,arrival_state=v.arrival_state)
        rows.append(row)
        kind=row["paint"]["posture_kind"];p=row["paint"]["posture_progress"]
        if row["state"]=="waking":
            saw_wake=True
            if wake_start is None:wake_start=elapsed
            if kind in {"belly_roll","crouch_rise"} and p is not None:
                selected.setdefault((kind,min(4,int(p*5))),row)
        elif saw_wake:
            if wake_end is None:wake_end=elapsed
            # Include a genuinely moving route frame, not manually positioned art.
            if "seek" not in selected and (abs(row["x"]-before["x"])+abs(row["y"]-before["y"]))>5:
                selected["seek"]=row;break
        engine.tick(DT);elapsed+=DT
        if v.cat_id not in engine.visitors:raise RuntimeError("Natural visitor departed before meal")
    required={(kind,i) for kind in ("belly_roll","crouch_rise") for i in range(5)}
    if not required.issubset(selected) or "seek" not in selected:
        raise RuntimeError("Ordinary path did not show both real postures and subsequent movement")
    active=[r for r in rows if r["state"]=="waking"]
    reference=active[0]
    drift=max(max(abs(r[k]-reference[k]) for k in ("x","y")) for r in active)
    phase_drift=max(max(abs(r["paint"][k]-reference["paint"][k]) for k in ("gait_phase","run_phase")) for r in active)
    assert drift<1e-8 and phase_drift<1e-8
    assert all(r["food"]==1 for r in active),"Meal was consumed while waking"
    roll_start=selected[("belly_roll",0)]["elapsed"]-selected[("belly_roll",0)]["paint"]["posture_progress"]*BELLY_ROLL_SECONDS
    rise_start=selected[("crouch_rise",0)]["elapsed"]-selected[("crouch_rise",0)]["paint"]["posture_progress"]*CROUCH_RISE_SECONDS
    assert abs((rise_start-roll_start)-BELLY_ROLL_SECONDS)<=DT+1e-7
    assert abs((wake_end-rise_start)-CROUCH_RISE_SECONDS)<=DT+1e-7
    frames=[dict(label="sleep before feeding",**before)]
    for kind in ("belly_roll","crouch_rise"):
        for i in range(5):frames.append(dict(label=kind+" / "+("source" if i==0 else "end pose" if i==4 else "middle "+str(i)),**selected[(kind,i)]))
    frames += [dict(label="seeking bowl",**selected["seek"])]
    return dict(fixture={"seed":3,"cat_id":"mist","trust":60,"demo":True,"visit_limit":1,"calm_mode":True,"bounds":[0,0,1600,1000],"bowl":"default after set_bounds","state_coordinate_timer_assignment":False},natural_sleep_seconds=round(setup,6),wake_start_after_feed=round(roll_start,6),wake_end_after_feed=round(wake_end,6),roll_seconds=round(rise_start-roll_start,6),rise_seconds=round(wake_end-rise_start,6),wake_position_drift=drift,wake_phase_drift=phase_drift,seek_after_feed_seconds=round(elapsed,6),meal_completed=False,common_source_canvas_anchor=[72,108],common_painted_canvas_anchor=[72,106],keyframes=frames,trajectory=rows)

def render(case,path):
    art=CatArt();frames=case["keyframes"]
    columns=5;cell_width=304;cell_height=320
    image=QImage(20+columns*cell_width,90+3*cell_height,QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#eee9df"));p=QPainter(image)
    p.setPen(QColor("#263d30"));p.setFont(QFont("Arial",16))
    p.drawText(20,27,"Ordinary sleep -> roll 0.55s -> rise 0.20s -> seek bowl | real Engine + production CatArt")
    p.setFont(QFont("Arial",12));p.drawText(20,49,"Normal alpha / actual gray coat / 2x inspection / common painted anchor (72,106); no pose or coordinate injection")
    p.drawText(20,69,"Aligned local canvases, not a desktop recording. Global x/y and real model timestamps are recorded below each frame.")
    for index,row in enumerate(frames):
        x=20+(index%columns)*cell_width;y=90+(index//columns)*cell_height
        p.setPen(QColor("#a7b4a0"));p.drawLine(x,y+212,x+288,y+212)
        p.drawLine(x+144,y+208,x+144,y+218)
        p.save();p.translate(x,y);p.scale(2,2)
        art.paint(p,QRectF(0,row["render_offset_y"],144,112),**row["paint"])
        p.restore();p.setPen(QColor("#263d30"));p.setFont(QFont("Arial",12))
        p.drawText(x,y+242,row["label"])
        progress=row["paint"]["posture_progress"]
        suffix="" if progress is None else " p="+format(progress,".3f")
        p.drawText(x,y+261,"t="+format(row["elapsed"],".3f")+"s / "+row["state"]+suffix)
        p.drawText(x,y+280,"x="+format(row["x"],".2f")+" y="+format(row["y"],".2f")+" face="+str(row["paint"]["facing"]))
    p.end();assert image.save(str(path))
    for kind in ("belly_roll","crouch_rise"):
        first=next(r for r in frames if r["paint"]["posture_kind"]==kind)["paint"]
        state=art.posture_state(kind,first["posture_source"],first["posture_target"])
        assert state and len(art.states[state])==5,"Production posture fallback would invalidate the preview"
    return art._compiled is not None

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,default=ROOT/"output/v09/daily-wake")
    options=parser.parse_args();out=options.output_dir
    for filename in ("daily-wake.png","daily-wake.json"):
        if (out/filename).exists():parser.error("Refusing existing preview output: "+str(out/filename))
    qt=QApplication.instance() or QApplication(["daily-wake-preview"])
    before=fingerprints();started=time.monotonic();case=replay()
    out.mkdir(parents=True,exist_ok=True);compiled=render(case,out/"daily-wake.png")
    assert fingerprints()==before,"Product changed while recording"
    report={"state":"complete","scope":__doc__,"dt_seconds":DT,"setup_tick_seconds":.05,"real_seconds":time.monotonic()-started,"qt_platform":qt.platformName(),"compiled_loaded":compiled,"source_sha256":before,"case":case,"limitations":["Logical model ticks run faster than wall time; not a native timing test.","One natural gray visitor fixture; discrete poses, not continuous motion capture. Recording ends at seeking, not a completed meal; ordinary visit lifetime is unchanged.","Common anchor is the rendering contract, not a claim that every anatomical paw occupies the same pixel throughout rolling."]}
    (out/"daily-wake.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({key:case[key] for key in ("natural_sleep_seconds","wake_start_after_feed","roll_seconds","rise_seconds","wake_position_drift","wake_phase_drift","seek_after_feed_seconds")},indent=2))
if __name__=="__main__":main()
