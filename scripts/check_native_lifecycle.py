"""Bounded Qt lifecycle endurance check, isolated from real progress and OS adapters.

Default invocation detaches a supervisor immediately. It starts its own source
controller with the Cocoa platform, records heartbeat/checkpoints, and can only
terminate its own child. No memory/CPU sampling, permissions, wallpaper writes,
screen changes, or existing app-instance control are performed. A finite native
run is not evidence of overnight use, real sleep, display hotplug, or a frozen app.
"""
from __future__ import annotations

import argparse
import ctypes
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
import weakref

ROOT = Path(__file__).resolve().parents[1]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def source_hashes():
    paths = sorted((ROOT / "backyard_visitor").glob("*.py")) + [Path(__file__).resolve(), ROOT / "scripts/probe_overlay_visibility.py"]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def resource_growth(baseline, current):
    """Positive per-class growth after each complete release cycle is a failure."""
    return {key: count - baseline.get(key, 0) for key, count in current.items()
            if count > baseline.get(key, 0)}


def final_state(child, exit_code, termination_reason, sources_unchanged):
    # A checkpoint/lock/PID alone never proves that a run finished or is alive.
    return "complete" if (child and child.get("state") == "checks_complete"
                          and exit_code == 0 and termination_reason is None
                          and sources_unchanged) else "failed"


def watchdog_reason(elapsed, heartbeat_age, timeout, startup_grace=20, heartbeat_timeout=12):
    if elapsed > timeout:
        return "overall_timeout"
    if heartbeat_age is None:
        return "startup_without_checkpoint" if elapsed > startup_grace else None
    if heartbeat_age > heartbeat_timeout:
        return "event_loop_heartbeat_stale"
    return None


def show_without_activating(journal):
    """Exercise this own window's lifecycle without raising/activating it."""
    from PyQt6.QtCore import Qt
    journal.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    journal.show()
    journal.lower()


def native_ignores_mouse_events(cocoa, widget):
    # Cocoa QWidget.winId is an NSView; Cocoa.window resolves its NSWindow.
    return bool(cocoa.send(cocoa.window(widget), "ignoresMouseEvents", ctypes.c_bool))


def coverage_checks(report, required_mouse_rounds):
    rounds = report.get("mouse_rounds", [])
    interactions = report.get("interaction_rounds", [])
    native = report.get("platform_plugin") == "cocoa"
    return {
        "requested_mouse_rounds": len(rounds) == required_mouse_rounds,
        "all_mice_waited_escaped_and_drained": all(all(row.get(key) for key in
             ("saw_idle", "saw_escaping", "removed", "active_pause_verified", "drained")) for row in rounds),
        "two_cats_observed_together": required_mouse_rounds == 0 or
             (report.get("multi_cat", {}).get("peak_visible", 0) >= 2 and
              report.get("multi_cat", {}).get("simultaneous_observed_seconds", 0) >= 3),
        "all_short_model_interactions_finished": len(interactions) == required_mouse_rounds and
             all(row.get("model_started") and row.get("model_stopped") and row.get("scratch_released") for row in interactions),
        "scratch_click_through_verified": all(row.get("qt_click_through") and
             (not native or row.get("native_ignores_mouse_events") is True) for row in interactions),
        "journal_did_not_take_focus": report.get("journal_cycles_completed", 0) >= 2 and
             len(report.get("journal_focus_samples", [])) == report.get("journal_cycles_completed", 0) and
             all(row.get("not_qt_active") and (not native or row.get("native_not_key"))
                 for row in report.get("journal_focus_samples", [])),
        "no_fullscreen_interaction_canvas": report.get("fullscreen_canvas_seen") is False,
        "no_global_cursor_override": report.get("global_cursor_override_seen") is False,
    }


def command_options(options):
    result = ["--output", str(options.output), "--platform", options.platform,
              "--journal-cycles", str(options.journal_cycles),
              "--mouse-rounds", str(options.mouse_rounds),
              "--minimum-seconds", str(options.minimum_seconds),
              "--timeout", str(options.timeout), "--seed", str(options.seed)]
    if options.quick_check:
        result.append("--quick-check")
    return result


def supervise(options):
    import fcntl
    lock_path = options.output.with_suffix(".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Output is already owned by another supervisor; no application launched.", flush=True)
            return 2
        report = {
            "schema_version": 2, "state": "running", "started_at": utc_now(),
            "supervisor_pid": os.getpid(), "child_pid": None,
            "scope": "offscreen_script_smoke" if options.quick_check else
                     "native_source_qt_short_endurance" if options.platform == "cocoa" else
                     "offscreen_source_qt_simulation",
            "requested_platform": options.platform,
            "timing": {"logical_acceleration": False, "model_dt_injected": False,
                       "minimum_real_seconds": options.minimum_seconds,
                       "maximum_real_seconds": options.timeout},
            "requested_journal_cycles_per_batch": options.journal_cycles,
            "requested_mouse_rounds": options.mouse_rounds,
            "source_sha256": source_hashes(), "exit_code": None,
            "termination_reason": None, "child": None,
            "limitations": [
                "Instrumented source Controller with a real Qt event loop, not the frozen .app.",
                "Finite duration; no overnight-use, real sleep/wake, screen-hotplug, or permissions claim.",
                "Counts and destroyed signals detect retained Qt objects, not every native resource leak.",
                "No RSS/USS/physical-footprint/CPU sampling and no memory-target assessment.",
                "Journal uses show-without-activation/lower, never show_journal or activateWindow.",
                "Scratch effects are staged directly; their native input transparency is read, no clicks are injected.",
                "Short teaser checks drive the model only; no full-screen cursor-capturing canvas is created.",
                "Separate demo progress, no native desktop adapter, no manipulation of existing app instances.",
            ],
        }
        write_json(options.output, report)
        started = time.monotonic()
        process = None
        try:
            # Only the child and this temporary checkpoint are used for liveness.
            with tempfile.TemporaryDirectory(prefix="backyard-lifecycle-supervisor-") as scratch:
                checkpoint = Path(scratch) / "child.json"
                command = [sys.executable, "-B", "-u", str(Path(__file__).resolve()),
                           "--child", "--checkpoint", str(checkpoint), *command_options(options)]
                environment = os.environ.copy()
                environment["QT_QPA_PLATFORM"] = options.platform
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                process = subprocess.Popen(command, cwd=ROOT, env=environment,
                                           stdin=subprocess.DEVNULL, stdout=sys.stdout,
                                           stderr=subprocess.STDOUT)
                report["child_pid"] = process.pid
                while process.poll() is None:
                    now = time.monotonic()
                    snapshot = read_json(checkpoint)
                    if snapshot:
                        report["child"] = snapshot
                    heartbeat = snapshot.get("heartbeat_monotonic") if snapshot else None
                    age = now - heartbeat if isinstance(heartbeat, (int, float)) else None
                    reason = watchdog_reason(now - started, age, options.timeout)
                    report["checked_at"] = utc_now()
                    report["elapsed_real_seconds"] = round(now - started, 3)
                    report["heartbeat_age_seconds"] = round(age, 3) if age is not None else None
                    write_json(options.output, report)
                    if reason:
                        report["termination_reason"] = reason
                        break
                    time.sleep(0.25)
                if process.poll() is None:
                    # Never search for names/PIDs or stop any other user's instance.
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                report["exit_code"] = process.wait(timeout=3)
                report["child"] = read_json(checkpoint) or report["child"]
        except Exception as error:
            report["error"] = {"type": type(error).__name__, "message": str(error)}
            report["termination_reason"] = report["termination_reason"] or "supervisor_error"
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                report["exit_code"] = process.returncode
            report["source_sha256_after"] = source_hashes()
            report["sources_unchanged"] = report["source_sha256"] == report["source_sha256_after"]
            report["state"] = final_state(report["child"], report["exit_code"],
                                          report["termination_reason"], report["sources_unchanged"])
            report["finished_at"] = utc_now()
            report["elapsed_real_seconds"] = round(time.monotonic() - started, 3)
            write_json(options.output, report)
        print(json.dumps({"state": report["state"], "output": str(options.output),
                          "elapsed_real_seconds": report["elapsed_real_seconds"],
                          "termination_reason": report["termination_reason"]}), flush=True)
        return 0 if report["state"] == "complete" else 1


def run_child(options):
    # QApplication and product imports exist only in the separate test child.
    os.environ["QT_QPA_PLATFORM"] = options.platform
    sys.path.insert(0, str(ROOT))
    from PyQt6 import sip
    from PyQt6.QtCore import QTimer, Qt, QT_VERSION_STR, PYQT_VERSION_STR
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter
    from scripts.probe_overlay_visibility import Cocoa

    qt = QApplication(["后院访客生命周期验证（独立体验）"])
    qt.setQuitOnLastWindowClosed(False)
    if qt.platformName() != options.platform:
        raise RuntimeError(f"Requested {options.platform}, got {qt.platformName()}")
    started = time.monotonic()
    c = Controller(qt, demo=True, hidden=True, adapter=DesktopAdapter(),
                   enable_native=False, seed=options.seed, visit_limit=2)
    # Test-only demo configuration isolates coexistence from random fights or
    # startling by the user's normal mouse use. No model clock is advanced here.
    c.settings["calm_mode"] = c.engine.calm_mode = True
    for cat in c.engine.cats.values():
        cat.trust = 60
    cocoa = Cocoa() if options.platform == "cocoa" else None
    report = {
        "state": "running", "pid": os.getpid(), "started_at": utc_now(),
        "platform_plugin": qt.platformName(), "qt_version": QT_VERSION_STR,
        "pyqt_version": PYQT_VERSION_STR, "source_sha256": source_hashes(),
        "state_isolation": "Controller demo owns a disposable temporary directory; no formal progress is loaded or saved.",
        "native_adapter_enabled": False, "logical_acceleration": False,
        "model_dt_injected": False, "phase": "bootstrap", "events": [],
        "resource_checkpoints": [], "mouse_rounds": [], "journal_cycles_completed": 0,
        "interaction_rounds": [], "journal_focus_samples": [],
        "multi_cat": {"peak_visitors": 0, "peak_visible": 0, "simultaneous_observed_seconds": 0.0},
        "profile_staging": "Isolated demo starts at trust 60 and calm mode; first mouse demo raises its giver to 80. No formal progress changes.",
        "scratch_staging": "Direct visual-effect invocation; no claim that a natural belly-touch reaction was exercised.",
        "fullscreen_canvas_seen": False, "global_cursor_override_seen": False,
        "heartbeat_count": 0, "max_event_loop_gap_seconds": 0.0,
        "object_lifetimes": {}, "shutdown_verified": False,
    }
    watches = []
    baseline = None
    phase_started = started
    last_heartbeat = started
    last_write = 0.0
    journal_batch_count = 0
    mouse_row = None
    interaction_row = None
    previous_two_visible = False
    paused_snapshot = None
    running = True
    timer = QTimer()
    timer.setInterval(100)

    def event(name, **details):
        record = {"event": name, "elapsed_real_seconds": round(time.monotonic() - started, 3), **details}
        report["events"].append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    def phase(name):
        nonlocal phase_started
        report["phase"] = name
        phase_started = time.monotonic()
        event(name)

    def open_journal():
        journal = c.journal
        show_without_activating(journal)
        watch(journal, "journal")

    def watch(widget, kind):
        if any(row["ref"]() is widget for row in watches):
            return
        token = f"{kind}-{len(watches) + 1}"
        row = {"ref": weakref.ref(widget), "kind": kind, "token": token, "destroyed": False}
        watches.append(row)
        widget.destroyed.connect(lambda _obj=None, r=row: r.update(destroyed=True))

    def lifetimes():
        result = {}
        for row in watches:
            widget = row["ref"]()
            result[row["token"]] = {
                "kind": row["kind"], "destroyed_signal": row["destroyed"],
                "python_wrapper_collected": widget is None,
                "cpp_object_deleted": widget is None or sip.isdeleted(widget),
            }
        return result

    def checkpoint(force=False):
        nonlocal last_write
        now = time.monotonic()
        if force or now - last_write >= 1:
            report["heartbeat_monotonic"] = now
            report["elapsed_real_seconds"] = round(now - started, 3)
            report["object_lifetimes"] = lifetimes()
            report["live"] = {
                "cats": len(c.engine.visitors), "gifts": len(c.engine.gifts),
                "journal": c._journal is not None, "fps": c.current_fps,
                "paused": c.paused, "gate_open": c.engine.gate_open,
                "workers": len(c._workers), "pool_threads": c.pool.activeThreadCount(),
            }
            write_json(options.checkpoint, report)
            last_write = now

    def resources(label):
        nonlocal baseline
        # Keep only primitive counts: never retain QWidget wrappers in a report.
        widgets = Counter(type(w).__name__ for w in QApplication.allWidgets())
        top = Counter(type(w).__name__ for w in QApplication.topLevelWidgets())
        current = dict(sorted(widgets.items()))
        row = {"label": label, "elapsed_real_seconds": round(time.monotonic() - started, 3),
               "widgets_by_class": current, "top_level_by_class": dict(sorted(top.items())),
               "controller_qtimers": len(c.findChildren(QTimer)),
               "controller_active_qtimers": sum(t.isActive() for t in c.findChildren(QTimer))}
        report["resource_checkpoints"].append(row)
        assert c._journal is None and not c.cat_windows and not c.gift_windows
        assert not c._scratches and c._teaser_session is None and c.engine.active_teaser_id is None
        assert not c.engine.visitors and not c.engine.gifts
        assert c.current_fps == 0 and not c.timer.isActive()
        assert not c.save_timer.isActive(), "Save timer did not settle before quiescent snapshot"
        assert not c._workers and c.pool.activeThreadCount() == 0
        assert all(v["cpp_object_deleted"] and v["destroyed_signal"] for v in lifetimes().values()), "A released transient Qt object is still live"
        assert all(current.get(kind, 0) == 0 for kind in ("Journal", "CatWindow", "GiftWindow", "ScratchMarks", "_TeaserCanvas"))
        if baseline is None:
            baseline = current
        else:
            growth = resource_growth(baseline, current)
            assert not growth, f"Retained QWidget classes after release: {growth}"
        event("quiescent_resources_checked", label=label, widget_count=sum(current.values()))

    def paused_values():
        return {
            "visitors": [(v.cat_id, v.x, v.y, v.state, v.stay_remaining) for v in c.engine.visitors.values()],
            "gifts": [(g.id, g.x, g.y, g.state, g.wait_remaining) for g in c.engine.gifts],
        }

    def finish(error=None):
        nonlocal running
        if not running:
            return
        running = False
        timer.stop()
        if error is not None:
            report["error"] = {"type": type(error).__name__, "message": str(error),
                               "traceback": traceback.format_exc()}
        c.shutdown()
        stopped = all(not t.isActive() for t in (c.timer, c.save_timer, c.native_timer, c.recycle_timer))
        hidden = all(not w.isVisible() for w in (c.gate, c.bowl, c.bin, *c.cat_windows.values(), *c.gift_windows.values()))
        report["shutdown_verified"] = bool(c._quitting and stopped and hidden and
                                            not c.tray.isVisible() and not c._screen_connections and
                                            not c._workers and c.pool.activeThreadCount() == 0)
        report["coverage_checks"] = coverage_checks(report, options.mouse_rounds)
        report["state"] = "checks_complete" if error is None and report["shutdown_verified"] and all(report["coverage_checks"].values()) else "failed"
        report["source_sha256_after"] = source_hashes()
        if report["source_sha256_after"] != report["source_sha256"]:
            report["state"] = "failed"
            report["source_changed_during_run"] = True
        report["finished_at"] = utc_now()
        checkpoint(True)
        qt.exit(0 if report["state"] == "checks_complete" else 1)

    def advance():
        nonlocal last_heartbeat, journal_batch_count, mouse_row, paused_snapshot, interaction_row, previous_two_visible
        if not running:
            return
        now = time.monotonic()
        report["heartbeat_count"] += 1
        report["max_event_loop_gap_seconds"] = round(max(report["max_event_loop_gap_seconds"], now - last_heartbeat), 6)
        observed_gap = now - last_heartbeat
        last_heartbeat = now
        try:
            assert not c.native_timer.isActive() and not c.recycle_timer.isActive()
            assert not c._workers and c.pool.activeThreadCount() == 0
            report["fullscreen_canvas_seen"] |= c._teaser_session is not None
            report["global_cursor_override_seen"] |= QApplication.overrideCursor() is not None
            assert c._teaser_session is None, "This probe must never install a click-capturing canvas"
            assert QApplication.overrideCursor() is None
            left, top, width, height = c.engine.bounds
            visible = sum(v.state not in {"peeking", "entering", "leaving", "fleeing"}
                          and left <= v.x <= left+width-144 and top <= v.y <= top+height-112
                          for v in c.engine.visitors.values())
            multi = report["multi_cat"]
            multi["peak_visitors"] = max(multi["peak_visitors"], len(c.engine.visitors))
            multi["peak_visible"] = max(multi["peak_visible"], visible)
            if visible >= 2 and previous_two_visible:
                # Conservative observed coverage, not an extrapolation across gaps.
                multi["simultaneous_observed_seconds"] = round(multi["simultaneous_observed_seconds"] + min(observed_gap, .2), 6)
            previous_two_visible = visible >= 2
            for scratch in c._scratches:
                watch(scratch, "scratch")
            for window in c.cat_windows.values():
                watch(window, "cat")
            for window in c.gift_windows.values():
                watch(window, "gift")
            elapsed_phase = now - phase_started
            name = report["phase"]
            if name == "bootstrap" and elapsed_phase >= 1:
                phase("journal_open")
                open_journal()
            elif name == "journal_open" and elapsed_phase >= 0.3:
                assert c._journal is not None and c._journal.isVisible()
                assert c._journal.refresh_timer.isActive()
                focus = {"not_qt_active": QApplication.activeWindow() is not c._journal,
                         "native_not_key": None}
                if cocoa is not None:
                    focus["native_not_key"] = not cocoa.describe(c._journal)["key"]
                report["journal_focus_samples"].append(focus)
                assert focus["not_qt_active"] and (cocoa is None or focus["native_not_key"])
                c._journal.close()  # Exercise the real hide/150ms deferred-release path.
                phase("journal_release")
            elif name == "journal_release" and elapsed_phase >= 0.4:
                assert c._journal is None, "Journal remains owned after its release delay"
                resources(f"journal-{report['journal_cycles_completed'] + 1}")
                report["journal_cycles_completed"] += 1
                journal_batch_count += 1
                if journal_batch_count < options.journal_cycles:
                    open_journal()
                    phase("journal_open")
                elif len(report["mouse_rounds"]) < options.mouse_rounds:
                    mouse_row = {"round": len(report["mouse_rounds"]) + 1,
                                 "started_real_seconds": round(now - started, 3),
                                 "saw_idle": False, "saw_escaping": False, "removed": False,
                                 "active_pause_verified": False, "drained": False}
                    report["mouse_rounds"].append(mouse_row)
                    c.demo_scene("mouse")
                    second = next((cid for cid in c.engine.cats if cid not in c.engine.visitors), None)
                    assert second is not None and c.engine.invite(second) is not None
                    c.changed()
                    phase("mouse_arrival")
                else:
                    phase("idle_soak")
            elif name == "mouse_arrival":
                mice = [g for g in c.engine.gifts if g.kind == "toy_mouse"]
                if mice:
                    mouse = mice[0]
                    assert mouse.state == "idle", "Mouse wait was not observed"
                    mouse_row.update(gift_id=mouse.id, cat_id=mouse.cat_id, saw_idle=True,
                                     initial_observed_wait_seconds=mouse.wait_remaining,
                                     dropped_real_seconds=round(now - started, 3))
                    assert 0 < mouse.wait_remaining <= 22
                    assert c.timer.isActive() and c.current_fps > 0
                    c.set_paused(True)
                    paused_snapshot = paused_values()
                    phase("mouse_paused")
            elif name == "mouse_paused" and elapsed_phase >= 0.7:
                assert c.paused and c.current_fps == 0 and not c.timer.isActive()
                assert paused_values() == paused_snapshot, "Model advanced while controller was paused"
                c.set_paused(False)
                assert c.timer.isActive() and c.current_fps > 0
                mouse_row["active_pause_verified"] = True
                mouse_row["paused_real_seconds"] = round(elapsed_phase, 3)
                # Model-only play tests transient ownership without capturing
                # desktop input. Scratch is a direct, explicitly staged effect.
                cid = mouse_row["cat_id"]
                interaction_row = {"round": mouse_row["round"], "model_started": c.engine.start_teaser(cid),
                                   "model_stopped": False, "scratch_released": False,
                                   "native_ignores_mouse_events": None}
                report["interaction_rounds"].append(interaction_row)
                assert interaction_row["model_started"], "Mouse giver was not eligible for short model play"
                c.changed()
                c._show_scratch(cid)
                assert c._scratches
                scratch = c._scratches[-1]
                watch(scratch, "scratch")
                interaction_row["qt_click_through"] = bool(scratch.windowFlags() & Qt.WindowType.WindowTransparentForInput) and scratch.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                if cocoa is not None:
                    interaction_row["native_ignores_mouse_events"] = native_ignores_mouse_events(cocoa, scratch)
                    assert interaction_row["native_ignores_mouse_events"], "Native ScratchMarks unexpectedly receives mouse events"
                assert interaction_row["qt_click_through"]
                phase("short_model_play")
            elif name == "short_model_play":
                if elapsed_phase >= 1.2 and not interaction_row["model_stopped"]:
                    c.engine.stop_teaser(mouse_row["cat_id"])
                    c.changed()
                    interaction_row["model_stopped"] = c.engine.active_teaser_id is None
                    assert interaction_row["model_stopped"]
                if elapsed_phase >= 1.6:
                    assert not c._scratches, "Scratch effect did not release after its deadline"
                    interaction_row["scratch_released"] = True
                    interaction_row["real_seconds"] = round(elapsed_phase, 3)
                    phase("mouse_wait_escape")
            elif name == "mouse_wait_escape":
                mouse = next((g for g in c.engine.gifts if g.id == mouse_row["gift_id"]), None)
                if mouse is not None and mouse.state == "escaping" and not mouse_row["saw_escaping"]:
                    mouse_row["saw_escaping"] = True
                    mouse_row["escaping_real_seconds"] = round(now - started, 3)
                    event("mouse_escape_observed", round=mouse_row["round"])
                if mouse is None:
                    assert mouse_row["saw_escaping"], "Mouse disappeared without an observed escape"
                    mouse_row["removed"] = True
                    mouse_row["removed_real_seconds"] = round(now - started, 3)
                    c.close_gate()
                    assert not c.engine.gate_open
                    phase("gate_drain")
            elif name == "gate_drain" and not c.engine.visitors:
                # Any incidental persistent gifts belong solely to this demo.
                # Collect them through the model, never touch desktop files.
                incidental = [gift.id for gift in c.engine.gifts]
                for gift_id in incidental:
                    c.engine.collect_gift(gift_id)
                if incidental:
                    mouse_row["incidental_virtual_gifts_collected"] = len(incidental)
                    c.changed()
                phase("drain_release")
            elif name == "drain_release" and elapsed_phase >= 1.1:
                resources(f"round-{mouse_row['round']}-drained")
                mouse_row["drained"] = True
                mouse_row["finished_real_seconds"] = round(now - started, 3)
                journal_batch_count = 0
                open_journal()
                phase("journal_open")
            elif name == "idle_soak":
                if now - started >= options.minimum_seconds:
                    resources("final")
                    checks = coverage_checks(report, options.mouse_rounds)
                    assert all(checks.values()), f"Coverage incomplete: {[k for k,v in checks.items() if not v]}"
                    event("all_requested_checks_finished")
                    finish()
                elif elapsed_phase >= 10:
                    # Continue exercising native allocation/release during the
                    # real soak, without repeatedly activating an app window.
                    journal_batch_count = 0
                    open_journal()
                    phase("journal_open")
            checkpoint()
        except Exception as error:
            finish(error)

    def qt_exception(kind, value, tb):
        # PyQt slot exceptions must become report failures rather than an unmarked abort.
        traceback.print_exception(kind, value, tb)
        finish(value)

    sys.excepthook = qt_exception
    timer.timeout.connect(advance)
    timer.start()
    checkpoint(True)
    code = qt.exec()
    # An unexpected application quit cannot turn an unfinished checkpoint into success.
    if running:
        finish(RuntimeError("Qt event loop exited before the requested checks finished"))
        code = 1
    report["qt_exec_exit_code"] = code
    checkpoint(True)
    return code


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "v08" / "native-lifecycle.json")
    parser.add_argument("--platform", choices=("cocoa", "offscreen"), default="cocoa")
    parser.add_argument("--journal-cycles", type=int, default=6, help="Show/close cycles before and after each mouse round.")
    parser.add_argument("--mouse-rounds", type=int, default=2, help="Natural arrival, 8–22s wait, escape, and gate-drain rounds.")
    parser.add_argument("--minimum-seconds", type=float, default=195, help="Minimum actual event-loop runtime; no logical time acceleration.")
    parser.add_argument("--timeout", type=float, default=240, help="Supervisor deadline; only its own child may be terminated.")
    parser.add_argument("--seed", type=int, default=71)
    parser.add_argument("--quick-check", action="store_true", help="Offscreen script smoke only: two journal cycles, no mice, no endurance claim.")
    parser.add_argument("--supervisor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--checkpoint", type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if options.quick_check:
        options.platform, options.journal_cycles, options.mouse_rounds = "offscreen", 2, 0
        options.minimum_seconds, options.timeout = 0, 20
    if options.platform == "cocoa" and sys.platform != "darwin":
        parser.error("Cocoa requires macOS; offscreen results are explicitly simulations.")
    if options.journal_cycles < 2 or options.mouse_rounds < 0:
        parser.error("Need at least two journal cycles; mouse rounds cannot be negative.")
    if any(not math.isfinite(value) or value < 0 for value in (options.minimum_seconds, options.timeout)) or options.timeout <= options.minimum_seconds:
        parser.error("Require finite 0 <= minimum-seconds < timeout.")
    if options.child and options.checkpoint is None:
        parser.error("Internal child requires its supervisor's checkpoint path.")
    options.output = options.output.resolve()
    return options


def main(argv=None):
    options = parse_args(argv)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    if options.child:
        return run_child(options)
    if options.supervisor:
        return supervise(options)
    command = [sys.executable, "-B", "-u", str(Path(__file__).resolve()),
               "--supervisor", *command_options(options)]
    log = options.output.with_suffix(".log")
    with log.open("a", encoding="utf-8") as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    metadata = {"state": "submitted", "supervisor_pid": process.pid, "submitted_at": utc_now(),
                "report": str(options.output), "log": str(log),
                "status_note": "Read report state/heartbeat and final child exit code; lock-file existence is not liveness."}
    write_json(options.output.with_suffix(".job.json"), metadata)
    print(json.dumps(metadata, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
