"""Rounded torso ends retain spatial detail instead of stretched edge columns."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PyQt6.QtWidgets import QApplication
from backyard_visitor.coat_mapping import MappingCatalog, _project_region
from backyard_visitor.black_coat import source_key
from backyard_visitor.art import CatArt
from scripts.build_coat_maps import project


def test_rump_and_shoulder_cap_coordinates_continue_past_centerline_endpoints():
    region={'part':'body','path':[(47,68),(96,69)],'radius':18,'sign':1,
            'bias':0,'side':'left','mode':'side'}
    rear=[_project_region(x,69,region)[1] for x in (96,100,104,108)]
    front=[_project_region(x,68,region)[1] for x in (34,38,42,46)]
    assert all(b>a for a,b in zip(rear,rear[1:]))
    assert all(b>a for a,b in zip(front,front[1:]))
    assert rear[-1]-rear[0]>.12 and front[-1]-front[0]>.12
    assert 0<front[0]<rear[-1]<1
    for x in (34,42,96,104,108):
        distance,u,v,side=_project_region(x,69,region)
        build=project(x,69,region)
        assert build[:3]==pytest.approx((distance,u,v))
        assert build[3]=='left'


def test_packaged_standing_map_has_multiple_columns_on_the_rump():
    qt=QApplication.instance() or QApplication(['body-cap-regression'])
    frame=CatArt().frame('standing',0,-1,'ginger')
    catalog=MappingCatalog();row,raw=catalog.get(source_key(frame),(frame.width(),frame.height()))
    width=frame.width();dpr=row['dpr'];values=[]
    body=next(r for r in row['regions'] if r['part']=='body')
    for y in range(125,143):
        for x in range(195,214):
            offset=(y*width+x)*4
            if raw[offset]&7==1 and raw[offset+3]:
                values.append(raw[offset+1])
                expected=round(_project_region((x+.5)/dpr,(y+.5)/dpr,body)[1]*255)
                assert raw[offset+1]==expected
    assert len(values)>80 and len(set(values))>12
    assert max(values)<255
