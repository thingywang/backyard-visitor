"""Own-window journal screenshots with isolated, synthetic sample collection data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter

def main():
    qt=QApplication(['BackyardJournalPreview'])
    c=Controller(qt,demo=True,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=77,visit_limit=1)
    out=ROOT/'output/v07';out.mkdir(parents=True,exist_ok=True)
    try:
        c.journal.show();c.journal.show_page(1);c.journal.refresh();qt.processEvents()
        assert c.journal.grab().save(str(out/'journal-locked.png'))
        c.engine.open_gate();c.engine.invite('amber');c.changed()
        for kind,count in [('feather',2),('bottle_cap',1),('toy_mouse',1)]:
            for _ in range(count):
                c.engine._drop(kind,300,300,'amber');c.engine.collect_gift(c.engine.gifts[-1].id)
        c.journal.show_page(4);c.journal.refresh();qt.processEvents()
        assert c.journal.grab().save(str(out/'journal-collection.png'))
        c.journal.show_page(1);c.journal.refresh();qt.processEvents()
        assert c.journal.grab().save(str(out/'journal-discovered.png'))
        print('Saved 3 own-window previews; sample counts are simulated demo data; no formal save accessed.')
    finally:c.shutdown();qt.processEvents()
if __name__=='__main__':main()
