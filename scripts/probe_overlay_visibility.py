"""Bounded Cocoa stack probe using only this script's own two GUI processes.

Default invocation detaches a supervisor and returns immediately. Window-server
records are filtered to our PIDs/IDs before reporting; no titles are reported.
This tests native ordering against an opaque helper, not screenshot pixels or
other applications. Demo entry is logically accelerated, then animation paused.
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
WATCHDOG_SECONDS = 32
OVERLAYS = ("gate", "bowl", "cat")


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def fingerprints():
    names = ["backyard_visitor/app.py", "backyard_visitor/overlays.py",
             "backyard_visitor/desktop.py", "scripts/probe_overlay_visibility.py"]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


class Cocoa:
    """Pointer-safe Objective-C messages scoped to our QWidget/NSApplication."""

    def __init__(self):
        self.objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self.objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self.objc.objc_getClass.restype = ctypes.c_void_p
        self.objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.objc.sel_registerName.restype = ctypes.c_void_p
        self._functions = {}

    def send(self, obj, selector, result=ctypes.c_void_p, args=(), values=()):
        key = (result, args)
        if key not in self._functions:
            signature = ctypes.CFUNCTYPE(result, ctypes.c_void_p, ctypes.c_void_p, *args)
            self._functions[key] = signature(("objc_msgSend", self.objc))
        return self._functions[key](obj, self.objc.sel_registerName(selector.encode()), *values)

    def singleton(self, cls, selector):
        return self.send(self.objc.objc_getClass(cls.encode()), selector)

    def app(self):
        return self.singleton("NSApplication", "sharedApplication")

    def active(self):
        return bool(self.send(self.app(), "isActive", ctypes.c_bool))

    def frontmost_pid(self):
        workspace = self.singleton("NSWorkspace", "sharedWorkspace")
        app = self.send(workspace, "frontmostApplication")
        return int(self.send(app, "processIdentifier", ctypes.c_int)) if app else None

    def window(self, widget):
        # On Cocoa, Qt's WId points to an NSView, NOT to a CGWindowID.
        view = int(widget.winId())
        window = self.send(view, "window")
        if not window:
            raise RuntimeError("QWidget has no NSWindow")
        return window

    def describe(self, widget):
        window = self.window(widget)
        return {"window_id": int(self.send(window, "windowNumber", ctypes.c_long)),
                "level": int(self.send(window, "level", ctypes.c_long)),
                "native_visible": bool(self.send(window, "isVisible", ctypes.c_bool)),
                "key": bool(self.send(window, "isKeyWindow", ctypes.c_bool)),
                "qt_visible": widget.isVisible(), "position": [widget.x(), widget.y()]}

    def activate_own_window(self, widget):
        self.send(self.app(), "setActivationPolicy:", ctypes.c_bool,
                  (ctypes.c_long,), (0,))
        self.send(self.app(), "activateIgnoringOtherApps:", None,
                  (ctypes.c_bool,), (True,))
        self.send(self.window(widget), "makeKeyAndOrderFront:", None,
                  (ctypes.c_void_p,), (None,))


def private_records(records, pid_to_ids):
    """Drop all fields except own numeric geometry/stack metadata."""
    result = {}
    for order, record in enumerate(records):
        pid, wid = record.get("kCGWindowOwnerPID"), record.get("kCGWindowNumber")
        if wid not in pid_to_ids.get(pid, set()):
            continue
        bounds = record.get("kCGWindowBounds", {})
        result[wid] = {"order": order, "layer": record.get("kCGWindowLayer"),
                       "alpha": record.get("kCGWindowAlpha"),
                       "bounds": {k: bounds.get(k) for k in ("X", "Y", "Width", "Height")}}
    return result


def intersects(a, b):
    try:
        return (min(a["X"] + a["Width"], b["X"] + b["Width"]) > max(a["X"], b["X"])
                and min(a["Y"] + a["Height"], b["Y"] + b["Height"]) > max(a["Y"], b["Y"]))
    except (KeyError, TypeError):
        return False


def evaluate_sample(sample, baseline):
    windows, enabled = sample["windows"], sample["enabled"]
    helper = sample.get("helper", {})
    hcg = helper.get("cg", {})
    checks = {"controller_inactive": not sample["controller_active"],
              "helper_frontmost": sample["helper_frontmost"],
              "helper_normal": helper.get("level") == 0 and hcg.get("layer") == 0,
              "helper_key": helper.get("key") is True,
              "hidden_bin_preserved": not sample["bin_visible"],
              "positions_preserved": all(windows[k]["position"] == baseline[k] for k in windows)}
    for name, window in windows.items():
        cg = window.get("cg", {})
        visible = (window["qt_visible"] and window["native_visible"] and bool(cg)
                   and (cg.get("alpha") or 0) > 0)
        checks[f"{name}_on_screen"] = bool(visible)
        checks[f"{name}_not_key"] = not window["key"]
        checks[f"{name}_overlaps_helper"] = intersects(cg.get("bounds", {}), hcg.get("bounds", {}))
        order, helper_order = cg.get("order"), hcg.get("order")
        above = order is not None and helper_order is not None and order < helper_order
        below = order is not None and helper_order is not None and order > helper_order
        if name == "journal":
            checks["journal_normal_below_helper"] = window["level"] == 0 and cg.get("layer") == 0 and below
        else:
            checks[f"{name}_expected_stack"] = (above and window["level"] > 0 and cg.get("layer", 0) > 0
                                                 if enabled else below and window["level"] == 0 and cg.get("layer") == 0)
    return checks


def helper(job):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
    qt = QApplication(["BackyardOverlayHelper"])
    qt.setQuitOnLastWindowClosed(False)
    cocoa = Cocoa()
    window = QWidget()
    window.setWindowTitle("后院访客 · 临时层级验证窗口")
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("后院访客 · 独立验证窗口\n\n正在检查桌面挂件 ON / OFF / ON。\n窗口会自动关闭，不操作其他应用。"))
    window.setStyleSheet("QWidget{background:#dedfcf;color:#344137;} QLabel{font-size:18px;}")
    window.setGeometry(*read_json(job / "controller.json")["helper_geometry"])
    window.show()
    cocoa.activate_own_window(window)  # Exactly once; never repair focus during sampling.

    def pulse():
        if read_json(job / "controller.json").get("state") in {"checks_complete", "failed"}:
            qt.quit()
            return
        write_json(job / "helper.json", {"pid": os.getpid(), "active": cocoa.active(),
                                         "window": cocoa.describe(window), "heartbeat": time.monotonic()})
    timer = QTimer(); timer.timeout.connect(pulse); timer.start(80)
    QTimer.singleShot(27000, qt.quit)
    pulse()
    return qt.exec()


def controller(job):
    sys.path.insert(0, str(ROOT))
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter, _CoreGraphics
    qt = QApplication(["BackyardOverlayProbe"])
    qt.setQuitOnLastWindowClosed(False)
    cocoa, graphics = Cocoa(), _CoreGraphics()
    c = Controller(qt, demo=True, hidden=True, adapter=DesktopAdapter(),
                   enable_native=False, seed=31, visit_limit=1)
    c.engine.open_gate(); c.engine.invite("amber")
    entry_ticks = 0
    while c.engine.visitors["amber"].state in {"peeking", "entering"} and entry_ticks < 800:
        c.engine.tick(.05)
        entry_ticks += 1
    c.changed(); c.set_paused(True)
    c.show_journal()
    available = c.screen.availableGeometry()
    x, y, width, height = available.x(), available.y(), available.width(), available.height()
    cat = next(iter(c.cat_windows.values()))
    cat.refresh_input_region()
    c.journal.move(x + 20, y + 15)
    c.bin.hide()
    windows = {"gate": c.gate, "bowl": c.bowl, "cat": cat, "journal": c.journal}
    baseline = {k: [v.x(), v.y()] for k, v in windows.items()}
    report = {"state": "ready", "pid": os.getpid(), "helper_geometry": [x, y, width, height],
              "samples": [], "activation_events_after_helper": [], "qt_platform": qt.platformName(),
              "device_pixel_ratio": c.screen.devicePixelRatio(), "baseline_positions": baseline,
              "entry_model_seconds": entry_ticks * .05, "entry_state": c.engine.visitors["amber"].state}
    started, helper_ready = time.monotonic(), None
    plan = [(0.8, "on_initial", True), (1.8, "off", False), (2.8, "on_restored", True)]
    next_step, toggled = 0, False
    qt.applicationStateChanged.connect(lambda state: report["activation_events_after_helper"].append(
        {"state": state.name, "elapsed": time.monotonic() - started}) if helper_ready is not None else None)

    def finish(error=None):
        report["elapsed_seconds"] = time.monotonic() - started
        report["error"] = error
        report["focus_never_returned"] = not any(e["state"] == "ApplicationActive" for e in report["activation_events_after_helper"])
        passed = len(report["samples"]) == 3 and all(all(s["checks"].values()) for s in report["samples"])
        report["state"] = "checks_complete" if passed and report["focus_never_returned"] and error is None else "failed"
        write_json(job / "controller.json", report)
        timer.stop(); c.quit()

    def pulse():
        nonlocal helper_ready, next_step, toggled
        try:
            now = time.monotonic()
            if now - started > 24:
                finish("Timed out waiting for an active independent helper or native samples")
                return
            h = read_json(job / "helper.json")
            if not h:
                return
            if helper_ready is None:
                if h.get("active") and h.get("window", {}).get("key") and cocoa.frontmost_pid() == h.get("pid"):
                    helper_ready = now
                else:
                    return
            delay, label, enabled = plan[next_step]
            elapsed = now - helper_ready
            if not toggled and elapsed >= delay - .45:
                c.set_overlay_topmost(enabled)
                toggled = True
            if elapsed < delay:
                return
            descriptions = {name: cocoa.describe(window) for name, window in windows.items()}
            helper_window = h["window"]
            records = private_records(graphics.window_info(), {
                os.getpid(): {v["window_id"] for v in descriptions.values()},
                h["pid"]: {helper_window["window_id"]}})
            for value in [*descriptions.values(), helper_window]:
                value["cg"] = records.get(value["window_id"], {})
            sample = {"label": label, "enabled": enabled, "elapsed_seconds": now - started,
                      "controller_active": cocoa.active(), "helper_frontmost": cocoa.frontmost_pid() == h["pid"],
                      "helper": helper_window, "helper_heartbeat_age": now - h["heartbeat"],
                      "bin_visible": c.bin.isVisible(), "windows": descriptions}
            if not report["samples"]:
                # Establish position baseline after initial Cocoa layout settles.
                # Following OFF and ON samples must preserve this exact baseline.
                baseline.update({k: v["position"][:] for k, v in descriptions.items()})
            sample["checks"] = evaluate_sample(sample, baseline)
            report["samples"].append(sample)
            report["state"] = "running"
            write_json(job / "controller.json", report)
            next_step += 1; toggled = False
            if next_step == len(plan):
                finish()
        except Exception:
            finish(traceback.format_exc())

    timer = QTimer(); timer.timeout.connect(pulse); timer.start(40)
    write_json(job / "controller.json", report)
    qt.exec()
    return 0 if report["state"] == "checks_complete" else 1


def stop_owned(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=1)


def supervise(output):
    start = time.monotonic()
    before = fingerprints()
    report = {"schema_version": 1, "state": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
              "supervisor_pid": os.getpid(), "source_hashes": before,
              "scope": "Cocoa source Controller demo; native adapters disabled; own helper only; logically accelerated entry then paused; no screenshot or memory measurement",
              "limitations": "Finite window-server ordering probe; does not prove behavior over full-screen Spaces, sleep, hotplug, or all third-party apps."}
    child = assistant = None
    try:
        if sys.platform != "darwin":
            raise RuntimeError("Requires a logged-in macOS Cocoa session")
        with tempfile.TemporaryDirectory(prefix="backyard-overlay-probe-") as folder:
            job = Path(folder)
            env = dict(os.environ, QT_QPA_PLATFORM="cocoa", PYTHONDONTWRITEBYTECODE="1")
            child = subprocess.Popen([sys.executable, __file__, "--controller", str(job)], cwd=ROOT, env=env)
            report["controller_pid"] = child.pid
            write_json(output, report)
            while child.poll() is None and time.monotonic() - start < WATCHDOG_SECONDS:
                snapshot = read_json(job / "controller.json")
                if assistant is None and snapshot.get("state") == "ready":
                    assistant = subprocess.Popen([sys.executable, __file__, "--helper", str(job)], cwd=ROOT, env=env)
                    report["helper_pid"] = assistant.pid
                report["controller"] = snapshot
                report["elapsed_seconds"] = time.monotonic() - start
                write_json(output, report)
                time.sleep(.15)
            if child.poll() is None:
                report["error"] = "bounded supervisor timeout"
            report["controller"] = read_json(job / "controller.json")
            stop_owned(child)
            report["controller_exit_code"] = child.returncode
            # Complete helper teardown while its temporary checkpoint folder
            # still exists. A failed/hung helper is stopped only via our Popen.
            if assistant is not None:
                try:
                    assistant.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    stop_owned(assistant)
            report["sources_unchanged"] = before == fingerprints()
            success = report["controller"].get("state") == "checks_complete" and child.returncode == 0 and assistant is not None and assistant.returncode == 0 and report["sources_unchanged"]
            report["state"] = "complete" if success else "failed"
    except Exception:
        report["state"], report["error"] = "failed", traceback.format_exc()
    finally:
        stop_owned(child); stop_owned(assistant)
        report["helper_exit_code"] = assistant.returncode if assistant is not None else None
        report["elapsed_seconds"] = time.monotonic() - start
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output, report)
    return 0 if report["state"] == "complete" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/overlay-visibility-v06.json")
    parser.add_argument("--supervisor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--controller", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--helper", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.controller:
        return controller(args.controller)
    if args.helper:
        return helper(args.helper)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.supervisor:
        return supervise(output)
    log = output.with_suffix(".log")
    with log.open("a") as stream:
        process = subprocess.Popen([sys.executable, __file__, "--supervisor", "--output", str(output)],
                                   cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    print(json.dumps({"state": "launched", "supervisor_pid": process.pid, "output": str(output), "log": str(log)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
