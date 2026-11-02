"""Render comparable coat art using current code or a saved pre-change baseline.

This invokes production procedural art; it does not retouch existing images.
"""
from pathlib import Path
from types import ModuleType, SimpleNamespace
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.baseline:
        import backyard_visitor
        for name in ('classic_coat', 'spotted_coat', 'calico_coat', 'tabby_face',
                     'coat_traits', 'coat_mapping', 'procedural_coat', 'portrait_art'):
            if name in ('classic_coat', 'spotted_coat', 'calico_coat', 'tabby_face') and not (args.baseline / (name + '.py')).exists():
                continue
            full = 'backyard_visitor.' + name
            module = ModuleType(full)
            module.__package__ = 'backyard_visitor'
            module.__file__ = str(ROOT / 'backyard_visitor' / (name + '.py'))
            sys.modules[full] = module
            setattr(backyard_visitor, name, module)
            exec(compile((args.baseline / (name + '.py')).read_text(), module.__file__, 'exec'), module.__dict__)
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter, QColor, QFont
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor import __version__
    from backyard_visitor.coat_traits import CoatSpec
    from backyard_visitor.art import CatArt
    from backyard_visitor import procedural_coat, portrait_art
    from backyard_visitor.parkour_art import ParkourArt
    qt = QApplication.instance() or QApplication([])
    art = CatArt()
    park = ParkourArt()
    rows = [
        ('纯黑', CoatSpec(122)), ('蓝灰', CoatSpec(122, dilute=True)),
        ('鱼骨纹', CoatSpec(122, dark_pattern='mackerel')),
        ('点斑', CoatSpec(122, dark_pattern='spotted')),
        ('古典斑', CoatSpec(122, dark_pattern='classic')),
        ('麻色', CoatSpec(122, dark_pattern='ticked')),
        ('橘白', CoatSpec(165, pigment='orange', white_mode='spotting', white_coverage=.40)),
        ('黑白', CoatSpec(222, white_mode='spotting', white_coverage=.65)),
        ('三花', CoatSpec(551, pigment='tortie', white_mode='spotting', white_coverage=.40)),
        ('玳瑁', CoatSpec(551, pigment='tortie')),
        ('纯白', CoatSpec(122, white_mode='all-white', white_coverage=1)),
        ('淡三花', CoatSpec(551, pigment='tortie', dilute=True, white_mode='spotting', white_coverage=.40)),
    ]
    def board(width, height, title):
        im = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        im.fill(QColor('#ecece5'))
        p = QPainter(im)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setPen(QColor('#333b33'))
        p.setFont(QFont('PingFang SC', 20))
        p.drawText(24, 32, title)
        return im, p
    def draw(p, frame, x, y, w, h):
        assert frame is not None and not frame.isNull()
        assert not procedural_coat.load_error and not portrait_art.load_error
        factor = min(w / frame.width(), h / frame.height())
        fw, fh = frame.width() * factor, frame.height() * factor
        p.drawPixmap(QRectF(x + (w-fw)/2, y + (h-fh)/2, fw, fh), frame,
                     QRectF(0, 0, frame.width(), frame.height()))
    im, p = board(1080, 1070, '后院访客 · 同一批种子与性状')
    for i, (label, spec) in enumerate(rows):
        x, y = i % 3 * 360, i // 3 * 240 + 58
        p.setFont(QFont('PingFang SC', 16)); p.drawText(x+25, y+27, label)
        frame = art.frame('standing', 0, -1, spec)
        draw(p, frame, x+36, y+25, 288, 224)
        assert frame.save(str(args.output / f'coat-{i:02d}.png'))
    p.end(); assert im.save(str(args.output / 'palette.png'))
    im, p = board(1080, 700, '侧身与正面 · 固定身份')
    for j, index in enumerate((2, 4, 6, 7, 8, 9)):
        label, spec = rows[index]; x, y = j % 3 * 360, j // 3 * 320 + 48
        p.setFont(QFont('PingFang SC', 16)); p.drawText(x+16, y+24, label)
        draw(p, art.frame('standing', 0, -1, spec), x+6, y+30, 192, 200)
        draw(p, portrait_art.portrait_frame(SimpleNamespace(render_coat=spec)), x+198, y+38, 155, 240)
    p.end(); assert im.save(str(args.output / 'portrait-pairs.png'))
    im, p = board(1200, 1000, '主要动作 · 相同花色坐标')
    for j, index in enumerate((2, 4, 6, 8)):
        label, spec = rows[index]; y = j*235+45
        frames = [art.frame('standing',0,-1,spec), art.frame('standing',0,1,spec),
                  art.frame('sleeping',0,-1,spec), park.frame('jump_up',0,-1,spec),
                  park.frame('climb',0,-1,spec)]
        for col, frame in enumerate(frames):
            p.setFont(QFont('PingFang SC',13));p.drawText(col*240+10,y+22,label+' · '+('左','右','睡觉','上跳','攀爬')[col])
            draw(p,frame,col*240+5,y+30,230,195)
    p.end(); assert im.save(str(args.output / 'motion-contact.png'))
    im, p = board(1200, 940, '不同种子 · 不只检查单个样例')
    samples = [('鱼骨', {'dark_pattern':'mackerel'}), ('古典', {'dark_pattern':'classic'}),
               ('黑白', {'white_mode':'spotting','white_coverage':.60}),
               ('三花', {'pigment':'tortie','white_mode':'spotting','white_coverage':.40})]
    for row, (label, traits) in enumerate(samples):
        for col, seed in enumerate((0, 42, 122, 551)):
            x, y = col*300, row*220+48
            p.setFont(QFont('PingFang SC',14));p.drawText(x+16,y+24,f'{label} · {seed}')
            draw(p,art.frame('standing',0,-1,CoatSpec(seed,**traits)),x+10,y+30,280,195)
    p.end(); assert im.save(str(args.output / 'seed-contact.png'))
    (args.output / 'preview.json').write_text(json.dumps({'baseline':str(args.baseline) if args.baseline else None,
        'specs':[{'label':label,'spec':spec.to_dict()} for label,spec in rows],
        'production_rendering':True,'application_version':__version__},ensure_ascii=False,indent=2)+'\n')
    print('Rendered palette, portrait-pairs and motion-contact at',args.output)


if __name__ == '__main__':
    main()
