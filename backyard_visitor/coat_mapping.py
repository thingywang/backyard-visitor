"""Source-keyed anatomical UV maps shared by every procedural coat.

Coordinates are authored around body/limb/tail landmarks for each pose. The
screen canvas and animation clock never enter a coat field. Map pixels encode
part+side, u, v and protected facial-material weight; alpha stays in the neutral
fur material. These are auxiliary geometry maps, not repainted source sprites.
"""
from __future__ import annotations
from collections import OrderedDict
import hashlib
import json
import math
from pathlib import Path
from PyQt6.QtGui import QImage

ASSETS=Path(__file__).with_name('assets')
MAPS=ASSETS/'coat-maps'
PART_NAMES=('body','head','tail','front_leg','hind_leg')
MAP_VERSION=1


class CoatMappingError(ValueError):
    pass


def _smooth_unit(value):
    t=max(0.,min(1.,value))
    return t*t*(3-2*t)


def body_longitude(x, y, region, fallback):
    """Cover the rounded shoulder/rump caps instead of stretching endpoint u.

    Distance/region ownership still use the original capsule. Only the body
    texture coordinate spans its complete anatomical extent. This is shared
    by offline maps and root-seam projections, not based on screen motion.
    """
    if region['part'] != 'body' or len(region['path']) != 2:
        return fallback
    a, b = region['path']
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 0 or region['radius'] <= 0:
        raise CoatMappingError('Degenerate anatomical body path')
    along = ((x-a[0])*dx + (y-a[1])*dy) / length
    cap = region['radius']
    return max(0., min(1., (along+cap)/(length+2*cap)))


def _project_region(x,y,region):
    """Evaluate the immutable pose-local anatomical path used by map build."""
    points=region['path']
    lengths=[math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(points,points[1:])]
    total=sum(lengths)
    if total<=0 or region['radius']<=0:
        raise CoatMappingError('Degenerate anatomical path')
    closest=None;before=0.
    for a,b,length in zip(points,points[1:],lengths):
        if not length:continue
        dx,dy=b[0]-a[0],b[1]-a[1]
        t=max(0.,min(1.,((x-a[0])*dx+(y-a[1])*dy)/(length*length)))
        px,py=a[0]+t*dx,a[1]+t*dy
        distance=math.hypot(x-px,y-py)/region['radius']
        signed=((x-px)*(-dy)+(y-py)*dx)/length/region['radius']*region['sign']
        if closest is None or distance<closest[0]:
            closest=(distance,(before+t*length)/total,signed,px)
        before+=length
    distance,u,signed,axis=closest
    u=body_longitude(x,y,region,u)
    v=max(0.,min(1.,.5+.5*signed));mode=region['mode']
    if mode=='front':v=.65+.35*(1-min(1.,abs(signed)))
    elif mode=='back':v=.05+.35*min(1.,abs(signed))
    elif mode=='front-side':v=.16+.84*v
    elif mode=='back-side':v=.84*v
    side=region['side']
    if side=='front-split':side='left' if x>=axis else 'right'
    elif side=='rear-split':side='left' if x<=axis else 'right'
    return distance+region['bias'],u,v,int(side=='right')


class MappingCatalog:
    def __init__(self, directory=None):
        self.directory=Path(directory) if directory is not None else MAPS
        self._rows=None
        self._cache=OrderedDict()
        self._seams=OrderedDict()
        self._joints=OrderedDict()

    def rows(self):
        if self._rows is None:
            path=self.directory/'manifest.json'
            if path.is_symlink() or path.stat().st_size>1024*1024:
                raise CoatMappingError('Coat map manifest is unavailable')
            data=json.loads(path.read_text())
            if data.get('version')!=MAP_VERSION or data.get('kind')!='backyard-anatomical-coat-maps':
                raise CoatMappingError('Unsupported coat mapping version')
            rows=data.get('frames')
            if not isinstance(rows,dict) or not rows:raise CoatMappingError('Empty coat mapping catalog')
            self._rows=rows
        return self._rows

    def get(self, key, size):
        row=self.rows().get(key)
        if row is None:raise CoatMappingError('No anatomical coat map for this source pose')
        if row.get('size')!=list(size):raise CoatMappingError('Coat map canvas differs from the source pose')
        if key not in self._cache:
            path=self.directory/(key+'.png')
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['map_sha256']:
                raise CoatMappingError('Coat mapping checksum differs from its catalog')
            image=QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
            if (image.width(),image.height())!=tuple(size):raise CoatMappingError('Coat map dimensions changed')
            raw=image.constBits().asstring(image.sizeInBytes())
            self._cache[key]=(row,raw)
            while len(self._cache)>12:self._cache.popitem(last=False)
        self._cache.move_to_end(key)
        return self._cache[key]

    def seam_samples(self, key, raw, width, height, dpr):
        """Extend the torso chart continuously into adjoining root regions.

        Each result is (body_u_byte, body_v_byte, body_side, weight). At the
        chart boundary the same pixel samples the torso's projected UV; with
        increasing distance into the head/limb root it smoothly returns to its
        own chart. This removes rectangular cuts without blurring fur or
        sampling arbitrary neighbouring screen pixels. Tail tips and feet are
        excluded, so merely passing near the body cannot repaint them.
        """
        if key in self._seams:
            self._seams.move_to_end(key)
            return self._seams[key]
        regions=self.rows()[key].get('regions',[])
        body=next((row for row in regions if row['part']=='body'),None)
        seams={}
        if body is not None:
            by_part={part:[r for r in regions if r['part']==name]
                     for part,name in enumerate(PART_NAMES,1)}
            for y in range(height):
                for x in range(width):
                    offset=(y*width+x)*4;code=raw[offset];part=code&7
                    if not raw[offset+3] or part not in (2,3,4,5):continue
                    u=raw[offset+1]/255
                    # The roots are defined along the existing anatomical
                    # axes, independent of the desktop or animation time.
                    if part==3:
                        root_fade=1-_smooth_unit((u-.16)/.14)
                    elif part in (4,5):
                        root_fade=1-_smooth_unit((u-.48)/.20)
                    else:
                        root_fade=1.
                    if not root_fade or not by_part[part]:continue
                    lx,ly=(x+.5)/dpr,(y+.5)/dpr
                    body_distance,bu,bv,side=_project_region(lx,ly,body)
                    region_distance=min(_project_region(lx,ly,r)[0]
                                        for r in by_part[part])
                    # The signed dominance gap is zero exactly where the UV
                    # builder switches charts. Fade only into the outer chart;
                    # no 50/50 line or single constant flank anchor is used.
                    weight=(1-_smooth_unit((body_distance-region_distance)/.72))*root_fade
                    if weight>0:
                        seams[offset]=(round(bu*255),round(bv*255),side,weight)
        self._seams[key]=seams
        while len(self._seams)>12:self._seams.popitem(last=False)
        return seams

    def joint_samples(self, key, raw, width, height, dpr):
        """Shared stable limb pigment chart for action and portrait renderers."""
        if key not in self._joints:
            from .coat_joints import build_joint_samples
            self._joints[key]=build_joint_samples(self,key,raw,width,height,dpr)
            while len(self._joints)>12:self._joints.popitem(last=False)
        self._joints.move_to_end(key)
        return self._joints[key]

    def clear(self):
        self._cache.clear();self._seams.clear();self._joints.clear();self._rows=None

    def neutral_path(self, row):
        relative=Path(row['neutral_file'])
        if relative.is_absolute() or '..' in relative.parts:raise CoatMappingError('Invalid neutral material path')
        path=ASSETS/relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['neutral_sha256']:
            raise CoatMappingError('Neutral fur material checksum differs from its catalog')
        return path


_catalog=MappingCatalog()
