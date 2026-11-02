"""Render genuine settling key poses at shared scale and paw anchor."""
import os
from pathlib import Path
import sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QTransform
from PyQt6.QtWidgets import QApplication
from backyard_visitor.standing_art import StandingArt
from backyard_visitor.transition_art import TransitionArt

def main():
    app = QApplication.instance() or QApplication(["settling-preview"])
    frames = [StandingArt().frame(), *TransitionArt().load_frames()]
    if len(frames) != 4 or any(f is None for f in frames):
        raise RuntimeError("All four actual production key poses are required")
    out = QImage(1280, 620, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(QColor("#f6f3ec"))
    p = QPainter(out)
    for row, facing in enumerate((-1, 1)):
        if row: p.fillRect(QRectF(0, 310, 1280, 310), QColor("#344238"))
        for i, frame in enumerate(frames):
            if facing > 0: frame = frame.transformed(QTransform().scale(-1, 1))
            x, y = i*320, row*310
            p.setFont(QFont("Helvetica", 17))
            p.setPen(QColor("#36472f" if row == 0 else "#e3eadd"))
            p.drawText(x+12,y+28,["standing", "early lower", "half sit", "side seated"][i])
            p.setPen(QPen(QColor("#8aa27b"), 1))
            p.drawLine(x+4,y+270,x+316,y+270)
            # Common near-front-paw reference; no repositioning of generated anatomy.
            paw = 52.3 if facing == -1 else 144-52.3
            p.drawLine(round(x+16+paw*2),y+40,round(x+16+paw*2),y+285)
            p.drawPixmap(QRectF(x+16,y+54,288,224),frame,QRectF(frame.rect()))
    p.end()
    target = ROOT / "output/settling-contact.png"
    if not out.save(str(target)): raise RuntimeError("Could not save contact sheet")
    print(target)

if __name__ == "__main__":
    main()
