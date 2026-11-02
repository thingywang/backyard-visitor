"""Build anatomical maps from reviewed plain-black canvases; leave art unchanged.

Every stored row is source-keyed. Pose labels are offline calibration handles,
never runtime frame indices. The explicit body/leg/head/tail paths below are
in each source's logical canvas coordinates, not a global screen projection.
"""
from pathlib import Path
import sys,os,json,math,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtCore import Qt,QRectF
from PyQt6.QtGui import QImage,QPainter,QColor,QFont
from PyQt6.QtWidgets import QApplication
from backyard_visitor.coat_mapping import PART_NAMES, body_longitude


def region(part,points,radius,*,side='left',mode='side',sign=1,bias=0):
    return dict(part=part,path=points,radius=radius,side=side,mode=mode,sign=sign,bias=bias)


def profile(head=((21,56),(34,49)),body=((47,68),(96,69)),front=((49,76),(43,106)),hind=((100,77),(119,106)),
            tail=((112,64),(123,50),(122,38),(116,35)),*,body_radius=18,head_radius=14,side='left',mode='side'):
    sign=1 if body[-1][0]>=body[0][0] else -1
    return [region('body',body,body_radius,side=side,mode=mode,sign=sign),
            region('head',head,head_radius,side=side,sign=1 if head[-1][0]>=head[0][0] else -1,bias=-.15),
            region('front_leg',front,6,side=side,bias=.08),
            region('hind_leg',hind,8,side=side,bias=.08),
            region('tail',tail,5.7,side=side,bias=-.14)]


def skeleton(label):
    pads=[]
    if label=='tail':
        return [region('tail',((106,359),(135,280),(145,186),(112,92),(43,37)),39)],pads
    if label=='rear':
        return [region('body',((74,61),(74,105)),25,mode='back',side='rear-split'),
                region('head',((74,47),(73,28)),14,mode='back',side='rear-split',bias=-.15),
                region('tail',((74,111),(75,137),(73,154),(66,161)),7.5),
                region('hind_leg',((61,98),(61,111)),9,side='left'),
                region('hind_leg',((87,98),(88,111)),9,side='right')],pads
    if label.startswith('depth-away-'):
        i=int(label.rsplit('-',1)[1]);phase=(i%4)
        near_hind=[(66,100),(58,103),(50,96),(62,98)][phase]
        near_front=[(107,89),(105,97),(95,98),(99,85)][phase]
        r=profile(head=((117,45),(108,41)),body=((96,62),(63,65)),front=((97,70),near_front),
                  hind=((62,75),near_hind),tail=((55,58),(45,44),(43,34),(49,30)),body_radius=18,head_radius=12,side='right',mode='back-side')
        r.extend([region('front_leg',((106,66),(114,90)),5,side='left',bias=.1),region('hind_leg',((72,74),(78,96)),6,side='left',bias=.1)])
        return r,pads
    if label.startswith('depth-toward-'):
        i=int(label.rsplit('-',1)[1]);fronts=[(45,105),(54,103),(46,101),(48,104)];hinds=[(99,94),(99,92),(98,96),(96,93)]
        r=profile(head=((45,49),(58,38)),body=((66,59),(87,69)),front=((57,68),fronts[i]),hind=((92,73),hinds[i]),
                  tail=((94,60),(106,47),(109,32),(104,28)),body_radius=17,head_radius=16,mode='front-side')
        r.extend([region('front_leg',((70,71),(66,101)),6,side='right',bias=.08),region('hind_leg',((83,76),(83,94)),6,side='right',bias=.12)])
        return r,pads
    if label=='depth-front-0':
        return [region('body',((74,58),(77,89)),19,side='front-split',mode='front'),region('head',((74,51),(74,35)),17,side='front-split',mode='front-head',bias=-.15),
                region('front_leg',((65,76),(64,104)),6,side='right'),region('front_leg',((81,76),(82,104)),6,side='left'),
                region('hind_leg',((88,86),(92,99)),7),region('tail',((91,78),(108,66),(112,43),(108,34)),5)],pads
    if label.startswith('parkour-'):
        kind,index=label.removeprefix('parkour-').rsplit('-',1);i=int(index)
        if kind=='jump_up':
            if i==0:r=profile(head=((55,31),(65,26)),body=((73,50),(95,78)),front=((64,43),(40,18)),hind=((97,81),(113,107)),tail=((101,79),(118,92),(133,99)),body_radius=18,head_radius=13)
            else:r=profile(head=((48,31),(58,27)),body=((69,52),(87,79)),front=((59,44),(37,21)),hind=((89,80),(80,92)),tail=((97,85),(117,104),(134,109)),body_radius=18,head_radius=13)
            r.append(region('front_leg',((66,46),(48,39)),5,side='right',bias=-.02))
        elif kind=='jump_down':
            r=profile(head=((38,65),(47,57)),body=((67,57),(105,42)),front=((55,72),(35 if i==0 else 38,102)),hind=((104,47),(108,68)),tail=((112,43),(128,27),(128,11)),body_radius=18,head_radius=13)
            r.append(region('front_leg',((66,74),(45 if i==0 else 49,108)),5,side='right'))
        else:
            # Climb art is deliberately anchored on the shared x=88 contact
            # line; its body is to the RIGHT of that line, not canvas-centred.
            r=profile(head=((98,28),(110,25)),body=((108,45),(115,68)),front=((108,44),(96,37),(88,22 if i==0 else 34)),hind=((113,73),(105,84),(92,94)),tail=((121,76),(129,90),(137,90),(138,82)),body_radius=12,head_radius=12,mode='front-side')
            r.extend([region('front_leg',((107,48),(89,44 if i==0 else 20)),4.5,side='right'),region('hind_leg',((110,67),(100,73),(91,68)),6,side='right')])
        return r,pads
    i=int(label.removeprefix('cat-'))
    if i in (0,1,10,11,12,13,18,19,22,24,25,26,27,28,34,35):
        r=profile()
        front={0:((47,75),(22,106)),1:((45,75),(37,91),(37,105)),10:((47,75),(23,106)),11:((48,76),(38,92),(37,105)),12:((47,75),(22,106)),13:((48,76),(41,85),(42,106)),18:((49,75),(47,105)),19:((49,79),(46,105)),22:((53,77),(52,106)),24:((50,78),(46,104)),25:((51,76),(37,86),(41,92)),26:((46,70),(20,78)),27:((47,76),(22,105)),28:((49,75),(39,85),(52,91)),34:((48,86),(44,105)),35:((49,78),(45,105))}[i]
        hind={0:((100,76),(119,106)),1:((100,76),(91,104)),10:((100,76),(125,106)),11:((100,76),(112,87),(97,103)),12:((100,76),(125,106)),13:((100,76),(114,105)),18:((99,76),(113,105)),19:((100,79),(110,105)),22:((101,78),(92,104)),24:((100,79),(94,104)),25:((98,82),(120,104)),26:((99,73),(126,87)),27:((100,76),(124,105)),28:((99,77),(102,91),(92,103)),34:((100,91),(95,105)),35:((101,80),(112,106))}[i]
        r[2]['path']=front;r[3]['path']=hind
        if i in (19,22,24):r[0]['path']=((49,73),(99,74))
        if i in (25,26,28):
            offset={25:0,26:-8,28:-5}[i];r[0]['path']=((49,72+offset),(99,79+offset));r[1]['path']=((21,68+offset),(34,61+offset));r[4]['path']=((108,75+offset),(125,66+offset),(132,57+offset))
        if i==34:r[0]['path']=((50,85),(103,86));r[1]['path']=((22,72),(35,65));r[4]['path']=((112,84),(128,83),(132,70))
        if i==35:r[0]['path']=((50,75),(101,77));r[1]['path']=((22,62),(35,55));r[4]['path']=((113,72),(128,61),(128,50))
        r.extend([region('front_leg',((54,76),(64,105)),5,side='right',bias=.12),region('hind_leg',((95,76),(85,105)),6,side='right',bias=.12)])
        return r,pads
    if i in (2,4,16,17):
        r=[region('body',((73,62),(77,94)),21,mode='front',side='front-split'),region('head',((53,48),(66,39)),16,bias=-.18),
           region('front_leg',((69,73),(64,105)),6,side='right'),region('front_leg',((80,74),(76,105)),6),
           region('hind_leg',((89,87),(90,104)),9),region('tail',((90,102),(68,105),(55,103)),8)]
        if i==4:r[1]['path']=((64,49),(74,42));r[2]['path']=((72,67),(58,64))
        return r,pads
    if i==6:
        return [region('body',((73,43),(79,77)),14,mode='front-side'),region('head',((59,22),(69,15)),14,bias=-.15),
                region('front_leg',((69,44),(66,60)),5),region('front_leg',((81,45),(78,64)),5,side='right'),
                region('hind_leg',((77,77),(65,95)),6),region('hind_leg',((88,76),(85,99)),6,side='right'),region('tail',((86,79),(92,98),(81,107)),6)],pads
    if i in (14,15):
        return [region('head',((58,21),(72,10)),15,bias=-.1),region('front_leg',((62,25),(62,31)),5),region('front_leg',((78,26),(78,31)),5,side='right'),region('body',((72,24),(72,31)),12)],pads
    if i==8:
        return [region('tail',((72,111),(83,84),(81,62),(65,48)),13)],pads
    if i in (20,21):
        r=profile(head=((24,61),(36,54)),body=((49,74),(79,90)),front=((49,77),(44,105)),hind=((82,91),(68,105)),tail=((86,102),(106,106),(123,99)),body_radius=23,head_radius=15)
        r.append(region('front_leg',((54,78),(53,105)),5,side='right'))
        return r,pads
    if i==9:
        r=profile(head=((21,62),(36,57)),body=((48,74),(97,76)),front=((48,79),(9,93)),hind=((101,78),(130,95)),tail=((112,72),(131,49),(138,43)),body_radius=14,head_radius=14)
        r.append(region('front_leg',((48,82),(14,88)),4,side='right'))
        return r,pads
    if i==7:
        return profile(head=((17,94),(27,84)),body=((43,77),(88,69)),front=((44,83),(38,105)),hind=((91,78),(96,106)),tail=((101,71),(115,93),(131,94)),body_radius=20,head_radius=14),pads
    if i in (5,29,30,36):
        r=profile(head=((19,97),(32,91)),body=((50,94),(98,94)),front=((50,89),(58,80)),hind=((100,92),(105,82)),tail=((113,99),(132,91)),body_radius=17,head_radius=14,mode='front')
        if i==5:r[2]['path']=((45,96),(47,106));r[3]['path']=((100,97),(109,105));r[4]['path']=((114,98),(136,104))
        if i==30:r[2]['path']=((43,88),(20,62))
        r.extend([region('front_leg',((57,94),(50,103)),5,side='right',bias=-.03),region('hind_leg',((109,96),(108,106)),5,side='right',bias=-.03)])
        pads=[(*r[2]['path'][-1],2.3),(*r[3]['path'][-1],2.3)]
        return r,pads
    if i in (3,23,31,32,33,37,38):
        head_y=91 if i not in (3,23) else 82
        r=profile(head=((21,head_y+5),(34,head_y-2)),body=((50,head_y+3),(98,head_y+3)),front=((49,head_y+7),(35,106)),hind=((101,head_y+5),(96,106)),tail=((113,head_y+4),(131,107 if i==31 else 99)),body_radius=13,head_radius=13)
        if i==32:r[0]['path']=((51,91),(98,83));r[0]['radius']=18;r[4]['path']=((112,88),(129,92),(136,85))
        return r,pads
    raise ValueError('Unmapped source label '+label)


def project(x,y,r):
    points=r['path'];lengths=[math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(points,points[1:])];total=sum(lengths)
    closest=None;before=0.;radius=r['radius']
    for a,b,length in zip(points,points[1:],lengths):
        dx,dy=b[0]-a[0],b[1]-a[1]
        t=max(0.,min(1.,((x-a[0])*dx+(y-a[1])*dy)/(length*length)))
        px,py=a[0]+t*dx,a[1]+t*dy;distance=math.hypot(x-px,y-py)/radius
        signed=((x-px)*(-dy)+(y-py)*dx)/length/radius*r['sign']
        if closest is None or distance<closest[0]:closest=(distance,(before+t*length)/total,signed,px)
        before+=length
    distance,u,signed,axis=closest
    u=body_longitude(x,y,r,u)
    v=max(0.,min(1.,.5+.5*signed))
    mode=r['mode']
    if mode=='front':v=.65+.35*(1-min(1.,abs(signed)))
    elif mode=='back':v=.05+.35*min(1.,abs(signed))
    elif mode=='front-side':v=.16+.84*v
    elif mode=='back-side':v=.84*v
    elif mode=='front-head':v=max(0.,min(1.,(y-29)/31))
    side=r['side']
    if side=='front-split':side='left' if x>=axis else 'right'
    elif side=='rear-split':side='left' if x<=axis else 'right'
    return distance+r['bias'],u,v,side


def main():
    qt=QApplication.instance() or QApplication([])
    inventory=json.loads((ROOT/'backyard_visitor/assets/coat-maps/source-inventory.json').read_text())['frames']
    output=ROOT/'backyard_visitor/assets/coat-maps';output.mkdir(exist_ok=True)
    catalog={'version':1,'kind':'backyard-anatomical-coat-maps','encoding':'R=part1..5 | sideRight<<3 | protected0..15<<4; G=u; B=v; A=255 on fur','parts':list(PART_NAMES),'frames':{}}
    for number,row in enumerate(inventory):
        path=ROOT/'backyard_visitor/assets'/row['neutral_file'];im=QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
        width,height=im.width(),im.height();raw=im.constBits().asstring(im.sizeInBytes());pixels=bytearray(width*height*4)
        regions,pads=skeleton(row['label']);dpr=row['dpr'];head=next((r for r in regions if r['part']=='head'),None)
        for y in range(height):
            for x in range(width):
                offset=(y*width+x)*4;r,g,b,a=raw[offset:offset+4]
                if not a:continue
                lx,ly=(x+.5)/dpr,(y+.5)/dpr
                results=[project(lx,ly,region) for region in regions]
                chosen=min(range(len(regions)),key=lambda i:results[i][0]);region=regions[chosen];_,u,v,side=results[chosen]
                protect=0
                if region['part']=='head':
                    # Preserve colored iris/inner-ear material and the nose;
                    # fur, including white facial patches, still takes the coat.
                    if g>r*.78 and g>b*1.30 and min(r,g)>b*1.15 and max(r,g)>30:protect=15
                    elif r>g*1.17 and b>g*.94 and r>35:protect=12
                    nx,ny=region['path'][0]
                    if region['mode']!='back' and ((lx-nx)/1.8)**2+((ly-ny)/1.4)**2<1:protect=15
                if any((lx-px)**2+(ly-py)**2<radius*radius for px,py,radius in pads):protect=14
                code=PART_NAMES.index(region['part'])+1+(8 if side=='right' else 0)+(protect<<4)
                pixels[offset:offset+4]=bytes((code,round(u*255),round(v*255),255))
        image=QImage(bytes(pixels),width,height,width*4,QImage.Format.Format_RGBA8888).copy();dest=output/(row['key']+'.png');image.save(str(dest))
        catalog['frames'][row['key']]={k:v for k,v in row.items() if k!='key'}
        catalog['frames'][row['key']].update(regions=regions,pads=pads,map_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),neutral_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        print(number+1,row['label'],flush=True)
    (output/'manifest.json').write_text(json.dumps(catalog,indent=2)+'\n')
    print('Mapped',len(catalog['frames']),'source-keyed poses',flush=True)

if __name__=='__main__':main()
