"""Generate the code-native wooden gate app icon using the same painter as the UI."""
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPainter,QPixmap,QColor,QLinearGradient,QPen
from PyQt6.QtCore import QRectF,Qt
from backyard_visitor.art import paint_door
app=QApplication([])
folder=ROOT/'build'/'BackyardVisitor.iconset';folder.mkdir(parents=True,exist_ok=True)
for size in (16,32,128,256,512):
    for ratio in (1,2):
        n=size*ratio
        pix=QPixmap(n,n);pix.fill(Qt.GlobalColor.transparent)
        p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.scale(n/1024,n/1024)
        gradient=QLinearGradient(0,0,1024,1024);gradient.setColorAt(0,QColor('#e8ecdc'));gradient.setColorAt(1,QColor('#b9c4a7'))
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(gradient);p.drawRoundedRect(QRectF(48,48,928,928),210,210)
        p.setBrush(QColor('#8e9e7b'));p.drawEllipse(QRectF(130,720,760,170))
        paint_door(p,QRectF(255,160,514,650),True)
        p.setPen(QPen(QColor('#718465'),10));p.drawLine(183,776,215,634);p.drawLine(217,783,260,668)
        p.end()
        suffix='@2x' if ratio==2 else ''
        pix.save(str(folder/f'icon_{size}x{size}{suffix}.png'))
        if n==1024:pix.save(str(ROOT/'backyard_visitor'/'assets'/'backyard-icon.png'))
subprocess.run(['iconutil','-c','icns',str(folder),'-o',str(ROOT/'backyard_visitor'/'assets'/'backyard-icon.icns')],check=True)
print('Generated native gate icon.')
