"""Show actual new posture intermediates between unmodified production endpoints."""
import base64
import hashlib
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
from backyard_visitor.activity_art import ActivityArt
from backyard_visitor.posture_art import PostureArt
from backyard_visitor.standing_art import StandingArt


def main():
    app=QApplication.instance() or QApplication(["posture-preview"])
    activity=ActivityArt().load_frames();posture=PostureArt().load_frames()
    standing=StandingArt().frame()
    if len(posture)!=2 or standing is None:raise RuntimeError("Accepted posture assets are unavailable")
    cases={"crouch_rise":[activity["play_crouch"],*posture["crouch_rise"],standing],
           "swatting_to_rest":[activity["swatting"],*posture["belly_roll"],activity["resting"]],
           "belly_to_watch":[activity["belly"],*posture["belly_roll"],activity["play_crouch"]]}
    durations={"crouch_rise":.20,"swatting_to_rest":.55,"belly_to_watch":.55}
    sheet=QImage(1540,850,QImage.Format.Format_ARGB32_Premultiplied);sheet.fill(Qt.GlobalColor.transparent)
    p=QPainter(sheet);p.setFont(QFont("Arial",13));encoded={}
    for row,(name,frames) in enumerate(cases.items()):
        y=row*280+35;encoded[name]=[]
        p.setPen(QColor("#536a5c"));p.drawText(20,y-12,name+" | unchanged start + 3 intermediates + unchanged end")
        for i,frame in enumerate(frames):
            x=20+i*300
            p.setPen(QColor("#9eaa91"));p.drawLine(x,y+216,x+288,y+216)
            p.drawPixmap(QRectF(x,y,288,224),frame,QRectF(frame.rect()))
            p.drawText(x,y+243,("start" if i==0 else "end" if i==4 else "intermediate "+str(i)))
            raw=QByteArray();buffer=QBuffer(raw);buffer.open(QIODevice.OpenModeFlag.WriteOnly);frame.save(buffer,"PNG")
            encoded[name].append("data:image/png;base64,"+base64.b64encode(bytes(raw)).decode())
    p.end()
    out=ROOT/"output/v08";out.mkdir(parents=True,exist_ok=True)
    sheet.save(str(out/"posture-contact.png"))
    review=QImage(sheet.size(),sheet.format());review.fill(QColor("#eee9df"))
    p=QPainter(review);p.drawImage(0,0,sheet);p.end();review.save(str(out/"posture-review.png"))
    page='''<!doctype html><meta charset="utf-8"><title>Posture transition review</title>
<style>body{font:16px system-ui;background:#eee9df;color:#243a2c;margin:30px}canvas{background:repeating-conic-gradient(#ecebe7 0% 25%,#f8f7f2 0% 50%) 50%/24px 24px}button,select{margin:8px}small{display:block;max-width:700px}</style>
<h1>Actual posture keyframes</h1><select id="case"></select><select id="speed"><option value="1">Model duration</option><option value=".5">Half speed</option><option value=".25">Quarter speed</option></select><br>
<canvas width="576" height="448"></canvas><p id="status"></p><button id="play">Pause</button><button id="step">Next keyframe</button><button id="flip">Flip</button><button id="background">Dark background</button>
<small>Actual production source frames only. No crossfade, squash/stretch, optical flow, or invented intermediate images. Endpoints are unchanged ActivityArt/StandingArt. This is an art sequence preview, not a full Engine/UI replay. Each cycle holds the start and end briefly for inspection.</small>
<script>
const sources=PAYLOAD,durations=DURATIONS,select=document.querySelector("#case"),canvas=document.querySelector("canvas"),ctx=canvas.getContext("2d");
Object.keys(sources).forEach(name=>{const option=document.createElement("option");option.value=name;option.textContent=name;select.append(option)});
const images=Object.fromEntries(Object.entries(sources).map(([name,list])=>[name,list.map(src=>{const im=new Image;im.src=src;return im})]));
let running=true,flip=false,elapsed=0,index=0,last=performance.now(),dark=false;
function draw(){ctx.clearRect(0,0,576,448);ctx.strokeStyle="#a4ac8e";ctx.beginPath();ctx.moveTo(0,432);ctx.lineTo(576,432);ctx.stroke();ctx.save();if(flip){ctx.translate(576,0);ctx.scale(-1,1)}const im=images[select.value][index];if(im.complete)ctx.drawImage(im,0,0,576,448);ctx.restore();document.querySelector("#status").textContent=(index+1)+"/5 | "+(index===0?"existing start":index===4?"existing end":"real intermediate "+index)+" | model duration "+durations[select.value]+"s"}
function tick(now){if(running){const duration=durations[select.value],speed=Number(document.querySelector("#speed").value);elapsed=(elapsed+(now-last)/1000*speed)%(duration+1.2);index=elapsed<.6?0:elapsed>=duration+.6?4:Math.min(4,Math.floor((elapsed-.6)/duration*5))}last=now;draw();requestAnimationFrame(tick)}
select.onchange=()=>{elapsed=0;index=0;draw()};
document.querySelector("#play").onclick=()=>{running=!running;document.querySelector("#play").textContent=running?"Pause":"Play"};
document.querySelector("#step").onclick=()=>{running=false;index=(index+1)%5;document.querySelector("#play").textContent="Play";draw()};
document.querySelector("#flip").onclick=()=>{flip=!flip;draw()};
document.querySelector("#background").onclick=()=>{dark=!dark;canvas.style.background=dark?"#26352c":"";document.querySelector("#background").textContent=dark?"Checkerboard":"Dark background"};
requestAnimationFrame(tick);
</script>'''.replace("PAYLOAD",json.dumps(encoded)).replace("DURATIONS",json.dumps(durations))
    (out/"posture-preview.html").write_text(page,encoding="utf-8")
    inputs=["activity.png","activity.json","standing.png","standing.json","posture.png","posture-belly.png","posture.json"]
    report={"groups":{name:len(frames) for name,frames in posture.items()},"cases":list(cases),"canvas":[288,224],"dpr":2,"endpoints_unchanged":True,"interpolated_images":False,"durations":durations,"sha256":{name:hashlib.sha256((ROOT/"backyard_visitor/assets"/name).read_bytes()).hexdigest() for name in inputs}}
    (out/"posture-verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="sha256"}))


if __name__=="__main__":main()
