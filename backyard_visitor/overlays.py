from __future__ import annotations

import math
import time

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRegion, QTransform
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from .art import paint_bowl, paint_door
from .gift_geometry import carried_gift_rect
from .fight_art import FightArt, cloud_path, scene_mask
from .desktop import configure_cocoa_spaces

BASE_FLAGS = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint |
              Qt.WindowType.WindowDoesNotAcceptFocus)
FLAGS = BASE_FLAGS | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint


def overlay_flags(topmost):
    # A macOS Tool is an NSPanel above ordinary windows even without the
    # explicit topmost hint. Opting out therefore changes the window type too.
    return FLAGS if topmost else BASE_FLAGS | Qt.WindowType.Window


def artwork_scale(c):
    value = getattr(c, 'artwork_scale', 1.)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else 1.


def to_display(c, x, y):
    convert = getattr(c, 'to_display', None)
    return convert(x, y) if callable(convert) else (x, y)


def to_scene(c, x, y):
    convert = getattr(c, 'to_scene', None)
    return convert(x, y) if callable(convert) else (x, y)


def scene_screen(c, *, available=False):
    convert = getattr(c, 'scene_rect', None)
    if callable(convert):
        value = convert(available=available)
        return QRectF(value.x, value.y, value.width, value.height)
    return QRectF(c.screen.availableGeometry() if available else c.screen.geometry())


def queue_furniture_stack(c):
    callback = getattr(c, 'queue_furniture_stack', None)
    if callable(callback):
        callback()


class Overlay(QWidget):
    def __init__(self,c,width,height,title):
        super().__init__(None,overlay_flags(getattr(c, "settings", {}).get("overlay_topmost", True)))
        self.c = c
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.base_size = QSize(round(width), round(height))
        self._unscaled_native = False
        self._scene_mask = None
        self.set_scene_size(width, height)
        self.setWindowTitle(title)
        self.setAccessibleName(title)

    @property
    def drawing_scale(self):
        return 1. if self._unscaled_native else artwork_scale(self.c)

    def scene_size(self):
        return QSize(self.base_size)

    def scene_canvas_rect(self):
        return QRectF(0, 0, self.base_size.width(), self.base_size.height())

    def set_scene_size(self, width, height):
        self.base_size = QSize(max(1, round(width)), max(1, round(height)))
        factor = self.drawing_scale
        QWidget.setFixedSize(self, max(1, math.ceil(self.base_size.width()*factor)),
                             max(1, math.ceil(self.base_size.height()*factor)))

    def set_scene_mask(self, region):
        self._scene_mask = QRegion(region)
        transform = QTransform().scale(self.drawing_scale, self.drawing_scale)
        self.setMask(transform.map(self._scene_mask))

    def rescale(self):
        self.set_scene_size(self.base_size.width(), self.base_size.height())
        if self._scene_mask is not None:
            self.set_scene_mask(self._scene_mask)
        refresh = getattr(self, 'refresh_input_region', None)
        if callable(refresh):
            refresh()
        self.update()

    def scene_painter(self):
        painter = QPainter(self)
        painter.scale(self.drawing_scale, self.drawing_scale)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        return painter

    def event(self, event):
        handled = super().event(event)
        if event.type() == QEvent.Type.WinIdChange and hasattr(self, 'c') and self.isVisible():
            queue_furniture_stack(self.c)
        return handled

    def setVisible(self, visible):
        was_visible = self.isVisible()
        super().setVisible(visible)
        if visible and not was_visible:
            queue_furniture_stack(self.c)
        # Cocoa may replace its NSPanel after ShowEvent while completing show.
        # Apply only after that work returns, once per hidden->visible change.
        # place_bowl calls setVisible every sync; visible windows do no native work.
        if visible and not was_visible and self.isVisible() and QApplication.platformName() == "cocoa":
            try:
                self._native_spaces = configure_cocoa_spaces(int(self.winId()),
                    bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            except (RuntimeError, OSError) as exc:
                # A Qt virtual callback must not propagate a native failure.
                # Keep ordinary desktop companionship and expose the limitation.
                if not getattr(self, "_space_warning_reported", False):
                    self._space_warning_reported = True
                    self.c.note(f"全屏陪伴设置暂未生效：{exc}")

    def set_topmost(self, enabled):
        flags = self.windowFlags() & ~Qt.WindowType.WindowType_Mask & ~Qt.WindowType.WindowStaysOnTopHint
        flags |= Qt.WindowType.Tool if enabled else Qt.WindowType.Window
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if flags == self.windowFlags():
            return
        position, visible, mask = self.pos(), self.isVisible(), self.mask()
        # Qt may reuse the NSWindow pointer but assign a new baseline level.
        # Shared fight smoke needs the same cache invalidation as a single cat.
        if hasattr(self, "_cat_level"):
            del self._cat_level
        # Changing a native window type hides it. Restore only windows that
        # were already visible, without activation or showing the cleanup bin.
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.move(position)
        if mask.isEmpty():
            self.clearMask()
        else:
            self.setMask(mask)
        if visible:
            self.show()
            self.move(position)
        queue_furniture_stack(self.c)


class Gate(Overlay):
    def __init__(self,c):
        super().__init__(c,30,30,"后院小门")
        self.setToolTip("点击开关小门 · 右键打开手册 · 拖动移动")
        self.drag_active = False

    def paintEvent(self,e):
        p=self.scene_painter(); paint_door(p,self.scene_canvas_rect(),self.c.engine.gate_open)

    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.RightButton:
            self.c.menu(e.globalPosition().toPoint()); return
        self.drag_active = e.button() == Qt.MouseButton.LeftButton
        self.press=e.globalPosition().toPoint(); self.offset=self.press-self.pos(); self.dragged=False

    def mouseMoveEvent(self,e):
        if self.drag_active and e.buttons() & Qt.MouseButton.LeftButton:
            if (e.globalPosition().toPoint()-self.press).manhattanLength()>4*self.drawing_scale:
                self.dragged=True; self.move(e.globalPosition().toPoint()-self.offset)

    def mouseReleaseEvent(self,e):
        if e.button() != Qt.MouseButton.LeftButton or not self.drag_active:
            return
        self.drag_active = False
        if not getattr(self,"dragged",False):
            self.c.toggle_gate()


class CatWindow(Overlay):
    def __init__(self,c,cat_id):
        super().__init__(c,144,112,"后院访客")
        self.cat_id=cat_id
        self.pressed=False; self.carrying=False; self.stroking=False
        self.press_time=0.0; self.last_stroke=0.0
        self._input_region = None
        self.hold=QTimer(self); self.hold.setSingleShot(True); self.hold.setInterval(600); self.hold.timeout.connect(self.begin_carry)
        self.setMouseTracking(True)

    def visitor(self):
        return self.c.engine.visitors.get(self.cat_id)

    def set_topmost(self, enabled):
        current = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if current != bool(enabled) and hasattr(self, "_cat_level"):
            # setWindowFlags may reuse the pointer but changes its Qt baseline.
            del self._cat_level
        super().set_topmost(enabled)
        if self.isVisible():
            origin = self.scene_position()
            self.sync_native_position(origin.x(), origin.y())
            self.refresh_input_region()
            self.update()

    def sync_native_position(self, x, y):
        display_x, display_y = to_display(self.c, x, y)
        display_x, display_y = round(display_x), round(display_y)
        if QApplication.platformName() == "cocoa":
            try:
                if not hasattr(self, "_cat_level"):
                    from .cat_native import CocoaCatLevel
                    self._cat_level = CocoaCatLevel()
                enabled = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                self._cat_level.configure(int(self.winId()),
                    enabled and display_y < self.c.screen.availableGeometry().top(), topmost=enabled)
            except (RuntimeError, OSError) as exc:
                if not getattr(self, "_cat_level_warning", False):
                    self._cat_level_warning = True
                    self.c.note(f"猫咪顶部显示暂未生效：{exc}")
        if (self.x(), self.y()) != (display_x, display_y):
            self.move(display_x, display_y)

    def setVisible(self, visible):
        super().setVisible(visible)
        # Showing or changing flags can replace Cocoa's NSPanel and reset its
        # level; restore the desired position after that native work completes.
        if visible and self.isVisible():
            visitor = self.visitor()
            if visitor is not None:
                self.sync_native_position(round(visitor.x), round(visitor.y))
                self.refresh_input_region()


    def scene_position(self):
        visitor = self.visitor()
        if visitor:
            return QPoint(round(visitor.x), round(visitor.y))
        x, y = to_scene(self.c, self.x(), self.y())
        return QPoint(round(x), round(y))

    def refresh_input_region(self):
        # If Cocoa constrains a non-topmost window below its menu bar, retain
        # the logical cat anchor by translating both paint and its input mask.
        origin = self.scene_position()
        region = QRegion(self.scene_canvas_rect().toAlignedRect()) & QRegion(scene_screen(self.c).translated(-origin.x(), -origin.y()).toAlignedRect())
        v = self.visitor()
        rear = getattr(v, "rear_blend", 0.) if v else 0.
        if rear > 0:
            coat = self.c.engine.cats[self.cat_id].render_coat
            shape = self.c.rear_art.input_region(v.age, rear, coat)
            if rear < 1:
                shape |= QRegion(QRect(0, 0, 144, 112))
            region &= shape
        elif v and v.state == "window_rear":
            region &= QRegion(QRect(0, 0, 144, 112))
        if v and v.clip_y is not None and rear <= 0:
            height = max(0, min(self.base_size.height(), math.ceil(v.clip_y - origin.y())))
            region &= QRegion(QRect(0, 0, self.base_size.width(), height))
        if v and getattr(v, "tree_trip", None) is not None:
            from .tree_art import canvas_rect
            region &= QRegion(canvas_rect(v.tree_pose).toAlignedRect())
            clip = self.c.cat_clip(origin, self.scene_size(), self.cat_id)
        elif v and getattr(v, "depth_trip", None) is not None:
            scale=v.depth_scale
            region &= QRegion(QRectF(72*(1-scale),108*(1-scale),144*scale,112*scale).toAlignedRect())
            clip = self.c.cat_clip(origin, self.scene_size(), self.cat_id)
        else:
            clip = self.c.cat_clip(origin, self.scene_size())
        if clip is not None:
            region &= QRegion(clip.toFillPolygon().toPolygon())
        display_x, display_y = to_display(self.c, origin.x(), origin.y())
        transform = QTransform()
        transform.translate(display_x-self.x(), display_y-self.y())
        transform.scale(self.drawing_scale, self.drawing_scale)
        region = transform.map(region)
        region &= QRegion(self.rect())
        # Aligning a scene boundary before scaling may round it outward by one
        # pixel. Input must still stay on the selected physical display.
        region &= QRegion(self.c.screen.geometry().translated(-self.x(), -self.y()))
        self._has_visible_content = not region.isEmpty()
        if region.isEmpty():
            region = QRegion(-2, -2, 1, 1)
        if region != self._input_region:
            self._input_region = region
            self.setMask(region)

    def paintEvent(self,e):
        v=self.visitor()
        if not v or v.state=="fighting":return
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        origin = self.scene_position()
        display_x, display_y = to_display(self.c, origin.x(), origin.y())
        p.translate(display_x-self.x(), display_y-self.y())
        p.scale(self.drawing_scale, self.drawing_scale)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Clip at the selected display boundary even if another monitor touches
        # this edge; a visitor must enter through the edge, never leak next door.
        screen=scene_screen(self.c)
        p.setClipRect(screen.translated(-origin.x(), -origin.y()))
        depth=getattr(v,"depth_trip",None) is not None
        tree=getattr(v,"tree_trip",None) is not None
        clip=self.c.cat_clip(origin,self.scene_size(),self.cat_id) if depth or tree else self.c.cat_clip(origin,self.scene_size())
        if clip is not None:p.setClipPath(clip,Qt.ClipOperation.IntersectClip)
        rear = getattr(v, "rear_blend", 0.)
        if v.clip_y is not None and rear <= 0:
            p.setClipRect(QRectF(0, 0, self.base_size.width(), max(0, v.clip_y-origin.y())), Qt.ClipOperation.IntersectClip)
        cat=self.c.engine.cats[self.cat_id]
        if tree:
            from .tree_art import paint_tree_visit
            paint_tree_visit(p, v.tree_pose, self.c.art, self.c.depth_art, cat.render_coat)
            return
        if depth:
            from .depth_art import select_depth_pose
            snapshot=v.depth_pose;selected=select_depth_pose(snapshot)
            scale=v.depth_scale
            p.translate(72*(1-scale),108*(1-scale));p.scale(scale,scale)
            frame=self.c.depth_art.frame(selected['kind'],selected['index'],cat.render_coat) if selected else None
            if frame is not None:
                p.drawPixmap(QRectF(0,0,144,112),frame,QRectF(frame.rect()))
            else:
                facing=(-1 if selected['kind']=='side_left' else 1) if selected else snapshot['facing']
                pose='idle' if snapshot['pose']=='turn' else snapshot['pose']
                self.c.art.paint(p,QRectF(0,0,144,112),pose,v.age,facing,cat.render_coat,
                                 gait_phase=snapshot['gait_phase'],run_phase=snapshot.get('run_phase'))
            return
        if rear > 0:
            self.c.rear_art.paint(p, v.age, cat.render_coat, rear)
        if rear >= 1:
            return
        if v.state == "window_peeking" and v.clip_y is not None:
            blend = max(0., min(1., (v.peek_progress - .48) / .08))
            if blend < 1:
                self.c.art.paint(p, QRectF(0, 6, 144, 112), "idle", v.age, v.facing, cat.render_coat, (1-blend)*(1-rear))
            if blend > 0:
                head_y = v.clip_y - origin.y() - 32
                self.c.art.paint(p, QRectF(0, head_y, 144, 112), "window_peeking", v.age, v.facing, cat.render_coat, blend*(1-rear))
        else:
            charging = getattr(v, "takeoff_remaining", 0) > 0
            offset = (6 if v.takeoff_edge_id else 0) if charging else (6 if v.window_edge_id or v.window_target_id else 0)
            if v.state == "climbing":
                offset = 0
            from .model import LANDING_SECONDS, SETTLE_SECONDS, RISE_SECONDS, TAKEOFF_SECONDS
            land_progress = (1 - v.landing_remaining / LANDING_SECONDS
                             if v.landing_remaining > 0 else None)
            pose = "sleeping" if v.on_back else "idle" if v.state == "window_rear" else v.state
            if pose == "teasing":
                pose = "running" if self.c.engine.teaser_phase == "chase" else "play_crouch"
            self.c.art.paint(p, QRectF(0,offset,144,112), pose, v.age, v.facing, cat.render_coat, opacity=1-rear,
                             gait_phase=v.gait_phase, run_phase=getattr(v, "run_phase", 0),
                             jump_vy=getattr(v, "jump_vy", None),
                             jump_pose_phase=getattr(v, "jump_pose_phase", None),
                             jump_vertical_span=getattr(v, "jump_vertical_span", None),
                             climb_phase=getattr(v, "climb_phase", None),
                             land_progress=land_progress,
                             posture_kind=getattr(v, "posture_kind", ""),
                             posture_source=getattr(v, "posture_source", ""),
                             posture_target=getattr(v, "posture_target", ""),
                             posture_progress=(1-v.posture_remaining/v.posture_duration
                                               if getattr(v, "posture_remaining", 0) > 0 and v.posture_duration > 0 else None),
                             settling=v.settle_remaining > 0, turning=v.turn_remaining > 0,
                             settle_progress=(1 - v.settle_remaining / SETTLE_SECONDS
                                              if v.settle_remaining > 0 else None),
                             rise_progress=(1 - v.rise_remaining / RISE_SECONDS
                                            if v.rise_remaining > 0 else None),
                             takeoff_progress=(1 - v.takeoff_remaining / TAKEOFF_SECONDS
                                               if charging else None))
        if v.state=="purring":
            from .affection_art import paint_purr_hearts
            paint_purr_hearts(p, v.state_age, v.facing, 1-rear,
                              text_on_left=(v.facing < 0 and v.true_name_remaining <= 0
                                            and getattr(v, "gift_response_remaining", 0) > 0))
            if v.true_name_remaining > 0:
                # A rare recognition has its own quiet, warm response.
                # No hidden name or implementation detail appears on screen.
                pulse = .88 + .12 * math.sin(v.age * 2.8)
                p.save(); p.setOpacity(pulse)
                p.setPen(QPen(QColor("#c4b49c"), .8))
                p.setBrush(QColor(250, 246, 231, 230))
                p.drawRoundedRect(QRectF(69, 1, 71, 24), 9, 9)
                p.setPen(QColor("#876b53")); p.setFont(QFont("PingFang SC", 10))
                p.drawText(QRectF(69, 1, 71, 24), Qt.AlignmentFlag.AlignCenter, "原来是你")
                p.setPen(QColor("#a88470")); p.setFont(QFont("PingFang SC", 10))
                p.drawText(QRectF(84, 27, 56, 18), Qt.AlignmentFlag.AlignCenter, "♡ 呼噜")
                p.restore()
            elif getattr(v, "gift_response_remaining", 0) > 0:
                # A collected gift gets a brief response near the head.
                # It shares the cat window and the model clock, so pause
                # and cleanup do not need another overlay or timer.
                p.save(); p.setOpacity(min(1., v.gift_response_remaining / .45))
                bubble = QRectF(7 if v.facing < 0 else 82, 2, 55, 23)
                p.setPen(QPen(QColor("#c4b49c"), .8))
                p.setBrush(QColor(250, 246, 231, 225))
                p.drawRoundedRect(bubble, 9, 9)
                p.setPen(QColor("#a47b66")); p.setFont(QFont("PingFang SC", 11))
                p.drawText(bubble, Qt.AlignmentFlag.AlignCenter, "♡ 呼噜")
                p.restore()
            else:
                p.setPen(QColor("#7c866d")); p.setFont(QFont("PingFang SC",11)); p.drawText(95,22,"呼噜")
        if v.carrying_gift:
            charging = getattr(v, "takeoff_remaining", 0) > 0
            surface_offset = (6 if v.takeoff_edge_id else 0) if charging else (6 if v.window_edge_id or v.window_target_id else 0)
            if v.state == "climbing":
                surface_offset = 0
            rect = QRectF(*carried_gift_rect(v.facing, surface_offset=surface_offset))
            if charging:
                from .model import TAKEOFF_SECONDS
                mouth = self.c.art.takeoff_mouth(1-v.takeoff_remaining/TAKEOFF_SECONDS, v.facing)
                rect.moveCenter(QPointF(mouth[0], mouth[1]+surface_offset))
            elif v.landing_remaining > 0 and v.state in {"idle", "purring", "eating", "grooming", "walking", "running", "entering"}:
                from .model import LANDING_SECONDS
                mouth = self.c.art.landing_mouth(1-v.landing_remaining/LANDING_SECONDS, v.facing)
                rect.moveCenter(QPointF(mouth[0], mouth[1]+surface_offset))
            elif v.state in {"jumping", "climbing"}:
                mouth = self.c.art.parkour_mouth(v.state, v.facing, cat.render_coat,
                                               jump_vy=getattr(v, "jump_vy", None),
                                               jump_pose_phase=getattr(v, "jump_pose_phase", None),
                                               jump_vertical_span=getattr(v, "jump_vertical_span", None),
                                               climb_phase=getattr(v, "climb_phase", None))
                if mouth is not None:
                    rect.moveCenter(QPointF(mouth[0], mouth[1]+surface_offset))
            elif v.state in {"running", "fleeing"} and v.rise_remaining <= 0 and v.turn_remaining <= 0:
                mouth = self.c.art.run_mouth(getattr(v, "run_phase", 0), v.facing)
                rect.moveCenter(QPointF(mouth[0], mouth[1]+surface_offset))
            self.c.paint_gift(p, rect,
                              v.carrying_gift, facing=v.facing)

    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.RightButton:
            self.context_menu(e.globalPosition().toPoint()); return
        if e.button()!=Qt.MouseButton.LeftButton:return
        v=self.visitor()
        if not v:return
        if v.state=="fighting":
            return  # The shared cloud owns all intervention input.
        self.pressed=True; self.carrying=False; self.stroking=False
        self.press=e.globalPosition().toPoint()
        sx, sy = to_scene(self.c, self.press.x(), self.press.y())
        self.offset=QPointF(sx-v.x, sy-v.y); self.press_time=time.monotonic()
        self.hold.start()

    def begin_carry(self):
        if self.pressed and not self.stroking:
            self.carrying=self.c.engine.carry(self.cat_id)
            if self.carrying:self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.c.wake()

    def mouseMoveEvent(self,e):
        if not self.pressed:return
        v=self.visitor()
        if not v:return
        point=e.globalPosition().toPoint()
        if self.carrying:
            sx, sy = to_scene(self.c, point.x(), point.y())
            pos=QPointF(sx, sy)-self.offset; v.x=float(pos.x()); v.y=float(pos.y()); self.sync_native_position(pos.x(), pos.y()); self.refresh_input_region(); self.update()
        elif (point-self.press).manhattanLength()>8*self.drawing_scale:
            self.stroking=True; self.hold.stop()
            if time.monotonic()-self.last_stroke>2:
                self.c.engine.pet(self.cat_id); self.last_stroke=time.monotonic(); self.c.changed()

    def mouseReleaseEvent(self,e):
        if e.button()!=Qt.MouseButton.LeftButton or not self.pressed:return
        self.hold.stop(); self.pressed=False
        v=self.visitor()
        if v and self.carrying:
            self.c.engine.release(self.cat_id,v.x,v.y)
        elif v and not self.stroking:
            self.c.engine.pet(self.cat_id)
        self.carrying=False; self.unsetCursor(); self.c.changed()

    def context_menu(self,pos):
        visitor = self.visitor()
        if visitor and visitor.state == "fighting":
            if self.c.fight_overlay is not None:
                self.c.fight_overlay.open_menu(pos)
            return
        menu=QMenu()
        cat=self.c.engine.cats[self.cat_id]
        title=menu.addAction(cat.display_name); title.setEnabled(False)
        if cat.is_neighbor:
            menu.addAction("轻声呼唤",lambda:self.c.call_cat(self.cat_id))
            menu.addAction("起个昵称",lambda:self.c.rename_cat(self.cat_id))
        if self.c.settings.get('depth_enabled',False) or (visitor and getattr(visitor,'depth_trip',None) is not None):
            in_backyard=visitor is not None and visitor.depth_trip is not None
            menu.addAction('回到前层' if in_backyard else '去后院转转',lambda:self.c.send_to_backyard(self.cat_id))
        if self.c.settings.get('tree_enabled', False) or (visitor and getattr(visitor, 'tree_trip', None) is not None):
            in_tree = visitor is not None and getattr(visitor, 'tree_trip', None) is not None
            menu.addAction('回到桌面' if in_tree else '去爬架玩', lambda: self.c.send_to_tree(self.cat_id))
        pet=menu.addAction("轻轻抚摸",lambda:self.c.pet(self.cat_id)); pet.setEnabled(cat.trust>=55 and visitor is not None and visitor.state not in {"top_entering", "top_leaving"})
        menu.addAction("抱起来（也可长按）",self.begin_menu_carry)
        v=self.visitor()
        play=menu.addAction("和它玩 · 15 秒",lambda:self.c.start_play(self.cat_id))
        play.setEnabled(bool(v and (v.state in {"idle", "sleeping", "purring", "walking", "running", "playing", "resting", "window_rear"}
                                   or getattr(v, "tree_trip", None) is not None)
                             and not v.carrying_gift and not v.demo_gift_pending
                             and getattr(v, "posture_remaining", 0) <= 0
                             and not v.true_name_pending and v.true_name_remaining <= 0
                             and self.c.engine.gate_open and not self.c.paused
                             and self.c.engine.active_teaser_id is None))
        if cat.adopted:
            if self.c.engine.bed is not None and self.c.engine.bed.cat_id == self.cat_id:
                menu.addAction("收起猫窝", self.c.remove_bed)
            else:
                menu.addAction("布置猫窝", lambda: self.c.place_bed(self.cat_id))
        menu.addSeparator(); menu.addAction("访客手册",self.c.show_journal); menu.addAction("关门送客",self.c.close_gate)
        menu.exec(pos)

    def begin_menu_carry(self):
        # Context menu teaches the actual hold gesture rather than leaving a stuck carried state.
        self.c.note("按住猫咪不动 0.6 秒，再拖向屏幕边缘，就能抱走。")


def fight_cloud_path(rect):
    """Shared cloud silhouette; decorative tails remain part of the same window."""
    return cloud_path(rect)


class FightOverlay(Overlay):
    """One local cloud for both cats; selection never blocks the model timer."""
    def __init__(self, c, cat_ids):
        super().__init__(c, 144, 112, "访客争执 · 点击选择护住谁")
        self.cat_ids = tuple(cat_ids)
        self._fight_art = FightArt(c.art.tail)
        self._menu = None
        self.setToolTip("点击或右键烟雾，选择护住哪位访客")

    def sync_geometry(self):
        visitors = [self.c.engine.visitors.get(cid) for cid in self.cat_ids]
        if any(v is None or v.state != "fighting" for v in visitors):
            return False
        bounds = QRect(round(visitors[0].x), round(visitors[0].y), 144, 112)
        for v in visitors[1:]:
            bounds = bounds.united(QRect(round(v.x), round(v.y), 144, 112))
        # Reserve local space for two tails and brief marks, without moving cats.
        bounds = bounds.adjusted(-18, -28, 18, 0).intersected(scene_screen(self.c).toAlignedRect())
        if bounds.isEmpty():
            return False
        dx, dy = to_display(self.c, bounds.x(), bounds.y())
        desired = QRect(round(dx), round(dy), math.ceil(bounds.width()*self.drawing_scale), math.ceil(bounds.height()*self.drawing_scale))
        changed = self.geometry() != desired
        if changed:
            self.dismiss_menu()
            self.set_scene_size(bounds.width(), bounds.height())
            self.move(desired.topLeft())
        if changed or self.mask().isEmpty():
            self.set_scene_mask(QRegion(scene_mask(self.scene_canvas_rect()).toFillPolygon().toPolygon(), Qt.FillRule.WindingFill))
        self.update()
        return True

    def visual_state(self):
        """Public coat/clock inputs shared with isolated render verification."""
        visitors = [self.c.engine.visitors.get(cid) for cid in self.cat_ids]
        ordered = sorted((v for v in visitors if v is not None), key=lambda v: (v.x, v.cat_id))
        coats = tuple(self.c.engine.cats[v.cat_id].render_coat for v in ordered)
        age = min((v.state_age for v in ordered), default=0.0)
        return coats, age

    def paintEvent(self, event):
        p = self.scene_painter()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        coats, age = self.visual_state()
        if len(coats) == 2:
            self._fight_art.paint(p, self.scene_canvas_rect(), coats, age)

    def open_menu(self, pos):
        if self.c._quitting or self.c.paused or not self.c._screen_ready or self.c.fight_overlay is not self:
            return
        if any(cid not in self.c.engine.visitors or self.c.engine.visitors[cid].state != "fighting" for cid in self.cat_ids):
            return
        self.dismiss_menu()
        menu = QMenu(self)
        self._menu = menu
        for cid in self.cat_ids:
            action = menu.addAction("护住" + self.c.engine.cats[cid].display_name)
            action.setData(cid)
            action.triggered.connect(lambda checked=False, selected=cid, epoch=self.c._desktop_epoch: self._choose(selected, epoch))
        menu.aboutToHide.connect(lambda m=menu: self._menu_hidden(m))
        menu.popup(pos)

    def _choose(self, cat_id, epoch):
        if (self.c.fight_overlay is self and epoch == self.c._desktop_epoch and not self.c._quitting and not self.c.paused
                and self.c._screen_ready and cat_id in self.cat_ids
                and all(cid in self.c.engine.visitors and self.c.engine.visitors[cid].state == "fighting" for cid in self.cat_ids)):
            self.c.intervene(cat_id)

    def _menu_hidden(self, menu):
        if self._menu is menu:
            self._menu = None
            menu.deleteLater()

    def dismiss_menu(self):
        menu, self._menu = self._menu, None
        if menu is not None:
            menu.close()
            menu.deleteLater()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self.open_menu(event.globalPosition().toPoint())

    def closeEvent(self, event):
        self.dismiss_menu()
        super().closeEvent(event)


class Bowl(Overlay):
    def __init__(self,c):
        super().__init__(c,62,40,"小鱼干碗")
        self.setToolTip("单击、双击或右键添食 · 拖动放置猫碗\n吃饱后会休息一会儿，稍后再来吃。")
        self.dragged=False; self.drag_active=False
        self.feed_click = QTimer(self)
        self.feed_click.setSingleShot(True)
        self.feed_click.timeout.connect(self.c.feed)

    def paintEvent(self,e):
        p=self.scene_painter(); paint_bowl(p,self.scene_canvas_rect(),self.c.engine.food)

    def mousePressEvent(self,e):
        queue_furniture_stack(self.c)
        if e.button() == Qt.MouseButton.RightButton:
            self.feed_click.stop()
            self.drag_active = False
            self.c.feed()
            e.accept()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.press=e.globalPosition().toPoint(); self.offset=self.press-self.pos()
        self.dragged=False; self.drag_active=True

    def mouseDoubleClickEvent(self,e):
        if e.button() == Qt.MouseButton.LeftButton:
            # Qt sends press/release/double-click/release. Coalesce the pending
            # single click, so one physical double click adds exactly one meal.
            self.feed_click.stop()
            self.drag_active=False
            self.c.feed()
            e.accept()

    def mouseMoveEvent(self,e):
        queue_furniture_stack(self.c)
        if self.drag_active and e.buttons() & Qt.MouseButton.LeftButton and (e.globalPosition().toPoint()-self.press).manhattanLength()>4*self.drawing_scale:
            self.feed_click.stop()
            self.dragged=True; self.move(e.globalPosition().toPoint()-self.offset)

    def mouseReleaseEvent(self,e):
        queue_furniture_stack(self.c)
        if e.button() != Qt.MouseButton.LeftButton or not self.drag_active:
            return
        self.drag_active=False
        if self.dragged:
            sx, sy = to_scene(self.c, self.x(), self.y())
            self.c.engine.set_bowl(sx+31,sy+20); self.c.place_bowl()
        else:
            self.feed_click.start(QApplication.doubleClickInterval() + 30)
        self.c.changed()


class GiftWindow(Overlay):
    def __init__(self,c,gift):
        super().__init__(c,38,38,"桌面小礼物")
        self.gift_id=gift.id; self.kind=gift.kind
        self.setToolTip("点击收下 · 拖到清理篮可丢弃")
        dx, dy = to_display(self.c, gift.x, gift.y)
        self.move(round(dx),round(dy)); self.dragged=False; self.drag_active=False

    def gift(self):
        return next((g for g in self.c.engine.gifts if g.id == self.gift_id), None)

    def refresh_input_region(self):
        screen = self.c.screen.geometry()
        visible = QRegion(self.rect()).intersected(QRegion(screen.translated(-self.x(), -self.y())))
        mask = visible if not visible.isEmpty() else QRegion(QRect(-2, -2, 1, 1))
        if mask != self.mask():
            self.setMask(mask)

    def paintEvent(self,e):
        gift = self.gift()
        facing = gift.facing if gift else -1
        p=self.scene_painter()
        if gift and gift.state == "escaping":
            from .model import MOUSE_ESCAPE_SPEED
            # Ground distance also freezes when paused; wall time does not.
            p.translate(0, math.sin(gift.x * 36 / MOUSE_ESCAPE_SPEED)*0.7)
        self.c.paint_gift(p,self.scene_canvas_rect(),self.kind,facing=facing)

    def mousePressEvent(self,e):
        self.press=e.globalPosition().toPoint(); self.offset=self.press-self.pos(); self.dragged=False; self.drag_active=e.button()==Qt.MouseButton.LeftButton
        if self.drag_active:
            self.c.engine.set_gift_held(self.gift_id, True)
            self.c.wake()
            self.update()

    def mouseMoveEvent(self,e):
        if self.drag_active and e.buttons() & Qt.MouseButton.LeftButton and (e.globalPosition().toPoint()-self.press).manhattanLength()>4*self.drawing_scale:
            if not self.dragged:self.c.begin_gift_drag()
            self.dragged=True; self.c.bin.show(); self.move(e.globalPosition().toPoint()-self.offset)
            self.refresh_input_region(); self.update()

    def mouseReleaseEvent(self,e):
        if e.button() != Qt.MouseButton.LeftButton or not self.drag_active:
            return
        self.drag_active=False
        if self.dragged:
            global_pos=e.globalPosition().toPoint()
            native_bin=self.c.recycle_target()
            if self.c.bin.geometry().contains(global_pos) or (native_bin and native_bin.contains(global_pos.x(),global_pos.y())):
                self.c.engine.discard_gift(self.gift_id)
            else:
                gift=next((g for g in self.c.engine.gifts if g.id==self.gift_id),None)
                if gift:
                    b=scene_screen(self.c, available=True)
                    sx, sy = to_scene(self.c, self.x(), self.y())
                    gift.x=max(b.left(),min(b.right()-38,sx)); gift.y=max(b.top(),min(b.bottom()-38,sy))
        elif e.button()==Qt.MouseButton.LeftButton:self.c.engine.collect_gift(self.gift_id)
        self.c.engine.set_gift_held(self.gift_id, False)
        self.c.bin.hide(); self.c.end_gift_drag(); self.c.changed()


class CleanupBin(Overlay):
    def __init__(self,c):
        super().__init__(c,96,74,"虚拟礼物清理篮")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput,True)

    def paintEvent(self,e):
        p=self.scene_painter(); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(241,243,233,235)); p.setPen(QPen(QColor("#9caa8c"),1)); p.drawRoundedRect(QRectF(1,1,94,72),12,12)
        p.setPen(QPen(QColor("#718063"),2)); p.drawRoundedRect(QRectF(33,13,30,30),4,4); p.drawLine(29,12,67,12)
        p.setFont(QFont("PingFang SC",10)); p.drawText(QRectF(0,49,96,20),Qt.AlignmentFlag.AlignCenter,"清理小礼物")


class IconRipple(Overlay):
    def __init__(self,c,target):
        super().__init__(c,86,86,"轻轻拨弄")
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput,True)
        self.move(round(target.rect.x-10*self.drawing_scale),round(target.rect.y-10*self.drawing_scale))
        self.phase=0
        self.timer=QTimer(self); self.timer.setInterval(80); self.timer.timeout.connect(self.step); self.timer.start()
        QTimer.singleShot(800,self.close)

    def step(self):
        self.phase+=1; self.update()

    def paintEvent(self,e):
        p=self.scene_painter(); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(145,155,113,max(0,160-self.phase*12)),2,Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush); p.drawRoundedRect(QRectF(10+math.sin(self.phase)*3,10,64,60),10,10)


class IconReplica(Overlay):
    """Brief input-transparent image; Finder geometry never changes."""
    def __init__(self, c, target, pixmap):
        super().__init__(c, round(target.rect.width), round(target.rect.height), "图标轻晃")
        self._unscaled_native = True
        self.rescale()
        self.target, self.pixmap, self.opacity = target, pixmap, 1.
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._advance)

    def _advance(self):
        if not self.step():
            self.finish()

    def start(self):
        if not self.step():
            return False
        self.timer.start()
        self.show()
        return True

    def step(self):
        latest = next((i for i in self.c._selected_icons() if i.id == self.target.id), None)
        frame = self.c._replica_policy.frame(latest, self.c._icons_checked)
        if frame is None or self.c.paused or self.c._quitting:
            self.timer.stop()
            self.hide()
            return False
        self.move(round(frame.rect.x), round(frame.rect.y))
        self.opacity = frame.opacity
        self.update()
        return True

    def finish(self):
        if self.c._icon_replica is self:
            self.c._icon_replica = None
        self.close()
        self.deleteLater()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(self.opacity)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(QRectF(self.rect()), self.pixmap, QRectF(self.pixmap.rect()))


class ScratchMarks(Overlay):
    """Three brief claw marks; all desktop input continues through the effect."""
    finished = pyqtSignal()
    duration = 1.35

    def __init__(self, c):
        super().__init__(c,184,150,"后院访客 · 爪痕")
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._started = 0.0
        self.progress = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(42)
        self.timer.timeout.connect(self.step)

    def start(self):
        self._started = time.monotonic()
        self.timer.start()

    def step(self):
        self.progress = min(1.,max(0.,(time.monotonic()-self._started)/self.duration))
        if self.progress >= 1:
            self.timer.stop()
            self.finished.emit()
        else:
            self.update()

    def paintEvent(self, event):
        p = self.scene_painter()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        opacity = min(1.,self.progress/.07) * min(1.,max(0.,(1-self.progress)/.55))
        p.setOpacity(opacity)
        for i in range(3):
            path = QPainterPath(QPointF(48+i*24,28+i*3))
            path.cubicTo(57+i*24,54+i*3,84+i*24,90+i*3,103+i*24,119+i*3)
            p.setPen(QPen(QColor(242,214,195,180),5,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawPath(path)
            p.setPen(QPen(QColor(155,83,67,200),1.5,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawPath(path)
