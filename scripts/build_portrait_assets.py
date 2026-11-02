from pathlib import Path
import sys,os,json,hashlib,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtCore import Qt,QRectF
from PyQt6.QtGui import QImage,QPixmap,QPainter,QColor
from PyQt6.QtWidgets import QApplication
from backyard_visitor.black_coat import source_key
from backyard_visitor.coat_mapping import PART_NAMES
from scripts.build_coat_maps import region,project
app=QApplication([]);folder=ROOT/'backyard_visitor/assets/portraits'
source=QImage(str(folder/'cat-front-source.png'))
image=QImage(288,336,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
p=QPainter(image);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
scale=min(288/source.width(),336/source.height());w,h=source.width()*scale,source.height()*scale
p.drawImage(QRectF((288-w)/2,(336-h)/2,w,h),source);p.end();image.save(str(folder/'cat-front-neutral.png'))
image=image.convertToFormat(QImage.Format.Format_RGBA8888);raw=image.constBits().asstring(image.sizeInBytes());width,height=image.width(),image.height()
regions=[region('body',((68,72),(70,126)),26,mode='front',side='front-split'),
         region('head',((68,53),(68,33)),23,mode='portrait-head',side='front-split',bias=-.15),
         region('front_leg',((57,92),(59,150)),8,side='right'),region('front_leg',((79,92),(78,150)),8,side='left'),
         region('hind_leg',((45,110),(48,142),(50,145)),10,side='right'),region('hind_leg',((94,110),(92,140),(90,145)),10,side='left'),
         region('tail',((96,134),(112,139),(117,146),(109,149),(99,146)),7)]
pixels=bytearray(width*height*4)
for y in range(height):
    for x in range(width):
        o=(y*width+x)*4;r,g,b,a=raw[o:o+4]
        if not a:continue
        lx,ly=(x+.5)/2,(y+.5)/2
        projections=[project(lx,ly,r) for r in regions];idx=min(range(len(regions)),key=lambda i:projections[i][0]);part=regions[idx];_,u,v,side=projections[idx]
        if part['mode']=='portrait-head':
            u=max(0,min(1,.62*abs(lx-68)/24+.10*max(0,43-ly)/29+.035*math.sin((ly-25)/12)))
            v=max(0,min(1,(ly-15)/48))
        protect=0
        if part['part']=='head':
            eye=any(((lx-ex)/4.5)**2+((ly-43)/3.8)**2<1 for ex in (58,77))
            ear=any(((lx-ex)/7)**2+((ly-28)/12)**2<1 for ex in (49,86))
            if eye and g>r*.78 and g>b*1.30 and min(r,g)>b*1.15 and max(r,g)>55:protect=15
            elif ear and r>g*1.12 and b>g*.88 and r>35:protect=12
            if ((lx-68)/3.2)**2+((ly-52)/2.6)**2<1:protect=15
        code=PART_NAMES.index(part['part'])+1+(8 if side=='right' else 0)+(protect<<4)
        pixels[o:o+4]=bytes((code,round(u*255),round(v*255),255))
mapim=QImage(bytes(pixels),width,height,width*4,QImage.Format.Format_RGBA8888).copy();key=source_key(QPixmap.fromImage(image));dest=folder/(key+'.png');mapim.save(str(dest))
row=dict(label='portrait-front-seated',size=[288,336],dpr=2,neutral_file='portraits/cat-front-neutral.png',regions=regions,pads=[],map_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),neutral_sha256=hashlib.sha256((folder/'cat-front-neutral.png').read_bytes()).hexdigest())
(folder/'manifest.json').write_text(json.dumps(dict(version=1,kind='backyard-anatomical-coat-maps',frames={key:row}),indent=2)+'\n')

print('portrait',source.width(),source.height(),'->',width,height,'mapped',key)
