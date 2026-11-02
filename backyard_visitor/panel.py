"""A quiet field journal, separate from the tiny desktop companions."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QSlider, QStackedWidget, QVBoxLayout, QWidget)

from . import __version__
from .art import ASSETS, paint_bowl
from .model import COLLECTIBLE_GIFTS, GIFT_DISPLAY_NAMES
from .display_scale import normalize_user_scale

STYLE = """
QWidget { background: #f5f3ed; color: #343b31; font-family: 'PingFang SC', 'Helvetica Neue', sans-serif; font-size: 13px; }
QMainWindow { background: #f5f3ed; }
QLabel#eyebrow { color: #818778; font-size: 10px; letter-spacing: 2px; }
QLabel#title { font-size: 30px; font-weight: 600; color: #303b2f; }
QLabel#muted { color: #87897d; font-size: 12px; }
QLabel#section { font-size: 17px; font-weight: 600; }
QPushButton { border: 1px solid #d9ddd0; border-radius: 8px; padding: 9px 15px; background: #f9faf5; }
QPushButton:hover { background: #e9eddf; }
QPushButton:disabled { color: #b1b3a8; background: #eeeee7; }
QPushButton#primary { background: #53694b; color: #fffef8; border: none; }
QPushButton#primary:hover { background: #627a58; }
QPushButton#nav { border: none; text-align: left; background: transparent; padding: 12px 10px; color: #858a7b; }
QPushButton#nav:checked { background: #e7ebdf; color: #3d5136; font-weight: 600; }
QFrame#card { background: #fcfcf7; border: 1px solid #e2e5d9; border-radius: 12px; }
QFrame#card QLabel { background: transparent; }
QWidget#neighborInteractions { background: transparent; }
QProgressBar { border: none; background: #e7eadf; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: #8e9d79; border-radius: 3px; }
QLineEdit { border: 1px solid #d9ddd0; border-radius: 6px; padding: 8px; background: #ffffff; }
QListWidget { background: transparent; border: none; color: #707a67; }
QListWidget::item { padding: 9px; border-bottom: 1px solid #e6e8df; }
QCheckBox { padding: 8px 0; }
QComboBox { padding: 7px; border: 1px solid #d9ddd0; border-radius: 5px; }
QScrollArea { border: none; }
"""


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


class ArtworkScaleControl(QWidget):
    """A relative artwork preference; the controller owns safe application."""
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("桌面元素大小", "section"))
        self.value_label = label("", "muted")
        row.addWidget(self.value_label, 1, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(75, 150)
        self.slider.setSingleStep(5)
        self.slider.setPageStep(10)
        self.slider.setAccessibleName("桌面元素大小")
        self.slider.setToolTip("在显示器自动适配的基础上，调整为75%–150%。")
        layout.addWidget(self.slider)
        layout.addWidget(label("猫咪、小门、食盆、猫窝、礼物、特效和逗猫羽毛会一起缩放；壁纸继续铺满屏幕。", "muted", True))
        self.refresh()
        self.slider.valueChanged.connect(self._changed)

    def _changed(self, value):
        self.value_label.setText(f"{value}%（自动适配基础上）")
        self.c.set_artwork_scale(value/100)

    def refresh(self):
        # A periodic journal refresh must not pull the handle away mid-drag.
        if self.slider.isSliderDown():
            return
        value = round(normalize_user_scale(self.c.settings.get("artwork_scale", 1.0))*100)
        blocked = self.slider.blockSignals(True)
        try:
            self.slider.setValue(value)
            self.value_label.setText(f"{value}%（自动适配基础上）")
        finally:
            self.slider.blockSignals(blocked)


class Garden(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.setMinimumHeight(260)
        self.setAccessibleName("后院一瞥")
        self.backdrop = QPixmap(str(ASSETS / "backyard-dusk.png")).scaledToWidth(1280, Qt.TransformationMode.SmoothTransformation)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect())
        clip = QPainterPath(); clip.addRoundedRect(box, 14, 14); p.setClipPath(clip)
        if not self.backdrop.isNull():
            # Aspect-fill preview; actual desktop preset uses its own full-screen
            # transform, managed by the controller.
            pix = self.backdrop
            ratio = max(box.width()/pix.width(), box.height()/pix.height())
            source = QRectF((pix.width()-box.width()/ratio)/2, (pix.height()-box.height()/ratio)/2, box.width()/ratio, box.height()/ratio)
            p.drawPixmap(box, pix, source)
        else:
            p.fillRect(box,QColor("#bac2ad"))
        g = QLinearGradient(0,0,0,self.height())
        g.setColorAt(0,QColor(20,30,25,72)); g.setColorAt(.4,QColor(20,30,25,0)); g.setColorAt(1,QColor(20,30,25,100))
        p.fillRect(box,g)
        cat = next(iter(self.c.engine.cats.values()))
        self.c.art.paint(p,QRectF(self.width()*.51,self.height()-151,200,147),"idle",0,-1,cat.render_coat)
        paint_bowl(p,QRectF(self.width()*.45,self.height()-33,44,28),self.c.engine.food)
        p.setPen(QColor("#f6f7ed")); p.setFont(QFont("Helvetica Neue",9))
        p.drawText(QRectF(22,18,300,22), "THE GARDEN IS NEVER REALLY EMPTY.")
        p.setPen(QColor("#f7f8ef")); p.setFont(QFont("PingFang SC",11))
        p.drawText(QRectF(22,self.height()-34,300,22),"让偶遇，慢一点发生。")


class CatCard(QFrame):
    def __init__(self, controller, cat):
        super().__init__()
        self.c, self.cat_id = controller, cat.id
        self.setObjectName("card")
        v = QVBoxLayout(self); v.setContentsMargins(16,15,16,15)
        header = QHBoxLayout(); header.setSpacing(16)
        self.portrait = QLabel()
        self.portrait.setFixedSize(120, 140)
        self.portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.portrait.setAccessibleName("猫咪正面端坐全身照")
        self._portrait_key = None
        header.addWidget(self.portrait, 0, Qt.AlignmentFlag.AlignTop)
        details = QVBoxLayout(); details.setSpacing(6)
        header.addLayout(details, 1)
        v.addLayout(header)
        self.name = label(cat.display_name, "section")
        # A user-supplied nickname is text, including any HTML-like characters.
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.name)
        self.identity = label("", "muted")
        self.identity.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.identity)
        personalities = {"mischievous":"淘气 · 总想看一眼你的光标", "shy":"慢热 · 给它留一点距离", "graceful":"优雅 · 有自己的来去时间"}
        details.addWidget(label(personalities[cat.personality],"muted",True))
        self.coat_label = label(cat.coat_description, "muted", True)
        self.coat_label.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.coat_label)
        self.coat_warning = label(cat.coat_warning or "", "muted", True)
        self.coat_warning.setTextFormat(Qt.TextFormat.PlainText)
        self.coat_warning.setVisible(bool(cat.coat_warning))
        details.addWidget(self.coat_warning)
        self.guest_hint = label("", "muted", True)
        details.addWidget(self.guest_hint)
        details.addStretch()
        self.interactions = QWidget()
        self.interactions.setObjectName("neighborInteractions")
        v.addWidget(self.interactions)
        actions = QVBoxLayout(self.interactions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.trust = QProgressBar(); self.trust.setRange(0,100); self.trust.setTextVisible(False); actions.addWidget(self.trust)
        self.status = label("", "muted"); actions.addWidget(self.status)
        self.relationships = label("", "muted", True)
        self.relationships.setTextFormat(Qt.TextFormat.PlainText)
        self.relationships.setAccessibleName("它对已认识邻居的好感")
        actions.addWidget(self.relationships)
        buttons = QHBoxLayout()
        self.rename_button = QPushButton("起个昵称")
        self.rename_button.clicked.connect(lambda: self.c.rename_cat(cat.id)); buttons.addWidget(self.rename_button)
        self.call_button = QPushButton("轻声呼唤")
        self.call_button.clicked.connect(lambda: self.c.call_cat(cat.id)); buttons.addWidget(self.call_button)
        actions.addLayout(buttons)
        self.adopt = QPushButton("邀请它长住"); self.adopt.clicked.connect(lambda: self.c.adopt(cat.id)); actions.addWidget(self.adopt)
        self.bed_button = QPushButton("布置猫窝")
        self.bed_button.clicked.connect(self.toggle_bed)
        actions.addWidget(self.bed_button)
        self.bed_hint = label("", "muted", True); actions.addWidget(self.bed_hint)
        self.cancel_residence_button = QPushButton("取消常住邀请")
        self.cancel_residence_button.clicked.connect(self.confirm_cancel_residence)
        self.cancel_residence_button.setToolTip("取消后信任降至50，猫窝会收起；以后仍可作为普通访客来往。")
        actions.addWidget(self.cancel_residence_button)
        self.depth_button=QPushButton('去后院转转')
        self.depth_button.clicked.connect(lambda:self.c.send_to_backyard(self.cat_id,close_journal=True))
        actions.addWidget(self.depth_button)
        self.tree_button = QPushButton('去爬架玩')
        self.tree_button.clicked.connect(lambda: self.c.send_to_tree(self.cat_id, close_journal=True))
        actions.addWidget(self.tree_button)
        self.refresh()

    def _refresh_portrait(self, cat):
        key = cat.render_coat
        if key == self._portrait_key:
            return
        from .portrait_art import portrait_frame
        image = portrait_frame(cat)
        self._portrait_key = key
        if image.isNull():
            self.portrait.clear()
            self.portrait.setText("照片暂不可用")
            return
        self.portrait.setText("")
        # The source is 144×168 logical points at DPR 2. Scale its raster while
        # retaining DPR, so the same portrait remains crisp on Retina screens.
        ratio = image.devicePixelRatio()
        image = image.scaled(round(120 * ratio), round(140 * ratio),
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        self.portrait.setPixmap(image)

    def confirm_cancel_residence(self):
        cat = self.c.engine.cats.get(self.cat_id)
        if cat is None or not cat.is_neighbor or not cat.adopted:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("取消常住邀请")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"取消{cat.display_name}的常住邀请？")
        dialog.setInformativeText("取消后，信任降至50，猫窝会收起。以后仍会作为普通访客来往；信任重新满格后可以再次邀请。")
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.button(QMessageBox.StandardButton.Yes).setText("确认取消")
        dialog.button(QMessageBox.StandardButton.No).setText("保留常住")
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        dialog.setEscapeButton(QMessageBox.StandardButton.No)
        try:
            accepted = dialog.exec() == QMessageBox.StandardButton.Yes
        finally:
            dialog.deleteLater()
        if accepted:
            self.c.cancel_residence(self.cat_id)

    def toggle_bed(self):
        cat = self.c.engine.cats.get(self.cat_id)
        if cat is None or not cat.is_neighbor:
            return
        bed = self.c.engine.bed
        if bed is not None and bed.cat_id == self.cat_id:
            self.c.remove_bed()
        else:
            self.c.place_bed(self.cat_id)

    def refresh(self):
        cat = self.c.engine.cats[self.cat_id]
        self._refresh_portrait(cat)
        self.name.setText(cat.display_name if cat.is_neighbor else cat.coat_description + "过客")
        waiting = not cat.is_neighbor and self.c.engine.guest_waiting_for_slot(self.cat_id)
        self.identity.setText("常住伙伴" if cat.adopted and cat.is_neighbor else
                              "正式邻居" if cat.is_neighbor else
                              "已离开的过客" if cat.is_departed else
                              "过客 · 等待邻居名额" if waiting else "过客")
        self.interactions.setVisible(cat.is_neighbor)
        for control in (self.trust, self.status, self.relationships, self.rename_button,
                        self.call_button, self.adopt, self.bed_button, self.bed_hint,
                        self.cancel_residence_button, self.depth_button, self.tree_button):
            control.setVisible(cat.is_neighbor)
        self.guest_hint.setVisible(not cat.is_neighbor)
        self.guest_hint.setText("它已经离开，名册留下这次相遇。" if cat.is_departed else
                               "它已经熟悉这里，正在等一个正式邻居名额。" if waiting else
                               "留一碗食物，等它熟悉这里后，就有机会成为正式邻居。")
        self.coat_label.setText(cat.coat_description)
        self.coat_warning.setText(cat.coat_warning or "")
        self.coat_warning.setVisible(bool(cat.coat_warning))
        if not cat.is_neighbor:
            self.status.clear()
            self.relationships.clear()
            return
        self.trust.setValue(round(cat.trust))
        visitor=self.c.engine.visitors.get(self.cat_id)
        trip=visitor is not None and getattr(visitor,'depth_trip',None) is not None
        self.depth_button.setVisible(self.c.settings.get('depth_enabled',False) or trip)
        self.depth_button.setText('回到前层' if trip else '去后院转转')
        self.depth_button.setEnabled(visitor is not None and not self.c.paused)
        on_tree = visitor is not None and getattr(visitor, 'tree_trip', None) is not None
        self.tree_button.setVisible(self.c.settings.get('tree_enabled', False) or on_tree)
        self.tree_button.setText('回到桌面' if on_tree else '去爬架玩')
        self.tree_button.setEnabled(visitor is not None and not self.c.paused)
        state = "已长住" if cat.adopted else ("正在桌面做客" if cat.id in self.c.engine.visitors else "在附近散步")
        self.status.setText(f"{state}   ·   信任 {round(cat.trust)}/100")
        neighbors = [cid for cid in self.c.engine.roster_cat_ids()
                     if cid != self.cat_id and self.c.engine.cats[cid].is_neighbor]
        rows = []
        for other_id in neighbors:
            score = self.c.engine.relations.score(self.cat_id, other_id)
            gain = self.c.engine.relations.meal_gain(self.cat_id, other_id)
            mood = next(name for ceiling, name in (
                (10, "不共戴天"), (25, "看不顺眼"), (40, "略有嫌弃"),
                (60, "不冷不热"), (75, "颇有好感"), (90, "十分喜爱"),
                (101, "格外偏爱")) if score < ceiling)
            other_name = self.c.engine.cats[other_id].display_name
            rows.append(f"对 {other_name}：{mood} · 好感 {score:g}（共餐累计 +{gain:g}）")
        self.relationships.setText("与邻居的关系\n" + "\n".join(rows) if rows else "")
        self.relationships.setVisible(bool(rows))
        self.bed_button.setVisible(cat.adopted)
        self.bed_hint.setVisible(cat.adopted)
        self.cancel_residence_button.setVisible(cat.adopted)
        has_bed = self.c.engine.bed is not None and self.c.engine.bed.cat_id == self.cat_id
        self.bed_button.setText("收起猫窝" if has_bed else "布置猫窝")
        self.bed_hint.setText("拖动窝沿可沿桌面底部移动；它会自己回窝休息。" if has_bed else "给常住伙伴留一个安心睡觉的位置。")
        self.adopt.setEnabled(cat.trust >= 100 and not cat.adopted)
        self.adopt.setText("已是常住伙伴" if cat.adopted else "邀请它长住 · 信任 100 解锁")


class GiftPortrait(QWidget):
    def __init__(self, controller, kind):
        super().__init__()
        self.c, self.kind = controller, kind
        self.setFixedSize(76, 76)
        self.setAccessibleName(GIFT_DISPLAY_NAMES[kind])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.c.paint_gift(p, QRectF(8, 8, 60, 60), self.kind)


class Journal(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.setWindowTitle("后院访客 · Backyard Visitor")
        self.resize(980,710)
        self.setMinimumSize(860,650)
        self.setStyleSheet(STYLE)
        root = QWidget(); self.setCentralWidget(root)
        horizontal = QHBoxLayout(root); horizontal.setContentsMargins(0,0,0,0); horizontal.setSpacing(0)
        rail = QWidget(); rail.setFixedWidth(174); rail.setStyleSheet("background:#eef0e6;")
        left = QVBoxLayout(rail); left.setContentsMargins(20,32,18,20); left.setSpacing(8)
        left.addWidget(label("BACKYARD\nVISITOR", "eyebrow"))
        left.addSpacing(14); left.addWidget(label("后院访客", "section")); left.addSpacing(24)
        self.pages = QStackedWidget()
        self.nav = []
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i,text in enumerate(["01   后院一瞥","02   邻居名册","03   来访手记","04   桌面偏好","05   礼物图鉴"]):
            b = QPushButton(text)
            b.setObjectName("nav")
            b.setCheckable(True)
            self.nav_group.addButton(b, i)
            # macOS AXPress may change checked state without emitting clicked.
            # Selection is the source of truth for mouse, keyboard and AX input.
            b.toggled.connect(lambda checked, idx=i: self.show_page(idx) if checked else None)
            left.addWidget(b)
            self.nav.append(b)
        left.addStretch()
        left.addWidget(label("它有自己的生活。\n只是偶尔，路过这里。", "muted", True))
        left.addSpacing(16); left.addWidget(label(f"MAC EDITION\nv{__version__}", "eyebrow"))
        horizontal.addWidget(rail); horizontal.addWidget(self.pages,1)
        self.build_home(); self.build_cats(); self.build_log(); self.build_settings(); self.build_collection()
        self.show_page(0)
        self.refresh_timer = QTimer(self); self.refresh_timer.setInterval(1000); self.refresh_timer.timeout.connect(self.refresh)
        self.refresh()

    def page(self, eyebrow, title, subtitle):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(30,30,30,26); layout.setSpacing(15)
        layout.addWidget(label(eyebrow,"eyebrow")); layout.addWidget(label(title,"title")); layout.addWidget(label(subtitle,"muted",True))
        self.pages.addWidget(page)
        return layout

    def build_home(self):
        v = self.page("A LITTLE COMPANY, ON ITS OWN TERMS", "今天，会有谁路过？", "打开小门，剩下的交给缘分。你随时可以关门，让桌面恢复安静。")
        self.garden = Garden(self.c); v.addWidget(self.garden,1)
        row = QHBoxLayout()
        card = QFrame(); card.setObjectName("card"); cl = QVBoxLayout(card)
        self.gate_status = label("", "section"); cl.addWidget(self.gate_status)
        self.visit_status = label("", "muted"); cl.addWidget(self.visit_status)
        self.gate_button = QPushButton(); self.gate_button.setObjectName("primary"); self.gate_button.clicked.connect(self.c.toggle_gate); cl.addWidget(self.gate_button)
        row.addWidget(card,1)
        bowl = QFrame(); bowl.setObjectName("card"); bl = QVBoxLayout(bowl)
        bl.addWidget(label("留一碗小鱼干", "section")); self.food_status = label("", "muted"); bl.addWidget(self.food_status)
        food = QPushButton("添一份食物"); food.setToolTip("吃饱后会休息一会儿，稍后再来吃。")
        food.clicked.connect(self.c.feed); bl.addWidget(food)
        bl.addWidget(label("双击或右键单击桌面上的碗，也可以添加食物。", "muted", True))
        row.addWidget(bowl,1)
        v.addLayout(row)
        bottom = QHBoxLayout()
        hint = label("单击猫咪轻轻抚摸，右键可互动，长按 0.6 秒抱起。小门右键可打开手册。", "muted",True); bottom.addWidget(hint,1)
        hide = QPushButton("回到桌面"); hide.clicked.connect(self.hide); bottom.addWidget(hide); v.addLayout(bottom)

    def build_cats(self):
        v = self.page("THE NEIGHBORS", "认识，不必占有。", "每次相遇都会留下身影。过客慢慢熟悉这里后，才会成为可以起名和互动的正式邻居。")
        scroll = QScrollArea(); scroll.setWidgetResizable(True); content = QWidget(); layout = QVBoxLayout(content)
        self.cards = []
        self._known_card_ids = ()
        self._cats_layout = layout
        self.unknown_neighbors = label("还没有遇见邻居。开门等一会儿，见过的猫咪才会留在这里。", "muted", True)
        layout.addWidget(self.unknown_neighbors)
        layout.addStretch(); scroll.setWidget(content); v.addWidget(scroll)

    def refresh_cats(self):
        known = self.c.engine.roster_cat_ids()
        if known != self._known_card_ids:
            for card in self.cards:
                self._cats_layout.removeWidget(card)
                card.hide(); card.deleteLater()
            self.cards = []
            for cat_id in known:
                card = CatCard(self.c, self.c.engine.cats[cat_id])
                self.cards.append(card)
                self._cats_layout.insertWidget(self._cats_layout.count()-1, card)
            self._known_card_ids = known
        self.unknown_neighbors.setVisible(not known)
        for card in self.cards:
            card.refresh()

    def build_collection(self):
        v = self.page("SMALL GIFTS, KEPT CLOSE", "它带来的小心意。", "记录你点击收下的礼物，按种类累计。清理或逃走的礼物不计入；已收下的数量会一直保留。")
        self.gift_counts = {}
        self.gift_portraits = []
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); rows = QVBoxLayout(content)
        for kind in COLLECTIBLE_GIFTS:
            card = QFrame(); card.setObjectName("card")
            row = QHBoxLayout(card); row.setContentsMargins(16,12,16,12)
            portrait = GiftPortrait(self.c, kind); self.gift_portraits.append(portrait); row.addWidget(portrait)
            copy = QVBoxLayout(); copy.addWidget(label(GIFT_DISPLAY_NAMES[kind], "section"))
            count = label("", "muted"); self.gift_counts[kind] = count; copy.addWidget(count)
            row.addLayout(copy,1); rows.addWidget(card)
        rows.addStretch(); scroll.setWidget(content); v.addWidget(scroll)

    def build_log(self):
        v = self.page("FIELD NOTES", "一些小事，值得记住。", "这里记录本次打开后发生的故事；信任、昵称与桌面礼物会单独保存。")
        self.logs = QListWidget(); v.addWidget(self.logs)

    def build_settings(self):
        outer = self.page("MAKE ROOM FOR A VISITOR", "给彼此，留些边界。", "所有系统联动都可以单独关闭。猫咪不会修改你的文件。")
        scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();v=QVBoxLayout(content);v.setSpacing(8);scroll.setWidget(content);outer.addWidget(scroll)
        self.topmost_check = QCheckBox("让猫咪、小门和食物盘始终置顶")
        self.topmost_check.setChecked(self.c.settings.get("overlay_topmost", True))
        self.topmost_check.toggled.connect(self.c.set_overlay_topmost); v.addWidget(self.topmost_check)
        v.addWidget(label("切换应用后仍可看见；关闭后按普通窗口层级显示。手记窗口保持普通窗口。", "muted", True))
        self.gate_visible_check = QCheckBox("在桌面显示小门")
        self.gate_visible_check.setChecked(self.c.settings.get("gate_visible", True))
        self.gate_visible_check.toggled.connect(self.c.set_gate_visible)
        v.addWidget(self.gate_visible_check)
        v.addWidget(label("隐藏后，可从菜单栏图标或访客手册开关门。菜单栏不可用时会保留手册入口。", "muted", True))
        self.artwork_scale_control = ArtworkScaleControl(self.c)
        self.artwork_scale_slider = self.artwork_scale_control.slider
        self.artwork_scale_value = self.artwork_scale_control.value_label
        v.addWidget(self.artwork_scale_control)
        self.window_check = QCheckBox("允许在其他应用窗口边缘活动")
        self.window_check.setChecked(self.c.settings.get("window_paths",False)); self.window_check.toggled.connect(self.c.set_window_paths); v.addWidget(self.window_check)
        v.addWidget(label("在窗顶散步，或沿可见侧面爬上去；关闭后回到地面。", "muted",True))
        self.icons_check = QCheckBox("允许靠近我选择的桌面图标")
        self.icons_check.setChecked(self.c.settings.get("icons_enabled",False)); self.icons_check.toggled.connect(self.c.set_icons_enabled); v.addWidget(self.icons_check)
        v.addWidget(label("首次使用可能需要 macOS 的 Finder 自动化许可；图标动作通过叠加效果呈现。", "muted",True))
        select_icons = QPushButton("选择允许互动的图标…"); select_icons.clicked.connect(self.c.choose_icons); v.addWidget(select_icons)
        self.icon_motion_check = QCheckBox("为已选图标添加轻微晃动")
        self.icon_motion_check.setChecked(self.c.settings.get("icon_replica", False))
        self.icon_motion_check.toggled.connect(self.c.set_icon_replica); v.addWidget(self.icon_motion_check)
        v.addWidget(label("用图标影像呈现短暂晃动，桌面布局保持不变。", "muted", True))
        self.recycle_check=QCheckBox("允许把小礼物拖入 Dock 废纸篓")
        self.recycle_check.setChecked(self.c.settings.get("recycle_enabled",False));self.recycle_check.toggled.connect(self.c.set_recycle_enabled);v.addWidget(self.recycle_check)
        v.addWidget(label("需主动授予辅助功能许可；只定位废纸篓，不操作真实文件。Dock 隐藏时用清理篮。","muted",True))
        self.mouse_check = QCheckBox("显示写实小老鼠礼物")
        self.mouse_check.setChecked(self.c.settings.get("realistic_mouse", True))
        self.mouse_check.toggled.connect(self.c.set_realistic_mouse); v.addWidget(self.mouse_check)
        v.addWidget(label("关闭后用简化玩具鼠呈现，送礼与信任不受影响。", "muted", True))
        self.calm_check = QCheckBox("安静陪伴：关闭打架与扑光标")
        self.calm_check.setChecked(self.c.settings.get("calm_mode",False)); self.calm_check.toggled.connect(self.c.set_calm_mode); v.addWidget(self.calm_check)
        self.pause_check = QCheckBox("暂停桌面活动（访客留在原地）"); self.pause_check.toggled.connect(self.c.set_paused); v.addWidget(self.pause_check)
        v.addWidget(label("猫咪活动屏幕", "section"))
        self.screens = QComboBox()
        for screen in self.c.qt.screens():
            self.screens.addItem(screen.name())
        self.refresh_screens()
        self.screens.currentIndexChanged.connect(self.c.change_screen); v.addWidget(self.screens)
        self.wallpaper_choices = QComboBox()
        self.wallpaper_choices.setAccessibleName("后院壁纸")
        for scene_id, scene in self.c.scenes.items():
            self.wallpaper_choices.addItem(scene["title"], scene_id)
        chosen = self.wallpaper_choices.findData(self.c.settings["wallpaper_id"])
        self.wallpaper_choices.setCurrentIndex(max(0, chosen))
        v.addWidget(self.wallpaper_choices)
        self.wallpaper_preview = QLabel()
        self.wallpaper_preview.setFixedSize(240, 180)
        self.wallpaper_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wallpaper_preview.setAccessibleName("所选壁纸预览（保留原图比例）")
        v.addWidget(self.wallpaper_preview)
        self.wallpaper_note = label("", "muted", True)
        v.addWidget(self.wallpaper_note)
        self.wallpaper_choices.currentIndexChanged.connect(self.preview_wallpaper)
        self.preview_wallpaper()
        v.addWidget(label("选择后点击应用，仅更换主屏幕；原壁纸可随时还原。默认在前层活动；下方可单独允许访客往后院走。", "muted", True))
        self.wallpaper_cycle_check = QCheckBox("随本机时间切换晨间 / 暮光")
        self.wallpaper_cycle_check.setToolTip("06:00–18:00 使用晨间，其他时间使用暮光；仅切换系统主屏幕。")
        self.wallpaper_cycle_check.toggled.connect(self.c.set_wallpaper_cycle)
        v.addWidget(self.wallpaper_cycle_check)
        self.depth_check=QCheckBox('允许访客往后院走（主屏预设壁纸）')
        self.depth_check.setChecked(self.c.settings.get('depth_enabled',False))
        self.depth_check.toggled.connect(self.c.set_depth_enabled);v.addWidget(self.depth_check)
        self.tree_check = QCheckBox('允许访客在猫爬架玩耍与休息（主屏预设壁纸）')
        self.tree_check.setChecked(self.c.settings.get('tree_enabled', False))
        self.tree_check.toggled.connect(self.c.set_tree_enabled); v.addWidget(self.tree_check)
        v.addWidget(label('开启后，猫会偶尔自己去爬架，也可在右键菜单或名册中选择“去爬架玩”。爬架被窗口挡住时先返回。', 'muted', True))
        self.refresh_wallpaper_controls()
        v.addWidget(label('开启后，在猫咪右键菜单选“去后院转转”；只有走到后层才会被植物遮挡。路线被窗口挡住时会返回。','muted',True))
        wallpaper = QHBoxLayout()
        preset = QPushButton("使用后院预设壁纸"); preset.clicked.connect(self.c.apply_wallpaper); wallpaper.addWidget(preset)
        restore = QPushButton("还原此前壁纸"); restore.clicked.connect(self.c.restore_wallpaper); wallpaper.addWidget(restore); v.addLayout(wallpaper)
        self.metrics = label("", "muted",True); v.addWidget(self.metrics)
        v.addWidget(label(f"软件更新 · 当前版本 {__version__}", "section"))
        update_button = QPushButton("检查更新…")
        update_button.setAccessibleName("检查后院访客更新")
        update_button.clicked.connect(self.c.check_for_updates)
        v.addWidget(update_button)
        v.addWidget(label("手动检查新版，下载并验证后再安装；日常陪伴不需要联网。", "muted", True))
        v.addStretch()
        row = QHBoxLayout()
        demo = QPushButton("打开独立体验模式"); demo.clicked.connect(self.c.start_demo); row.addWidget(demo)
        quit_button = QPushButton("退出后院访客"); quit_button.clicked.connect(self.c.quit); row.addWidget(quit_button)
        v.addLayout(row)

    def preview_wallpaper(self):
        scene = self.c.scenes.get(self.wallpaper_choices.currentData())
        if scene is None:
            self.wallpaper_preview.clear()
            self.wallpaper_note.setText("这套壁纸素材暂时不可用。")
            return
        pixmap = QPixmap(str(ASSETS / scene["file"]))
        self.wallpaper_preview.setPixmap(pixmap.scaled(
            self.wallpaper_preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        if scene.get("orientation") == "portrait":
            description = "9:16 竖屏设计，需要竖向系统主屏幕。"
        else:
            description = "横屏设计，预览保留原图比例。"
        description += (" 此景不启用纵深，访客在桌面前层活动。" if not scene.get("occluders") else
                        " 应用后可单独允许访客往后院走。")
        if scene.get("layout_geometry", {}).get("supports"):
            description += " 应用后可单独开启猫爬架互动：平台跳跃、猫窝休息、舔毛与磨爪。"
        self.wallpaper_note.setText(description)

    def refresh_wallpaper_controls(self):
        self.wallpaper_cycle_check.blockSignals(True)
        self.wallpaper_cycle_check.setChecked(self.c.wallpaper_cycle_enabled)
        self.wallpaper_cycle_check.setEnabled(not self.c.demo and self.c.adapter.capabilities.get("wallpaper_guarded", False))
        self.wallpaper_cycle_check.blockSignals(False)
        supported = bool(self.c._mask_data.get("occluders"))
        self.depth_check.setEnabled(supported)
        self.depth_check.setToolTip("当前应用的壁纸不启用纵深。" if not supported else "")
        tree_supported = bool(self.c._mask_data.get("layout_geometry", {}).get("supports"))
        self.tree_check.setEnabled(tree_supported or self.c.settings.get("tree_enabled", False))
        self.tree_check.setToolTip("需要先应用带猫爬架的预设壁纸。" if not tree_supported else "")

    def refresh_screens(self):
        screens=self.c.qt.screens()
        names=[screen.name() for screen in screens]
        self.screens.blockSignals(True)
        if [self.screens.itemText(i) for i in range(self.screens.count())] != names:
            self.screens.clear(); self.screens.addItems(names)
        self.screens.setCurrentIndex(next((i for i,screen in enumerate(screens) if screen==self.c.screen),0))
        self.screens.blockSignals(False)

    def show_page(self,index):
        if not 0 <= index < self.pages.count():
            return
        self.pages.setCurrentIndex(index)
        self.nav[index].setChecked(True)

    def refresh(self):
        self.artwork_scale_control.refresh()
        self.refresh_screens()
        self.refresh_wallpaper_controls()
        e = self.c.engine
        self.icon_motion_check.setEnabled(self.c.settings["icons_enabled"] and bool(self.c.settings["icon_ids"]))
        self.gate_status.setText("小门开着" if e.gate_open else "小门轻轻合着")
        self.gate_button.setText("关门 · 暂停迎客" if e.gate_open else "开门 · 等一次偶遇")
        self.visit_status.setText(f"{len(e.visitors)} 位访客正在做客" if e.visitors else "附近暂时没有动静")
        self.food_status.setText(f"碗里还有 {e.food} 份" if e.food else "空碗也没关系，慢慢等。")
        self.refresh_cats()
        for kind, count in self.gift_counts.items():
            count.setText(f"累计收下 {self.c.engine.collected_count(kind)} 件")
        for portrait in self.gift_portraits:
            portrait.update()
        self.metrics.setText(f"{'体验模式 · 不写入正式进度' if self.c.demo else '本地存档 · 无网络服务'}   /   当前更新 {self.c.current_fps} FPS\n静止 1–2 FPS，行走 24 FPS；关门无访客时停止动画。")
        if getattr(self,"_garden_food",None)!=e.food:
            self._garden_food=e.food
            self.garden.update()

    def showEvent(self,event):
        self.refresh_timer.start(); self.refresh(); super().showEvent(event)

    def hideEvent(self,event):
        self.refresh_timer.stop(); super().hideEvent(event)
        QTimer.singleShot(150,lambda:self.c.release_journal(self))

    def closeEvent(self,event):
        event.ignore(); self.hide()

    def note(self,message):
        self.logs.insertItem(0,message)
        while self.logs.count()>120:
            self.logs.takeItem(self.logs.count()-1)
