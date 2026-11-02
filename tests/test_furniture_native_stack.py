"""Owned furniture ordering never activates or globally raises normal windows."""
import pytest
from backyard_visitor.cat_native import CocoaFurnitureStack


class Bridge(CocoaFurnitureStack):
    def __init__(self):
        self.order = [30, 40, 20, 10]
        self.levels = {10:0,20:0,30:0,40:0}
        self.visible = {10:True,20:True,30:True,40:True}
        self.calls = []
    def cls(self, name): return name
    def send(self, obj, selector, result=None, args=(), values=()):
        if selector in {'isMainThread','isKindOfClass:'}: return True
        if selector == 'window': return obj
        if selector == 'isVisible': return self.visible[obj]
        if selector == 'sharedApplication': return 1
        if selector == 'orderedWindows': return 2
        if selector == 'count': return len(self.order)
        if selector == 'objectAtIndex:': return self.order[values[0]]
        if selector == 'level': return self.levels[obj]
        if selector == 'windowNumber': return obj+100
        if selector == 'orderWindow:relativeTo:':
            assert values[0] == -1
            reference = values[1]-100
            self.order.remove(obj); self.order.insert(self.order.index(reference)+1,obj)
            self.calls.append((obj, values))
            return
        raise AssertionError('Unexpected native selector: '+selector)


def test_furniture_moves_behind_both_cats_without_changing_levels_or_cat_order():
    b = Bridge(); levels = dict(b.levels)
    assert b.configure([30,40],[10,20]) == {'reordered':2,'visible_furniture':2,'visible_cats':2}
    assert b.order[:2] == [20,10]
    assert b.levels == levels
    assert b.configure([30,40],[10,20])['reordered'] == 0
    assert len(b.calls) == 2


def test_later_furniture_reorder_is_repaired_once():
    b=Bridge(); b.configure([30,40],[10,20]); b.order.remove(30); b.order.insert(0,30)
    assert b.configure([30,40],[10,20])['reordered'] == 1
    assert b.order.index(30)>max(b.order.index(10),b.order.index(20))


def test_hidden_furniture_is_never_ordered_and_hidden_cat_is_not_reference():
    b=Bridge(); b.visible[40]=False; b.visible[10]=False
    assert b.configure([30,40],[10,20]) == {'reordered':1,'visible_furniture':1,'visible_cats':1}
    assert b.calls == [(30,(-1,120))]


def test_higher_cat_level_needs_no_same_level_write():
    b=Bridge(); b.levels[10]=26; b.levels[20]=9
    assert b.configure([30,40],[10,20])['reordered'] == 0
    assert not b.calls


def test_duplicate_views_are_reconciled_once():
    b=Bridge(); assert b.configure([30,30],[10,10])['reordered'] == 1


def test_empty_or_unordered_own_windows_do_not_raise_or_show_anything():
    b=Bridge(); assert b.configure([30],[])['reordered'] == 0
    b.order.remove(10); assert b.configure([30],[10])['reordered'] == 0
    assert not b.calls


@pytest.mark.parametrize('bad', [True,False,0,-1,None,'10'])
def test_invalid_view_rejected_before_native_lookup(bad):
    with pytest.raises(ValueError): Bridge().configure([bad],[10])


def test_same_window_cannot_be_both_cat_and_furniture():
    with pytest.raises(ValueError): Bridge().configure([10],[10])
