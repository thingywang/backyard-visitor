"""Render real before/after mouth delivery for both cat directions, without UI."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication
from backyard_visitor.art import CatArt
from backyard_visitor.gift_art import GiftArt
from backyard_visitor.gift_geometry import carried_gift_rect, mouth_anchor
from backyard_visitor.model import Engine, Visitor


def main():
    qt = QApplication.instance() or QApplication([])
    cats, gifts = CatArt(), GiftArt()
    image = QImage(960, 520, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor('#f5f5ee'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    rows = []
    for row, facing in enumerate((-1, 1)):
        engine = Engine(seed=4)
        v = Visitor('amber', 200, 200, state='entering', facing=facing,
                    target_x=200, target_y=200, carrying_gift='toy_mouse',
                    next_action=1000, stay_remaining=1000)
        engine.visitors[v.cat_id] = v
        carry = carried_gift_rect(facing)
        # Zero remaining route triggers the real arrival/drop path.
        engine.tick(.001)
        assert len(engine.gifts) == 1 and v.carrying_gift is None
        gift = engine.gifts[0]
        drop = (gift.x-v.x, gift.y-v.y, 38, 38)
        mouth = mouth_anchor(facing)
        assert gift.facing == facing
        assert carry[2:] == drop[2:]
        assert abs(drop[0]+19-mouth[0]) < 1e-9
        assert drop[1]+19 > mouth[1]
        rows.append({'facing':facing, 'carried_rect':carry, 'actual_drop_rect':drop,
                     'mouth':mouth, 'actual_engine_arrival_drop':True, 'drop_facing':gift.facing})
        for col, (label, state, progress) in enumerate((('carried / mouth','walking',None),
                                                       ('just placed / below head','standing',None),
                                                       ('resting / same gift','idle',1))):
            x, y = col*320, row*260
            painter.setPen(QColor('#344532')); painter.setFont(QFont('Helvetica',13))
            painter.drawText(QRectF(x, y, 320, 25), Qt.AlignmentFlag.AlignCenter,
                             ('LEFT | ' if facing<0 else 'RIGHT | ')+label)
            painter.save(); painter.translate(x+16,y+30); painter.scale(2,2)
            painter.setPen(QPen(QColor('#becbb7'),.5)); painter.drawLine(0,106,144,106)
            cats.paint(painter,QRectF(0,0,144,112),state,0,facing,
                       gait_phase=0,settle_progress=progress)
            gifts.paint(painter,QRectF(*(carry if col==0 else drop)),'toy_mouse',
                        facing=facing if col==0 else gift.facing)
            painter.restore()
    painter.end()
    output=ROOT/'output/v051';output.mkdir(parents=True,exist_ok=True)
    assert image.save(str(output/'mouth-delivery.png'))
    record={'scope':'Actual CatArt/GiftArt render plus real Engine arrival/drop; no native UI',
            'cases':rows,'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                for name in ('backyard_visitor/model.py','backyard_visitor/gift_geometry.py',
                             'backyard_visitor/art.py','backyard_visitor/gift_art.py')}}
    (output/'mouth-delivery.json').write_text(json.dumps(record,indent=2)+'\n')
    print(output/'mouth-delivery.png')

if __name__=='__main__':
    main()
