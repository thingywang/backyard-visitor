"""Render actual new RunArt/BowlArt frames, plus a lightweight cycle preview."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from PyQt6.QtCore import QByteArray,QBuffer,QIODevice,QRectF,Qt
from PyQt6.QtGui import QColor,QFont,QImage,QPainter
from PyQt6.QtWidgets import QApplication
from backyard_visitor.run_art import RunArt,FRAME_NAMES
from backyard_visitor.bowl_art import BowlArt
from backyard_visitor.standing_art import StandingArt


def main():
    app=QApplication.instance() or QApplication(["run-bowl-preview"])
    run,bowl=RunArt(),BowlArt()
    frames=run.load_frames()
    if len(frames)!=4 or len(bowl.load_frames())!=2:
        raise RuntimeError((run.load_error,bowl.load_error))
    image=QImage(720,690,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor("#eee9df"))
    p=QPainter(image);p.setFont(QFont("Arial",13))
    for i,name in enumerate(FRAME_NAMES):
        y=35+i*135
        p.setPen(QColor("#3e4b43"));p.drawText(18,y-8,f"{i+1}. {name}")
        for j,facing in enumerate((-1,1)):
            x=30+j*350
            if j:p.fillRect(x-10,y-5,320,125,QColor("#25312d"))
            p.setPen(QColor("#b29f7d"));p.drawLine(x,y+108,x+288,y+108)
            run.paint(p,QRectF(x,y,288,112),facing=facing,gait_phase=i/4)
            mx,my=run.mouth_anchors[i]
            if facing==1:mx=144-mx
            p.setPen(QColor("#ba6f40"));p.drawEllipse(QRectF(x+72+mx-1.5,y+my-1.5,3,3))
    y=590
    p.setPen(QColor("#3e4b43"));p.drawText(20,y-8,"Same bowl: empty / full (2x preview)")
    bowl.paint(p,QRectF(30,y,124,80),False);bowl.paint(p,QRectF(180,y,124,80),True)
    standing=StandingArt().frame()
    if standing:p.drawPixmap(460,552,standing)
    p.end()
    output=ROOT/"output";output.mkdir(exist_ok=True)
    image.save(str(output/"run-bowl-contact.png"))
    encoded=[]
    for frame in frames:
        raw=QByteArray();buffer=QBuffer(raw);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        frame.save(buffer,"PNG")
        encoded.append("data:image/png;base64,"+base64.b64encode(bytes(raw)).decode())
    payload=json.dumps(encoded)
    page='''<!doctype html><meta charset="utf-8"><title>Backyard Visitor run keyframes</title>
<style>body{background:#eee9df;color:#283d33;font:16px system-ui;margin:32px}canvas{border-bottom:1px solid #9d8a6d;background:#f8f5ee}button,input{margin:12px}small{display:block;max-width:700px}</style>
<h1>Run: four real key poses</h1><canvas width="576" height="448"></canvas><p id="label"></p>
<button id="play">Pause</button><button id="step">Next frame</button><button id="flip">Flip</button>
<label>Cycle seconds <input id="speed" type="range" min=".32" max="1.6" step=".04" value=".48"></label>
<small>Actual RunArt output, enlarged 2x. Playback uses four discrete images with no optical-flow, body squash or invented in-between frames. This is an art preview, not a full Engine replay.</small>
<script>
const sources=PAYLOAD,names=["hind push","extended flight","fore contact","gathered flight"];
const images=sources.map(src=>{let i=new Image();i.src=src;return i}),c=document.querySelector("canvas"),ctx=c.getContext("2d");
let playing=true,mirror=false,index=0,phase=0,last=performance.now();
function draw(){ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle="#b29f7d";ctx.beginPath();ctx.moveTo(0,432);ctx.lineTo(576,432);ctx.stroke();ctx.save();if(mirror){ctx.translate(c.width,0);ctx.scale(-1,1)}if(images[index].complete)ctx.drawImage(images[index],0,0,576,448);ctx.restore();document.querySelector("#label").textContent=(index+1)+"/4 — "+names[index]}
function tick(now){if(playing){phase=(phase+(now-last)/1000/Number(document.querySelector("#speed").value))%1;index=Math.floor(phase*4)}last=now;draw();requestAnimationFrame(tick)}
document.querySelector("#play").onclick=()=>{playing=!playing;document.querySelector("#play").textContent=playing?"Pause":"Play"};
document.querySelector("#step").onclick=()=>{playing=false;index=(index+1)%4;phase=index/4;document.querySelector("#play").textContent="Play";draw()};
document.querySelector("#flip").onclick=()=>{mirror=!mirror;draw()};
requestAnimationFrame(tick);
</script>'''.replace("PAYLOAD",payload)
    (output/"run-preview.html").write_text(page,encoding="utf-8")
    report={"frames":4,"canvas":[288,224],"dpr":2,"cycle_seconds":run.cycle_seconds,"mouth_anchors":run.mouth_anchors,"bowl_canvas":[124,80],"source_only":True,"no_interpolation":True}
    (output/"run-bowl-art-verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report))
    return 0


if __name__=="__main__":raise SystemExit(main())
