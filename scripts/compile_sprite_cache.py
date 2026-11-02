"""Compile CatArt's exact small native frames without changing product loading.

PNG output is a disposable build cache. Source hashes include the renderer and
all source atlases, locomotion images and manifests; it preserves states, blink timing,
DPR and the unmodified tail. --benchmark compares fresh native Cocoa processes,
with identical instrumentation and imports before each memory baseline.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ASSETS = ROOT / "backyard_visitor" / "assets"
METRICS = ("rss_bytes", "uss_bytes", "phys_footprint_bytes", "native_rss_bytes")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_fingerprints():
    sources = [ROOT / "backyard_visitor" / "art.py"]
    for name in ("sprites.json", "extra.json", "interactions.json", "locomotion.json"):
        path = ASSETS / name
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        sources.append(path)
        if isinstance(data.get("file"), str):
            sources.append(ASSETS / data["file"])
        for frame in data.get("frames", []):
            if isinstance(frame.get("file"), str):
                sources.append(ASSETS / frame["file"])
    motion_renderer = ROOT / "backyard_visitor" / "motion_art.py"
    if motion_renderer.exists():
        sources.append(motion_renderer)
    files = {str(path.relative_to(ROOT)): sha256_file(path) for path in sources}
    combined = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"files": files, "combined_sha256": combined}


def qt_application():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Backyard Visitor Sprite Cache Probe")
    app.setQuitOnLastWindowClosed(False)
    return app


def pixels(image):
    from PyQt6.QtGui import QImage
    # Fixed channel ordering, with premultiplication retained. Comparing raw
    # encoded PNGs would miss color/alpha changes caused by round-trip decoding.
    normalized = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    return normalized.constBits().asstring(normalized.sizeInBytes())


def write_exact_pixmap(pixmap, destination, index=None):
    from PyQt6.QtGui import QImage
    original = pixmap.toImage()
    if not original.save(str(destination), "PNG"):
        raise RuntimeError(f"Could not write {destination.name}")
    reloaded = QImage(str(destination))
    if reloaded.isNull() or reloaded.size() != original.size():
        raise AssertionError(f"PNG dimensions changed: {destination.name}")
    before, after = pixels(original), pixels(reloaded)
    if before != after:
        mismatches = sum(a != b for a, b in zip(before, after))
        raise AssertionError(f"PNG round trip changed {mismatches} channel bytes: {destination.name}")
    return {"index": index, "file": destination.name,
            "width": original.width(), "height": original.height(),
            "device_pixel_ratio": pixmap.devicePixelRatio(),
            "png_sha256": sha256_file(destination),
            "pixel_sha256": hashlib.sha256(before).hexdigest(),
            "pixel_format": "RGBA8888_Premultiplied", "pixel_bytes": len(before),
            "pixel_roundtrip_equal": True}


def load_all_native_frames(art):
    groups = {}
    # Public state requests activate optional loaders without guessing their
    # aliases or fixed pose counts. Export still traverses every frame index.
    for state in ("walking", "window_peeking", "purring"):
        previous_count = len(art.frames)
        frame = art.frame(state, 0, -1, "ginger")
        if frame is None:
            raise AssertionError(f"Required source state did not load: {state}")
        del frame
        groups[state] = list(range(previous_count, len(art.frames)))
    return groups


def compile_cache(output):
    app = qt_application()
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    from backyard_visitor.art import CatArt
    fingerprints = source_fingerprints()
    # Never compile an existing cache back into itself.
    art = CatArt(prefer_compiled=False, retain_source_frames=True)
    base_count = len(art.frames)
    base_states = {name: list(indices) for name, indices in art.states.items()}
    optional_groups = load_all_native_frames(art)
    if not art.frames:
        raise AssertionError("No source poses were loaded")
    if art.tail is None:
        raise AssertionError("The independent screen-edge tail is missing")
    output.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, frame in enumerate(art.frames):
        if (frame.width(), frame.height(), frame.devicePixelRatio()) != (288, 224, 2):
            raise AssertionError(f"Unexpected standard frame geometry at {index}")
        frames.append(write_exact_pixmap(frame, output / f"frame-{index:02d}@2x.png", index))
    tail = write_exact_pixmap(art.tail, output / "tail.png")
    # Concurrent asset edits must not produce a cache with a misleading hash.
    if source_fingerprints() != fingerprints:
        raise RuntimeError("Source renderer/assets changed while compiling; rerun after edits finish")
    manifest = {
        "schema_version": 1,
        "kind": "backyard-visitor-compiled-sprite-cache",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_fingerprints": fingerprints,
        "compiler_sha256": sha256_file(Path(__file__)),
        "compiled_with": {"python": platform.python_version(), "pyqt": PYQT_VERSION_STR,
                          "qt": QT_VERSION_STR, "platform_plugin": app.platformName()},
        "native_facing": art.facing,
        "logical_frame_size": [144, 112],
        "frame_count": len(frames), "frames": frames, "tail": tail,
        "base_frame_count": base_count,
        "optional_frame_groups": optional_groups,
        "interaction_frame_indices": optional_groups["window_peeking"] + optional_groups["purring"],
        "base_states": base_states,
        "states": art.states,
        "blink_windows": art._blink_windows,
        "locomotion_cycle_seconds": json.loads((ASSETS / "locomotion.json").read_text()).get("cycle_seconds")
            if (ASSETS / "locomotion.json").exists() and optional_groups["walking"] else None,
        "pixel_validation": {"passed": True, "frames_compared": len(frames), "tail_compared": True,
                             "method": "Exact source QPixmap.toImage and decoded PNG channel-byte equality after common premultiplied RGBA format conversion"},
        "dpr_contract": "PNG dimensions are physical pixels; restore device_pixel_ratio from this manifest. @2x filenames are a convenience, not a substitute for metadata.",
        "product_loading_changed": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Compiled {len(frames)} exact 288x224 DPR2 poses and original {art.tail.width()}x{art.tail.height()} tail; every pixel round trip passed.", flush=True)
    return manifest


def measure_loading(output, mode):
    # The same modules and native QApplication are initialized in both workers.
    # No Controller, desktop backend, state directory or system permission call.
    from profile_runtime import memory_snapshot
    import psutil
    from PyQt6.QtGui import QPixmap
    from backyard_visitor.art import CatArt
    app = qt_application()
    if app.platformName() != "cocoa":
        raise RuntimeError("Memory comparison requires native Cocoa, not offscreen")
    process = psutil.Process()

    def settle_sample():
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        samples = []
        for index in range(3):
            if index:
                time.sleep(0.1)
                app.processEvents()
            samples.append(memory_snapshot(process))
        medians = {}
        for key in METRICS:
            values = [row[key] for row in samples if row[key] is not None]
            medians[key] = statistics.median(values) if values else None
        return {"samples": samples, "median_bytes": medians}

    expected_frame_count = json.loads((output / "manifest.json").read_text())["frame_count"]
    baseline = settle_sample()
    peak_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    if mode == "traditional":
        loaded = CatArt(prefer_compiled=False, retain_source_frames=True)
        load_all_native_frames(loaded)
        frame_count = len(loaded.frames)
        dprs = [frame.devicePixelRatio() for frame in loaded.frames]
        tail_size = [loaded.tail.width(), loaded.tail.height()]
    else:
        manifest = json.loads((output / "manifest.json").read_text())
        loaded = []
        for row in [*manifest["frames"], manifest["tail"]]:
            frame = QPixmap(str(output / row["file"]))
            if frame.isNull() or [frame.width(), frame.height()] != [row["width"], row["height"]]:
                raise AssertionError(f"Compiled frame failed to load: {row['file']}")
            frame.setDevicePixelRatio(row["device_pixel_ratio"])
            loaded.append(frame)
        frame_count = len(loaded) - 1
        dprs = [frame.devicePixelRatio() for frame in loaded[:-1]]
        tail_size = [loaded[-1].width(), loaded[-1].height()]
    loading_ms = (time.perf_counter() - started) * 1000
    immediate = memory_snapshot(process)
    after = settle_sample()
    assert frame_count == expected_frame_count and set(dprs) == {2.0}
    delta = {key: after["median_bytes"][key] - baseline["median_bytes"][key]
             if after["median_bytes"][key] is not None and baseline["median_bytes"][key] is not None else None
             for key in METRICS}
    result = {"mode": mode, "pid": os.getpid(), "platform_plugin": app.platformName(),
              "loading_ms": round(loading_ms, 3), "baseline": baseline, "immediate_after_load": immediate,
              "after": after, "delta_bytes": delta, "frame_count": frame_count, "dprs": sorted(set(dprs)),
              "tail_size": tail_size, "peak_rss_before_bytes": peak_before,
              "peak_rss_after_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "scope": "Instrumented source child process, all compiled native pose indices plus tail retained; no variants, journal, Controller or visitor windows"}
    print(json.dumps(result), flush=True)


def benchmark(output, report_path):
    report = {"schema_version": 1, "state": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "source_fingerprints": source_fingerprints(),
              "purpose": "Independent-process loading deltas only; absolute RSS includes identical probe overhead and is not release performance",
              "measurement": "scripts/profile_runtime.py:memory_snapshot; byte values, decimal MB; 600ms settle then median of 3 samples",
              "permission_requests": False, "formal_progress_used": False, "product_loading_changed": False,
              "runs": []}
    path = report_path
    path.parent.mkdir(parents=True, exist_ok=True)
    expected_sources = json.loads((output / "manifest.json").read_text())["source_fingerprints"]
    if report["source_fingerprints"] != expected_sources:
        raise RuntimeError("Cache is stale before benchmark; compile again")
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "cocoa"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # ABBA ordering reduces the temptation to attribute cache/order effects to
    # the new loader. Every sample still comes from a fresh interpreter process.
    for mode in ("traditional", "compiled", "compiled", "traditional"):
        path.write_text(json.dumps(report, indent=2) + "\n")
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--output", str(output),
                                 "--measure-worker", mode], cwd=ROOT, env=environment,
                                text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError(f"{mode} worker failed: {result.stderr.strip()}")
        row = json.loads(result.stdout.strip().splitlines()[-1])
        report["runs"].append(row)
        print(f"{mode}: RSS delta {row['delta_bytes']['rss_bytes']/1e6:.3f}MB, footprint delta {row['delta_bytes']['phys_footprint_bytes']/1e6:.3f}MB, load {row['loading_ms']:.3f}ms", flush=True)
    report["summary"] = {}
    for mode in ("traditional", "compiled"):
        rows = [row for row in report["runs"] if row["mode"] == mode]
        report["summary"][mode] = {
            "runs": len(rows), "median_loading_ms": statistics.median(row["loading_ms"] for row in rows),
            "median_delta_bytes": {key: statistics.median(row["delta_bytes"][key] for row in rows)
                                   if all(row["delta_bytes"][key] is not None for row in rows) else None for key in METRICS}}
    report["median_delta_reduction_bytes"] = {
        key: report["summary"]["traditional"]["median_delta_bytes"][key] - report["summary"]["compiled"]["median_delta_bytes"][key]
        if report["summary"]["traditional"]["median_delta_bytes"][key] is not None
        and report["summary"]["compiled"]["median_delta_bytes"][key] is not None else None for key in METRICS}
    if source_fingerprints() != expected_sources:
        raise RuntimeError("Sources changed during benchmark; compile and measure again")
    report["state"] = "complete"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["conclusion"] = "Worth an opt-in build-pipeline integration experiment if pixel verification remains exact; do not claim single-cat 100MB or multi-cat 150MB compliance until the real bundle is measured. Product loading is unchanged by this POC."
    path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description="Compile exact CatArt frames into an optional small-PNG build cache")
    parser.add_argument("--output", type=Path, default=ASSETS / "compiled-sprites")
    parser.add_argument("--force-source", action="store_true",
                        help="Explicit build flag: bypass product cache (compilation always uses source frames)")
    parser.add_argument("--benchmark-report", type=Path, default=ROOT / "output" / "compiled-sprites-benchmark.json")
    parser.add_argument("--benchmark", action="store_true", help="Compare two fresh processes per loader on native Cocoa")
    parser.add_argument("--measure-worker", choices=("traditional", "compiled"), help=argparse.SUPPRESS)
    options = parser.parse_args()
    options.output = options.output.resolve()
    # Process-local override is inherited by workers, never a system setting.
    if options.force_source:
        os.environ["BV_DISABLE_SPRITE_CACHE"] = "1"
    if options.measure_worker:
        measure_loading(options.output, options.measure_worker)
        return
    if options.benchmark and sys.platform != "darwin":
        parser.error("--benchmark requires native macOS")
    if sys.platform == "darwin":
        os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
    compile_cache(options.output)
    if options.benchmark:
        benchmark(options.output, options.benchmark_report.resolve())


if __name__ == "__main__":
    main()
