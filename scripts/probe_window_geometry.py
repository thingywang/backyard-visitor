"""Bounded, permission-free native macOS window geometry acceptance probe.

Only the child process created by this script is moved or closed. The report
contains that child's geometry and display scaling, never other window titles.
Run with the project's Python on a logged-in macOS graphical session.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TITLE = "后院访客几何验证"


def child(events_path: Path) -> int:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    qt = QApplication(["BackyardGeometryProbe"])
    qt.setApplicationName("BackyardGeometryProbe")
    screen = qt.primaryScreen()
    if screen is None:
        return 3
    available = screen.availableGeometry()
    window = QWidget()
    window.setWindowTitle(TITLE)
    window.setStyleSheet("QWidget { background: #faf5e9; color: #344137; } QLabel { font-size: 16px; }")
    layout = QVBoxLayout(window)
    label = QLabel("后院访客 · 几何验证\n\n这是独立测试窗口，将自行移动一次。\n约 8 秒后自动关闭，不操作其他窗口。")
    label.setWordWrap(True)
    layout.addWidget(label)
    width = min(420, max(240, available.width() - 220))
    height = min(220, max(140, available.height() - 350))
    initial_x, initial_y = available.x() + 70, available.y() + 170
    dx, dy = 96, 64
    window.setGeometry(initial_x, initial_y, width, height)
    events = []

    def geometry(rect):
        return {"x": rect.x(), "y": rect.y(), "width": rect.width(), "height": rect.height()}

    def record(stage):
        events.append({
            "stage": stage,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "frame": geometry(window.frameGeometry()),
            "content": geometry(window.geometry()),
            "screen": geometry(screen.geometry()),
            "available_screen": geometry(available),
            "device_pixel_ratio": screen.devicePixelRatio(),
            "logical_dpi": [screen.logicalDotsPerInchX(), screen.logicalDotsPerInchY()],
            "physical_dpi": [screen.physicalDotsPerInchX(), screen.physicalDotsPerInchY()],
        })
        pending = events_path.with_suffix(".pending")
        pending.write_text(json.dumps(events), encoding="utf-8")
        pending.replace(events_path)

    def move_window():
        # QWidget.move uses the frame origin for a top-level decorated window.
        frame = window.frameGeometry()
        window.move(frame.x() + dx, frame.y() + dy)
        label.setText("后院访客 · 几何验证\n\n测试窗口已移动，正在核对逻辑坐标。\n即将自动关闭。")

    def close_window():
        window.close()
        record("closed")
        QTimer.singleShot(100, qt.quit)

    qt.setQuitOnLastWindowClosed(False)
    started = time.monotonic()
    window.show()
    window.raise_()
    window.activateWindow()
    QTimer.singleShot(700, lambda: record("initial"))
    QTimer.singleShot(3500, move_window)
    QTimer.singleShot(4200, lambda: record("moved"))
    QTimer.singleShot(7500, close_window)
    QTimer.singleShot(25000, qt.quit)
    return qt.exec()


def probe(output: Path) -> int:
    from backyard_visitor.desktop import MacDesktopAdapter, Rect, visible_window_edges

    report = {
        "schema_version": 1,
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "test_window_title": TITLE,
        "privacy": "Only geometry of the separately spawned test process is recorded; no other window titles.",
        "permission_requests": False,
        "coordinate_units": "logical desktop points",
        "position_tolerance_points": 2.0,
        "samples": {},
        "checks": {},
        "limitations": [],
    }
    if sys.platform != "darwin":
        report["limitations"].append("Native macOS CoreGraphics is unavailable on this platform.")
    else:
        adapter = MacDesktopAdapter()
        process = None
        observed_ids: set[str] = set()
        with tempfile.TemporaryDirectory(prefix="backyard-window-probe-") as temporary:
            events_path = Path(temporary) / "events.json"
            environment = os.environ.copy()
            environment["QT_QPA_PLATFORM"] = "cocoa"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--child", str(events_path)],
                    env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                report["test_pid"] = process.pid
                deadline = started + 18
                while time.monotonic() < deadline:
                    try:
                        events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else []
                    except (OSError, ValueError):
                        events = []
                    # Identify the child by PID, never by user document titles.
                    own_records = [
                        record for record in adapter._graphics().window_info()
                        if record.get("kCGWindowOwnerPID") == process.pid
                        and record.get("kCGWindowLayer") == 0
                    ]
                    child_ids = {f"mac-window-{int(record['kCGWindowNumber'])}" for record in own_records}
                    targets = adapter.windows()
                    own_targets = [target for target in targets if target.id in child_ids]
                    observed_ids.update(target.id for target in own_targets)
                    for event in events:
                        stage = event["stage"]
                        if stage in report["samples"]:
                            continue
                        if stage == "closed":
                            remaining = [target for target in targets if target.id in observed_ids]
                            if not remaining:
                                report["samples"][stage] = {
                                    "adapter_window_absent": True,
                                    "coregraphics_child_window_absent": not child_ids,
                                }
                            continue
                        # Only compare a stage while that stage is still current.
                        if event is not events[-1] or not own_targets:
                            continue
                        expected = Rect(**event["frame"])
                        selected = min(own_targets, key=lambda target: sum(
                            abs(getattr(target.rect, name) - getattr(expected, name))
                            for name in ("x", "y", "width", "height")
                        ))
                        errors = {
                            name: round(getattr(selected.rect, name) - getattr(expected, name), 3)
                            for name in ("x", "y", "width", "height")
                        }
                        screen = Rect(**event["screen"])
                        visible = [
                            list(edge) for edge in visible_window_edges(targets, screen)
                            if edge[0].startswith(selected.id + ":top:")
                        ]
                        sample = dict(event)
                        sample.update({
                            "adapter_id": selected.id,
                            "adapter_rect": asdict(selected.rect),
                            "adapter_minus_qt_frame": errors,
                            "matches_qt_logical_frame": all(abs(value) <= 2 for value in errors.values()),
                            "visible_standing_edges": visible,
                        })
                        # Keep the first settled observation; mismatches remain explicit.
                        report["samples"][stage] = sample
                    if {"initial", "moved", "closed"} <= report["samples"].keys() and process.poll() is not None:
                        break
                    if process.poll() is not None and not events:
                        break
                    time.sleep(0.12)
                if adapter.last_error:
                    report["limitations"].append("Adapter query failed: " + adapter.last_error)
            except (OSError, ValueError, RuntimeError) as error:
                report["limitations"].append(f"Native probe unavailable: {type(error).__name__}: {error}")
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()  # Only our child; never a pre-existing application.
                    try:
                        report["child_exit_code"] = process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        report["child_exit_code"] = process.wait(timeout=2)
                        report["limitations"].append("Own test child required forced cleanup.")
            report["duration_seconds"] = round(time.monotonic() - started, 3)

        initial, moved, closed = (report["samples"].get(stage) for stage in ("initial", "moved", "closed"))
        report["checks"] = {
            "initial_position_matches_qt_logical_coordinates": bool(initial and initial["matches_qt_logical_frame"]),
            "moved_position_matches_qt_logical_coordinates": bool(moved and moved["matches_qt_logical_frame"]),
            "window_identity_stable_after_move": bool(initial and moved and initial["adapter_id"] == moved["adapter_id"]),
            "position_changed_after_child_move": bool(initial and moved and initial["adapter_rect"] != moved["adapter_rect"]),
            "initial_visible_edge_detected": bool(initial and initial["visible_standing_edges"]),
            "moved_visible_edge_detected": bool(moved and moved["visible_standing_edges"]),
            "closed_window_disappears_from_adapter": bool(closed and closed["adapter_window_absent"]),
            "closed_window_disappears_from_coregraphics": bool(closed and closed["coregraphics_child_window_absent"]),
            "child_exited_normally": report.get("child_exit_code") == 0,
        }
        if not initial or not moved:
            report["limitations"].append("A native test window was not observed in every stage; graphical session or visibility may be unavailable.")
        if any(sample and not sample["visible_standing_edges"] for sample in (initial, moved)):
            report["limitations"].append("The test window's top edge was unavailable in the live stacking order; another window or screen boundary may obscure it.")
    report["passed"] = bool(report["checks"]) and all(report["checks"].values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "output": str(output),
                      "limitations": report["limitations"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "window-geometry.json")
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    return child(arguments.child) if arguments.child else probe(arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
