"""Owned companion layers: cats above furniture without activating a window."""
import ctypes
from .desktop import _CocoaInputSurface


class CocoaCatLevel(_CocoaInputSurface):
    def __init__(self):
        super().__init__()
        self.cg.CGWindowLevelForKey.argtypes = [ctypes.c_int32]
        self.cg.CGWindowLevelForKey.restype = ctypes.c_int32
        # kCGStatusWindowLevelKey (9), plus one. Below pop-up menus, alerts,
        # and the cursor; higher than both the menu bar and its status items.
        self.raised_level = int(self.cg.CGWindowLevelForKey(9)) + 1
        self.window = None
        self.baseline = None
        self.applied = None

    def configure(self, view, enabled, *, topmost=False):
        """Keep ordinary companions just above topmost furniture.

        The menu-band override remains higher; non-topmost windows retain Qt's
        original normal level and are ordered only relative to owned furniture.
        """
        if not isinstance(enabled, bool) or not isinstance(topmost, bool):
            raise TypeError("Cat layer flags must be bool")
        if not self.send(self.cls('NSThread'), 'isMainThread', ctypes.c_bool):
            raise RuntimeError('Cat window level requires the main thread')
        if not self.send(view, 'isKindOfClass:', ctypes.c_bool,
                         (ctypes.c_void_p,), (self.cls('NSView'),)):
            raise RuntimeError('Cat window level requires its own NSView')
        window = self.send(view, 'window')
        if not window:
            raise RuntimeError('Cat window has no NSWindow')
        if window != self.window:
            self.window = window
            self.baseline = int(self.send(window, 'level', ctypes.c_long))
            self.applied = None
        target = self.raised_level if enabled else (
            min(self.baseline + 1, self.raised_level - 1) if topmost else self.baseline)
        # Readback also detects Qt resetting the level while keeping its NSView.
        actual = int(self.send(window, 'level', ctypes.c_long))
        if actual != target:
            self.send(window, 'setLevel:', None, (ctypes.c_long,), (target,))
            actual = int(self.send(window, 'level', ctypes.c_long))
        if actual != target:
            raise RuntimeError('Cat window level was not retained')
        self.applied = actual
        return actual


class CocoaFurnitureStack(_CocoaInputSurface):
    """Place visible owned furniture below visible owned cats at the same level.

    Higher/lower native levels already have deterministic order. In ordinary
    non-topmost mode all windows must remain at Qt's normal level, so move only
    furniture behind the backmost cat, never the cat ahead of another app.
    Call after show, native flag replacement and furniture drag, not per frame.
    """

    def _owned_window(self, view):
        if isinstance(view, bool) or not isinstance(view, int) or view <= 0:
            raise ValueError("Furniture stack requires own positive NSView addresses")
        if not self.send(view, 'isKindOfClass:', ctypes.c_bool,
                         (ctypes.c_void_p,), (self.cls('NSView'),)):
            raise RuntimeError("Furniture stack requires its own NSView")
        window = self.send(view, 'window')
        if not window:
            raise RuntimeError("Furniture stack has no NSWindow")
        return window

    def configure(self, furniture_views, cat_views):
        if not self.send(self.cls('NSThread'), 'isMainThread', ctypes.c_bool):
            raise RuntimeError("Furniture stack requires the main thread")
        furniture = list(dict.fromkeys(self._owned_window(v) for v in furniture_views))
        cats = list(dict.fromkeys(self._owned_window(v) for v in cat_views))
        if set(furniture) & set(cats):
            raise ValueError("A cat cannot also be its own furniture")
        visible = lambda w: bool(self.send(w, 'isVisible', ctypes.c_bool))
        furniture = [w for w in furniture if visible(w)]
        cats = [w for w in cats if visible(w)]
        if not furniture or not cats:
            return {'reordered': 0, 'visible_furniture': len(furniture), 'visible_cats': len(cats)}
        application = self.send(self.cls('NSApplication'), 'sharedApplication')
        ordered = self.send(application, 'orderedWindows')
        count = int(self.send(ordered, 'count', ctypes.c_ulong))
        order = [self.send(ordered, 'objectAtIndex:', ctypes.c_void_p,
                           (ctypes.c_ulong,), (index,)) for index in range(count)]
        levels = {w: int(self.send(w, 'level', ctypes.c_long)) for w in furniture+cats}
        changes = 0
        for window in furniture:
            same_level = [cat for cat in cats if levels[cat] == levels[window] and cat in order]
            if not same_level or window not in order:
                continue
            backmost = max(same_level, key=order.index)
            if order.index(window) > order.index(backmost):
                continue
            number = int(self.send(backmost, 'windowNumber', ctypes.c_long))
            if number <= 0:
                raise RuntimeError("Furniture stack cat has no window number")
            # NSWindowBelow=-1. Relative ordering preserves key/main windows;
            # importantly, do not use raise(), orderFrontRegardless or level+1
            # for ordinary windows when the user turned topmost off.
            self.send(window, 'orderWindow:relativeTo:', None,
                      (ctypes.c_long, ctypes.c_long), (-1, number))
            order.remove(window)
            order.insert(order.index(backmost)+1, window)
            changes += 1
        return {'reordered': changes, 'visible_furniture': len(furniture), 'visible_cats': len(cats)}
