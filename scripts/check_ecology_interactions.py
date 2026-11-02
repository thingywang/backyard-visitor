"""Isolated, real-time Cocoa meal-cooldown and gift-response acceptance.

This prepares its own source Controller, never the user's running app. Default
invocation detaches a supervisor; its deadline includes a six-second shutdown
reserve. Only the owned child can be terminated. No injected model dt, cooldown
writes, global cursor operations, OS permissions, or formal progress are used.
"""
from __future__ import annotations

import argparse
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_native_lifecycle import final_state, read_json, watchdog_reason, write_json


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def source_hashes():
    paths = sorted((ROOT / "backyard_visitor").glob("*.py")) + [Path(__file__).resolve(),
        ROOT / "scripts/check_native_lifecycle.py", ROOT / "scripts/probe_overlay_visibility.py"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def meal_started_model_time(model_time, cooldown, duration=180.0):
    """Infer the debit's model instant from the observed newly reset countdown."""
    if not all(math.isfinite(v) for v in (model_time, cooldown, duration)) or not 0 < cooldown <= duration:
        raise ValueError("A successful meal must expose its positive, newly reset cooldown")
    return model_time - (duration-cooldown)


def coverage_checks(report):
    meals = report.get("meals", [])
    ordinary, delayed, cancelled = (report.get("gifts", {}).get(k, {}) for k in ("ordinary", "jump", "cancel"))
    def photographed(row):
        frames = row.get("screenshots", {})
        return all(frames.get(k, {}).get("sha256") and frames[k].get("path") for k in ("before", "during", "after"))
    def response(row):
        return (row.get("collected") is True and row.get("saw_purring") is True
                and row.get("initial_remaining", 0) >= 2.8 and row.get("ended") is True
                and row.get("response_model_seconds", 0) >= 2.85 and photographed(row))
    return {
        "two_real_food_debits": len(meals) == 2 and all(m.get("food_after_debit") == 0 for m in meals),
        "both_meals_reached_grooming": len(meals) == 2 and all(m.get("saw_eating") and m.get("saw_grooming") for m in meals),
        "second_meal_reached_bowl_naturally": len(meals) == 2 and meals[1].get("mouth_at_bowl") is True and bool(report.get("second_meal_route_samples")),
        "effective_cooldown_at_least_180_seconds": len(meals) == 2 and meals[1]["started_model_seconds"]-meals[0]["started_model_seconds"] >= 180-1e-6,
        "real_wait_not_accelerated": len(meals) == 2 and meals[1]["observed_real_seconds"]-meals[0]["observed_real_seconds"] >= 179.5 and report.get("model_dt_injected") is False,
        "second_portion_preserved_during_cooldown": report.get("cooldown_samples", 0) >= 100 and not report.get("cooldown_violations", []),
        "ordinary_gift_response": response(ordinary),
        "airborne_gift_deferred_until_supported": response(delayed) and delayed.get("collected_airborne") is True and delayed.get("pending_in_air") is True and delayed.get("responded_after_air") is True,
        "emergency_cancelled_pending_response": cancelled.get("pending_before_carry") is True and cancelled.get("cleared_by_carry") is True and cancelled.get("no_replay_after_release") is True,
        "no_global_cursor_override_or_fullscreen_capture": report.get("global_cursor_override_seen") is False and report.get("fullscreen_canvas_seen") is False,
        "journal_never_created": report.get("journal_seen") is False,
        "captured_cat_window_not_key": report.get("platform_plugin") != "cocoa" or report.get("native_key_seen") is False,
        "shutdown_clean": report.get("shutdown_verified") is True,
    }


def command_options(options):
    return ["--output", str(options.output), "--platform", options.platform,
            "--timeout", str(options.timeout), "--seed", str(options.seed)]


def supervise(options):
    import fcntl
    with options.output.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another supervisor owns this output; no child started.", flush=True)
            return 2
        report = {"schema_version": 1, "state": "running", "started_at": utc_now(),
            "supervisor_pid": os.getpid(), "child_pid": None, "child": None,
            "scope": "source_cocoa_real_time_interactions" if options.platform == "cocoa" else "offscreen_real_time_simulation",
            "source_sha256": source_hashes(), "hard_deadline_seconds": options.timeout,
            "exit_code": None, "termination_reason": None,
            "limitations": ["Source Controller, not the frozen application or overnight stability.",
                "Trust/adoption, virtual gifts, and synthetic window routes are explicit fixture staging.",
                "Screenshots are the owned native CatWindow's QWidget.grab rendering, not full desktop capture.",
                "No mouse movement, fullscreen input canvas, native desktop adapter, or memory benchmark."]}
        started, process = time.monotonic(), None
        write_json(options.output, report)
        try:
            with tempfile.TemporaryDirectory(prefix="backyard-ecology-probe-") as temporary:
                checkpoint = Path(temporary)/"child.json"
                environment = {**os.environ, "QT_QPA_PLATFORM": options.platform, "PYTHONDONTWRITEBYTECODE": "1"}
                process = subprocess.Popen([sys.executable, "-B", "-u", str(Path(__file__).resolve()),
                    "--child", "--checkpoint", str(checkpoint), *command_options(options)],
                    cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=subprocess.STDOUT)
                report["child_pid"] = process.pid
                while process.poll() is None:
                    now = time.monotonic()
                    child = read_json(checkpoint)
                    if child:
                        report["child"] = child
                    heartbeat = child.get("heartbeat_monotonic") if child else None
                    age = now-heartbeat if isinstance(heartbeat, (int, float)) else None
                    reason = watchdog_reason(now-started, age, options.timeout-6)
                    report.update(checked_at=utc_now(), elapsed_real_seconds=round(now-started, 3),
                                  heartbeat_age_seconds=round(age, 3) if age is not None else None)
                    write_json(options.output, report)
                    if reason:
                        report["termination_reason"] = reason
                        break
                    time.sleep(.25)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=3)
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
                    process.kill(); process.wait(timeout=3)
                report["exit_code"] = process.returncode
            report["source_sha256_after"] = source_hashes()
            report["sources_unchanged"] = report["source_sha256"] == report["source_sha256_after"]
            report["state"] = final_state(report["child"], report["exit_code"], report["termination_reason"], report["sources_unchanged"])
            report.update(finished_at=utc_now(), elapsed_real_seconds=round(time.monotonic()-started, 3))
            write_json(options.output, report)
        print(json.dumps({"state": report["state"], "exit_code": report["exit_code"], "output": str(options.output)}), flush=True)
        return 0 if report["state"] == "complete" else 1


def run_child(options):
    os.environ["QT_QPA_PLATFORM"] = options.platform
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter
    from backyard_visitor.model import MEAL_COOLDOWN_SECONDS, EATING_MUZZLE_LEFT, EATING_MUZZLE_RIGHT
    from scripts.probe_overlay_visibility import Cocoa
    if MEAL_COOLDOWN_SECONDS != 180:
        raise RuntimeError("Acceptance contract requires a 180-second production cooldown")
    qt = QApplication(["后院访客 · 独立生态验收"])
    qt.setQuitOnLastWindowClosed(False)
    if qt.platformName() != options.platform:
        raise RuntimeError("Requested Qt platform was not selected")
    c = Controller(qt, demo=True, hidden=True, adapter=DesktopAdapter(), enable_native=False,
                   seed=options.seed, visit_limit=1)
    c.engine.calm_mode = c.settings["calm_mode"] = True
    cocoa = Cocoa() if options.platform == "cocoa" else None
    started, last_observer, last_write = time.monotonic(), time.monotonic(), 0.0
    screenshots = options.output.parent/(options.output.stem+"-screenshots")
    screenshots.mkdir(parents=True, exist_ok=True)
    report = {"state": "running", "pid": os.getpid(), "started_at": utc_now(),
        "source_sha256": source_hashes(), "platform_plugin": qt.platformName(),
        "phase": "arrival", "model_dt_injected": False, "logical_acceleration": False,
        "fixture": "Natural amber invite; trust raised to 100 and public adoption for residence. Calm mode and one-cat cap. Gifts staged through demo_scene; synthetic edges via _move_to_window. Carry/release is scripted only on this model, never physical pointer input.",
        "meals": [], "second_meal_route_samples": [], "cooldown_samples": 0, "cooldown_violations": [], "cooldown_trace": [],
        "gifts": {key: {"screenshots": {}} for key in ("ordinary", "jump", "cancel")},
        "events": [], "max_event_loop_gap_seconds": 0.0, "heartbeat_count": 0,
        "global_cursor_override_seen": False, "fullscreen_canvas_seen": False,
        "journal_seen": False, "native_key_seen": False, "shutdown_verified": False}
    cid, stage, gift_id, phase_at = "amber", "first_meal", None, started
    running, fed_again = True, False
    timer = QTimer(); timer.setInterval(50)

    def event(name, **details):
        value = {"event": name, "elapsed_real_seconds": round(time.monotonic()-started, 4),
                 "model_seconds": c.engine._time, **details}
        report["events"].append(value)
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def phase(name):
        nonlocal stage, phase_at
        stage, phase_at = name, time.monotonic()
        report["phase"] = name
        event(name)

    def snapshot():
        v = c.engine.visitors.get(cid)
        return {"model_seconds": c.engine._time, "food": c.engine.food,
                "cooldown": c.engine.cats[cid].meal_cooldown_remaining, "can_eat": c.engine.can_eat(cid),
                "state": v.state if v else None, "x": v.x if v else None, "y": v.y if v else None,
                "gift_remaining": v.gift_response_remaining if v else None,
                "gift_pending": v.gift_response_pending if v else None,
                "landing_remaining": v.landing_remaining if v else None}

    def checkpoint(force=False):
        nonlocal last_write
        now = time.monotonic()
        if force or now-last_write >= 1:
            report.update(heartbeat_monotonic=now, elapsed_real_seconds=round(now-started, 4), live=snapshot())
            report["cooldown_trace"].append({"elapsed_real_seconds": report["elapsed_real_seconds"], **report["live"]})
            write_json(options.checkpoint, report)
            last_write = now

    def capture(case, label):
        window = c.cat_windows.get(cid)
        if window is None or not window.isVisible():
            raise RuntimeError("Owned CatWindow missing at capture")
        if cocoa and cocoa.describe(window)["key"]:
            report["native_key_seen"] = True
            raise RuntimeError("Test cat window became key")
        path = screenshots/f"{case}-{label}.png"
        pixmap = window.grab()
        if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
            raise RuntimeError("Owned CatWindow capture failed")
        report["gifts"][case]["screenshots"][label] = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "elapsed_real_seconds": round(time.monotonic()-started, 4), "captured_at": utc_now(),
            "pixel_width": pixmap.width(), "pixel_height": pixmap.height(),
            "kind": "owned_native_QWidget_grab_not_desktop_screenshot", **snapshot()}

    def stage_gift():
        before = {g.id for g in c.engine.gifts}
        if not c.engine.demo_scene("gift"):
            raise RuntimeError("Could not stage an isolated gift")
        c.changed()
        new = [g.id for g in c.engine.gifts if g.id not in before and g.cat_id == cid]
        if len(new) != 1:
            raise RuntimeError("Idle demo gift did not appear")
        return new[0]

    def stage_jump(v):
        left, top, width, _ = c.engine.bounds
        edge_top = max(top+140, c.engine.floor_y+112-90)
        c.engine.set_window_edges([("probe-only-edge", left, left+width, edge_top)])
        if not c.engine._move_to_window(v, "probe-only-edge", v.x):
            raise RuntimeError("Could not stage a synthetic jump from real current position")
        c.changed()

    def collect(case, v):
        row = report["gifts"][case]
        row["collected_airborne"] = v.state == "jumping" and v.takeoff_remaining <= 0 and v.jump_elapsed > 0
        capture(case, "before")
        row["collected"] = c.engine.collect_gift(gift_id)
        if not row["collected"]:
            raise RuntimeError("Expected virtual gift was not collected")
        c.changed()
        row["collected_model_seconds"] = c.engine._time
        row["pending_in_air"] = bool(v.gift_response_pending and v.gift_response_remaining == 0 and v.state == "jumping")
        if case != "ordinary" and not row["pending_in_air"]:
            raise RuntimeError("Airborne collection did not remain pending")
        if v.gift_response_remaining > 0:
            begin_response(case, v)

    def begin_response(case, v):
        row = report["gifts"][case]
        row.update(saw_purring=v.state == "purring", initial_remaining=v.gift_response_remaining,
                   response_model_start=c.engine._time-(3-v.gift_response_remaining),
                   responded_after_air=v.state != "jumping", onset_landing_remaining=v.landing_remaining)
        if v.state != "purring":
            raise RuntimeError("Gift response timer exists outside purring")
        event("gift_response_started", case=case)

    def finish(error=None):
        nonlocal running
        if not running:
            return
        running = False
        timer.stop()
        if error:
            report["error"] = {"type": type(error).__name__, "message": str(error)}
        try:
            c.shutdown()
            report["shutdown_verified"] = (c._quitting and c._journal is None and c._teaser_session is None
                and not c._workers and not c._screen_connections
                and not any(t.isActive() for t in (c.timer, c.native_timer, c.recycle_timer, c.save_timer))
                and not any(w.isVisible() for w in (*c.cat_windows.values(), *c.gift_windows.values(), c.gate, c.bowl, c.bin)))
        except Exception as shutdown_error:
            report["shutdown_error"] = str(shutdown_error)
        report["coverage_checks"] = coverage_checks(report)
        report["source_sha256_after"] = source_hashes()
        ok = error is None and all(report["coverage_checks"].values()) and report["source_sha256"] == report["source_sha256_after"]
        report["state"] = "checks_complete" if ok else "failed"
        checkpoint(True)
        qt.exit(0 if ok else 1)

    def advance():
        nonlocal last_observer, fed_again, gift_id
        if not running:
            return
        try:
            now = time.monotonic()
            report["max_event_loop_gap_seconds"] = max(report["max_event_loop_gap_seconds"], now-last_observer)
            last_observer = now
            report["heartbeat_count"] += 1
            report["global_cursor_override_seen"] |= qt.overrideCursor() is not None
            report["fullscreen_canvas_seen"] |= c._teaser_session is not None
            report["journal_seen"] |= c._journal is not None
            if report["global_cursor_override_seen"] or report["fullscreen_canvas_seen"] or report["journal_seen"]:
                raise RuntimeError("Unexpected UI interception or journal creation")
            v = c.engine.visitors.get(cid)
            if v is None:
                if stage == "drain":
                    if not c.cat_windows and not c.gift_windows and now-phase_at > 1:
                        finish()
                        return
                else:
                    raise RuntimeError("Adopted fixture visitor unexpectedly departed")
            else:
                profile = c.engine.cats[cid]
                if len(report["meals"]) < 2 and c.engine.food == 0 and v.state == "eating":
                    if not report["meals"] or fed_again:
                        meal = {"food_after_debit": 0, "saw_eating": True, "saw_grooming": False,
                            "observed_real_seconds": now-started, "observed_model_seconds": c.engine._time,
                            "started_model_seconds": meal_started_model_time(c.engine._time, profile.meal_cooldown_remaining),
                            "initial_observed_cooldown": profile.meal_cooldown_remaining,
                            "bowl": list(c.engine.bowl), "cat_position": [v.x, v.y],
                            "mouth_at_bowl": (abs(v.x+(EATING_MUZZLE_RIGHT if v.facing>0 else EATING_MUZZLE_LEFT)-c.engine.bowl[0]) <= 1
                                              and abs(v.y-(c.engine.bowl[1]-112+15)) <= 1)}
                        report["meals"].append(meal)
                        event("food_debited", meal=len(report["meals"]))
                        if len(report["meals"]) == 1:
                            c.feed(); fed_again = True
                            if c.engine.food != 1:
                                raise RuntimeError("Second portion was not added")
                if report["meals"] and v.state == "grooming":
                    report["meals"][-1]["saw_grooming"] = True
                if (len(report["meals"]) == 1 and c.engine.can_eat(cid)
                        and v.arrival_state == "eating" and v.state in {"walking", "jumping"}):
                    report["second_meal_route_samples"].append({"elapsed_real_seconds": now-started, **snapshot()})
                if len(report["meals"]) == 1 and profile.meal_cooldown_remaining > 1e-8:
                    report["cooldown_samples"] += 1
                    if c.engine.food != 1 or c.engine.can_eat(cid):
                        report["cooldown_violations"].append(snapshot())
                        raise RuntimeError("Second portion consumed/eligible during cooldown")
                if stage == "first_meal" and report["meals"] and report["meals"][0]["saw_grooming"] and v.state == "idle":
                    gift_id = stage_gift(); collect("ordinary", v); phase("ordinary_response")
                elif stage in {"ordinary_response", "jump_response"}:
                    case = "ordinary" if stage == "ordinary_response" else "jump"
                    row = report["gifts"][case]
                    if v.gift_response_remaining > 0 and "response_model_start" not in row:
                        begin_response(case, v)
                    if v.gift_response_remaining > 0 and v.state == "jumping":
                        raise RuntimeError("Gift response interrupted a jump")
                    if .5 < v.gift_response_remaining <= 1.8 and "during" not in row["screenshots"]:
                        capture(case, "during")
                    if "response_model_start" in row and v.gift_response_remaining <= 0:
                        row.update(ended=True, response_model_seconds=c.engine._time-row["response_model_start"])
                        capture(case, "after")
                        if case == "ordinary":
                            phase("jump_ready")
                        else:
                            c.engine.set_window_edges([]); c.changed(); phase("cancel_ready")
                elif stage in {"jump_ready", "cancel_ready"} and v.state == "idle" and abs(v.y-c.engine.floor_y) < 1:
                    gift_id = stage_gift(); stage_jump(v)
                    phase("jump_air" if stage == "jump_ready" else "cancel_air")
                elif stage in {"jump_air", "cancel_air"} and v.state == "jumping" and v.takeoff_remaining <= 0 and v.jump_elapsed > 0:
                    case = "jump" if stage == "jump_air" else "cancel"
                    collect(case, v)
                    if case == "jump":
                        phase("jump_response")
                    else:
                        row = report["gifts"]["cancel"]
                        row["pending_before_carry"] = v.gift_response_pending
                        if not c.engine.carry(cid):
                            raise RuntimeError("Scripted carry did not cancel the pending reaction")
                        row["cleared_by_carry"] = not v.gift_response_pending and v.gift_response_remaining == 0
                        c.changed(); phase("cancel_carried")
                elif stage == "cancel_carried" and now-phase_at >= .8:
                    if v.gift_response_pending or v.gift_response_remaining:
                        raise RuntimeError("Cancelled response persisted while carried")
                    c.engine.set_window_edges([])
                    if not c.engine.release(cid, v.x, c.engine.floor_y):
                        raise RuntimeError("Could not release the scripted carry")
                    c.changed(); phase("cancel_released")
                elif stage == "cancel_released":
                    if v.gift_response_pending or v.gift_response_remaining:
                        raise RuntimeError("Cancelled response replayed after release")
                    if now-phase_at >= 4:
                        report["gifts"]["cancel"]["no_replay_after_release"] = True
                        phase("wait_second_meal")
                elif stage == "wait_second_meal" and len(report["meals"]) == 2 and report["meals"][1]["saw_grooming"] and v.state == "idle":
                    for gift in list(c.engine.gifts):
                        c.engine.discard_gift(gift.id)
                    c.close_gate(); phase("drain")
            checkpoint()
        except Exception as error:
            traceback.print_exc()
            finish(error)

    def qt_exception(kind, value, tb):
        traceback.print_exception(kind, value, tb)
        finish(value)
    sys.excepthook = qt_exception
    c.engine.open_gate()
    if c.engine.invite(cid) != cid:
        raise RuntimeError("Natural invite failed")
    c.engine.cats[cid].trust = 100
    if not c.engine.adopt(cid):
        raise RuntimeError("Explicit residence fixture was not accepted")
    c.feed()
    event("fixture_invited_adopted_and_fed")
    timer.timeout.connect(advance); timer.start()
    checkpoint(True)
    result = qt.exec()
    if running:
        finish(RuntimeError("Qt exited before all acceptance checks completed"))
        result = 1
    report["qt_exec_exit_code"] = result
    checkpoint(True)
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"output"/"v09"/"ecology-interactions.json")
    parser.add_argument("--platform", choices=("cocoa", "offscreen"), default="cocoa")
    parser.add_argument("--timeout", type=float, default=330, help="Hard deadline including six seconds for own-child shutdown; no accelerated mode.")
    parser.add_argument("--seed", type=int, default=71)
    parser.add_argument("--supervisor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--checkpoint", type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if not math.isfinite(options.timeout) or not 210 <= options.timeout <= 330:
        parser.error("Require 210 <= timeout <= 330 real seconds; no time acceleration is supported.")
    if options.platform == "cocoa" and sys.platform != "darwin":
        parser.error("Cocoa requires macOS")
    if options.child and options.checkpoint is None:
        parser.error("Internal child requires its own supervisor checkpoint")
    options.output = options.output.resolve()
    return options


def main(argv=None):
    options = parse_args(argv)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    if options.child:
        return run_child(options)
    if options.supervisor:
        return supervise(options)
    log = options.output.with_suffix(".log")
    with log.open("a") as stream:
        process = subprocess.Popen([sys.executable, "-B", "-u", str(Path(__file__).resolve()),
            "--supervisor", *command_options(options)], cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    metadata = {"state": "submitted", "supervisor_pid": process.pid, "submitted_at": utc_now(),
                "report": str(options.output), "log": str(log),
                "status_note": "Read Popen exit status and fresh heartbeat; a PID/lock/checkpoint alone is not liveness."}
    write_json(options.output.with_suffix(".job.json"), metadata)
    print(json.dumps(metadata), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
