"""Externally profile the real macOS .app main process, with isolated progress.

Default invocation starts a detached worker and returns immediately. The worker
runs closed (12s), single-cat demo (40s), then two-cat fight flow (40s),
sampling every second with 100/100/150 MB RSS limits. It never
imports measurement libraries into the application being measured.
Use --scenarios multi to run only the multi-cat flow, without closed/single runs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RSS_TARGET_BYTES = 100_000_000
METRICS = ("rss_bytes", "uss_bytes", "phys_footprint_bytes", "native_rss_bytes")

SCENARIO_RSS_LIMITS = {"closed": 100_000_000, "demo": 100_000_000, "multi": 150_000_000}
SCENARIO_DESCRIPTIONS = {
    "closed": "Gate closed, journal hidden; the entire observed scenario has a 100 MB RSS limit.",
    "demo": "Feeding demo configured for at most one cat; naturally varying behavior and no per-sample idle/pose claim. Entire scenario limit: 100 MB RSS.",
    "multi": "Fight demo configured for at most two cats, including arrivals and departures; samples are not assumed to contain two cats. Conservative entire-scenario limit: 150 MB RSS.",
}


def scenario_command(label, seconds, executable, state_dir):
    """Build only the isolated app command; never launch from this helper."""
    if label not in SCENARIO_RSS_LIMITS:
        raise ValueError(f"Unknown profile scenario: {label}")
    command = [str(executable), "--hidden", "--smoke-seconds", str(seconds),
               "--state-dir", str(state_dir)]
    if label in {"demo", "multi"}:
        command.extend(["--demo", "--demo-cat-limit", "1" if label == "demo" else "2"])
    if label == "multi":
        command.extend(["--demo-scene", "fight"])
    return command


def assess_rss(report):
    """Evaluate scenario-specific RSS limits, including legacy schema-1 reports.

    Whole-report maxima are descriptive only: a 150 MB multi-cat allowance
    must never hide a single-cat sample above 100 MB. Missing samples or a
    failed run cannot earn a pass, even when physical footprint is smaller.
    """
    version = report.get("schema_version", 1)
    selection_valid = version in (1, 2, 3)
    if version == 3:
        requested = report.get("requested_scenarios")
        selection_valid = (isinstance(requested, list) and bool(requested)
                           and all(isinstance(x, str) and x in SCENARIO_RSS_LIMITS for x in requested))
        selection_valid = selection_valid and len(requested) == len(set(requested))
        expected = set(requested) if selection_valid else set()
    else:
        expected = {"closed", "demo"} if version == 1 else set(SCENARIO_RSS_LIMITS)
    complete = report.get("state") == "complete" and selection_valid
    by_scenario = {}
    exceeded = False
    for scenario in report.get("scenarios", []):
        label = scenario.get("label")
        limit = SCENARIO_RSS_LIMITS.get(label)
        rows = scenario.get("samples", [])
        values = [
            row.get("rss_bytes") for row in rows
            if type(row.get("rss_bytes")) in (int, float)
            and math.isfinite(row["rss_bytes"]) and row["rss_bytes"] >= 0
        ]
        peak = max(values) if values else None
        over = limit is not None and peak is not None and peak > limit
        exceeded |= over
        valid = (limit is not None and label in expected and label not in by_scenario and bool(rows)
                 and len(values) == len(rows) and scenario.get("exit_code") == 0
                 and not scenario.get("timed_out") and not scenario.get("termination_requested")
                 and not scenario.get("sampling_errors"))
        complete &= valid
        by_scenario[label] = {
            "rss_limit_bytes": limit,
            "max_rss_bytes": peak,
            "assessment": "exceeded_limit" if over else
                          "within_limit_in_observed_samples" if valid else "inconclusive",
        }
    complete &= expected == by_scenario.keys()
    return {
        "assessment": "exceeded_scenario_limit" if exceeded else
                      "within_scenario_limits_in_all_observed_samples" if complete else "inconclusive",
        "by_scenario": by_scenario,
        "population_scope_note": (
            "Legacy demo had no enforced one-cat limit or per-sample counts; do not relabel it as a strict single-cat measurement."
            if report.get("schema_version", 1) < 2 else
            "Demo cat limits are launch configuration, not observed per-sample cat counts."
        ),
    }




def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_report(path, report):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def bundle_details(bundle):
    with (bundle / "Contents" / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    name = info.get("CFBundleExecutable")
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError("The bundle has no safe CFBundleExecutable.")
    executable = bundle / "Contents" / "MacOS" / name
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("The bundle executable is missing or not executable.")
    return executable, {
        "bundle": str(bundle),
        "executable": str(executable),
        "version": info.get("CFBundleShortVersionString"),
        "build": info.get("CFBundleVersion"),
        "executable_size_bytes": executable.stat().st_size,
        "executable_modified_ns": executable.stat().st_mtime_ns,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "info_plist_sha256": hashlib.sha256((bundle / "Contents" / "Info.plist").read_bytes()).hexdigest(),
    }


def summarize(rows, rss_target_bytes=RSS_TARGET_BYTES):
    summary = {"sample_count": len(rows)}
    cpu = [row["cpu_percent_one_core"] for row in rows if row["cpu_percent_one_core"] is not None]
    summary["mean_cpu_percent_one_core"] = round(statistics.mean(cpu), 3) if cpu else None
    summary["max_cpu_percent_one_core"] = max(cpu) if cpu else None
    for metric in METRICS:
        values = [row[metric] for row in rows if row.get(metric) is not None]
        summary[metric] = {
            "available_samples": len(values),
            "max_bytes": max(values) if values else None,
            "mean_mb_decimal": round(statistics.mean(values) / 1e6, 3) if values else None,
            "max_mb_decimal": round(max(values) / 1e6, 3) if values else None,
        }
    rss = [row["rss_bytes"] for row in rows if row.get("rss_bytes") is not None]
    summary["rss_within_100mb_in_observed_samples"] = bool(rss) and max(rss) <= RSS_TARGET_BYTES
    summary["rss_target_bytes"] = rss_target_bytes
    summary["rss_within_target_in_observed_samples"] = (
        bool(rss) and max(rss) <= rss_target_bytes if rss_target_bytes is not None else None
    )
    return summary



def selected_scenarios(options):
    return [(label, getattr(options, {"closed": "closed_seconds", "demo": "demo_seconds",
                                     "multi": "multi_seconds"}[label])) for label in options.scenarios]


def own_cat_window_metadata(records, pid, displays):
    """Own-PID numeric metadata only. Geometry identifies candidates, not pixels.

    Fresh demo disables icon replicas; CatWindow is its only 144 x 112 overlay.
    CG on-screen/alpha metadata does not prove unoccluded pixels or distinguish
    a cat pose from the fight-cloud painted in that same window.
    """
    windows = []
    for record in records:
        if record.get("kCGWindowOwnerPID") != pid or not record.get("kCGWindowIsOnscreen"):
            continue
        bounds = record.get("kCGWindowBounds", {})
        try:
            x, y, w, h = (float(bounds[k]) for k in ("X", "Y", "Width", "Height"))
            alpha = float(record.get("kCGWindowAlpha", 0))
            wid = int(record["kCGWindowNumber"])
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        if not all(math.isfinite(v) for v in (x, y, w, h, alpha)) or alpha <= 0 or wid <= 0:
            continue
        if abs(w-144) > .5 or abs(h-112) > .5:
            continue
        contained = [i for i, d in enumerate(displays)
                     if x >= d[0] and y >= d[1] and x+w <= d[0]+d[2] and y+h <= d[1]+d[3]]
        windows.append({"window_id": wid, "bounds": [x, y, w, h], "alpha": alpha,
                        "fully_contained_display_indices": contained})
    # Do not let duplicate WindowServer entries inflate the count.
    windows = list({w["window_id"]: w for w in windows}.values())
    per_display = {str(i): sorted(w["window_id"] for w in windows
                    if i in w["fully_contained_display_indices"]) for i in range(len(displays))}
    return {"available": True, "candidate_windows": windows, "display_bounds": displays,
            "fully_contained_window_ids_by_display": per_display,
            "max_count_on_one_display": max(map(len, per_display.values()), default=0)}


def summarize_window_evidence(rows, interval=1):
    observed, streak, longest, previous_time, previous_groups = 0, 0., 0., None, {}
    available = 0
    for row in rows:
        metadata = row.get("window_metadata") or {}
        available += metadata.get("available") is True
        groups = metadata.get("fully_contained_window_ids_by_display", {})
        groups = {key: set(ids) for key, ids in groups.items() if len(set(ids)) >= 2}
        current_time = row["elapsed_seconds"]
        if groups:
            observed += 1
        same_pair = any(len(ids & previous_groups.get(key, set())) >= 2 for key, ids in groups.items())
        gap = current_time-previous_time if previous_time is not None else None
        streak = streak+gap if same_pair and gap is not None and 0 < gap <= interval*1.6 else 0.
        longest = max(longest, streak)
        previous_time, previous_groups = current_time, groups
    return {"available_samples": available, "coobserved_two_window_samples": observed,
            "max_consecutive_same_pair_interval_seconds": round(longest, 3),
            "assessment": "two_cat_sized_windows_coobserved" if observed else
                          "no_two_cat_sized_windows_observed" if available else "unavailable",
            "cat_pixels_verified": False,
            "scope": "Owned-PID 144x112 windows fully inside one display. Role inferred from fresh demo geometry; metadata only, no screenshots or permissions. Occlusion, actual cat pixels, and fight-cloud vs cat pose are not verified."}


def run_scenario(label, seconds, executable, options, report):
    # These imports run only in the separate observer, never in the frozen app.
    import psutil
    from scripts.profile_runtime import memory_snapshot

    graphics = None
    metadata_error = None
    if label == "multi":
        try:
            from backyard_visitor.desktop import _CoreGraphics
            graphics = _CoreGraphics()
        except (OSError, AttributeError) as error:
            metadata_error = str(error)

    scenario = {
        "label": label, "requested_runtime_seconds": seconds,
        "description": SCENARIO_DESCRIPTIONS[label],
        "rss_target_bytes": SCENARIO_RSS_LIMITS[label],
        "configured_demo_cat_limit": {"closed": None, "demo": 1, "multi": 2}[label],
        "per_sample_cat_count_observed": False,
        "started_at": utc_now(), "exit_code": None, "timed_out": False,
        "termination_requested": False, "sampling_errors": [], "samples": [],
        "window_metadata_errors": [],
    }
    report["scenarios"].append(scenario)
    write_report(options.output, report)
    process = None
    with tempfile.TemporaryDirectory(prefix=f"backyard-bundle-{label}-") as state_dir:
        command = scenario_command(label, seconds, executable, state_dir)
        scenario["arguments"] = command[1:]
        scenario["state_isolation"] = "Temporary directory; demo also uses its own disposable state."
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "cocoa"
        started = time.monotonic()
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=subprocess.STDOUT)
            scenario["pid"] = process.pid
            observed = psutil.Process(process.pid)
            scenario["process_created_at"] = observed.create_time()
            observed.cpu_percent(interval=None)
            next_sample = started
            cpu_primed = False
            while process.poll() is None:
                now = time.monotonic()
                if now - started > seconds + options.timeout_grace:
                    scenario["timed_out"] = True
                    break
                if now < next_sample:
                    time.sleep(min(0.1, next_sample - now))
                    continue
                try:
                    cpu = observed.cpu_percent(interval=None) if cpu_primed else None
                    row = {
                        "scenario": label,
                        "elapsed_seconds": round(now - started, 3),
                        "sampled_at": utc_now(),
                        "cpu_percent_one_core": cpu,
                    }
                    row.update(memory_snapshot(observed))
                    if label == "multi":
                        if graphics is None:
                            row["window_metadata"] = {"available": False, "error": metadata_error}
                        else:
                            try:
                                displays = [[d.x, d.y, d.width, d.height] for d in graphics.display_rects()]
                                row["window_metadata"] = own_cat_window_metadata(graphics.window_info(), process.pid, displays)
                            except (OSError, ValueError, TypeError, KeyError) as error:
                                row["window_metadata"] = {"available": False, "error": str(error)}
                                scenario["window_metadata_errors"].append({"elapsed_seconds": row["elapsed_seconds"], "message": str(error)})
                    scenario["samples"].append(row)
                    report["heartbeat_at"] = utc_now()
                    report["heartbeat_monotonic"] = time.monotonic()
                    cpu_primed = True
                    write_report(options.output, report)
                except psutil.NoSuchProcess:
                    break
                except (psutil.Error, OSError) as error:
                    scenario["sampling_errors"].append({
                        "elapsed_seconds": round(now - started, 3),
                        "type": type(error).__name__, "message": str(error),
                    })
                next_sample += options.interval
                if next_sample < time.monotonic():
                    next_sample = time.monotonic() + options.interval
        finally:
            if process is not None:
                if process.poll() is None:
                    scenario["termination_requested"] = True
                    process.terminate()  # Only this Popen child, never other app instances.
                try:
                    scenario["exit_code"] = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    scenario["forced_kill"] = True
                    process.kill()
                    scenario["exit_code"] = process.wait(timeout=3)
            scenario["actual_duration_seconds"] = round(time.monotonic() - started, 3)
            scenario["finished_at"] = utc_now()
            scenario["summary_all_samples"] = summarize(scenario["samples"], SCENARIO_RSS_LIMITS[label])
            scenario["summary_samples_after_3_seconds"] = summarize([
                row for row in scenario["samples"] if row["elapsed_seconds"] >= 3
            ], SCENARIO_RSS_LIMITS[label])
            if label == "multi":
                scenario["window_evidence"] = summarize_window_evidence(scenario["samples"], options.interval)
            write_report(options.output, report)
    print(json.dumps({"scenario": label, "exit_code": scenario["exit_code"],
                      "summary": scenario["summary_all_samples"]}, ensure_ascii=False), flush=True)
    return scenario["exit_code"] == 0 and not scenario["timed_out"] and bool(scenario["samples"])


def worker(options, executable, details):
    import fcntl

    lock_path = options.output.with_suffix(".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another profiler owns this output; this worker did not launch an application.", flush=True)
            return 2
        report = {
            "schema_version": 3, "state": "running", "started_at": utc_now(),
            "requested_scenarios": list(options.scenarios),
            "observer_pid": os.getpid(), "platform": platform.platform(),
            "architecture": platform.machine(), "bundle": details,
            "measurement": {
                "location": "Separate observer process; measured application is the actual frozen .app executable.",
                "sample_interval_seconds": options.interval,
                "byte_unit": "bytes; displayed MB uses 1,000,000 bytes",
                "cpu": "psutil process CPU percent; one logical core is 100%; first sample is null.",
                "rss": "Resident process pages including clean/shared resident mappings; psutil.memory_info().rss.",
                "uss": "Unique resident process memory; psutil.memory_full_info().uss; null if unavailable.",
                "phys_footprint": "macOS charged physical footprint via proc_pid_rusage RUSAGE_INFO_V2; distinct from RSS/USS.",
                "native_rss": "macOS ri_resident_size cross-check, separate from psutil RSS.",
                "scope": "Only the launched app main PID is measured. Observer and any child-process memory are excluded.",
                "overhead": "No probe library is imported into the app; external OS sampling still has some measurement cost.",
                "limits": "Finite samples can miss short peaks. Demo behavior varies; no sample is labeled idle or assumed to contain two cats. Multi is a fight flow including arrival/departure, with a conservative 150 MB whole-scenario limit.",
                "platform_plugin": "cocoa",
                "inherited_qt_scale_settings": {
                    key: os.environ[key] for key in ("QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS")
                    if key in os.environ
                },
            },
            "rss_target_bytes": SCENARIO_RSS_LIMITS[options.scenarios[0]] if len(options.scenarios) == 1 else None,
            "rss_limits_bytes_by_scenario": {k: SCENARIO_RSS_LIMITS[k] for k in options.scenarios}, "scenarios": [],
        }
        write_report(options.output, report)
        try:
            outcomes = []
            for label, seconds in selected_scenarios(options):
                outcomes.append(run_scenario(label, seconds, executable, options, report))
            report["state"] = "complete" if all(outcomes) else "failed"
        except Exception as error:
            report["state"] = "failed"
            report["error"] = {"type": type(error).__name__, "message": str(error)}
        report["finished_at"] = utc_now()
        _, report["bundle_after"] = bundle_details(options.bundle)
        report["bundle_identity_unchanged"] = details == report["bundle_after"]
        if not report["bundle_identity_unchanged"]:
            report["state"] = "failed"
        rows = [row for scenario in report["scenarios"] for row in scenario["samples"]]
        report["summary_all_scenarios"] = summarize(rows, rss_target_bytes=None)
        assessment = assess_rss(report)
        report["rss_target_assessment"] = assessment["assessment"]
        report["rss_assessment_by_scenario"] = assessment["by_scenario"]
        report["population_scope_note"] = assessment["population_scope_note"]
        write_report(options.output, report)
        print(json.dumps({"state": report["state"], "rss_target_assessment": report["rss_target_assessment"],
                          "output": str(options.output)}, ensure_ascii=False), flush=True)
        return 0 if report["state"] == "complete" else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist" / "BackyardVisitor.app",
                        help="Built macOS .app to measure.")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "performance-bundle-v03.json",
                        help="Full sampled report; adjacent .log and .job.json contain worker logs and metadata.")
    parser.add_argument("--scenarios", nargs="+", choices=tuple(SCENARIO_RSS_LIMITS), default=list(SCENARIO_RSS_LIMITS),
                        help="Run only the selected scenarios, in order; default: closed demo multi.")
    parser.add_argument("--closed-seconds", type=float, default=12)
    parser.add_argument("--demo-seconds", type=float, default=40, help="Single-cat feeding demo duration.")
    parser.add_argument("--multi-seconds", type=float, default=40, help="Two-cat fight flow duration, including arrival/departure.")
    parser.add_argument("--interval", type=float, default=1, help="Sampling interval in seconds (default: 1).")
    parser.add_argument("--timeout-grace", type=float, default=10, help="Extra startup/exit allowance per scenario.")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if len(options.scenarios) != len(set(options.scenarios)):
        parser.error("--scenarios must not contain duplicates.")
    for name in ("closed_seconds", "demo_seconds", "multi_seconds", "interval", "timeout_grace"):
        value = getattr(options, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive.")
    if sys.platform != "darwin":
        parser.error("This probe requires native macOS.")
    options.bundle, options.output = options.bundle.resolve(), options.output.resolve()
    return options


def main(argv=None):
    options = parse_args(argv)
    try:
        executable, details = bundle_details(options.bundle)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    options.output.parent.mkdir(parents=True, exist_ok=True)
    if options.worker:
        return worker(options, executable, details)
    command = [
        sys.executable, "-u", str(Path(__file__).resolve()), "--worker",
        "--bundle", str(options.bundle), "--output", str(options.output),
        "--closed-seconds", str(options.closed_seconds), "--demo-seconds", str(options.demo_seconds),
        "--multi-seconds", str(options.multi_seconds),
        "--scenarios", *options.scenarios,
        "--interval", str(options.interval), "--timeout-grace", str(options.timeout_grace),
    ]
    log_path = options.output.with_suffix(".log")
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    metadata = {"observer_pid": process.pid, "submitted_at": utc_now(), "log": str(log_path),
                "report": str(options.output), "bundle": details}
    write_report(options.output.with_suffix(".job.json"), metadata)
    print(json.dumps(metadata, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
