"""Prepare/run one bounded Cocoa background-companion and teaser lifecycle probe.

Default launch detaches a supervisor. A separate own GUI helper activates itself
once; it is never replaced by NSApplication.deactivate. The source Controller
uses natural Qt ticks and an isolated demo. No global input, permissions, memory
measurement, screenshots, or user-application manipulation. Inform the user
before running: the selected screen receives game clicks for 15 real seconds.
This metadata probe does NOT prove physical background click routing.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_overlay_visibility import read_json, write_json, stop_owned, private_records, intersects

HARD_SECONDS = 55
ENTRY_SECONDS = 30
CORE_WINDOWS = ("gate", "bowl", "cat")


def hashes():
    files = sorted((ROOT / "backyard_visitor").glob("*.py"))
    files += [Path(__file__).resolve(), ROOT / "scripts/probe_overlay_visibility.py"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def frontmost_role(pid, controller_pid, helper_pid):
    """Never persist the PID or identity of an unrelated application."""
    return "controller" if pid == controller_pid else "helper" if pid == helper_pid else "other"


def describe_window(cocoa, widget):
    """Owned NSWindow metadata only; native full-screen is not a Qt boolean."""
    row = cocoa.describe(widget)
    native = cocoa.window(widget)
    row.update(style_mask=int(cocoa.send(native, "styleMask", ctypes.c_ulong)),
               collection_behavior=int(cocoa.send(native, "collectionBehavior", ctypes.c_ulong)),
               on_active_space=bool(cocoa.send(native, "isOnActiveSpace", ctypes.c_bool)))
    return row


def fullscreen_checks(helper, screen):
    # Apple NSWindow.h: NSWindowStyleMaskFullScreen = 1 << 14. A borderless
    # screen-sized rectangle alone does not establish a native full-screen Space.
    bounds = helper.get("cg", {}).get("bounds", {})
    try:
        covers = len(screen) == 4 and all(abs(float(bounds[k])-float(v)) <= 1
            for k, v in zip(("X", "Y", "Width", "Height"), screen))
    except (TypeError, ValueError, KeyError):
        covers = False
    return {"helper_native_fullscreen": bool(helper.get("style_mask", 0) & (1 << 14)),
            "helper_on_active_space": helper.get("on_active_space") is True,
            "helper_covers_selected_screen": covers}


def sample_checks(sample):
    helper = sample.get("helper", {})
    hcg = helper.get("cg", {})
    names = CORE_WINDOWS + (("canvas", "hint") if sample.get("phase") == "play" else ())
    checks = {"controller_inactive": sample.get("controller_active") is False,
              "helper_frontmost": sample.get("frontmost_role") == "helper",
              "helper_fresh": 0 <= sample.get("helper_heartbeat_age", 99) < 1,
              "helper_active_key_and_normal": sample.get("helper_active") is True
                  and helper.get("key") is True and helper.get("level") == 0
                  and hcg.get("layer") == 0,
              "global_cursor_untouched": sample.get("global_override_none") is True,
              "hidden_bin_preserved": sample.get("bin_visible") is False,
              "cat_inside_selected_screen": sample.get("cat_fully_inside") is True}
    for name in names:
        w = sample.get("windows", {}).get(name, {})
        cg = w.get("cg", {})
        order, helper_order = cg.get("order"), hcg.get("order")
        checks[name + "_visible"] = (w.get("qt_visible") is True and w.get("native_visible") is True
                                      and (cg.get("alpha") or 0) > 0)
        checks[name + "_not_key"] = w.get("key") is False
        checks[name + "_above_helper"] = (order is not None and helper_order is not None
                                            and order < helper_order and (w.get("level") or 0) > 0)
        checks[name + "_overlaps_helper"] = intersects(cg.get("bounds", {}), hcg.get("bounds", {}))
    if sample.get("fullscreen_required"):
        checks.update(fullscreen_checks(helper, sample.get("screen_geometry", [])))
        for name in names:
            checks[name+"_on_active_space"] = sample.get("windows", {}).get(name, {}).get("on_active_space") is True
    if sample.get("phase") == "play":
        checks["local_cursor_scoped"] = sample.get("local_cursor_bitmap") is True or sample.get("pointer_outside_screen") is True
        checks["session_and_model_agree"] = sample.get("session_active") is True and sample.get("model_active_id") == "amber"
    return checks


def final_checks(report):
    samples = report.get("samples", [])
    phases = {name: [r for r in samples if r.get("phase") == name] for name in ("baseline", "play", "cleanup")}
    active = phases["play"]
    start, end = report.get("play_started_monotonic"), report.get("play_ended_monotonic")
    valid_times = isinstance(start, (float, int)) and isinstance(end, (float, int)) and end >= start
    times = [start, *(r["monotonic"] for r in active), end] if valid_times else []
    max_gap = max((b-a for a, b in zip(times, times[1:])), default=float("inf"))
    cleanup = report.get("cleanup", {})
    return {"natural_full_15_seconds": valid_times and report.get("finished_reason") == "completed" and 15.0-1e-6 <= end-start <= 17,
            "play_sample_coverage": len(active) >= 50 and max_gap <= .8,
            "local_wand_observed": any(r.get("local_cursor_bitmap") is True for r in active),
            "all_background_samples_valid": bool(samples) and all(all(sample_checks(r).values()) for r in samples),
            "baseline_and_cleanup_observed": len(phases["baseline"]) >= 3 and len(phases["cleanup"]) >= 8,
            "no_controller_reactivation": not report.get("controller_activated_after_helper", False),
            "session_canvas_hint_and_timer_removed": all(cleanup.get(k) is True for k in
                ("session_removed", "model_stopped", "canvas_deleted", "hint_deleted", "timer_stopped", "cursor_binding_removed", "native_windows_absent")),
            "no_global_cursor_override": cleanup.get("global_override_none") is True,
            "only_normal_ticks": report.get("clock_acceleration_seen") is False}


def watchdog(elapsed, child_age, helper_age, helper_started):
    # Leave time for bounded terminate/kill cleanup before the 55-second ceiling.
    if elapsed >= 50: return "overall_deadline"
    if child_age is None and elapsed > 8: return "controller_startup_without_checkpoint"
    if child_age is not None and child_age > 4: return "controller_heartbeat_stale"
    if helper_started is not None:
        if helper_age is None and elapsed-helper_started > 6: return "helper_startup_without_checkpoint"
        if helper_age is not None and helper_age > 3: return "helper_heartbeat_stale"
    return None


def owned_helper_identity(heartbeat, pid, executable):
    """Only the Popen-owned frozen helper may receive the one activation request."""
    return (heartbeat.get("pid") == pid and heartbeat.get("frozen") is True
        and bool(heartbeat.get("window", {}).get("window_id"))
        and Path(heartbeat.get("executable", "")).resolve() == Path(executable).resolve())


def native_fullscreen_request_ready(active, frontmost_pid, own_pid, window, requested):
    return (not requested and active is True and frontmost_pid == own_pid
        and window.get("key") is True and window.get("native_visible") is True)


def run_helper(job):
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
    from scripts.probe_overlay_visibility import Cocoa
    qt = QApplication(["BackyardBackgroundHelper"]); qt.setQuitOnLastWindowClosed(False)
    fullscreen = read_json(job / "config.json").get("fullscreen", False)
    cocoa = Cocoa()
    flags = Qt.WindowType.Window if fullscreen else Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
    window = QWidget(None, flags)
    window.setWindowTitle("后院访客 · 自有后台验证底窗")
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("后院访客 · 自有前台测试窗口\n\n正在验证后台陪伴与 15 秒逗猫。\n逗猫期间当前屏幕点击暂用于游戏，右键结束。\n本窗口会自动关闭；不会操作其他应用。"))
    window.setStyleSheet("QWidget{background:#e1e4d5;color:#3d493c} QLabel{font-size:20px}")
    window.setGeometry(*read_json(job / "controller.json")["helper_geometry"])
    window.show()
    # Run only after the event loop has completed application launch. The
    # supervisor may use LaunchServices once if macOS declines self-activation.
    QTimer.singleShot(0, lambda: cocoa.activate_own_window(window))
    fullscreen_requested_at = None
    print(json.dumps({"event":"own_helper_shown","pid":os.getpid(),"fullscreen_requested":fullscreen}),flush=True)
    identity = {"pid":os.getpid(), "frozen":bool(getattr(sys,"frozen",False)), "executable":sys.executable}
    closing = False
    def close_helper():
        ended = describe_window(cocoa, window)
        write_json(job / "helper.json", {**identity, "active":cocoa.active(), "window":ended,
            "heartbeat_monotonic":time.monotonic(), "shutdown_requested":True,
            "fullscreen_exit_observed":not bool(ended["style_mask"] & (1 << 14))})
        window.hide(); window.close(); qt.quit()
    def pulse():
        nonlocal closing, fullscreen_requested_at
        if closing:return
        controller = read_json(job / "controller.json")
        if controller.get("state") in {"checks_complete", "failed", "inconclusive"}:
            closing = True
            if fullscreen:
                window.showNormal()
                QTimer.singleShot(750, close_helper)
            else:close_helper()
            return
        active, native = cocoa.active(), describe_window(cocoa, window)
        if fullscreen and native_fullscreen_request_ready(active, cocoa.frontmost_pid(), os.getpid(),
                native, fullscreen_requested_at is not None):
            fullscreen_requested_at = time.monotonic()
            window.showFullScreen()
        write_json(job / "helper.json", {**identity, "active":active,
            "window":native, "qt_fullscreen":window.isFullScreen(),
            "fullscreen_requested_at":fullscreen_requested_at, "heartbeat_monotonic":time.monotonic()})
    timer = QTimer(); timer.timeout.connect(pulse); timer.start(100)
    QTimer.singleShot(51000, qt.quit); pulse()
    return qt.exec()


def run_controller(job):
    from PyQt6 import sip
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QCursor
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter, _CoreGraphics
    from scripts.probe_overlay_visibility import Cocoa
    tempfile.tempdir = str(job)
    qt = QApplication(["BackyardBackgroundProbe"]); qt.setQuitOnLastWindowClosed(False)
    cocoa, graphics = Cocoa(), _CoreGraphics()
    fullscreen = read_json(job / "config.json").get("fullscreen", False)
    if read_json(job / "config.json").get("accessory", False):
        cocoa.send(cocoa.app(), "setActivationPolicy:", ctypes.c_bool, (ctypes.c_long,), (1,))
    c = Controller(qt, demo=True, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=31, visit_limit=1)
    for index, screen in enumerate(qt.screens()):
        if screen.geometry().contains(QCursor.pos()): c.change_screen(index); break
    c.set_overlay_topmost(True)
    c.engine.calm_mode = c.settings["calm_mode"] = True
    c.engine.cats["amber"].trust = 60
    c.engine.open_gate(); c.engine.invite("amber"); c.changed()
    available = c.screen.availableGeometry()
    started = time.monotonic(); running = True; helper_since = baseline_since = None
    session = canvas = hint = None; native_game_ids = set()
    report = {"state":"ready", "phase":"entry", "pid":os.getpid(), "samples":[], "entry_samples":[],
        "helper_geometry":[available.x(), available.y(), available.width(), available.height()],
        "screen_geometry":[c.screen.geometry().x(), c.screen.geometry().y(), c.screen.geometry().width(), c.screen.geometry().height()],
        "screen_dpr":c.screen.devicePixelRatio(), "fullscreen_requested":fullscreen,
        "activation_policy":int(cocoa.send(cocoa.app(), "activationPolicy", ctypes.c_long)), "activation_events":[], "controller_activated_after_helper":False,
        "clock_acceleration_seen":False, "fixture":"Single amber invite, seed31, trust60, calm mode; natural Controller Qt ticks only. No state/coordinate/dt assignment.",
        "input_scope":"Read-only pointer. No injected click, global override or warp. This probe does not prove physical background click routing."}
    def alive(obj): return obj is not None and not sip.isdeleted(obj)
    def checkpoint():
        report.update(heartbeat_monotonic=time.monotonic(), elapsed_seconds=time.monotonic()-started)
        write_json(job / "controller.json", report)
    def activated(state):
        if helper_since is not None:
            report["activation_events"].append({"state":state.name, "seconds":time.monotonic()-started})
            if state.name == "ApplicationActive": report["controller_activated_after_helper"] = True
    qt.applicationStateChanged.connect(activated)
    def finish(error=None, inconclusive=None):
        nonlocal running
        if not running:return
        running = False; timer.stop()
        if error:report["error"] = error
        if inconclusive:report["inconclusive_reason"] = inconclusive
        c.shutdown()
        report["shutdown_verified"] = c._quitting and c._teaser_session is None and not c._workers and c.pool.activeThreadCount() == 0 and not any(t.isActive() for t in (c.timer,c.native_timer,c.save_timer,c.recycle_timer)) and all(not w.isVisible() for w in (c.gate,c.bowl,c.bin,*c.cat_windows.values()))
        report["state"] = ("failed" if error or not report["shutdown_verified"] else "inconclusive" if inconclusive else "checks_complete" if all(report.get("checks", {"missing":False}).values()) else "failed")
        checkpoint(); print(json.dumps({"event":"controller_finished","state":report["state"],"error":error,"inconclusive":inconclusive}),flush=True)
        qt.exit(0 if report["state"] in {"checks_complete","inconclusive"} else 1)
    def ended(reason):
        report.update(finished_reason=reason, session_error=session.error if session else None, play_ended_monotonic=time.monotonic(), phase="cleanup")
        print(json.dumps({"event":"play_finished","reason":reason}),flush=True)
    def snapshot(h, now):
        objects = {"gate":c.gate, "bowl":c.bowl, "cat":c.cat_windows.get("amber")}
        if report["phase"] == "play":objects.update(canvas=canvas, hint=hint)
        windows = {name:describe_window(cocoa,w) for name,w in objects.items() if alive(w)}
        helper = dict(h["window"])
        own_ids = {v["window_id"] for v in windows.values()} | native_game_ids
        records = private_records(graphics.window_info(), {os.getpid():own_ids, h["pid"]:{helper["window_id"]}})
        for value in [helper,*windows.values()]:value["cg"] = records.get(value["window_id"], {})
        cat = c.cat_windows.get("amber"); visitor = c.engine.visitors.get("amber")
        row = {"monotonic":now, "phase":report["phase"], "controller_active":cocoa.active(),
            "fullscreen_required":fullscreen, "screen_geometry":report["screen_geometry"],
            "frontmost_role":frontmost_role(cocoa.frontmost_pid(),os.getpid(),h["pid"]),
            "helper_active":h.get("active"), "helper_heartbeat_age":now-h["heartbeat_monotonic"],
            "helper":helper, "windows":windows, "global_override_none":qt.overrideCursor() is None,
            "bin_visible":c.bin.isVisible(), "cat_fully_inside":bool(cat and cat.isVisible() and c.screen.geometry().contains(cat.geometry())),
            "cat_state":visitor.state if visitor else None, "model_active_id":c.engine.active_teaser_id,
            "session_active":session.active if session else False,
            "local_cursor_bitmap":alive(canvas) and not canvas.cursor().pixmap().isNull(),
            "pointer_outside_screen":not c.screen.geometry().contains(QCursor.pos())}
        if report["phase"] == "cleanup":
            report["cleanup"] = {"session_removed":c._teaser_session is None, "model_stopped":c.engine.active_teaser_id is None,
                "canvas_deleted":not alive(canvas), "hint_deleted":not alive(hint),
                "timer_stopped":session is None or not alive(session.timer) or not session.timer.isActive(),
                "cursor_binding_removed":not alive(canvas), "global_override_none":qt.overrideCursor() is None,
                "native_windows_absent":not any(wid in records for wid in native_game_ids),
                "rest_state":visitor.state if visitor else None, "rest_arrival_state":visitor.arrival_state if visitor else None}
        return row
    def pulse():
        nonlocal helper_since, baseline_since, session, canvas, hint
        if not running:return
        try:
            now = time.monotonic(); elapsed = now-started
            if elapsed > 50:finish("Controller deadline exceeded");return
            report["clock_acceleration_seen"] |= c.engine._time > elapsed+1
            h = read_json(job / "helper.json")
            if not h or not h.get("window"):
                checkpoint();return
            role = frontmost_role(cocoa.frontmost_pid(),os.getpid(),h["pid"])
            if helper_since is None:
                preparation = report.setdefault("helper_preparation", [])
                preparation.append({"seconds":elapsed,"frontmost_role":role,"helper_active":h.get("active"),
                    "controller_active":cocoa.active(),"qt_fullscreen":h.get("qt_fullscreen"),
                    "helper_window":h["window"]})
                if len(preparation)>40:del preparation[0]
                native_space_ready = (not fullscreen or (h["window"].get("style_mask", 0) & (1 << 14)
                    and h["window"].get("on_active_space") is True))
                if role == "helper" and h.get("active") and not cocoa.active() and h["window"].get("key") and native_space_ready:
                    helper_since = now; report["helper_confirmed_seconds"] = elapsed
                    print(json.dumps({"event":"helper_genuinely_frontmost","seconds":elapsed}),flush=True)
                elif elapsed > 10:finish(inconclusive="Own helper did not become genuinely frontmost");return
                checkpoint();return
            if role == "other":finish(inconclusive="Another application became frontmost; no reactivation attempted");return
            if role == "controller" or cocoa.active():finish("Controller took focus after helper became frontmost");return
            cat = c.cat_windows.get("amber"); v = c.engine.visitors.get("amber")
            ready = bool(v and cat and cat.isVisible() and c.screen.geometry().contains(cat.geometry())
                and v.state in {"idle","sleeping","purring","walking","running","playing","resting"}
                and v.route_motion not in {"leaving","fleeing"} and v.posture_remaining <= 0
                and not v.carrying_gift and not v.demo_gift_pending and not v.true_name_pending and v.true_name_remaining <= 0)
            if report["phase"] == "entry":
                report["entry_samples"].append({"seconds":elapsed,"frontmost_role":role,"controller_active":cocoa.active(),"cat_state":v.state if v else None,"fully_inside":bool(cat and c.screen.geometry().contains(cat.geometry()))})
                if ready and now-helper_since >= 1:
                    report["phase"] = "baseline"; baseline_since = now
                    print(json.dumps({"event":"natural_entry_ready","seconds":elapsed}),flush=True)
                elif elapsed >= ENTRY_SECONDS:finish(inconclusive="Natural eligible entry not observed within 30 seconds");return
                else:checkpoint();return
            row = snapshot(h,now); report["samples"].append(row)
            if not all(sample_checks(row).values()):
                finish("Background metadata failed: "+", ".join(k for k,v in sample_checks(row).items() if not v));return
            if report["phase"] == "baseline" and now-baseline_since >= 1:
                if not ready:finish(inconclusive="Cat became ineligible before game start");return
                if not c.start_play("amber"):finish(inconclusive="Controller rejected naturally eligible start");return
                session = c._teaser_session; canvas = session._canvas; hint = session._hint
                native_game_ids.update(cocoa.describe(w)["window_id"] for w in (canvas,hint))
                report.update(phase="play",play_started_monotonic=session._started)
                print(json.dumps({"event":"real_15_second_play_started","seconds":elapsed}),flush=True)
                session.finished.connect(ended)
            elif report["phase"] == "cleanup" and now-report["play_ended_monotonic"] >= 3:
                if report.get("session_error"):finish("Session error: "+report["session_error"]);return
                if report.get("finished_reason") != "completed":finish(inconclusive="Game interrupted: "+str(report.get("finished_reason")));return
                report["checks"] = final_checks(report);finish();return
            checkpoint()
        except Exception:finish(traceback.format_exc())
    timer = QTimer(); timer.timeout.connect(pulse); timer.start(250)
    checkpoint(); code = qt.exec()
    if running:finish("Unexpected Qt exit");return 1
    return code


def supervise(output, fullscreen=False, helper_app=None, accessory=False):
    start = time.monotonic(); before = hashes(); child = helper = activation = None; helper_started = None
    activation_started = None
    report = {"schema_version":1,"state":"running","supervisor_pid":os.getpid(),"source_hashes":before,
        "started_utc":datetime.now(timezone.utc).isoformat(),"hard_deadline_seconds":HARD_SECONDS,
        "fullscreen_requested":fullscreen,
        "scope":"Source Cocoa; own helper genuinely frontmost; optional native full-screen Space, natural entry, 15-second play, metadata-only stack/focus and cleanup.",
        "limitations":"No frozen-app, memory, physical background click, screenshot, long-term, sleep or hotplug claim. Native Space applies only when explicitly requested and all native checks pass."}
    try:
        if sys.platform != "darwin":raise RuntimeError("Requires logged-in macOS Cocoa")
        with tempfile.TemporaryDirectory(prefix="background-teaser-",dir=output.parent) as folder:
            job = Path(folder); report["temporary_job"] = str(job)
            write_json(job / "config.json", {"fullscreen":fullscreen,"accessory":accessory})
            env = dict(os.environ,QT_QPA_PLATFORM="cocoa",PYTHONDONTWRITEBYTECODE="1")
            helper_executable = helper_app / "Contents/MacOS/BackyardFullscreenHelper" if helper_app else None
            if helper_executable:
                report["helper_app"] = str(helper_app)
                report["helper_executable_sha256"] = hashlib.sha256(helper_executable.read_bytes()).hexdigest()
            child = subprocess.Popen([sys.executable,"-B","-u",__file__,"--controller",str(job)],cwd=ROOT,env=env)
            report["controller_pid"] = child.pid
            while child.poll() is None:
                now = time.monotonic(); data = read_json(job/"controller.json"); h = read_json(job/"helper.json")
                if helper is None and data.get("state") == "ready":
                    helper_command = ([str(helper_executable), str(job)] if helper_executable else
                        [sys.executable,"-B","-u",__file__,"--helper",str(job)])
                    helper = subprocess.Popen(helper_command,cwd=ROOT,env=env)
                    helper_started = now-start; report["helper_pid"] = helper.pid
                # Keep ownership through the existing Popen handle. `open` only
                # activates this known bundled helper, never a foreign app. A
                # mistaken second launch receives no job argument and exits2.
                if (helper_executable and helper and activation is None
                        and data.get("state") == "ready" and "helper_confirmed_seconds" not in data
                        and h.get("fullscreen_requested_at") is None and now-start-helper_started >= .5
                        and owned_helper_identity(h, helper.pid, helper_executable)
                        and not h.get("active")):
                    activation = subprocess.Popen(["/usr/bin/open", "-a", str(helper_app)],
                        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    activation_started = now
                    report["launchservices_activation"] = {"requested":True,"seconds":now-start,
                        "owned_helper_pid":helper.pid,"command_scope":"exact prepared helper app"}
                if activation is not None:
                    if activation.poll() is None and now-activation_started > 3:
                        stop_owned(activation)
                    report["launchservices_activation"]["exit_code"] = activation.poll()
                beat, hbeat = data.get("heartbeat_monotonic"), h.get("heartbeat_monotonic")
                reason = watchdog(now-start,None if beat is None else now-beat,None if hbeat is None else now-hbeat,helper_started)
                if helper is not None and helper.poll() is not None and data.get("state") not in {"checks_complete","failed","inconclusive"}:reason = "helper_exited_early"
                report.update(controller=data,helper=h,elapsed_seconds=now-start,heartbeat_monotonic=now)
                write_json(output,report)
                if reason:report["termination_reason"] = reason;break
                time.sleep(.15)
            stop_owned(child)
            report["controller"] = read_json(job/"controller.json")
            if helper is not None:
                try:helper.wait(timeout=2 if fullscreen else 1)
                except subprocess.TimeoutExpired:stop_owned(helper)
            report.update(controller_exit_code=child.returncode,helper_exit_code=helper.returncode if helper else None,sources_unchanged=before==hashes(),helper=read_json(job/"helper.json"))
            data = report["controller"]
            fullscreen_exit = not fullscreen or report.get("helper", {}).get("fullscreen_exit_observed") is True
            helper_identity = not helper_app or (helper is not None and
                owned_helper_identity(report.get("helper", {}), helper.pid, helper_executable))
            report["helper_identity_verified"] = helper_identity
            valid_exit = fullscreen_exit and helper_identity and child.returncode == 0 and helper is not None and helper.returncode == 0 and report["sources_unchanged"] and not report.get("termination_reason") and data.get("shutdown_verified") is True
            report["state"] = "complete" if valid_exit and data.get("state") == "checks_complete" else "inconclusive" if valid_exit and data.get("state") == "inconclusive" else "failed"
    except Exception:report.update(state="failed",error=traceback.format_exc())
    finally:
        stop_owned(child);stop_owned(helper);stop_owned(activation)
        report.update(elapsed_seconds=time.monotonic()-start,finished_utc=datetime.now(timezone.utc).isoformat(),
            controller_handle_exited=child is None or child.poll() is not None,helper_handle_exited=helper is None or helper.poll() is not None)
        write_json(output,report)
    return 0 if report["state"] in {"complete","inconclusive"} else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"output/v09/background-teaser.json")
    parser.add_argument("--fullscreen",action="store_true",help="Use an owned native full-screen Space; it closes automatically")
    parser.add_argument("--helper-app",type=Path,help="Use an owned, bundled helper in project output/")
    parser.add_argument("--accessory",action="store_true",help="Match the frozen product accessory activation policy in this source-only fixture")
    parser.add_argument("--supervisor",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--controller",type=Path,help=argparse.SUPPRESS)
    parser.add_argument("--helper",type=Path,help=argparse.SUPPRESS)
    options = parser.parse_args(argv);options.output = options.output.resolve()
    if not options.output.is_relative_to(ROOT/"output"):parser.error("All evidence must stay inside project output/")
    if options.helper_app:
        options.helper_app = options.helper_app.resolve()
        if not options.helper_app.is_relative_to(ROOT/"output") or not (options.helper_app/"Contents/MacOS/BackyardFullscreenHelper").is_file():
            parser.error("Helper must be the prepared app inside project output/")
    return options


def main():
    options = parse_args()
    if options.helper:return run_helper(options.helper)
    if options.controller:
        try:return run_controller(options.controller)
        except Exception:
            write_json(options.controller/"controller.json",{"state":"failed","pid":os.getpid(),"error":traceback.format_exc(),"heartbeat_monotonic":time.monotonic()});return 1
    options.output.parent.mkdir(parents=True,exist_ok=True)
    if options.supervisor:return supervise(options.output, options.fullscreen, options.helper_app, options.accessory)
    if any(p.exists() for p in (options.output, options.output.with_suffix(".job.json"), options.output.with_suffix(".log"))):
        raise SystemExit("Refusing to overwrite existing evidence; choose a new --output")
    log = options.output.with_suffix(".log")
    with log.open("a") as stream:
        command = [sys.executable,"-B","-u",__file__,"--supervisor","--output",str(options.output)]
        if options.fullscreen:command.append("--fullscreen")
        if options.accessory:command.append("--accessory")
        if options.helper_app:command += ["--helper-app", str(options.helper_app)]
        process = subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    metadata = {"state":"submitted","supervisor_pid":process.pid,"report":str(options.output),"log":str(log)}
    write_json(options.output.with_suffix(".job.json"),metadata);print(json.dumps(metadata))
    return 0


if __name__ == "__main__":raise SystemExit(main())
