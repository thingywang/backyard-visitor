"""Render source-keyed coat examples across the actual full action inventory."""
from pathlib import Path
import os,sys,json,time,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QImage,QPainter,QColor,QFont
from PyQt6.QtWidgets import QApplication
from scripts.compile_black_coat import collect
from backyard_visitor.parkour_art import ParkourArt,KINDS
from backyard_visitor.coat_traits import CoatSpec
from backyard_visitor.procedural_coat import procedural_frame,clear_procedural_cache


def main():
    qt=QApplication.instance() or QApplication([]);out=ROOT/'output/procedural-coat-20261030';out.mkdir(exist_ok=True)
    art,frames=collect();park=ParkourArt()
    for kind in KINDS:
        for i in (0,1):frames[f'parkour-{kind}-{i}']=park.frame(kind,i,-1,'ginger')
    specs=[('solid-black',CoatSpec(13)),('orange-white',CoatSpec(28,pigment='orange',white_mode='spotting',white_coverage=.32)),
        ('mackerel',CoatSpec(39,dark_pattern='mackerel')),('cow',CoatSpec(73,white_mode='spotting',white_coverage=.7)),
        ('calico',CoatSpec(120,pigment='tortie',white_mode='spotting',white_coverage=.45)),('pure-white',CoatSpec(21,white_mode='all-white',white_coverage=1))]
    poses=['cat-010','cat-011','cat-012','cat-013','cat-018','cat-021','cat-029','cat-031','rear','tail','depth-away-0','depth-toward-0','parkour-jump_up-0','parkour-jump_down-0','parkour-climb-0']
    timings=[]
    for board in range(3):
        image=QImage(1200,1110,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('#e8e7e2'));p=QPainter(image);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform);p.setFont(QFont('Helvetica',12))
        for group,(name,spec) in enumerate(specs[board*2:board*2+2]):
            for index,label in enumerate(poses):
                started=time.perf_counter();frame=procedural_frame(frames[label],spec);elapsed=time.perf_counter()-started
                timings.append({'spec':name,'pose':label,'milliseconds':round(elapsed*1000,3)})
                x,y=index%5*240,(group*3+index//5)*185
                p.setPen(QColor('#252525'));p.drawText(x+6,y+16,name+' / '+label)
                scale=min(225/frame.width(),155/frame.height());w,h=frame.width()*scale,frame.height()*scale
                p.drawPixmap(QRectF(x+(240-w)/2,y+25,w,h),frame,QRectF(0,0,frame.width(),frame.height()))
                if label=='cat-018':frame.save(str(out/(name+'-standing.png')))
        p.end();image.save(str(out/f'generated-contact-{board+1}.png'))
    (out/'preview-timings.json').write_text(json.dumps({'frames':timings,'max_ms':max(r['milliseconds'] for r in timings),'subsequent_max_ms':max(r['milliseconds'] for r in timings if r['pose']!='cat-010')},indent=2)+'\n')
    print('Rendered',len(timings),'frames; max ms',max(r['milliseconds'] for r in timings))

if __name__=='__main__':main()
