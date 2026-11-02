#!/bin/bash
# Finder-friendly source launcher. All paths are relative to this file.
set -u

VISITOR_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$VISITOR_ROOT" || exit 1

fail() {
    printf '\n后院访客暂时没有启动：%s\n' "$1"
    printf '可以把这段提示交给 Codex 帮你处理。\n'
    if [ -t 0 ]; then
        read -r -p "按回车关闭此窗口…" _
    fi
    exit 1
}

is_supported_python() {
    [ -x "$1" ] && "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

VISITOR_PYTHON="$VISITOR_ROOT/.venv/bin/python"
if ! is_supported_python "$VISITOR_PYTHON"; then
    if [ -d "$VISITOR_ROOT/.venv" ]; then
        fail "现有 .venv 环境不能运行，请让 Codex 修复环境后重试。"
    fi
    VISITOR_BASE_PYTHON=""
    for candidate in \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
        "$(command -v python3 2>/dev/null || true)"; do
        if is_supported_python "$candidate"; then
            VISITOR_BASE_PYTHON="$candidate"
            break
        fi
    done
    if [ -z "$VISITOR_BASE_PYTHON" ]; then
        fail "需要 Python 3.10 或更新版本（推荐 3.12/3.13）。请从 https://www.python.org/downloads/macos/ 安装，然后再双击本文件。"
    fi
    printf '首次启动：正在建立项目专用环境…\n'
    "$VISITOR_BASE_PYTHON" -m venv "$VISITOR_ROOT/.venv" || fail "无法创建 Python 环境。请确认项目文件夹可写。"
fi

if ! "$VISITOR_PYTHON" -c 'from PyQt6.QtCore import PYQT_VERSION_STR; from PyQt6 import QtWidgets; assert PYQT_VERSION_STR == "6.9.1"' >/dev/null 2>&1; then
    printf '首次启动需要联网下载 PyQt6；之后日常运行不需要联网。\n'
    "$VISITOR_PYTHON" -m pip --version >/dev/null 2>&1 || \
        "$VISITOR_PYTHON" -m ensurepip || fail "无法准备依赖安装工具。"
    "$VISITOR_PYTHON" -m pip install --disable-pip-version-check -r "$VISITOR_ROOT/requirements.txt" || \
        fail "依赖下载失败。请检查网络后重新双击；现有进度不会丢失。"
fi

"$VISITOR_PYTHON" "$VISITOR_ROOT/scripts/run.py" "$@" || fail "请查看上方提示和启动日志。"
