"""Shared source/PyInstaller entry point; asset paths remain package-relative."""
from __future__ import annotations

from pathlib import Path
import sys
import traceback


def run() -> int:
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # A windowed .app has no terminal streams. Keep startup failures inspectable.
    if sys.stdout is None or sys.stderr is None:
        log_dir = Path.home() / "Library" / "Logs" / "BackyardVisitor"
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = (log_dir / "visitor.log").open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    try:
        from backyard_visitor.app import main
        return main() or 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
