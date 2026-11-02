"""A swaying tail stays attached without fading its overlapping root twice."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from collections import deque
import math
import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QImage, QPainter, QTransform
from PyQt6.QtWidgets import QApplication
from backyard_visitor.rear_art import RearArt, WIDTH, HEIGHT


@pytest.fixture
def art():
    qt=QApplication.instance() or QApplication([])
    yield RearArt()


def render(art, coat, age, amount, scale=1., dpr=4, masked=False):
    factor=scale*dpr
    image=QImage(math.ceil(WIDTH*factor),math.ceil(HEIGHT*factor),QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image)
    if masked:
        painter.setClipRegion(QTransform().scale(factor,factor).map(art.input_region(age,amount,coat)))
    painter.scale(factor,factor)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    art.paint(painter,age,coat,amount)
    painter.end()
    return image


@pytest.mark.parametrize("coat", ["ginger", "black"])
@pytest.mark.parametrize("scale", [.6, .75, 1., 1.5, 2.5])
@pytest.mark.parametrize("sign", [-1, 1])
def test_resting_endpoints_keep_the_opaque_seat_collar(art, coat, scale, sign):
    # The original bug removed opaque source pixels along the horizontal cut
    # at y112 when either half of the rotating crop moved away from the body.
    age=(3*math.pi/2 if sign<0 else math.pi/2)/.85
    reference=render(art,coat,0,1,scale)
    actual=render(art,coat,age,1,scale)
    factor=scale*4
    collar=[(x,y) for y in range(math.ceil(112*factor),math.floor(113*factor))
            for x in range(actual.width()) if reference.pixelColor(x,y).alpha()>=220]
    assert collar
    assert all(actual.pixelColor(x,y).alpha()>=128 for x,y in collar)


def connected(image, first, second, threshold, factor):
    start=(round(first.x()*factor),round(first.y()*factor))
    target=(round(second.x()*factor),round(second.y()*factor))
    left,top,right,bottom=(math.floor(50*factor),math.floor(98*factor),
                           math.ceil(104*factor),math.ceil(136*factor))
    def solid(point):
        x,y=point
        return left<=x<right and top<=y<bottom and image.pixelColor(x,y).alpha()>=threshold
    if not solid(start) or not solid(target):return False
    queue=deque([start]);seen={start}
    while queue:
        x,y=queue.popleft()
        if (x,y)==target:return True
        for dx,dy in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,-1),(-1,1),(1,1)):
            point=x+dx,y+dy
            if point not in seen and solid(point):seen.add(point);queue.append(point)
    return False


@pytest.mark.parametrize("coat", ["ginger", "black"])
@pytest.mark.parametrize("scale", [.6, 1., 2.5])
def test_turn_and_rest_keep_connected_alpha_with_matching_mask(art, coat, scale):
    # Include the turn's near-horizontal start, intermediate poses and both
    # resting endpoints. Clip by the very same public input region as Qt uses.
    for amount in (.05,.25,.5,.75,1.):
        for age in (math.pi/2/.85,3*math.pi/2/.85):
            image=render(art,coat,age,amount,scale,dpr=2,masked=True)
            tail=art.tail_transform(age,amount).map(QPointF(74,126))
            assert connected(image,QPointF(72,106),tail,max(1,int(80*amount)),scale*2)
            region=art.input_region(age,amount,coat)
            assert region.contains(QPoint(72,106))
            assert region.contains(tail.toPoint())
            assert not region.contains(QPoint(0,140))
            assert not region.contains(QPoint(143,140))


@pytest.mark.parametrize("coat", ["ginger", "black"])
@pytest.mark.parametrize("scale", [.6, 1., 2.5])
def test_transition_opacity_applies_once_and_does_not_mutate_source(art, coat, scale):
    source=art.frame(coat).toImage().copy()
    canvas=None
    for amount in (0,.05,.25,.5,.75,1.):
        image=render(art,coat,math.pi/2/.85,amount,scale,dpr=2)
        alpha=image.convertToFormat(QImage.Format.Format_Alpha8)
        data=alpha.constBits().asstring(alpha.sizeInBytes())
        maximum=max(max(data[y*alpha.bytesPerLine():y*alpha.bytesPerLine()+alpha.width()])
                    for y in range(alpha.height()))
        assert maximum<=math.ceil(255*amount)+1
        assert maximum>=max(0,math.floor(255*amount)-2)
        if 0<amount<1:
            if canvas is None:canvas=art._transition_canvas
            assert art._transition_canvas is canvas  # One bounded scratch canvas.
    assert art.frame(coat).toImage()==source


def test_original_turn_and_sway_angle_and_seat_anchor_are_preserved(art):
    for amount in (0,.05,.25,.5,.75,1.):
        eased=amount*amount*(3-2*amount)
        for age in (0,math.pi/2/.85,3*math.pi/2/.85):
            angle=math.radians(-80*(1-eased)+math.sin(age*.85)*2.5*eased)
            transform=art.tail_transform(age,amount)
            anchor=transform.map(QPointF(72,112));tip=transform.map(QPointF(72,160))
            assert (anchor.x(),anchor.y())==pytest.approx((72,112))
            assert (tip.x(),tip.y())==pytest.approx((72-48*math.sin(angle),112+48*math.cos(angle)))
