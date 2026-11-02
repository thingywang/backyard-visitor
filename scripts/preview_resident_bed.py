"""Render production bed and cat poses at their unchanged floor coordinates.

This is an offscreen composition, never a desktop screenshot or native verdict.
It touches no model, user save, wallpaper, or permissions.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt
from backyard_visitor.bed_art import BedArt, CORE_ALPHA_MIN
from backyard_visitor.resident_bed import BED_HEIGHT, BED_WIDTH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', type=Path, default=None)
    parser.add_argument('--output', type=Path, default=ROOT/'output/v09/resident-bed-art')
    args = parser.parse_args()
    qt = QApplication.instance() or QApplication(['resident-bed-preview'])
    bed, cat = BedArt(args.asset), CatArt()
    if bed.frame() is None:
        raise RuntimeError(bed.load_error)
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    width, height, floor = 240, 170, 139
    image = QImage(width*4*2, height*4*2, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(2)
    image.fill(QColor('#ece8df'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setFont(QFont('Arial', 10))
    rows = []
    for row, coat in enumerate(('ginger', 'gray', 'tuxedo', 'black')):
        for col, pose in enumerate(('empty', 'play_crouch', 'belly_lie', 'sleeping')):
            x, y = col*width, row*height
            dark = row % 2 == 1
            painter.fillRect(x, y, width, height, QColor('#26322e' if dark else '#ece8df'))
            painter.setPen(QColor('#c4c8b7' if dark else '#5e655a'))
            painter.drawText(x+10, y+16, f'{coat} / {pose}')
            painter.setPen(QColor('#7c846f' if dark else '#c6c4b8'))
            painter.drawLine(x+12, y+floor, x+width-12, y+floor)
            bed_rect = QRectF(x+width/2-BED_WIDTH/2, y+floor-BED_HEIGHT, BED_WIDTH, BED_HEIGHT)
            assert bed.paint(painter, bed_rect)
            if pose != 'empty':
                options = ({'posture_kind': 'belly_lie', 'posture_source': 'play_crouch',
                            'posture_target': 'sleeping', 'posture_progress': .5}
                           if pose == 'belly_lie' else {})
                cat.paint(painter, QRectF(x+width/2-72, y+floor-112, 144, 112),
                          'bed_laying' if options else pose, 0, -1 if col%2 else 1, coat, **options)
            rows.append({'coat': coat, 'pose': pose, 'floor': floor,
                         'bed_top': floor-BED_HEIGHT, 'cat_top': floor-112,
                         'facing': -1 if col%2 else 1})
    painter.end()
    assert image.save(str(out/'resident-bed-contact.png'))
    assert bed.frame().save(str(out/'resident-bed-render.png'))
    def rect(r): return [r.x(), r.y(), r.width(), r.height()]
    report = {'scope': 'Offscreen production BedArt/CatArt composition; no native or model test',
              'bed_size': [BED_WIDTH, BED_HEIGHT], 'bed_dpr': bed.frame().devicePixelRatio(),
              'source_alpha_rect': rect(bed.source_rect), 'fitted_rect': rect(bed.target_rect),
              'core_alpha_rect': rect(bed.core_rect), 'core_alpha_min': CORE_ALPHA_MIN,
              'source_padding_px': bed.source_padding,
              'load_error': bed.load_error, 'mask_top_left': bed.input_region().contains(QPoint(0, 0)),
              'samples': rows}
    (out/'render-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'samples': len(rows), 'load_error': bed.load_error,
                      'preview': str(out/'resident-bed-contact.png')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
