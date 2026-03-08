#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
koto_setup.py — Koto 启动入口（带首次设置向导）

打包后产生的 EXE 入口：
  1. 首次运行 → 弹出系统检测 + 本地模型下载器
  2. 后续运行 → 直接启动 Koto 桌面程序
"""

import os
import sys
import runpy
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────
if getattr(sys, "frozen", False):
    # PyInstaller 环境
    APP_ROOT = Path(sys.executable).parent
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    # koto_setup.py 位于 src/ 子目录，APP_ROOT 指向项目根目录
    APP_ROOT = Path(__file__).parent.parent
    BUNDLE_DIR = APP_ROOT

# 确保 BUNDLE_DIR（包含所有 py/资源）在导入路径最前
if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(1, str(APP_ROOT))

# 必要目录
for _d in ["logs", "chats", "config", "workspace", "assets"]:
    (APP_ROOT / _d).mkdir(parents=True, exist_ok=True)

# ── 设置向导 ──────────────────────────────────────────
def _run_setup_if_needed():
    """首次运行时弹出本地模型下载器"""
    setup_flag = APP_ROOT / "config" / "model_setup_done.json"
    
    # 支持命令行强制重新设置:  koto_setup.exe --setup
    force = "--setup" in sys.argv or "--reconfigure" in sys.argv

    if setup_flag.exists() and not force:
        return  # 已设置过，直接跳过

    try:
        from model_downloader import run_downloader_gui
        run_downloader_gui()
    except ImportError:
        # 打包后 model_downloader 以数据文件形式存在，用 runpy 加载
        dl_path = BUNDLE_DIR / "model_downloader.py"
        if dl_path.exists():
            ns = runpy.run_path(str(dl_path), run_name="__main__")
            if "run_downloader_gui" in ns:
                ns["run_downloader_gui"]()
        else:
            # 找不到下载器，跳过（不阻塞主程序启动）
            pass
    except Exception as e:
        # 下载器崩溃不影响主程序
        try:
            (APP_ROOT / "logs" / "setup_error.log").write_text(
                f"Setup error: {e}", encoding="utf-8"
            )
        except Exception:
            pass


# ── 主程序入口 ────────────────────────────────────────
def main():
    # Step 1: 运行设置向导（首次/强制模式）
    _run_setup_if_needed()

    # Step 2: 启动 Koto 桌面程序
    koto_main_path = BUNDLE_DIR / "koto_app.py"
    if koto_main_path.exists():
        # 开发模式：直接 runpy
        os.chdir(str(APP_ROOT))
        runpy.run_path(str(koto_main_path), run_name="__main__")
    else:
        # 打包后：koto_app 已编译进 exe，直接导入调用
        os.chdir(str(APP_ROOT))
        try:
            import koto_app
            koto_app.main()
        except Exception as e:
            # 崩溃时写日志并弹窗
            err_msg = f"Koto 启动失败:\n{e}"
            try:
                (APP_ROOT / "logs" / "crash.log").write_text(err_msg, encoding="utf-8")
            except Exception:
                pass
            try:
                import tkinter as tk
                from tkinter import messagebox
                _root = tk.Tk()
                _root.withdraw()
                messagebox.showerror("Koto 启动失败", err_msg)
                _root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    main()
