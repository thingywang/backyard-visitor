"""Atomic, bounded JSON persistence for the visitor's local relationships."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

MAX_SAVE_BYTES = 1_048_576


def load_state(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Read a save, falling back to the previous good snapshot if interrupted.

    Missing or corrupt files mean a fresh start. They are preserved for manual
    recovery; merely loading never renames, overwrites, or deletes a file.
    """
    target = Path(path)
    for candidate in (target, target.with_suffix(target.suffix + ".bak")):
        state = _read(candidate)
        if state is not None:
            return state
    return None


def _read(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SAVE_BYTES + 1)
        if len(raw) > MAX_SAVE_BYTES:
            return None
        state = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
        return state if isinstance(state, dict) else None
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite number in JSON: {value}")


def save_state(path: str | os.PathLike[str], state: dict[str, Any]) -> None:
    """Replace a save atomically and retain one previous valid JSON snapshot.

    Errors propagate so the application can show a useful save warning. Data is
    serialized before touching disk, and temporary files always share the same
    directory as the destination for atomic ``os.replace`` on Windows too.
    """
    if not isinstance(state, dict):
        raise TypeError("State must be a dictionary")
    encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
    if len(encoded) > MAX_SAVE_BYTES:
        raise ValueError("Save state is too large")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = _read(target)
    if previous is not None:
        backup_bytes = json.dumps(previous, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
        _replace(target.with_suffix(target.suffix + ".bak"), backup_bytes)
    _replace(target, encoded)


def _replace(target: Path, content: bytes) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
