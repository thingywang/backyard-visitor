"""Native policy restores Qt's own level and tolerates native window replacement."""
from backyard_visitor.cat_native import CocoaCatLevel

class Bridge(CocoaCatLevel):
    def __init__(self):
        self.raised_level=26;self.window=None;self.baseline=None;self.applied=None
        self.current=10;self.levels={10:8,20:0};self.writes=[]
    def cls(self,name):return name
    def send(self,obj,selector,result=None,args=(),values=()):
        if selector in ['isMainThread','isKindOfClass:']:return True
        if selector=='window':return self.current
        if selector=='level':return self.levels[obj]
        if selector=='setLevel:':self.levels[obj]=values[0];self.writes.append((obj,values[0]));return
        raise AssertionError(selector)

def test_level_round_trip_preserves_original_and_replaced_window():
    bridge=Bridge()
    assert bridge.configure(7,True)==26
    assert bridge.configure(7,True)==26
    assert bridge.writes==[(10,26)]
    assert bridge.configure(7,False)==8
    bridge.current=20
    assert bridge.configure(7,True)==26
    assert bridge.configure(7,False)==0

def test_qt_level_reset_is_repaired_without_assuming_unchanged_native_flags():
    bridge=Bridge();bridge.configure(7,True);bridge.levels[10]=8
    assert bridge.configure(7,True)==26
    assert bridge.writes==[(10,26),(10,26)]


def test_topmost_cat_stays_above_furniture_without_repeated_level_writes():
    bridge = Bridge()
    assert bridge.configure(7, False, topmost=True) == 9
    assert bridge.configure(7, False, topmost=True) == 9
    assert bridge.writes == [(10, 9)]
    assert bridge.configure(7, True, topmost=True) == 26
    assert bridge.configure(7, False, topmost=True) == 9
    assert bridge.configure(7, False, topmost=False) == 8


def test_non_topmost_recreated_window_keeps_normal_level_zero():
    bridge = Bridge(); bridge.configure(7, True, topmost=True)
    bridge.current = 20
    assert bridge.configure(7, False, topmost=False) == 0
    assert (20, 1) not in bridge.writes
