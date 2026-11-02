"""Lazy macOS updater. Importing or launching the app performs no network request."""
from __future__ import annotations

import base64
import ctypes
from pathlib import Path
import plistlib
import sys
import threading
from urllib.parse import urlsplit
from typing import Callable


class UpdateUnavailable(RuntimeError):
    pass


def app_bundle() -> Path | None:
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    bundle = executable.parent.parent.parent
    if executable.parent.name != "MacOS" or not bundle.name.endswith(".app"):
        return None
    return bundle


def validate_configuration(bundle: Path) -> None:
    try:
        info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
        feed = urlsplit(info.get("SUFeedURL", ""))
        key = base64.b64decode(info.get("SUPublicEDKey", ""), validate=True)
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException) as exc:
        raise UpdateUnavailable("更新配置不完整，请重新安装正式发布包。") from exc
    if (feed.scheme != "https" or not feed.hostname or feed.username or feed.password
            or len(key) != 32 or info.get("SURequireSignedFeed") is not True
            or info.get("SUVerifyUpdateBeforeExtraction") is not True):
        raise UpdateUnavailable("更新配置未通过安全检查，请重新安装正式发布包。")


class NativeUpdater:
    def __init__(self, save: Callable[[], bool], shutdown: Callable[[], None]):
        self._save, self._shutdown = save, shutdown
        self._library = None
        self._callback = None

    def _state_callback(self, stage: int) -> int:
        try:
            if stage == 1:
                return int(bool(self._save()))
            if stage == 2:
                self._shutdown()
                return 1
            return 0
        except Exception:
            # Do not let a Python exception cross a native callback boundary.
            return 0

    def check(self) -> bool:
        if threading.current_thread() is not threading.main_thread():
            raise UpdateUnavailable("请从软件菜单中检查更新。")
        if self._library is None:
            bundle = app_bundle()
            if bundle is None:
                raise UpdateUnavailable("请在正式发布的 macOS 应用中检查更新；源码运行不执行应用替换。")
            validate_configuration(bundle)
            bridge = bundle / "Contents/Frameworks/libBackyardUpdater.dylib"
            if not bridge.is_file():
                raise UpdateUnavailable("此安装包不含更新组件，请安装带更新功能的正式发布包。")
            try:
                callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)
                callback = callback_type(self._state_callback)
                library = ctypes.CDLL(str(bridge))
                library.BVUpdaterStart.argtypes = [callback_type]
                library.BVUpdaterStart.restype = ctypes.c_int
                library.BVUpdaterCheck.argtypes = []
                library.BVUpdaterCheck.restype = ctypes.c_int
                library.BVUpdaterError.argtypes = []
                library.BVUpdaterError.restype = ctypes.c_char_p
                if not library.BVUpdaterStart(callback):
                    error = library.BVUpdaterError()
                    raise UpdateUnavailable(error.decode("utf-8", "replace") if error else "更新组件暂时无法启动。")
            except OSError as exc:
                raise UpdateUnavailable("更新组件无法载入，请重新安装正式发布包。") from exc
            # Keep the callback alive for the entire native updater lifetime.
            self._library, self._callback = library, callback
        result = self._library.BVUpdaterCheck()
        if result == 0:
            raise UpdateUnavailable("更新组件暂时不可用，请稍后再试。")
        return result == 1
