"""Launch the source app independently of the Finder terminal window."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def check_runtime() -> str | None:
    if sys.version_info < (3, 10):
        return "需要 Python 3.10 或更新版本。"
    if not (ROOT / "backyard_visitor" / "app.py").is_file():
        return "未找到应用入口 backyard_visitor/app.py；请等待本次开发完成。"
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR
        from PyQt6 import QtWidgets  # noqa: F401; verify native Qt dependencies too.
        if PYQT_VERSION_STR != "6.9.1":
            return "PyQt6 版本不符，请通过 launch.command 更新项目依赖。"
    except ImportError as exc:
        return f"PyQt6 尚未就绪：{exc}。请双击 launch.command。"
    assets = ROOT / "backyard_visitor" / "assets"
    for name in ("cats.png", "sprites.json", "extra.png", "extra.json",
                 "interactions.png", "interactions.json", "gifts.png", "gifts.json",
                 "standing.png", "standing.json", "settling.png", "settling.json", "jump_transitions.png", "jump_transitions.json", "run.png", "run.json", "bowl.png", "bowl.json", "activity.png", "activity.json", "posture.png", "posture-belly.png", "posture.json", "locomotion.json", "locomotion-front-contact.png", "locomotion-hind-passing.png",
                 "locomotion-back-contact.png", "locomotion-front-passing.png",
                 "backyard-dusk.png", "backyard-dusk.json", "backyard-morning.png", "backyard-morning.json",
                 "backyard-cat-tree-portrait.png", "backyard-cat-tree-portrait.json", "resident-bed.png"):
        if not (assets / name).is_file():
            return f"缺少素材 {name}，请恢复完整项目文件夹。"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="启动后院访客")
    parser.add_argument("--check", action="store_true", help="仅检查运行环境，不打开应用")
    parser.add_argument("--foreground", action="store_true", help="开发时在当前终端运行")
    args, app_args = parser.parse_known_args()
    error = check_runtime()
    if error:
        print(error, file=sys.stderr)
        return 1
    if args.check:
        print("运行环境和素材检查通过；尚未启动应用。")
        return 0
    command = [sys.executable, "-u", str(ROOT / "scripts" / "entry.py"), *app_args]
    if args.foreground:
        os.chdir(ROOT)
        os.execv(sys.executable, command)
    log_dir = Path.home() / "Library" / "Logs" / "BackyardVisitor"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "visitor.log"
    if log_path.exists() and log_path.stat().st_size > 1_048_576:
        log_path.replace(log_path.with_suffix(".previous.log"))
    try:
        with log_path.open("a", encoding="utf-8", buffering=1) as log:
            log.write(f"\n--- 启动 {datetime.now().astimezone().isoformat(timespec='seconds')} ---\n")
            child = subprocess.Popen(
                command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
        try:
            code = child.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            code = None
        if code is not None and code != 0:
            print(f"启动失败。请把日志交给 Codex：{log_path}", file=sys.stderr)
            return code
        print("后院访客已启动。可以关闭这个终端窗口。")
        print(f"启动日志：{log_path}")
        print("先在手册或桌面小门处开门，再等猫咪来访。")
        return 0
    except OSError as exc:
        print(f"无法启动：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
