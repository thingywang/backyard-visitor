"""Export an offline, playable QA view of the production CatArt renderer.

Run: .venv/bin/python scripts/preview_motion.py
Only the real CatArt.paint outputs become images. The player selects these
images without blending, warping, or manufacturing additional limb poses.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt
from backyard_visitor.model import GAIT_STRIDE, LANDING_SECONDS, SETTLE_SECONDS, RISE_SECONDS, TAKEOFF_SECONDS

FOOT_BASELINE = 106  # Production render anchor 108 minus CatArt.paint's 2px.


def phase_for_distance(distance: float, stride: float = GAIT_STRIDE) -> float:
    """Same distance-driven phase contract as the engine (non-negative travel)."""
    if not math.isfinite(distance) or not math.isfinite(stride) or stride <= 0:
        raise ValueError("Distance and stride must be finite; stride must be positive")
    return (max(0.0, distance) / stride) % 1.0


def render_pose(art, state="idle", facing=-1, *, age=0.0,
                gait_phase=None, land_progress=None, settling=False, turning=False,
                settle_progress=None, rise_progress=None, takeoff_progress=None):
    image = QImage(288, 224, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        art.paint(painter, QRectF(0, 0, 144, 112), state, age, facing, "ginger",
                  gait_phase=gait_phase, land_progress=land_progress, settling=settling, turning=turning,
                  settle_progress=settle_progress, rise_progress=rise_progress, takeoff_progress=takeoff_progress)
    finally:
        painter.end()
    return image


def png_bytes(image):
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode a production frame")
    buffer.close()
    return bytes(data)


def build_payload(art):
    """Deduplicate identical production PNGs, including held landing poses."""
    images, by_hash = [], {}
    frames, rendered = {}, {}
    def add(key, image):
        encoded = png_bytes(image)
        digest = hashlib.sha256(encoded).hexdigest()
        if digest not in by_hash:
            by_hash[digest] = len(images)
            images.append("data:image/png;base64," + base64.b64encode(encoded).decode("ascii"))
        frames[key] = by_hash[digest]
        rendered[key] = image
    for direction in (-1, 1):
        prefix = str(direction) + ":"
        for index in range(4):
            add(prefix + "walk" + str(index),
                render_pose(art, "walking", direction, gait_phase=index/4))
        add(prefix + "idle", render_pose(art, "idle", direction))
        add(prefix + "standing", render_pose(art, "idle", direction, settling=True))
        add(prefix + "jumping", render_pose(art, "jumping", direction))
        add(prefix + "turning", render_pose(art, "walking", direction, gait_phase=.75, turning=True))
        for index in range(9):
            add(prefix + "takeoff" + str(index),
                render_pose(art, "jumping", direction, takeoff_progress=min(index/8, .999)))
            add(prefix + "land" + str(index),
                render_pose(art, "idle", direction, land_progress=index/8, settling=True))
            add(prefix + "settle" + str(index),
                render_pose(art, "idle", direction, settle_progress=index/8))
            add(prefix + "rise" + str(index),
                render_pose(art, "walking", direction, rise_progress=index/8))
    payload = {
        "stride": GAIT_STRIDE, "landing_seconds": LANDING_SECONDS,
        "takeoff_seconds": TAKEOFF_SECONDS, "settle_seconds": SETTLE_SECONDS, "rise_seconds": RISE_SECONDS, "foot_baseline": FOOT_BASELINE,
        "logical_size": [144, 112], "pixel_ratio": 2,
        "renderer": "backyard_visitor.art.CatArt.paint",
        "frames": frames, "images": images,
        "art_sha256": hashlib.sha256((ROOT / "backyard_visitor/art.py").read_bytes()).hexdigest(),
        "model_sha256": hashlib.sha256((ROOT / "backyard_visitor/model.py").read_bytes()).hexdigest(),
    }
    return payload, rendered


HTML = r"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>后院访客 · 动作验收</title>
<style>
:root{font:16px system-ui,sans-serif;color:#2d3a32;background:#f5f5ee}
body{max-width:1040px;margin:30px auto;padding:0 22px}h1{font-size:25px;margin-bottom:8px}
p{line-height:1.65}small{color:#627165}button,select,input{font:inherit}
button,select{border:1px solid #bec9bd;border-radius:7px;background:#fff;padding:8px 12px;color:inherit}
button{cursor:pointer}label{display:flex;align-items:center;gap:8px}
.controls{display:flex;flex-wrap:wrap;gap:14px;margin:18px 0;align-items:center}
canvas{display:block;width:100%;height:360px;background:#e9eee5;border:1px solid #cad3c5;border-radius:10px}
#info{font:14px ui-monospace,monospace;line-height:1.7;padding:13px;background:#fff;border-radius:8px;margin-top:12px;white-space:pre-wrap}
#loading{color:#785d32}.notes{border-top:1px solid #ced5c8;padding-top:14px;font-size:14px}
</style>
<h1>后院访客 · 动作验收</h1>
<p>使用实际生产 <code>CatArt.paint</code> 输出。猫保持在视口中央，地面刻度按真实路程反向滚动，便于观察脚底滑移。图片之间直接切换，没有外观混合、拉伸或补造中间帧。</p>
<div class="controls">
<button id="play">暂停</button><button id="step">前进 1/30 秒</button><button id="reset">重置</button>
<label>模式<select id="mode"><option value="walk">持续行走</option><option value="stop">行走 → 站稳 → 坐下</option><option value="land">蓄力 → 腾空 → 屈腿落地</option><option value="rise">坐下 → 起身 → 行走</option><option value="idle">侧坐休息</option><option value="turn">转身驻足</option></select></label>
<label>朝向<select id="direction"><option value="-1">向左</option><option value="1">向右</option></select></label>
</div>
<div class="controls">
<label>速度 <input id="speed" type="range" min="0" max="130" step="1" value="66"><output id="speedlabel">66 px/s</output></label>
<label>回放<select id="rate"><option value="1">正常时间</option><option value=".25">¼ 速检查</option></select></label>
<label>显示<select id="zoom"><option value="1">实际大小</option><option value="2" selected>2×</option><option value="3">3×</option></select></label>
</div>
<canvas id="canvas" aria-label="猫动作预览，脚底固定基线和滚动距离刻度"></canvas>
<div id="loading">正在载入实际渲染帧…</div><div id="info" aria-live="off"></div>
<p class="notes">持续行走的速度滑条只改变本预览的路程增长率，未模拟 Engine 的加速/减速曲线。切换朝向用于比较镜像，不模拟实际转身延迟。跳跃模式只检查姿态切换与地面锚点，不模拟真实窗口跳跃轨迹。站坐和起身使用真实关键姿态，终点与侧坐休息共用一帧。起身期间地面不移动。起跳与落地使用新屈腿关键姿态，仍为有限帧；真实 Engine 轨迹另见跳跃验收记录。</p>
<script type="application/json" id="data">__PAYLOAD__</script>
<script>
"use strict";
const data=JSON.parse(document.getElementById("data").textContent);
const $=id=>document.getElementById(id), canvas=$("canvas"), ctx=canvas.getContext("2d");
let running=true, distance=0, signedDistance=0, elapsed=0, previous=null, loaded=[];
const wrap=(a,b)=>((a%b)+b)%b;
function choice(){
 const mode=$("mode").value, phase=wrap(distance/data.stride,1);
 if(mode==="idle") return {key:"idle",state:"idle",phase,land:null};
 if(mode==="turn") return {key:"turning",state:"walking + turning (standing hold)",phase,land:null};
 if(mode==="walk") return {key:"walk"+Math.min(3,Math.floor(phase*4+1e-9)),state:"walking",phase,land:null};
 if(mode==="rise"){
  const t=elapsed%5;
  if(t<1) return {key:"idle",state:"idle (seated)",phase,land:null};
  if(t<1+data.rise_seconds) return {key:"rise"+Math.min(7,Math.floor((t-1)/data.rise_seconds*8)),state:"walking + rising",phase,land:null};
  if(t<3) return {key:"walk"+Math.min(3,Math.floor(phase*4+1e-9)),state:"walking",phase,land:null};
  if(t<3+data.settle_seconds) return {key:"settle"+Math.min(8,Math.floor((t-3)/data.settle_seconds*8)),state:"idle + sitting down",phase,land:null};
  return {key:"idle",state:"idle (seated)",phase,land:null};
 }
 if(mode==="stop"){
  const t=elapsed%5;
  if(t<2) return {key:"walk"+Math.min(3,Math.floor(phase*4+1e-9)),state:"walking",phase,land:null};
  if(t<2+data.settle_seconds) return {key:"settle"+Math.min(8,Math.floor((t-2)/data.settle_seconds*8)),state:"idle + sitting down",phase,land:null};
  return {key:"idle",state:"idle (seated)",phase,land:null};
 }
 const t=elapsed%3;
 if(t<data.takeoff_seconds) return {key:"takeoff"+Math.min(8,Math.floor(t/data.takeoff_seconds*8)),state:"jumping + takeoff",phase,land:null};
 if(t<.7+data.takeoff_seconds) return {key:"jumping",state:"jumping pose (fixed location)",phase,land:null};
 const contact=t-.7-data.takeoff_seconds;
 if(contact<data.landing_seconds){
  const progress=contact/data.landing_seconds;
  return {key:"land"+Math.min(8,Math.floor(progress*8)),state:"idle + landing",phase,land:progress};
 }
 if(contact<data.settle_seconds) return {key:"settle"+Math.min(8,Math.floor(contact/data.settle_seconds*8)),state:"idle + sitting down",phase,land:1};
 return {key:"idle",state:"idle (seated)",phase,land:1};
}
function advance(dt){
 const mode=$("mode").value; let walkTime=0;
 if(mode==="walk") walkTime=dt;
 if(mode==="stop"){
  // Split at exact walk/rest boundaries so a long frame never moves while idle.
  let left=dt, t=elapsed;
  while(left>1e-10){const cycle=wrap(t,5), n=Math.min(left,(cycle<2?2:5)-cycle);
   if(cycle<2) walkTime+=n; t+=n; left-=n;}
 }
 if(mode==="rise"){
  let left=dt, t=elapsed;
  const begin=1+data.rise_seconds;
  while(left>1e-10){const cycle=wrap(t,5), boundary=cycle<begin?begin:cycle<3?3:5;
   const n=Math.min(left,boundary-cycle); if(cycle>=begin&&cycle<3) walkTime+=n; t+=n; left-=n;}
 }
 const travel=Number($("speed").value)*walkTime;
 distance+=travel; signedDistance+=travel*Number($("direction").value); elapsed+=dt;
}
function draw(){
 if(!loaded.length)return;
 const width=canvas.clientWidth, height=360, dpr=window.devicePixelRatio||1;
 if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){
  canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
 }
 ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
 const zoom=Number($("zoom").value), y=height-36, x=width/2-72*zoom;
 ctx.strokeStyle="#a6b79f";ctx.lineWidth=1;
 ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();
 ctx.font="12px system-ui";ctx.fillStyle="#5c7057";
 const spacing=20*zoom, shift=wrap(-signedDistance*zoom,spacing);
 for(let a=shift;a<width;a+=spacing){ctx.beginPath();ctx.moveTo(a,y);ctx.lineTo(a,y+7);ctx.stroke();}
 ctx.fillText("脚底参照 y="+data.foot_baseline+" / 每格20逻辑px",12,height-10);
 const state=choice(), facing=$("direction").value, image=loaded[data.frames[facing+":"+state.key]];
 ctx.imageSmoothingEnabled=true;
 ctx.drawImage(image,x,y-data.foot_baseline*zoom,144*zoom,112*zoom);
 const speed=Number($("speed").value);
 $("speedlabel").textContent=speed+" px/s";
 $("info").textContent="state: "+state.state+"  |  facing: "+facing+
  "\ndistance: "+distance.toFixed(2)+" px  |  phase: "+state.phase.toFixed(4)+"  |  stride: "+data.stride+" px"+
  "\nproduction frame: "+state.key+"  |  time: "+elapsed.toFixed(3)+"s"+
  (state.land===null?"":"  |  land_progress: "+state.land.toFixed(3))+
  "\nnormal-speed cycle: "+(speed?(data.stride/speed).toFixed(3)+"s":"stopped; phase frozen")+
  "  |  landing hold: "+data.landing_seconds+"s  |  settle hold: "+data.settle_seconds+"s";
}
function tick(now){
 if(previous!==null&&running)advance(Math.min((now-previous)/1000,.1)*Number($("rate").value));
 previous=now;draw();requestAnimationFrame(tick);
}
$("play").onclick=()=>{running=!running;$("play").textContent=running?"暂停":"播放";};
$("step").onclick=()=>{running=false;$("play").textContent="播放";advance(1/30);draw();};
function reset(){distance=0;signedDistance=0;elapsed=0;draw();}
$("reset").onclick=reset;$("mode").onchange=reset;
for(const id of ["direction","speed","rate","zoom"])$(id).oninput=draw;
document.addEventListener("visibilitychange",()=>{previous=null;});
Promise.all(data.images.map(src=>new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=reject;im.src=src;})))
.then(images=>{loaded=images;$("loading").textContent=images.length+" 张实际 PNG，全部本地载入。";requestAnimationFrame(tick);})
.catch(()=>{$("loading").textContent="图片载入失败，请重新导出预览。";});
</script></html>"""


def contact_sheet(rendered, destination):
    specs = [
        [("walk0", "walk phase 0"), ("walk1", "walk phase .25"),
         ("walk2", "walk phase .5"), ("walk3", "walk phase .75"),
         ("standing", "settling / turning"), ("idle", "long idle / seated")],
        [("standing", "standing / contact"), ("settle2", "sit progress .25"),
         ("settle4", "sit progress .5"), ("settle6", "sit progress .75"),
         ("idle", "seated endpoint"), ("rise4", "rising halfway")],
    ]
    cell_w, cell_h = 312, 292
    sheet = QImage(cell_w*6, cell_h*4, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#f5f5ee"))
    painter = QPainter(sheet)
    painter.setFont(QFont("Helvetica", 14))
    for direction_row, direction in enumerate((-1, 1)):
        for row, entries in enumerate(specs):
            for col, (key, label) in enumerate(entries):
                x, y = col*cell_w, (direction_row*2+row)*cell_h
                painter.setPen(QColor("#344532"))
                painter.drawText(QRectF(x+4,y+6,cell_w-8,30), Qt.AlignmentFlag.AlignCenter,
                                 ("LEFT | " if direction == -1 else "RIGHT | ") + label)
                painter.setPen(QPen(QColor("#acbba1"), 1))
                baseline = y+48+FOOT_BASELINE*2
                painter.drawLine(x+5, baseline, x+cell_w-5, baseline)
                # Explicit rect uses the physical PNG at 2x logical size.
                painter.drawImage(QRectF(x+12, y+48, 288, 224), rendered[f"{direction}:{key}"])
    painter.end()
    if not sheet.save(str(destination)):
        raise RuntimeError(f"Could not save contact sheet {destination}")


def export_preview(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    art = CatArt()
    payload, rendered = build_payload(art)
    html = output / "motion-preview.html"
    html.write_text(HTML.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":"))),
                    encoding="utf-8")
    contact_sheet(rendered, output / "motion-transitions.png")
    meta = {key: value for key, value in payload.items() if key not in ("images", "frames")}
    meta["unique_images"] = len(payload["images"])
    meta["frame_map"] = payload["frames"]
    (output / "motion-preview.json").write_text(json.dumps(meta, indent=2)+"\n", encoding="utf-8")
    return html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication(["backyard-motion-preview"])
    result = export_preview(args.output.resolve())
    print(result)
    print(f"Production stride {GAIT_STRIDE:g}px; landing {LANDING_SECONDS:g}s; "
          f"settling {SETTLE_SECONDS:g}s. PNGs come only from CatArt.paint.")


if __name__ == "__main__":
    main()
