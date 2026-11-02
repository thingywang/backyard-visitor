"""Bounded, same-process Cocoa memory-layer diagnostic; never a target benchmark.

The probe itself imports psutil and measurement helpers. Its absolute memory is
therefore diagnostic only. Compare stage deltas, and use profile_bundle.py for
release measurements. No desktop integration or permissions are enabled.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import tempfile
import time
import weakref

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from profile_runtime import memory_snapshot
import psutil

METRICS = ("rss_bytes", "uss_bytes", "phys_footprint_bytes", "native_rss_bytes")


def interpret(report):
    rows = {row["name"]: row for row in report["stages"]}
    first = rows["first_cat_art"]
    relief = rows["diagnostic_allocator_pressure_relief"]["details"]
    return {
        "rss_target_conclusion": "Qt/Python already consume most of the 100MB budget before art. Absolute values include this instrumented source probe; only a frozen-app measurement can certify the target.",
        "qt_application_rss_bytes": rows["qt_application_baseline"]["settled"]["rss_bytes"]["median_bytes"],
        "closed_controller_rss_bytes": rows["hidden_closed_controller"]["settled"]["rss_bytes"]["median_bytes"],
        "first_art_rss_delta_bytes": first["delta_from_previous_bytes"]["rss_bytes"],
        "first_art_retained_pixel_estimate_bytes": first["details"]["estimated_unique_pixel_payload_bytes"],
        "art_release_conclusion": "Weak reference confirms CatArt owner destruction, but RSS does not immediately fall. These counters alone cannot distinguish fragmented allocator pages from framework/native caches.",
        "allocator_relief_returned_bytes": relief["returned_released_bytes"],
        "allocator_relief_immediate_rss_delta_bytes": relief["immediate_delta_bytes"]["rss_bytes"],
        "allocator_relief_recommendation": "Diagnostic only; zero returned bytes and no immediate reduction provide no evidence for adding periodic relief calls to production.",
        "coverage": "No journal, gift artwork, visitor windows, animation, other-app queries or permission workflows were exercised; events are pumped with QApplication.processEvents.",
        "next_experiments": [
            {
                "priority": 1,
                "action": "Pre-render final 288x224 Retina pose frames at asset-build time; load compact frames at runtime without decoding and cropping 1254x1254 source atlases.",
                "evidence": "First Art initialization is the largest app-specific delta, far larger than its retained pixel estimate. Each 1254x1254 RGBA sheet represents about 6.29MB before temporary copies.",
                "validation": "Re-run first_art and actual frozen demo; verify pose anchors, alpha and DPR, and compare peak as well as settled RSS.",
            },
            {
                "priority": 2,
                "action": "Measure deferring native-backend imports and constructing tray/menu/dialog objects only on demand.",
                "evidence": "Controller import and hidden initialization add memory before CatArt or journal exists, so art changes alone cannot improve the closed baseline.",
                "validation": "Compare closed native bundle RSS and menu responsiveness; no promised savings without an A/B measurement.",
            },
            {
                "priority": 3,
                "action": "Optionally compare an 8-entry variant LRU against 16 after compact assets are in place.",
                "evidence": "Eight 288x224 RGBA variants represent at most about 2.06MB of pixel payload; the existing Qt-native gray conversion is already fast in the 84-query stress stage.",
                "validation": "Measure two-cat repaint latency and steady memory; do not trade visible stutter for a small cache reduction.",
            },
            {
                "priority": 4,
                "action": "If the strict all-scene 100MB RSS requirement remains after measured reductions, compare a minimal native UI prototype with the same overlay and art workload.",
                "evidence": "The instrumented QApplication baseline is already close to 100MB. This is evidence of a narrow budget, not proof that every PyQt design is impossible.",
                "validation": "Use an external observer and identical scenarios; count all application processes if the journal is split into another process.",
            },
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Native Qt memory layers, about 10 seconds, isolated state")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "memory-layers.json")
    parser.add_argument("--settle-ms", type=int, default=600)
    options = parser.parse_args(argv)
    if not 100 <= options.settle_ms <= 2000:
        parser.error("--settle-ms must be between 100 and 2000")
    if sys.platform != "darwin":
        parser.error("This diagnostic requires a native macOS Cocoa session")
    # Avoid silently inheriting offscreen settings from a test runner.
    os.environ["QT_QPA_PLATFORM"] = "cocoa"
    process = psutil.Process()
    report = {
        "schema_version": 1,
        "state": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "project_root": str(ROOT),
        "measurement": {
            "purpose": "Same-process layer deltas; the probe's own imports, samples and event pumping add overhead",
            "absolute_values_are_release_benchmark": False,
            "scope": "This source-run main PID only, not WindowServer or other processes",
            "units": "bytes; MB is decimal 1,000,000 bytes",
            "settle_ms": options.settle_ms,
            "settled_samples": 3,
            "sample_spacing_ms": 100,
            "helper": "scripts/profile_runtime.py:memory_snapshot",
            "peak_rss": "macOS getrusage ru_maxrss; cumulative process high-water mark, not a per-stage current value",
        },
        "safety": {
            "formal_progress_used": False,
            "native_integrations_enabled": False,
            "permission_requests": [],
            "desktop_reads": [],
            "note": "Only Qt's own display/cursor metadata; no other applications, icons, wallpaper or Dock are queried",
        },
        "stages": [],
    }
    qt = controller = session = None
    QCoreApplication = QEvent = QPixmapCache = None
    overall_started = time.perf_counter()

    def save_report():
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def pump(milliseconds):
        deadline = time.monotonic() + milliseconds / 1000
        while time.monotonic() < deadline:
            if qt is not None:
                qt.processEvents()
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))

    def measure_stage(name, operation, note=""):
        start = time.perf_counter()
        details = operation() or {}
        operation_ms = (time.perf_counter() - start) * 1000
        immediate = memory_snapshot(process)
        pump(options.settle_ms)
        samples = []
        for index in range(3):
            if index:
                pump(100)
            samples.append(memory_snapshot(process))
        settled = {}
        for key in METRICS:
            values = [sample[key] for sample in samples if sample[key] is not None]
            settled[key] = {
                "median_bytes": statistics.median(values) if values else None,
                "min_bytes": min(values) if values else None,
                "max_bytes": max(values) if values else None,
            }
        previous = report["stages"][-1]["settled"] if report["stages"] else None
        baseline = report["stages"][0]["settled"] if report["stages"] else None
        row = {
            "name": name, "note": note, "operation_ms": round(operation_ms, 3),
            "total_stage_ms": round((time.perf_counter() - start) * 1000, 3),
            "cumulative_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "immediate": immediate, "samples": samples, "settled": settled,
            "delta_from_previous_bytes": {}, "delta_from_probe_baseline_bytes": {},
            "details": details,
        }
        for key in METRICS:
            value = settled[key]["median_bytes"]
            prior = previous[key]["median_bytes"] if previous else None
            initial = baseline[key]["median_bytes"] if baseline else None
            row["delta_from_previous_bytes"][key] = value - prior if value is not None and prior is not None else None
            row["delta_from_probe_baseline_bytes"][key] = value - initial if value is not None and initial is not None else None
        report["stages"].append(row)
        save_report()
        print(json.dumps({"stage": name, "operation_ms": row["operation_ms"],
                          "rss_mb": round(settled["rss_bytes"]["median_bytes"] / 1e6, 3),
                          "footprint_mb": round(settled["phys_footprint_bytes"]["median_bytes"] / 1e6, 3)
                          if settled["phys_footprint_bytes"]["median_bytes"] is not None else None}, ensure_ascii=False), flush=True)

    def art_inventory(art):
        # QPixmap implicit copies may share storage. cacheKey deduplicates those
        # payload estimates; it does not measure native objects or GPU storage.
        pixmaps = [*art.frames, *art.cache.values()]
        if art.tail is not None:
            pixmaps.append(art.tail)
        unique = {pixmap.cacheKey(): pixmap for pixmap in pixmaps}
        return {"frame_count": len(art.frames), "cache_entries": len(art.cache),
                "cache_limit": art.cache_limit,
                "interactions_loaded": art._interactions_loaded,
                "unique_pixmap_count": len(unique),
                "estimated_unique_pixel_payload_bytes": sum(p.width() * p.height() * p.depth() // 8 for p in unique.values()),
                "payload_estimate_note": "Pixel payload only; excludes native objects, row padding, image decode temporaries and window backing stores"}

    try:
        measure_stage("probe_baseline", lambda: {"measurement_modules_preloaded": True},
                      "Python plus this diagnostic, psutil, ctypes and report scaffolding; no Qt imported yet")

        def import_qt():
            nonlocal QCoreApplication, QEvent, QPixmapCache
            from PyQt6.QtCore import QCoreApplication as Core, QEvent as Event, PYQT_VERSION_STR, QT_VERSION_STR
            from PyQt6.QtGui import QPixmapCache as PixmapCache
            from PyQt6.QtWidgets import QApplication
            QCoreApplication, QEvent, QPixmapCache = Core, Event, PixmapCache
            report["pyqt"], report["qt"] = PYQT_VERSION_STR, QT_VERSION_STR
            return {"application_created": False}
        measure_stage("qt_imports", import_qt)

        def create_qt():
            nonlocal qt
            from PyQt6.QtWidgets import QApplication
            qt = QApplication([])
            qt.setApplicationName("Backyard Visitor Memory Layers Probe")
            qt.setQuitOnLastWindowClosed(False)
            assert qt.platformName() == "cocoa", qt.platformName()
            return {"platform_plugin": qt.platformName(), "screen_count": len(qt.screens())}
        measure_stage("qt_application_baseline", create_qt,
                      "Native QApplication without a Controller, journal, sprites or app windows")

        def import_controller():
            from backyard_visitor.app import Controller
            return {"journal_module_loaded": "backyard_visitor.panel" in sys.modules,
                    "controller_class": Controller.__name__}
        measure_stage("controller_import", import_controller)

        def initialize_controller():
            nonlocal controller, session
            from backyard_visitor.app import Controller
            from backyard_visitor.desktop import DesktopAdapter

            class GuardAdapter(DesktopAdapter):
                def forbidden(self, *args, **kwargs):
                    report["safety"]["desktop_reads"].append("Unexpected integration call blocked by probe")
                    raise RuntimeError("Native desktop/permission API is forbidden in this probe")
                windows = frontmost_window = desktop_icons = current_wallpaper = forbidden
                query_recycle_rect = request_icon_access = request_recycle_access = forbidden
                set_wallpaper = restore_wallpaper = forbidden

            session = tempfile.TemporaryDirectory(prefix="backyard-memory-layers-")
            controller = Controller(qt, state_dir=session.name, hidden=True,
                                    adapter=GuardAdapter(), enable_native=False, seed=17)
            assert not controller.engine.gate_open and not controller.engine.visitors
            assert controller._journal is None and controller._art is None
            assert not controller.native_timer.isActive()
            return {"gate_open": False, "journal_created": False, "art_created": False,
                    "fps": controller.current_fps, "state_isolation": "TemporaryDirectory; removed after probe"}
        measure_stage("hidden_closed_controller", initialize_controller,
                      "The normal tiny gate and tray are visible; journal stays unconstructed")

        def first_art():
            return art_inventory(controller.art)
        measure_stage("first_cat_art", first_art,
                      "Initial base/extra sprite atlases decoded to small Retina QPixmaps; no visitor window")

        def exercise_poses(coats):
            art = controller.art
            art.frame("window_peeking", 0, -1, "ginger")
            art.frame("purring", 0, -1, "ginger")
            queries = 0
            key = "__memory_layer_probe_pose__"
            try:
                for coat in coats:
                    for facing in (-1, 1):
                        for index in range(len(art.frames)):
                            # Select every retained pose exactly once per coat
                            # and direction, independent of time-based aliases.
                            art.states[key] = [index]
                            result = art.frame(key, 0, facing, coat)
                            assert result is not None and not result.isNull()
                            del result
                            queries += 1
            finally:
                art.states.pop(key, None)
            assert len(art.cache) <= art.cache_limit
            inventory = art_inventory(art)
            inventory.update({"coats_exercised": coats, "facing_values": [-1, 1], "frame_queries": queries})
            return inventory
        measure_stage("all_poses_base_coat", lambda: exercise_poses(["ginger"]),
                      "Load lazy interaction atlas; exercise all poses in both directions with bounded LRU")
        measure_stage("all_poses_three_coats", lambda: exercise_poses(["ginger", "gray", "tuxedo"]),
                      "Stress all poses/coats/directions without retaining returned frames outside the 16-entry LRU")

        def release_art():
            owner_reference = weakref.ref(controller._art)
            controller._art = None
            collected = gc.collect()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert owner_reference() is None, "CatArt still has a strong reference"
            return {"art_released": controller._art is None, "cat_art_weakref_dead": owner_reference() is None,
                    "gc_collected": collected, "journal_created": controller._journal is not None}
        measure_stage("release_art_and_gc", release_art,
                      "Release CatArt references and collect Python garbage, retaining the closed Controller")

        def clear_qt_cache():
            QPixmapCache.clear()
            return {"qt_pixmap_cache_cleared": True, "gc_collected": gc.collect()}
        measure_stage("clear_qt_pixmap_cache", clear_qt_cache,
                      "Separate check for Qt's global pixmap cache; allocator pages may remain resident")

        def allocator_relief():
            import ctypes
            source = "https://github.com/apple-oss-distributions/libmalloc/blob/main/include/malloc/malloc.h"
            library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            function = library.malloc_zone_pressure_relief
            function.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            function.restype = ctypes.c_size_t
            before = memory_snapshot(process)
            started = time.perf_counter()
            released = int(function(None, 0))
            duration_ms = (time.perf_counter() - started) * 1000
            after = memory_snapshot(process)
            return {"api": "malloc_zone_pressure_relief(NULL, 0)", "source": source,
                    "semantics": "One best-effort maximum relief request for all malloc zones in this process",
                    "returned_released_bytes": released, "call_ms": round(duration_ms, 3),
                    "before_call": before, "after_call": after,
                    "immediate_delta_bytes": {key: after[key] - before[key]
                        if after[key] is not None and before[key] is not None else None for key in METRICS},
                    "production_code_changed": False, "invocations": 1}
        measure_stage("diagnostic_allocator_pressure_relief", allocator_relief,
                      "One diagnostic-only request after objects were freed; no periodic calls and no business-code changes")
        assert not report["safety"]["desktop_reads"]
        assert not controller.engine.gate_open and not controller.engine.visitors
        report["state"] = "complete"
        report["duration_seconds"] = round(time.perf_counter() - overall_started, 3)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["interpretation"] = interpret(report)
        save_report()
        return 0
    except Exception as exc:
        report["state"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["duration_seconds"] = round(time.perf_counter() - overall_started, 3)
        save_report()
        raise
    finally:
        if controller is not None:
            controller.shutdown()  # Saves only into the temporary probe directory.
        if qt is not None:
            qt.processEvents()
        if session is not None:
            session.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
