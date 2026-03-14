# -*- mode: python ; coding: utf-8 -*-
"""
local_model_installer.spec
打包本地模型安装器为独立 EXE（无需 Python 环境）

打包命令：
    pyinstaller local_model_installer.spec

输出位置：
    dist\LocalModelInstaller\LocalModelInstaller.exe
    （或单文件版，见下方 onefile 注释）
"""

import sys
from pathlib import Path

# ── 图标（可选） ──────────────────────────────────────────────
_icon = str(Path("src/assets/koto_icon.ico")) if Path("src/assets/koto_icon.ico").exists() else None

block_cipher = None

a = Analysis(
    ["src/local_model_installer.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # 如果有图标资源可加入：
        # ("assets/koto_icon.ico", "assets"),
    ],
    hiddenimports=[
        "tkinter",
        "tkinter.ttk",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
        "tkinter.font",
        "urllib.request",
        "urllib.error",
        "subprocess",
        "threading",
        "platform",
        "shutil",
        "socket",
        "json",
        "pathlib",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除 Koto 业务逻辑，严格精简
        "web", "app", "google", "flask",
        "torch", "transformers", "faiss",
        "langchain", "langgraph",
        "pandas", "numpy", "PIL",
        "psutil",          # 不依赖，用 wmic 兜底
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── 单文件 EXE（推荐：方便分发，解压稍慢） ──
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="LocalModelInstaller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无黑色终端窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    onefile=True,           # ← 单文件
)

# ── 如需目录版（启动更快，取消下方注释并注释掉上面 onefile EXE） ──
# exe = EXE(
#     pyz, a.scripts, [],
#     name="LocalModelInstaller",
#     debug=False, bootloader_ignore_signals=False,
#     strip=False, upx=True, console=False,
#     icon=_icon,
# )
# coll = COLLECT(
#     exe, a.binaries, a.zipfiles, a.datas, [],
#     name="LocalModelInstaller",
# )
