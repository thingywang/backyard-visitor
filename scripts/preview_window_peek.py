"""Render the real CatWindow at selected peek stages without requesting OS access.

This is UI test rendering, not editing sprite artwork. Run from the project root:
    QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/preview_window_peek.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt
from backyard_visitor.overlays import CatWindow


def render_stage(art, progress, phase):
    visitor = SimpleNamespace(state="window_peeking", age=1.0, facing=-1,
        clip_y=400., peek_progress=progress, peek_phase=phase,
        carrying_gift=None, window_edge_id="preview", window_target_id=None)
    controller = SimpleNamespace(art=art, screen=SimpleNamespace(geometry=lambda: QRect(0, 0, 1600, 1000)),
        cat_clip=lambda pos, size: None,
        engine=SimpleNamespace(visitors={"amber": visitor}, cats={"amber": SimpleNamespace(coat="ginger")}))
    widget = CatWindow(controller, "amber")
    widget.move(200, round(400-112+78*progress))
    widget.refresh_input_region()
    cat = QImage(144, 112, QImage.Format.Format_ARGB32_Premultiplied)
    cat.fill(Qt.GlobalColor.transparent)
    p = QPainter(cat)
    widget.render(p, QPoint(0, 0))
    p.end()
    scene = QImage(400, 360, QImage.Format.Format_ARGB32_Premultiplied)
    scene.fill(QColor("#edf0e8"))
    p = QPainter(scene)
    p.scale(2, 2)
    # Fixed world viewport x=172,y=260, so its window edge is local y=140.
    p.fillRect(QRectF(0, 140, 200, 40), QColor("#ced7d3"))
    p.setPen(QPen(QColor("#7a8d84"), .8))
    p.drawLine(0, 140, 200, 140)
    p.setPen(QColor("#62756b"))
    p.setFont(QFont("Helvetica", 7))
    p.drawText(QRectF(7, 146, 185, 16), Qt.AlignmentFlag.AlignLeft, "window top (global y=400)")
    p.drawImage(QPoint(widget.x()-172, widget.y()-260), cat)
    p.end()
    opaque = [(x, y) for y in range(112) for x in range(144) if cat.pixelColor(x, y).alpha() > 8]
    result = {"phase": phase, "progress": progress, "widget_y": widget.y(),
              "clip_local_y": visitor.clip_y-widget.y(), "visible_pixel_count": len(opaque),
              "visible_local_bounds": [min(x for x, _ in opaque), min(y for _, y in opaque),
                                       max(x for x, _ in opaque), max(y for _, y in opaque)] if opaque else None}
    widget.close()
    return scene, result


def main():
    app = QApplication.instance() or QApplication(["backyard-window-peek-preview"])
    art = CatArt()
    sheet = QImage(2000, 880, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#fafaf7"))
    p = QPainter(sheet)
    p.setFont(QFont("Helvetica", 19))
    p.setPen(QColor("#35453e"))
    records = []
    for row, (phase, values) in enumerate((("lowering", (0, .25, .5, .75, 1)),
                                         ("rising", (1, .75, .5, .25, 0)))):
        for column, progress in enumerate(values):
            scene, result = render_stage(art, progress, phase)
            records.append(result)
            x, y = column*400, row*440
            p.drawText(QRectF(x+10, y+10, 380, 35), Qt.AlignmentFlag.AlignCenter,
                       f"{phase}  progress={progress:g}")
            p.drawImage(QPoint(x, y+55), scene)
    p.end()
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    target = output / "window-peek-stages.png"
    if not sheet.save(str(target)):
        raise RuntimeError(f"Could not save {target}")
    (output / "window-peek-stages.json").write_text(json.dumps(records, indent=2)+"\n")
    print(target)
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
