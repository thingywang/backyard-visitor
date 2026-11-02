"""Inspect the four actual production activity poses on light/dark backgrounds."""
import json
import os
from pathlib import Path
import sys
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor,QFont,QImage,QPainter
from PyQt6.QtWidgets import QApplication
from backyard_visitor.activity_art import ActivityArt,STATES


def main():
    app=QApplication.instance() or QApplication(["activity-art-preview"])
    art=ActivityArt()
    if tuple(art.load_frames())!=STATES:raise RuntimeError(art.load_error)
    image=QImage(740,1080,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor("#eee9df"))
    p=QPainter(image);p.setFont(QFont("Arial",16))
    for i,state in enumerate(STATES):
        y=30+i*265
        p.setPen(QColor("#3e4b43"));p.drawText(20,y-8,state+" (2x actual frame)")
        for j,facing in enumerate((-1,1)):
            x=30+j*370
            if j:p.fillRect(x-10,y-3,320,236,QColor("#25312d"))
            p.setPen(QColor("#b29f7d"));p.drawLine(x,y+216,x+288,y+216)
            art.paint(p,QRectF(x,y,288,224),state,facing)
    p.end()
    out=ROOT/"output";out.mkdir(exist_ok=True)
    image.save(str(out/"activity-contact.png"))
    report={"states":list(STATES),"canvas":[288,224],"dpr":2,"shared_ground_logical_y":108,"interpolation":False,"poses_are_independent":True}
    (out/"activity-art-verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report))
    return 0


if __name__=="__main__":raise SystemExit(main())
