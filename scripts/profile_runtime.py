"""Bounded native Qt probe; isolated progress and no desktop permissions.

RSS, USS and macOS physical footprint are different metrics. See Apple's
https://developer.apple.com/videos/play/wwdc2022/10106/ and the local macOS SDK
sys/resource.h + libproc.h for proc_pid_rusage(RUSAGE_INFO_V2).
Importing this file does not start Qt, so memory_snapshot can also inspect an
already-running frozen application from a separate observer.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil


class _RusageInfoV2(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups",
            "ri_interrupt_wkups", "ri_pageins", "ri_wired_size",
            "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime",
            "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time",
            "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups",
            "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
            "ri_diskio_byteswritten",
        )
    ]


_LIBPROC = None


def memory_snapshot(process: psutil.Process | None = None) -> dict:
    """Byte counts; unavailable counters stay None, never become zero."""
    global _LIBPROC
    process = process or psutil.Process()
    started = time.perf_counter()
    row = {"rss_bytes": process.memory_info().rss, "uss_bytes": None,
           "phys_footprint_bytes": None, "native_rss_bytes": None}
    try:
        row["uss_bytes"] = getattr(process.memory_full_info(), "uss", None)
    except (psutil.Error, AttributeError, OSError):
        pass
    if sys.platform == "darwin":
        try:
            if _LIBPROC is None:
                _LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
                _LIBPROC.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
                _LIBPROC.proc_pid_rusage.restype = ctypes.c_int
            usage = _RusageInfoV2()
            if _LIBPROC.proc_pid_rusage(process.pid, 2, ctypes.byref(usage)) == 0:
                row["phys_footprint_bytes"] = usage.ri_phys_footprint
                row["native_rss_bytes"] = usage.ri_resident_size
        except (OSError, AttributeError):
            pass
    row["memory_probe_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return row


def summarize(rows: list[dict]) -> dict:
    result = {}
    for name in ("closed", "walking", "resting", "journal", "journal_hidden"):
        values = [r for r in rows if r["stage"] == name]
        if not values:
            continue
        settled = [r for r in values if r["stage_seconds"] >= 1.5]
        data = {
            "sample_count": len(values),
            "mean_cpu_percent": round(statistics.mean(r["cpu_percent_one_core"] for r in values), 2),
            "steady_mean_cpu_percent": round(statistics.mean(r["cpu_percent_one_core"] for r in settled), 2) if settled else None,
            "fps": [r["fps"] for r in values],
            "mean_probe_ms": round(statistics.mean(r["memory_probe_ms"] for r in values), 3),
        }
        for key, label in (("rss_bytes", "rss"), ("uss_bytes", "uss"), ("phys_footprint_bytes", "phys_footprint")):
            available = [r[key] for r in values if r[key] is not None]
            data[f"max_{label}_mb"] = round(max(available) / 1e6, 2) if available else None
            steady_values = [r[key] for r in settled if r[key] is not None]
            data[f"steady_max_{label}_mb"] = round(max(steady_values) / 1e6, 2) if steady_values else None
        result[name] = data
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="约 30 秒的原生 Qt 性能探测")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "performance.json")
    options = parser.parse_args()
    from PyQt6.QtCore import QTimer, PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])
    app.setApplicationName("Backyard Visitor Runtime Probe")
    process = psutil.Process()
    baseline = memory_snapshot(process)
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter

    session = tempfile.TemporaryDirectory(prefix="backyard-visitor-performance-")
    controller = Controller(app, state_dir=session.name, hidden=True,
                            adapter=DesktopAdapter(), enable_native=False, seed=71)
    initial = memory_snapshot(process)
    rows = []
    stage = "closed"
    begun = time.monotonic()
    stage_begun = begun
    process.cpu_percent()

    def sample():
        row = {"stage": stage, "seconds": round(time.monotonic() - begun, 3),
               "stage_seconds": round(time.monotonic() - stage_begun, 3),
               "cpu_percent_one_core": process.cpu_percent(), "fps": controller.current_fps,
               "visitor_states": [v.state for v in controller.engine.visitors.values()]}
        row.update(memory_snapshot(process))
        rows.append(row)

    def change_stage(name):
        nonlocal stage, stage_begun
        stage, stage_begun = name, time.monotonic()

    def walk():
        change_stage("walking")
        controller.engine.open_gate()
        controller.engine.cats["amber"].trust = 100
        controller.engine.invite("amber")
        controller.changed()

    def rest():
        change_stage("resting")
        visitor = controller.engine.visitors.get("amber")
        if visitor is not None:
            visitor.state = "idle"
            visitor.state_age = 0
            visitor.next_action = 1000
            visitor.target_x = visitor.x
            visitor.target_y = visitor.y
        controller.engine._visit_countdown = 1000
        controller.changed()

    def journal():
        change_stage("journal")
        controller.show_journal()

    def hide_journal():
        change_stage("journal_hidden")
        controller.journal.hide()

    def finish():
        timer.stop()
        report = {
            "platform": sys.platform, "macos": platform.mac_ver()[0],
            "architecture": platform.machine(), "python": platform.python_version(),
            "pyqt": PYQT_VERSION_STR, "qt": QT_VERSION_STR, "pid": os.getpid(),
            "duration_seconds": round(time.monotonic() - begun, 3),
            "note": "Native macOS, source run, one cat, no native integrations. CPU: one core=100%, includes probe overhead. Decimal MB=1,000,000 bytes. Walking interval includes entry/peeking. steady_mean_cpu_percent excludes the first 1.5s after each stage change. RSS, USS and Apple physical footprint are separate counters; no one replaces the others. This bounded baseline is not a long-duration or frozen-build guarantee.",
            "metric_definitions": {
                "rss": "Resident pages, including resident clean/shared mappings; psutil.memory_info().rss.",
                "uss": "Unique resident process memory from psutil.memory_full_info().uss.",
                "phys_footprint": "macOS charged physical footprint from proc_pid_rusage RUSAGE_INFO_V2; includes dirty/compressed accounting, excludes much reclaimable clean memory.",
            },
            "baseline_qapplication": baseline,
            "initial_controller_hidden": initial,
            "samples": rows, "summary": summarize(rows),
        }
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
        controller.quit()

    timer = QTimer()
    timer.timeout.connect(sample)
    timer.start(1000)
    QTimer.singleShot(5500, walk)
    QTimer.singleShot(12500, rest)
    QTimer.singleShot(18500, journal)
    QTimer.singleShot(23500, hide_journal)
    QTimer.singleShot(29500, finish)
    code = app.exec()
    session.cleanup()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
