"""Compile the unchanged generated rear cutout to its small Qt runtime canvas."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from PyQt6.QtWidgets import QApplication
from backyard_visitor.rear_art import compile_frame
app = QApplication(['compile-rear-tail'])
frame = compile_frame()
print(f'Rear pose: {frame.width()}x{frame.height()} physical pixels, DPR {frame.devicePixelRatio()}.')
