"""Small cached raster frames; no browser, video decoder or continuous GPU loop."""
from __future__ import annotations

from collections import OrderedDict
import json
import math
import os
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform

from .coat_traits import CoatSpec

ASSETS = Path(__file__).with_name("assets")


class CatArt:
    def __init__(self, prefer_compiled=True, *, retain_source_frames=False):
        self._retain_source_frames = retain_source_frames
        self.frames: list[QPixmap] = []
        self.states = {}
        self.facing = -1
        self.cache = OrderedDict()
        self.cache_limit = 16
        self.tail = None
        self._tail_coat_cache = OrderedDict()
        self._interactions_loaded = False
        self._motion_loaded = False
        self._standing_loaded = False
        self._transitions_loaded = False
        self._jumps_loaded = False
        self._jump_mouths = {}
        self._parkour = None
        self._running_loaded = False
        self._activity_loaded = False
        self._postures_loaded = False
        self._posture_groups = {}
        self._run_mouths = []
        self._run_cycle = .48
        self._motion_cycle = 0.8
        self._blink_windows = {}
        self._compiled = None
        self._compiled_indices = {}
        if prefer_compiled and os.environ.get("BV_DISABLE_SPRITE_CACHE") != "1":
            from .sprite_cache import load_compiled
            compiled = load_compiled(ASSETS)
            if compiled is not None:
                count = compiled.manifest["base_frame_count"]
                frames = [compiled.frame(i) for i in range(count)]
                tail = compiled.tail()
                if all(frame is not None for frame in frames) and tail is not None:
                    self._compiled = compiled
                    self.frames = frames
                    self.tail = tail
                    self.states = {state: list(indices) for state, indices
                                   in compiled.manifest["base_states"].items()}
                    self.facing = compiled.manifest["native_facing"]
                    self._compiled_indices = {i: i for i in range(count)}
                    return
        manifest = ASSETS / "sprites.json"
        if manifest.exists():
            data = json.loads(manifest.read_text())
            atlas = QImage(str(ASSETS / data["file"]))
            self.states = data["states"]
            self.facing = data.get("native_facing", -1)
            frames = data.get("frames", [])
            rects = data.get("rects") or [f["rect"] for f in frames]
            if not rects:
                cw, ch = atlas.width() // data["columns"], atlas.height() // data["rows"]
                rects = [(c*cw, r*ch, cw, ch) for r in range(data["rows"]) for c in range(data["columns"])]
            # All poses share physical scale and the same ground line. Cache at
            # Retina resolution, then release the original 6 MB decoded sheet.
            scale = 0.39
            for i, rect in enumerate(rects):
                anchor = frames[i]["foot_anchor"] if i < len(frames) else [rect[2]/2, rect[3]-4]
                canvas = QPixmap(288, 224)
                canvas.fill(Qt.GlobalColor.transparent)
                p = QPainter(canvas)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                target = QRectF(144-anchor[0]*scale, 216-anchor[1]*scale,rect[2]*scale,rect[3]*scale)
                p.drawImage(target, atlas, QRectF(*rect))
                p.end()
                canvas.setDevicePixelRatio(2)
                self.frames.append(canvas)

        extra_manifest = ASSETS / "extra.json"
        if extra_manifest.exists():
            extra = json.loads(extra_manifest.read_text())
            atlas = QImage(str(ASSETS / extra["file"]))
            offset = len(self.frames)
            for info in extra["frames"]:
                x,y,w,h = info["content_bounds"]
                crop = atlas.copy(*info["rect"])
                if info["name"] == "tail_peeking":
                    self.tail = QPixmap.fromImage(crop.copy(x,y,w,h))
                anchor = info["foot_anchor"]
                scale = min(0.39,208/max(h,1),274/max(w,1))
                canvas = QPixmap(288,224); canvas.fill(Qt.GlobalColor.transparent)
                p = QPainter(canvas); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawImage(QRectF(144-anchor[0]*scale,216-anchor[1]*scale,crop.width()*scale,crop.height()*scale),crop)
                p.end(); canvas.setDevicePixelRatio(2); self.frames.append(canvas)
            for state, indices in extra["states"].items():
                self.states[state] = [offset+i for i in indices]

    def _retire_replaced_frames(self, previous_indices):
        """Drop replaced pixels without changing any live frame or cache index."""
        if self._retain_source_frames:
            return  # Offline compilation exports every original fallback index.
        referenced = {index for indices in self.states.values() for index in indices}
        referenced.update(index for indices in self._posture_groups.values() for index in indices)
        retired = {index for index in previous_indices
                   if index not in referenced and 0 <= index < len(self.frames)}
        if not retired:
            return
        for key in list(self.cache):
            if key[0] in retired:
                del self.cache[key]
        for index in retired:
            # Empty slots keep state and pose indices stable; never compact frames.
            self.frames[index] = QPixmap()
        # A later explicit compiled request must decode again, not reuse an empty slot.
        self._compiled_indices = {source: index for source, index in self._compiled_indices.items()
                                  if index not in retired}

    def _load_compiled_group(self, indices, states):
        """Append optional poses atomically, regardless of which group loads first."""
        if self._compiled is None or not indices:
            return False
        missing = [i for i in indices if i not in self._compiled_indices]
        frames = [self._compiled.frame(i) for i in missing]
        if any(frame is None for frame in frames):
            return False
        mapping = dict(self._compiled_indices)
        mapping.update({index: len(self.frames) + n for n, index in enumerate(missing)})
        source_states = self._compiled.manifest["states"]
        if any(state not in source_states or any(i not in mapping for i in source_states[state])
               for state in states):
            return False
        previous = [index for state in states for index in self.states.get(state, [])]
        self.frames.extend(frames)
        self._compiled_indices = mapping
        for state in states:
            self.states[state] = [mapping[i] for i in source_states[state]]
        self._retire_replaced_frames(previous)
        return True

    def _load_standing(self):
        if self._standing_loaded:
            return
        self._standing_loaded = True
        from .standing_art import StandingArt
        frame = StandingArt(ASSETS).frame()
        if frame is not None:
            index = len(self.frames)
            self.frames.append(frame)
            self.states["standing"] = self.states["landing"] = [index]

    def _load_transitions(self):
        if self._transitions_loaded:
            return
        self._transitions_loaded = True
        from .transition_art import TransitionArt
        frames = TransitionArt(ASSETS).load_frames()
        if not frames or any(frame.isNull() for frame in frames):
            return
        self._load_standing()
        if "standing" not in self.states:
            return
        offset = len(self.frames)
        self.frames.extend(frames)
        self.states["settling"] = [self.states["standing"][0], *range(offset, offset + len(frames))]
        # The final side-seated pose is also the idle endpoint, so the last
        # transition frame never snaps back to the earlier front-view portrait.
        previous = self.states.get("idle", [])
        self.states["idle"] = [offset + len(frames) - 1]
        self._retire_replaced_frames(previous)

    def _load_jumps(self):
        if self._jumps_loaded:
            return
        self._jumps_loaded = True
        self._load_standing()
        from .jump_art import JumpArt
        jump = JumpArt(ASSETS)
        frames = jump.load_frames()
        if (len(frames) != 3 or any(frame.isNull() for frame in frames)
                or "standing" not in self.states):
            return
        offset = len(self.frames)
        self.frames.extend(frames)
        standing = self.states["standing"][0]
        self.states["takeoff"] = [standing, offset+2, offset]
        self.states["landing"] = [standing, offset+1, offset+2, standing]
        self._jump_mouths = {offset+i: (point[0], point[1]-2)
                             for i, point in enumerate(jump.mouth_anchors)}

    def takeoff_mouth(self, progress, facing):
        """Match the mouth overlay to the same selected crouching pose."""
        from .gift_geometry import mouth_anchor
        self._load_jumps()
        choices = self.states.get("takeoff", [])
        if not choices:
            return mouth_anchor(facing)
        progress = max(0., min(1., progress))
        index = choices[min(len(choices)-1, int(progress*len(choices)))]
        x, y = self._jump_mouths.get(index, mouth_anchor(-1))
        return (144-x if facing > 0 else x, y)

    def landing_mouth(self, progress, facing):
        from .gift_geometry import mouth_anchor
        self._load_jumps()
        choices = self.states.get("landing", [])
        if not choices:
            return mouth_anchor(facing)
        progress = max(0., min(1., progress))
        index = choices[min(len(choices)-1, int(progress*len(choices)))]
        x,y = self._jump_mouths.get(index, mouth_anchor(-1))
        return (144-x if facing > 0 else x, y)

    def _load_activity(self):
        if self._activity_loaded:
            return
        self._activity_loaded = True
        from .activity_art import ActivityArt, STATES
        frames = ActivityArt(ASSETS).load_frames()
        if set(frames) != set(STATES) or any(frames[name].isNull() for name in STATES):
            return
        offset = len(self.frames)
        self.frames.extend(frames[name] for name in STATES)
        for i, name in enumerate(STATES):
            self.states[name] = [offset+i]
        # Preserve the original sleep index until the entire new group exists.
        previous = self.states.get("sleeping", [])
        self.states["sleeping"] = self.states["belly"][:]
        self._retire_replaced_frames(previous)

    def _load_postures(self):
        if self._postures_loaded:
            return
        self._postures_loaded = True
        from .posture_art import PostureArt
        groups = PostureArt(ASSETS).load_frames()
        names = ("crouch_rise", "belly_roll")
        if any(len(groups.get(name, [])) != 3 or any(frame.isNull() for frame in groups[name]) for name in names):
            return
        for name in names:
            offset = len(self.frames)
            self.frames.extend(groups[name])
            self._posture_groups[name] = list(range(offset, offset+3))

    def posture_state(self, kind, source, target):
        """Reuse real endpoint poses; append only accepted intermediate frames."""
        valid = ((kind == "crouch_rise" and source == "play_crouch" and target == "standing")
                 or (kind == "belly_roll" and source in {"sleeping", "swatting"}
                     and target in {"resting", "play_crouch"}))
        reverse_group = {("crouch_lower", "standing", "play_crouch"): "crouch_rise",
                         ("belly_lie", "play_crouch", "sleeping"): "belly_roll"}.get((kind, source, target))
        if not valid and reverse_group is None:
            return None
        self._load_activity()
        self._load_standing()
        self._load_postures()
        group = reverse_group or kind
        if group not in self._posture_groups or source not in self.states or target not in self.states:
            return None
        name = f"posture_{kind}_{source}_{target}"
        if name not in self.states:
            intermediate = self._posture_groups[group]
            if reverse_group:
                intermediate = list(reversed(intermediate))
            self.states[name] = [self.states[source][0], *intermediate, self.states[target][0]]
        return name

    def _load_running(self):
        if self._running_loaded:
            return
        self._running_loaded = True
        from .run_art import RunArt
        run = RunArt(ASSETS)
        frames = run.load_frames()
        if len(frames) != 4 or any(frame.isNull() for frame in frames):
            self._load_standing()
            return
        offset = len(self.frames)
        self.frames.extend(frames)
        self.states["running"] = list(range(offset, offset+4))
        self._run_cycle = run.cycle_seconds
        self._run_mouths = [(x,y-2) for x,y in run.mouth_anchors]

    def run_mouth(self, phase, facing):
        from .gift_geometry import mouth_anchor
        self._load_running()
        if not self._run_mouths:
            return mouth_anchor(facing)
        phase = phase % 1 if type(phase) in (int,float) and math.isfinite(phase) else 0
        x,y = self._run_mouths[min(3, int(phase*4+1e-9))]
        return (144-x if facing > 0 else x, y)

    def _load_motion(self):
        if self._motion_loaded:
            return
        self._motion_loaded = True
        if self._compiled is not None:
            data = self._compiled.manifest
            if self._load_compiled_group(data["optional_frame_groups"].get("walking", []), ("walking",)):
                self._motion_cycle = data["locomotion_cycle_seconds"]
                return
        from .motion_art import MotionArt
        motion = MotionArt(ASSETS)
        frames = motion.load_frames()
        if frames:
            offset = len(self.frames)
            self.frames.extend(frames)
            previous = self.states.get("walking", [])
            self.states["walking"] = list(range(offset, offset + len(frames)))
            self._motion_cycle = motion.cycle_seconds
            self._retire_replaced_frames(previous)

    def _load_interactions(self):
        """Retain only small Retina frames, loading the atlas on first interaction."""
        if self._interactions_loaded:
            return
        self._interactions_loaded = True
        if self._compiled is not None:
            data = self._compiled.manifest
            if self._load_compiled_group(data["interaction_frame_indices"], ("window_peeking", "purring")):
                self._blink_windows.update(data["blink_windows"])
                return
        manifest = ASSETS / "interactions.json"
        if not manifest.exists():
            return
        data = json.loads(manifest.read_text())
        atlas = QImage(str(ASSETS / data["file"]))
        if atlas.isNull():
            return
        offset = len(self.frames)
        for info in data["frames"]:
            rect = info["rect"]
            anchor = info["foot_anchor"]
            destination = info["render_anchor"]
            scale = info["render_scale"]
            canvas = QPixmap(288, 224)
            canvas.fill(Qt.GlobalColor.transparent)
            p = QPainter(canvas)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            target = QRectF(destination[0]*2-anchor[0]*scale,
                            destination[1]*2-anchor[1]*scale,
                            rect[2]*scale, rect[3]*scale)
            p.drawImage(target, atlas, QRectF(*rect))
            p.end()
            canvas.setDevicePixelRatio(2)
            self.frames.append(canvas)
        # Screen-edge tails and walking retain their own existing sprites.
        for state in ("window_peeking", "purring"):
            if state in data["states"]:
                self.states[state] = [offset+i for i in data["states"][state]]
                if state in data.get("blink_windows", {}):
                    self._blink_windows[state] = data["blink_windows"][state]

    @property
    def parkour_load_error(self):
        return self._parkour.load_error if self._parkour is not None else None

    def _parkour_art(self):
        if self._parkour is None:
            from .parkour_art import ParkourArt
            self._parkour = ParkourArt(ASSETS / "parkour")
        return self._parkour

    def parkour_mouth(self, state, facing, coat="ginger", *, jump_vy=None,
                      jump_pose_phase=None, jump_vertical_span=None, climb_phase=None):
        from .parkour_art import select_parkour_pose
        selected = select_parkour_pose(state, jump_vy=jump_vy, jump_pose_phase=jump_pose_phase,
                                      jump_vertical_span=jump_vertical_span, climb_phase=climb_phase)
        return self._parkour_art().mouth_anchor(*selected, facing, coat) if selected else None

    def frame(self, state, age, facing, coat="ginger", *, gait_phase=None, pose_progress=None,
              run_phase=None, jump_vy=None, jump_pose_phase=None, jump_vertical_span=None,
              climb_phase=None) -> QPixmap | None:
        from .parkour_art import select_parkour_pose
        selected = select_parkour_pose(state, jump_vy=jump_vy, jump_pose_phase=jump_pose_phase,
                                      jump_vertical_span=jump_vertical_span, climb_phase=climb_phase)
        if selected is not None:
            return self._parkour_art().frame(*selected, facing, coat)
        moving_pose = state in {"walking", "entering", "leaving", "playing"}
        running_pose = state in {"running", "fleeing"}
        if state in {"sleeping", "belly", "swatting", "resting", "play_crouch"}:
            self._load_activity()
        if running_pose:
            self._load_running()
        if moving_pose:
            self._load_motion()
        if state in {"idle", "settling"}:
            self._load_transitions()
        if state == "standing":
            self._load_standing()
        if state in {"takeoff", "landing"}:
            self._load_jumps()
        if state in ("window_peeking", "purring"):
            self._load_interactions()
        if not self.frames:
            return None
        aliases = {"entering": "walking", "leaving": "walking", "fleeing": "running", "running": "standing", "playing": "walking", "purring": "idle", "fighting": "peeking", "jumping": "walking", "landing": "standing", "takeoff": "standing", "standing": "idle", "settling": "standing", "belly": "sleeping", "swatting": "sleeping", "resting": "peeking", "play_crouch": "peeking"}
        choices = self.states.get(state, self.states.get(aliases.get(state,state), self.states.get("idle", [0])))
        if running_pose:
            choices = self.states.get("running", self.states.get("standing", self.states.get("idle", [0])))
        blink = self._blink_windows.get(state)
        if state in {"settling", "takeoff", "landing"} or (isinstance(state, str) and state.startswith("posture_")):
            progress = (max(0.0, min(1.0, pose_progress))
                        if type(pose_progress) in (int, float) and math.isfinite(pose_progress) else 0.0)
            idx = choices[min(len(choices) - 1, int(progress * len(choices)))]
        elif blink and len(choices) > 1:
            phase = age % blink["period"]
            idx = choices[1 if blink["closed_from"] <= phase < blink["closed_until"] else 0]
        elif running_pose and len(choices) == 4:
            phase = (run_phase % 1.0 if type(run_phase) in (int,float) and math.isfinite(run_phase)
                     else max(0., age) % self._run_cycle / self._run_cycle)
            idx = choices[min(3, int(phase*4+1e-9))]
        elif moving_pose and len(choices) == 4:
            if type(gait_phase) in (int, float) and math.isfinite(gait_phase):
                # Ground distance drives the cycle. A faster fleeing cat gets
                # more steps from its motion, never an additional time multiplier.
                phase = gait_phase % 1.0
            else:
                cycle = self._motion_cycle * (0.6 if state == "fleeing" else 1)
                phase = max(0., age) % cycle / cycle
            idx = choices[min(len(choices)-1, int(phase*len(choices)+1e-9))]
        else:
            idx = choices[int(age * 6) % len(choices)]
        key = (idx, facing, coat)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        frame = self.frames[idx]
        if isinstance(coat, CoatSpec):
            from .procedural_coat import procedural_frame
            frame = procedural_frame(frame, coat)
        elif coat == "gray":
            # Qt converts in native code; Python pixel loops previously blocked
            # the first paint of each gray pose. Preserve alpha explicitly.
            source = frame.toImage()
            source.setDevicePixelRatio(1)
            alpha = source.convertToFormat(QImage.Format.Format_Alpha8)
            im = source.convertToFormat(QImage.Format.Format_Grayscale8)
            im = im.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            im.setAlphaChannel(alpha)
            im.setDevicePixelRatio(frame.devicePixelRatio())
            frame = QPixmap.fromImage(im)
        elif coat == "tuxedo":
            # A darker tabby variant, not a claim of anatomically painted tuxedo markings.
            im = frame.toImage()
            p = QPainter(im)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            p.fillRect(im.rect(), QColor(33, 31, 29, 94))
            p.end()
            frame = QPixmap.fromImage(im)
        elif coat == "black":
            from .black_coat import black_frame
            frame = black_frame(frame)
        if facing != self.facing:
            frame = frame.transformed(QTransform().scale(-1, 1))
        self.cache[key] = frame
        while len(self.cache) > self.cache_limit:
            self.cache.popitem(last=False)
        return frame

    def paint(self, painter, rect, state="idle", age=0, facing=-1, coat="ginger", opacity=1,
              *, gait_phase=None, land_progress=None, settling=False, turning=False,
              settle_progress=None, rise_progress=None, takeoff_progress=None, run_phase=None,
              posture_kind=None, posture_source=None, posture_target=None, posture_progress=None,
              jump_vy=None, jump_pose_phase=None, jump_vertical_span=None, climb_phase=None):
        # Keep anchored, actual artwork throughout the posture changes.
        pose_progress = None
        if (type(land_progress) in (int, float) and math.isfinite(land_progress)
                and 0 <= land_progress < 1 and state in {"idle", "purring", "eating", "grooming", "walking", "running", "entering", "resting", "play_crouch"}):
            state, pose_progress = "landing", land_progress
        elif (state == "jumping" and type(takeoff_progress) in (int, float)
              and math.isfinite(takeoff_progress) and 0 <= takeoff_progress < 1):
            state, pose_progress = "takeoff", takeoff_progress
        elif (state != "jumping" and type(posture_progress) in (int, float)
              and math.isfinite(posture_progress) and 0 <= posture_progress < 1
              and (posture_state := self.posture_state(posture_kind, posture_source, posture_target))):
            state, pose_progress = posture_state, posture_progress
        elif (state in {"walking", "playing", "running", "play_crouch"} and type(rise_progress) in (int, float)
              and math.isfinite(rise_progress) and 0 <= rise_progress < 1):
            state, pose_progress = "settling", 1 - rise_progress
        elif (state == "idle" and type(settle_progress) in (int, float)
              and math.isfinite(settle_progress) and 0 <= settle_progress <= 1):
            state, pose_progress = "settling", settle_progress
        elif (settling and state == "idle") or (turning and state in {"walking", "entering", "playing", "running"}):
            state = "standing"
        if state == "peeking" and self.tail is not None:
            painter.save()
            painter.setOpacity(opacity)
            source_tail = self.tail
            if coat == "black" or isinstance(coat, CoatSpec):
                from .black_coat import black_frame
                key = (self.tail.cacheKey(), coat)
                if key not in self._tail_coat_cache:
                    if isinstance(coat, CoatSpec):
                        from .procedural_coat import procedural_frame
                        self._tail_coat_cache[key] = procedural_frame(self.tail, coat)
                    else:
                        self._tail_coat_cache[key] = black_frame(self.tail)
                self._tail_coat_cache.move_to_end(key)
                while len(self._tail_coat_cache) > 2:
                    self._tail_coat_cache.popitem(last=False)
                source_tail = self._tail_coat_cache[key]
            tail = source_tail.transformed(QTransform().rotate(90 if facing > 0 else -90))
            width = rect.width()*0.38
            height = rect.height()*0.24
            x = rect.right()-width+12 if facing > 0 else rect.left()-12
            painter.drawPixmap(QRectF(x,rect.bottom()-55+math.sin(age*4)*3,width,height),tail,QRectF(tail.rect()))
            painter.restore()
            return
        from .parkour_art import select_parkour_pose
        parkour_options = dict(jump_vy=jump_vy, jump_pose_phase=jump_pose_phase,
                               jump_vertical_span=jump_vertical_span, climb_phase=climb_phase)
        parkour_pose = select_parkour_pose(state, **parkour_options)
        frame = self.frame(state, age, facing, coat, gait_phase=gait_phase,
                           pose_progress=pose_progress, run_phase=run_phase, **parkour_options)
        painter.save()
        painter.setOpacity(opacity)
        if frame:
            scale = min(rect.width()/144, rect.height()/112)
            w, h = frame.width()/frame.devicePixelRatio()*scale, frame.height()/frame.devicePixelRatio()*scale
            # Walk and run already contain their anatomical vertical motion.
            # Never offset the whole canvas: it would lift planted contact paws,
            # especially when fleeing loads before any walking pose.
            # Window peeking uses the canvas top as its coordinate contract:
            # paws at y=32 remain above the window clip at local y=34.
            # Directional parkour uses its literal canvas origin so the model's
            # climbing contact line and per-pose mouth landmarks stay aligned.
            top = rect.top() if state == "window_peeking" or parkour_pose else rect.bottom()-h-2
            target = QRectF(rect.center().x()-w/2, top, w, h)
            painter.drawPixmap(target, frame, QRectF(frame.rect()))
        else:
            # Asset failure is visible and diagnosed, never substituted with a cartoon.
            painter.setPen(QColor("#8b8072"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "跑酷素材暂不可用" if parkour_pose else "猫咪素材载入中")
        painter.restore()


def paint_door(p, rect, opened=False):
    from .gate_art import paint_gate
    paint_gate(p, rect, opened)


_bowl_art = None


def paint_bowl(p, rect, food=0):
    global _bowl_art
    if _bowl_art is None:
        from .bowl_art import BowlArt
        _bowl_art = BowlArt(ASSETS)
    if _bowl_art.paint(p, rect, food=food):
        return
    # Missing optional artwork keeps a usable food bowl with the same bounds.
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.x(), rect.y())
    p.scale(rect.width()/62, rect.height()/40)
    p.setPen(QPen(QColor("#8a8981"), 1))
    p.setBrush(QColor("#c7cfc3"))
    path = QPainterPath()
    path.moveTo(6, 14); path.lineTo(12, 34); path.quadTo(31, 40, 50, 34); path.lineTo(56, 14)
    p.drawPath(path)
    p.setBrush(QColor("#737c6e")); p.drawEllipse(QRectF(6, 7, 50, 16))
    if food:
        p.setBrush(QColor("#bf9667")); p.setPen(Qt.PenStyle.NoPen)
        for x,y in [(16,12),(28,10),(40,13),(25,16),(35,17)]:
            p.drawEllipse(QRectF(x,y,7,4))
    p.restore()


_fur_art = None


def paint_gift(p, rect, kind, *, facing=-1):
    if kind == "fur":
        global _fur_art
        if _fur_art is None:
            from .fur_art import FurArt
            _fur_art = FurArt(ASSETS)
        if _fur_art.paint(p, rect):
            return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.center())
    if facing > 0:
        p.scale(-1, 1)
    p.setPen(QPen(QColor("#8e7d64"), 1.6))
    if kind == "bottle_cap":
        p.setBrush(QColor("#9daa9b")); p.drawEllipse(QRectF(-10,-10,20,20))
        p.setBrush(QColor("#c1c9ba")); p.drawEllipse(QRectF(-6,-6,12,12))
    elif kind == "fur":
        for i in range(7):
            p.drawArc(QRectF(-12+i*2,-8+i,18,14), 20*16, 240*16)
    elif kind == "toy_mouse":
        p.setBrush(QColor("#8e877f")); p.drawEllipse(QRectF(-10,-6,20,12))
        p.drawArc(QRectF(7,-12,20,17), 160*16, 160*16)
        p.setBrush(QColor("#b6a399")); p.drawEllipse(QRectF(-7,-9,6,6))
    else:
        p.rotate(28); p.setBrush(QColor("#d0cabd")); p.drawEllipse(QRectF(-5,-15,10,26))
        p.drawLine(0,-13,0,18)
    p.restore()
