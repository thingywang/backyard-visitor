"""Offline black-coat colour design; preserve every source pose and alpha pixel.

Pillow/numpy are build-only. Runtime loads small authored variants, with no
image filtering or additional animation frames. User-authorized program colour
editing; source atlases remain unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def source_key(frame):
    from PyQt6.QtGui import QImage
    im = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    raw = im.constBits().asstring(im.sizeInBytes())
    return hashlib.sha256(f'{im.width()}x{im.height()}:'.encode()+raw).hexdigest()


def pil_image(frame):
    from PyQt6.QtGui import QImage
    im = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes('RGBA', (im.width(), im.height()), im.constBits().asstring(im.sizeInBytes()))


def blur_float(values, radius):
    # Blur only the lightness estimate; preserve source alpha separately.
    return np.asarray(Image.fromarray(np.clip(values,0,255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius)), dtype=float)


def design_black(source):
    rgba=np.asarray(source,dtype=float); rgb=rgba[:,:,:3]; alpha=rgba[:,:,3]; a=alpha/255
    light=rgb@np.array([.2126,.7152,.0722])
    # Separate broad illumination from the tabby's medium-scale pigment bands.
    # Alpha-normalized blur prevents transparent pixels making black edge halos.
    weighted=blur_float(light*a,8); coverage=blur_float(alpha,8)/255
    broad=weighted/np.maximum(coverage,1/255)
    fine=light-blur_float(light, .7)
    surface=blur_float(alpha,8)/255
    slope=np.zeros_like(surface); slope[1:-1]=(surface[2:]-surface[:-2])*.5
    tone=38 + np.clip((broad-95)*.22,-12,18) + np.clip(slope*700,-18,40) + np.clip(fine*.40,-13,13)
    # Restrained reflected light on the upper/left fur boundary, always inside
    # the unchanged alpha; no painted outline or glow outside the silhouette.
    soft=blur_float(alpha,1.8)/255
    gx=np.zeros_like(soft); gy=np.zeros_like(soft)
    gx[:,1:-1]=(soft[:,2:]-soft[:,:-2])*.5
    gy[1:-1]=(soft[2:]-soft[:-2])*.5
    rim=np.clip((gx*.35+gy*.65)*12,0,1)*13
    tone=np.clip(tone+rim,14,115)
    out=np.stack([tone*.96,tone,tone*1.045],axis=-1)
    # Preserve only naturally green/olive iris colour, not brown fur or pink
    # skin. All geometry, facial features and whisker alpha remain unchanged.
    eye=(rgb[:,:,1]>rgb[:,:,0]*.92)&(rgb[:,:,1]>rgb[:,:,2]*1.16)&(light>45)&(a>.5)
    out[eye]=rgb[eye]*.88
    result=np.concatenate([np.rint(np.clip(out,0,255)),alpha[:,:,None]],axis=-1).astype(np.uint8)
    result[alpha==0,:3]=0
    return Image.fromarray(result)


def collect():
    from backyard_visitor.art import CatArt
    from backyard_visitor.depth_art import DepthArt, COUNTS
    from backyard_visitor.rear_art import RearArt
    art=CatArt(prefer_compiled=False,retain_source_frames=True)
    for method in ('_load_motion','_load_interactions','_load_standing','_load_transitions','_load_jumps','_load_running','_load_activity','_load_postures'):
        getattr(art,method)()
    frames={}
    for i,frame in enumerate(art.frames):
        if not frame.isNull():frames[f'cat-{i:03d}']=frame
    if art.tail is not None:frames['tail']=art.tail
    frames['rear']=RearArt().frame('ginger')
    depth=DepthArt()
    for kind,count in COUNTS.items():
        for i in range(count):frames[f'depth-{kind}-{i}']=depth.frame(kind,i)
    return art,frames


def preview(data, directory, dest):
    states=data['preview_states']
    sheet=Image.new('RGB',(1200,280*len(states)), '#eee9df'); d=ImageDraw.Draw(sheet)
    for y,(state,key) in enumerate(states.items()):
        original=Image.open(directory/(key+'.png')).convert('RGBA'); black=design_black(original)
        for x,bg in enumerate(('#eee9df','#eee9df','#24312d','#101315')):
            ox=x*300; oy=y*280
            d.rectangle((ox,oy,ox+300,oy+280),fill=bg)
            chosen=original if x==0 else black
            sheet.paste(chosen,(ox+6,oy+28),chosen.getchannel('A'))
            d.text((ox+10,oy+8),state+(' / source' if x==0 else ' / black'),fill='#98a59e' if x>1 else '#3b4b42')
    sheet.save(dest)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--compile',action='store_true');ap.add_argument('--export',action='store_true');args=ap.parse_args()
    if args.compile:
        ap.error('Rejected formula prototype: installation is disabled. Use reviewed solid-black materials with compose_black_coat.py instead.')
    out=ROOT/'output/v09/black-cat';out.mkdir(parents=True,exist_ok=True); source=out/'source-frames';source.mkdir(exist_ok=True)
    if args.export:
        from PyQt6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication(['black-coat-export'])
        art,frames=collect(); rows={}
        for label,frame in frames.items():
            key=source_key(frame)
            if key in rows:rows[key]['uses'].append(label);continue
            frame.save(str(source/(key+'.png')),'PNG')
            rows[key]={'size':[frame.width(),frame.height()],'uses':[label]}
        states=('standing','walking','running','idle','resting','sleeping','window_peeking','carried')
        data={'frames':rows,'preview_states':{state:source_key(art.frame(state,0,-1)) for state in states}}
        (source/'manifest.json').write_text(json.dumps(data,indent=2)+'\n')
        print('Exported',len(rows),'unique original poses');return
    global np,Image,ImageFilter,ImageDraw
    import numpy as np
    from PIL import Image,ImageFilter,ImageDraw
    data=json.loads((source/'manifest.json').read_text())
    preview(data,source,out/'black-coat-contact-v2.png')
    print('Rejected formula preview only: output/v09/black-cat/black-coat-contact-v2.png')

if __name__=='__main__': main()
