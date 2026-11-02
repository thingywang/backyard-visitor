"""Offline normal-alpha visual comparison; never modifies production assets."""
from pathlib import Path
import hashlib
import json
import os
import sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt6.QtCore import QRectF,Qt
from PyQt6.QtGui import QImage,QPainter,QColor,QFont
from PyQt6.QtWidgets import QApplication
OUT=ROOT/"output/experiments/color"
ASSETS=OUT/"original-assets"  # Immutable pre-correction originals for repeatable comparisons.
CANDIDATE=OUT/"run-calibrated-candidate-3.png"


def frame(image, spec, row):
    canvas=QImage(288,224,QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    p=QPainter(canvas);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    crop=row["rect"];anchor=row["foot_anchor"]
    scale=spec["render_scale"];dest=spec["render_anchor"]
    p.drawImage(QRectF(dest[0]*2-anchor[0]*scale,dest[1]*2-anchor[1]*scale,
                       crop[2]*scale,crop[3]*scale),image,QRectF(*crop))
    p.end()
    return canvas


def paint_pose(p,image,x,y,scale=1):
    # Normal alpha compositing, at actual 144x112 logical size or an exact zoom.
    p.drawImage(QRectF(x,y-2*scale,144*scale,112*scale),image,QRectF(image.rect()))


def label(p,x,y,text,color="#253c30"):
    p.setPen(QColor(color));p.setFont(QFont("Arial",13));p.drawText(x,y,text)



def render_pose_groups():
    folder=OUT/"pose-calibration"
    specs={name:json.loads((ASSETS/(name+".json")).read_text()) for name in ("run","activity")}
    postures=json.loads((ASSETS/"posture.json").read_text())["groups"]
    specs.update({"posture":postures["crouch_rise"],"posture-belly":postures["belly_roll"]})
    image=QImage(1330,850,QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#eee9df"))
    p=QPainter(image);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    label(p,20,25,"All four bounded chroma candidates / actual 144 x 112 logical size / same normal alpha background")
    label(p,20,48,"Each pair: original LEFT / calibrated RIGHT. Keep pose-specific shadows and light belly fur.")
    for group,(name,spec) in enumerate(specs.items()):
        old=QImage(str(ASSETS/(name+".png")));new=QImage(str(folder/(name+".png")))
        top=90+group*185
        label(p,20,top,name)
        for col,row in enumerate(spec["frames"]):
            x=20+326*col
            label(p,x,top+22,row["name"])
            paint_pose(p,frame(old,spec,row),x,top+35,1)
            paint_pose(p,frame(new,spec,row),x+160,top+35,1)
            p.setPen(QColor("#b6beae"));p.drawLine(x,top+141,x+304,top+141)
            label(p,x+35,top+160,"original")
            label(p,x+195,top+160,"calibrated")
    p.end();assert image.save(str(OUT/"pose-calibration-comparison.png"))
    print("pose-calibration-comparison.png")


def main():
    app=QApplication.instance() or QApplication(["run-color-review"])
    run=json.loads((ASSETS/"run.json").read_text())
    standing=json.loads((ASSETS/"standing.json").read_text())
    activity=json.loads((ASSETS/"activity.json").read_text())
    posture=json.loads((ASSETS/"posture.json").read_text())["groups"]["crouch_rise"]
    raw=QImage(str(ASSETS/"run.png"));candidate=QImage(str(CANDIDATE))
    orig=[frame(raw,run,r) for r in run["frames"]]
    corrected=[frame(candidate,run,r) for r in run["frames"]]
    st=frame(QImage(str(ASSETS/"standing.png")),standing,standing)
    crouch=frame(QImage(str(ASSETS/"activity.png")),activity,activity["frames"][3])
    rise=[frame(QImage(str(ASSETS/"posture.png")),posture,r) for r in posture["frames"]]
    names=["play_crouch","rise 1","rise 2","rise 3","standing reference","original run phase 1","candidate 3 phase 1"]
    values=[crouch,*rise,st,orig[0],corrected[0]]
    context=QImage(2148,875,QImage.Format.Format_ARGB32_Premultiplied);context.fill(QColor("#eee9df"))
    p=QPainter(context);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    label(p,20,26,"Same normal compositing background; no display filters, changed poses or lighting compensation.")
    label(p,20,48,"Run candidate 3 only changes bounded warm-fur chroma. All other poses remain original.")
    for index,(name,value) in enumerate(zip(names,values)):
        x=20+304*index
        label(p,x,80,name)
        paint_pose(p,value,x+72,105,1)
        p.setPen(QColor("#b6beae"));p.drawLine(x,211,x+288,211)
        paint_pose(p,value,x,270,2)
        p.setPen(QColor("#b6beae"));p.drawLine(x,482,x+288,482)
        p.fillRect(QRectF(x,570,288,250),QColor("#26352c"))
        paint_pose(p,value,x,590,2)
        p.setPen(QColor("#819178"));p.drawLine(x,802,x+288,802)
    label(p,20,240,"Top: actual 144 x 112 logical canvas. Middle/bottom: 2x inspection, same artwork on light/dark backgrounds.")
    label(p,20,850,"Ground reference matches CatArt's logical -2 px drawing offset. This is an art comparison, not an Engine replay.")
    p.end();assert context.save(str(OUT/"run-calibration-context.png"))
    details=QImage(1510,1320,QImage.Format.Format_ARGB32_Premultiplied);details.fill(QColor("#eee9df"))
    p=QPainter(details);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    label(p,20,28,"Four real run phases: original / candidate 3; identical source-space torso crops at right.")
    crop_local=[(430,135,220,160),(430,65,220,160),(430,70,220,160),(430,80,220,160)]
    for row,(old,new,info,detail) in enumerate(zip(orig,corrected,run["frames"],crop_local)):
        y=70+row*300
        label(p,20,y-10,info["name"]+" - original")
        label(p,325,y-10,"candidate 3")
        label(p,650,y-10,"original torso / 1.5x source pixels")
        label(p,1070,y-10,"candidate 3 / identical source crop")
        paint_pose(p,old,20,y,2);paint_pose(p,new,325,y,2)
        for x in (20,325):
            p.setPen(QColor("#b6beae"));p.drawLine(x,y+212,x+288,y+212)
        cx,cy,cw,ch=detail
        rect=QRectF(cx,info["rect"][1]+cy,cw,ch)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,False)
        p.drawImage(QRectF(650,y,cw*1.5,ch*1.5),raw,rect)
        p.drawImage(QRectF(1070,y,cw*1.5,ch*1.5),candidate,rect)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
    label(p,20,1290,"No sharpening, denoising, alpha changes, resampling of source files or generated replacement fur.")
    p.end();assert details.save(str(OUT/"run-calibration-details.png"))
    files=[ASSETS/"run.png",ASSETS/"run.json",ASSETS/"standing.png",ASSETS/"activity.png",ASSETS/"posture.png",CANDIDATE]
    report={"scope":"Normal alpha composite art comparison, not native application or motion replay",
            "candidate":str(CANDIDATE),"source_sha256":{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
            "outputs":["run-calibration-context.png","run-calibration-details.png"],"display_sizes":[[144,112],[288,224]],
            "source_or_production_modified":False}
    (OUT/"run-calibration-composite-review.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report["outputs"]))
    render_pose_groups()


if __name__=="__main__":main()
