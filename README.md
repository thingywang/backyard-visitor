# 后院访客 · Backyard Visitor

让邻居家的猫，偶尔来你的桌面坐一会儿。

一款 macOS 优先的桌面猫咪原型，使用 Python 3.10+、PyQt6 与 PyInstaller。你决定何时开门，猫咪会探访、吃饭、玩耍并逐渐熟悉你。日常运行可离线，关系与设置保存在本机。

## 使用

[下载 macOS Apple 芯片版 0.9.4](https://github.com/thingywang/backyard-visitor/releases/download/v0.9.4/BackyardVisitor-0.9.4-macOS-arm64.zip)，解压后运行。当前验证平台为 Apple 芯片 Mac、macOS 12 或更新。默认关门，打开手册后点击开门即可迎接访客。

带更新功能的安装包可从“桌面偏好”或托盘菜单选择“检查更新”。软件读取签名清单、下载并验证新版，安装前保存进度，再替换应用并重启。日常陪伴不会自动联网或安装更新。当前版本为 **0.9.4**，更新地址指向本仓库最新正式 Release。

当前 Mac 测试包采用 ad-hoc 本地签名，尚未完成 Apple Developer ID 发布签名及公证。其他电脑兼容性仍需继续验证。

## 从源码运行

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m backyard_visitor
```

## 测试与构建

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/build_macos.py
```

发布含更新功能的应用需使用 scripts/prepare_github_release.py。Sparkle 版本和官方归档校验值位于 packaging/update-config.json。私钥仅在本地 .release-secrets 中保存，不随源码或安装包分发。

代码采用 [GNU GPL v3.0](LICENSE)；第三方组件保留各自的许可。
