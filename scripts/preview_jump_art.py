"""QA contact sheet of actual jump frames; never edits the source artwork."""
import json
import os
from pathlib import Path
import sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QTransform
from PyQt6.QtWidgets import QApplication
from backyard_visitor.jump_art import JumpArt
from backyard_visitor.standing_art import StandingArt

def main():
    app = QApplication.instance() or QApplication(["jump-preview"])
    art = JumpArt()
    frames = [StandingArt().frame(), *art.load_frames()]
    if len(frames) != 4 or any(f is None for f in frames):
        raise RuntimeError(art.load_error or "Four real key poses are required")
    sheet = QImage(1280, 620, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#f6f3ec"))
    p = QPainter(sheet)
    for row in range(2):
        if row: p.fillRect(QRectF(0,310,1280,310), QColor("#344238"))
        for i, frame in enumerate(frames):
            x, y = i*320, row*310
            if row: frame = frame.transformed(QTransform().scale(-1,1))
            p.setFont(QFont("Helvetica",17))
            p.setPen(QColor("#36472f" if not row else "#e3eadd"))
            p.drawText(x+12,y+28,["standing","takeoff","compressed","recover"][i])
            p.setPen(QPen(QColor("#8aa27b"),1))
            p.drawLine(x+4,y+270,x+316,y+270)
            paw = 144-52.3 if row else 52.3
            p.drawLine(round(x+16+paw*2),y+40,round(x+16+paw*2),y+285)
            p.drawPixmap(QRectF(x+16,y+54,288,224),frame,QRectF(frame.rect()))
            if i:
                mx, my = art.mouth_anchors[i-1]
                if row: mx = 144-mx
                p.setPen(QPen(QColor("#b67b3b"),1))
                p.drawEllipse(QPointF(x+16+mx*2,y+54+my*2),2.5,2.5)
    p.end()
    output = ROOT / "output/jump-contact.png"
    if not sheet.save(str(output)): raise RuntimeError("Could not save contact sheet")
    data = json.loads((ROOT/"backyard_visitor/assets/jump_transitions.json").read_text())
    source = QImage(str(ROOT/"backyard_visitor/assets"/data["file"]))
    rows = []
    for info, mouth in zip(data["frames"], art.mouth_anchors):
        image = source.copy(*info["rect"]).convertToFormat(QImage.Format.Format_RGBA8888)
        ptr = image.constBits(); ptr.setsize(image.sizeInBytes()); alpha = bytes(ptr)[3::4]
        rows.append({"name":info["name"],"transparent_fraction":alpha.count(0)/len(alpha),
                     "mouth_logical":mouth,"rect":info["rect"],"foot_anchor":info["foot_anchor"]})
    (ROOT/"output/jump-art-verification.json").write_text(json.dumps(rows,indent=2)+"\n")
    print(output)
    print(json.dumps(rows))

if __name__ == "__main__":
    main()
