"""Production CatArt activity and initial seated-rise contact sheet."""
import json
import os
from pathlib import Path
import sys
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt6.QtCore import QRectF,Qt
from PyQt6.QtGui import QColor,QFont,QImage,QPainter
from PyQt6.QtWidgets import QApplication
from backyard_visitor.art import CatArt


def pose(art,state,facing=-1,coat="ginger",**options):
    image=QImage(288,224,QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent);image.setDevicePixelRatio(2)
    p=QPainter(image)
    art.paint(p,QRectF(0,0,144,112),state,age=0,facing=facing,coat=coat,**options)
    p.end()
    return image


def main():
    app=QApplication.instance() or QApplication(["activity-integration-preview"])
    art=CatArt()
    canvas=QImage(990,1260,QImage.Format.Format_ARGB32_Premultiplied);canvas.fill(QColor("#eee9df"))
    p=QPainter(canvas);p.setFont(QFont("Arial",15))
    for i,state in enumerate(("sleeping","swatting","resting","play_crouch")):
        y=30+i*245
        p.setPen(QColor("#36473e"));p.drawText(20,y-8,state+" - production CatArt (2x)")
        for j,(facing,coat) in enumerate(((-1,"ginger"),(1,"gray"),(-1,"tuxedo"))):
            x=18+j*325
            if j==2:p.fillRect(x-3,y-3,300,231,QColor("#25312d"))
            p.setPen(QColor("#b29f7d"));p.drawLine(x,y+212,x+288,y+212)
            frame=pose(art,state,facing,coat)
            p.drawImage(QRectF(x,y,288,224),frame,QRectF(frame.rect()))
    y=1010
    p.setPen(QColor("#36473e"));p.drawText(20,y-8,"Initial idle -> play watch: seated rise at progress 0, .5, .95")
    for j,progress in enumerate((0,.5,.95)):
        x=18+j*325
        p.setPen(QColor("#b29f7d"));p.drawLine(x,y+212,x+288,y+212)
        frame=pose(art,"play_crouch",rise_progress=progress)
        p.drawImage(QRectF(x,y,288,224),frame,QRectF(frame.rect()))
    p.end()
    output=ROOT/"output";output.mkdir(exist_ok=True)
    canvas.save(str(output/"activity-integration-contact.png"))
    report={"renderer":"CatArt","compiled_base":art._compiled is not None,
            "states":["sleeping","swatting","resting","play_crouch"],
            "coats":["ginger","gray","tuxedo"],"rise_origin":"idle",
            "no_crouch_to_run_inbetween_claim":True}
    (output/"activity-integration-verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report))


if __name__=="__main__":main()
