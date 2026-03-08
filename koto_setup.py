#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
koto_setup.py — Koto 启动入口（带首次设置向导）

打包后产生的 EXE 入口：
  1. 首次运行 → 弹出 API 密钥配置向导
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
    here = Path(__file__).resolve().parent
    APP_ROOT = here.parent if here.name == "src" else here
    BUNDLE_DIR = APP_ROOT

# 确保 BUNDLE_DIR（包含所有 py/资源）在导入路径最前
if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(1, str(APP_ROOT))

# 必要目录
for _d in ["logs", "chats", "config", "workspace", "assets"]:
    (APP_ROOT / _d).mkdir(parents=True, exist_ok=True)

# ── API 密钥配置向导 ───────────────────────────────────
def _show_api_setup_wizard(initial_status: str = "") -> dict:
    """显示 Gemini API 密钥配置弹窗，返回 True 表示用户完成配置，False 表示取消"""
    import tkinter as tk
    from tkinter import font as tkfont

    result = {"key": None, "base": "", "cancelled": False}

    root = tk.Tk()
    root.title("Koto 初始化配置")
    root.resizable(False, False)

    # ── 颜色常量 ──
    BG      = "#05080f"
    BG2     = "#0b111d"
    BG3     = "#111a2a"
    ACCENT  = "#63c6ff"
    TEXT    = "#e8eefc"
    TEXT2   = "#9fb3d1"
    BORDER  = "#1e2d45"
    SUCCESS = "#76f7d4"
    DANGER  = "#ef6b6b"

    root.configure(bg=BG)

    # ── 让窗口居中 ──
    W, H = 480, 560
    root.geometry(f"{W}x{H}")
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{W}x{H}+{(sw - W)//2}+{(sh - H)//2}")

    # 固定在最顶层
    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))

    # ── 尝试设置图标 ──
    try:
        ico = APP_ROOT / "assets" / "koto_icon.ico"
        if not ico.exists():
            ico = BUNDLE_DIR / "assets" / "koto_icon.ico"
        if ico.exists():
            root.iconbitmap(str(ico))
    except Exception:
        pass

    # ── Fonts ──
    f_title  = tkfont.Font(family="Microsoft YaHei UI", size=16, weight="bold")
    f_sub    = tkfont.Font(family="Microsoft YaHei UI", size=10)
    f_label  = tkfont.Font(family="Microsoft YaHei UI", size=9, weight="bold")
    f_input  = tkfont.Font(family="Consolas", size=11)
    f_hint   = tkfont.Font(family="Microsoft YaHei UI", size=8)
    f_btn    = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")
    f_step   = tkfont.Font(family="Microsoft YaHei UI", size=8)

    # ── 顶部品牌区 ──
    top = tk.Frame(root, bg=BG2, height=90)
    top.pack(fill="x")
    top.pack_propagate(False)

    tk.Label(top, text="Koto  言", font=f_title, bg=BG2, fg=ACCENT).pack(pady=(18, 2))
    tk.Label(top, text="AI 助手 · 首次启动配置", font=f_sub, bg=BG2, fg=TEXT2).pack()

    # ── 分隔线 ──
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x")

    # ── 主内容区 ──
    body = tk.Frame(root, bg=BG, padx=28, pady=20)
    body.pack(fill="both", expand=True)

    # ── 步骤说明 ──
    steps_frame = tk.Frame(body, bg=BG3, padx=12, pady=10)
    steps_frame.pack(fill="x", pady=(0, 16))
    tk.Label(steps_frame, text="如何获取 Gemini API 密钥：",
             font=f_step, bg=BG3, fg=TEXT2, anchor="w").pack(fill="x")
    steps = [
        "① 访问  https://aistudio.google.com/apikey",
        "② 登录 Google 账号",
        "③ 点击「Create API key」",
        "④ 复制密钥粘贴到下方输入框",
    ]
    for s in steps:
        tk.Label(steps_frame, text=s, font=f_step, bg=BG3,
                 fg=TEXT2, anchor="w").pack(fill="x", pady=1)

    # ── API Key 输入 ──
    tk.Label(body, text="Gemini API 密钥  *", font=f_label,
             bg=BG, fg=ACCENT, anchor="w").pack(fill="x", pady=(0, 4))

    key_var = tk.StringVar()
    key_frame = tk.Frame(body, bg=BORDER, padx=1, pady=1)
    key_frame.pack(fill="x", pady=(0, 4))
    key_inner = tk.Frame(key_frame, bg=BG2)
    key_inner.pack(fill="x")
    key_entry = tk.Entry(key_inner, textvariable=key_var, font=f_input,
                         bg=BG2, fg=TEXT, insertbackground=ACCENT,
                         relief="flat", show="•", bd=8)
    key_entry.pack(fill="x")

    # 显示/隐藏密钥
    show_var = tk.BooleanVar(value=False)
    def toggle_show():
        key_entry.config(show="" if show_var.get() else "•")
    tk.Checkbutton(body, text="显示密钥", variable=show_var,
                   command=toggle_show, font=f_hint,
                   bg=BG, fg=TEXT2, activebackground=BG,
                   activeforeground=TEXT, selectcolor=BG3,
                   relief="flat", bd=0).pack(anchor="w", pady=(0, 12))

    # ── 自定义 API 端点（可选）──
    tk.Label(body, text="自定义 API 端点（可选，中转代理用）", font=f_label,
             bg=BG, fg=TEXT2, anchor="w").pack(fill="x", pady=(0, 4))
    base_var = tk.StringVar()
    base_frame = tk.Frame(body, bg=BORDER, padx=1, pady=1)
    base_frame.pack(fill="x", pady=(0, 4))
    base_inner = tk.Frame(base_frame, bg=BG2)
    base_inner.pack(fill="x")
    tk.Entry(base_inner, textvariable=base_var, font=f_input,
             bg=BG2, fg=TEXT, insertbackground=ACCENT,
             relief="flat", bd=8).pack(fill="x")
    tk.Label(body, text="例: https://your-proxy.com/v1beta",
             font=f_hint, bg=BG, fg=TEXT2, anchor="w").pack(fill="x", pady=(0, 14))

    # ── 状态提示 ──
    status_var = tk.StringVar(value="")
    status_lbl = tk.Label(body, textvariable=status_var, font=f_hint,
                          bg=BG, fg=DANGER, anchor="w", wraplength=420)
    status_lbl.pack(fill="x", pady=(0, 8))

    # 如果携带了初始状态（例如：密钥失效提示），立即显示
    if initial_status:
        status_var.set(initial_status)
        status_lbl.config(fg=DANGER)

    # ── 按钮行 ──
    btn_row = tk.Frame(body, bg=BG)
    btn_row.pack(fill="x")

    def on_cancel():
        result["cancelled"] = True
        root.destroy()

    def on_confirm():
        raw_key = key_var.get().strip()
        if not raw_key:
            status_var.set("❌ 请输入 API 密钥")
            status_lbl.config(fg=DANGER)
            key_entry.focus_set()
            return
        if not (raw_key.startswith("AIza") and len(raw_key) >= 30):
            status_var.set("⚠️ 密钥格式看起来不对（应以 AIza 开头），确认继续？")
            status_lbl.config(fg="#ffb44a")
            confirm_btn.config(text="仍然保存", command=_save)
            return
        _save()

    def _save():
        raw_key = key_var.get().strip()
        base    = base_var.get().strip()
        result["key"]  = raw_key
        result["base"] = base
        status_var.set("✅ 保存成功，正在启动…")
        status_lbl.config(fg=SUCCESS)
        root.after(600, root.destroy)

    def on_test():
        raw_key = key_var.get().strip()
        if not raw_key:
            status_var.set("❌ 请先输入 API 密钥")
            status_lbl.config(fg=DANGER)
            return
        base = base_var.get().strip()
        status_var.set("⏳ 正在验证密钥…")
        status_lbl.config(fg=TEXT2)
        root.update()
        ok, msg = _validate_api_key(raw_key, base)
        if ok:
            status_var.set("✅ 密钥有效！可以保存")
            status_lbl.config(fg=SUCCESS)
        else:
            status_var.set(msg or "❌ 密钥验证失败")
            status_lbl.config(fg=DANGER)

    cancel_btn = tk.Button(btn_row, text="跳过", font=f_btn,
                           bg=BG3, fg=TEXT2, activebackground=BORDER,
                           relief="flat", bd=0, padx=18, pady=10,
                           cursor="hand2", command=on_cancel)
    cancel_btn.pack(side="left")

    test_btn = tk.Button(btn_row, text="测试密钥", font=f_btn,
                         bg=BG3, fg=TEXT2, activebackground=BORDER,
                         relief="flat", bd=0, padx=14, pady=10,
                         cursor="hand2", command=on_test)
    test_btn.pack(side="left", padx=(8, 0))

    confirm_btn = tk.Button(btn_row, text="保存并启动  →", font=f_btn,
                            bg=ACCENT, fg="#05080f", activebackground="#4db8f0",
                            relief="flat", bd=0, padx=18, pady=10,
                            cursor="hand2", command=on_confirm)
    confirm_btn.pack(side="right")

    # Enter 键确认
    root.bind("<Return>", lambda e: on_confirm())
    root.bind("<Escape>", lambda e: on_cancel())

    key_entry.focus_set()
    root.mainloop()
    return result


def _write_gemini_config(api_key: str, api_base: str = ""):
    """将用户填写的 API 信息写入 gemini_config.env"""
    config_dir = APP_ROOT / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "gemini_config.env"

    lines = [
        "# Koto 配置文件（由启动向导自动生成）\n",
        f"GEMINI_API_KEY={api_key}\n",
        f"API_KEY={api_key}\n",
        f"GEMINI_API_BASE={api_base}\n",
        "FORCE_PROXY=auto\n",
    ]
    config_path.write_text("".join(lines), encoding="utf-8")


def _api_key_configured() -> bool:
    """检查是否已有有效的 API 密钥配置"""
    cfg = APP_ROOT / "config" / "gemini_config.env"
    if not cfg.exists():
        return False
    text = cfg.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY=") or line.startswith("API_KEY="):
            val = line.split("=", 1)[1].strip()
            if val and val not in ("your_api_key_here", "", "None"):
                return True
    return False


def _read_config_values() -> tuple:
    """从 gemini_config.env 读取 (api_key, api_base)"""
    cfg = APP_ROOT / "config" / "gemini_config.env"
    key, base = "", ""
    if not cfg.exists():
        return key, base
    for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY=") and not key:
            key = line.split("=", 1)[1].strip()
        elif line.startswith("GEMINI_API_BASE=") and not base:
            base = line.split("=", 1)[1].strip()
    return key, base


def _validate_api_key(key: str, base: str = "") -> tuple:
    """向 Gemini 服务器发送轻量请求验证密钥，返回 (ok: bool, msg: str)。
    超时 8 秒，网络异常时返回 (False, ⚠️ 提示) 而不是 (False, ❌ 无效)。"""
    try:
        import urllib.request
        import urllib.error
        base_url = (base.strip() or "https://generativelanguage.googleapis.com").rstrip("/")
        url = f"{base_url}/v1beta/models?key={key}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return (r.status == 200, "")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return (False, "❌ 密钥无效（API_KEY_INVALID）")
        if e.code == 403:
            return (False, "❌ 密钥被拒绝（权限不足）")
        return (False, f"❌ HTTP 错误 {e.code}")
    except Exception as e:
        msg = str(e)
        return (False, f"⚠️ 网络异常，无法验证（{msg[:60]}）")


def _run_setup_if_needed():
    """首次运行（或未配置 / API 密钥失效）时弹出配置向导"""
    # 支持命令行强制重新配置:  Koto.exe --setup
    force = "--setup" in sys.argv or "--reconfigure" in sys.argv

    wizard_status = ""  # 传给向导的初始提示（密钥失效时填充）

    if not force:
        if not _api_key_configured():
            pass  # 没有密钥 → 弹向导
        else:
            # 密钥存在 → 静默验证（网络正常时 ~1-3s）
            key, base = _read_config_values()
            ok, err_msg = _validate_api_key(key, base)
            if ok:
                return  # 验证通过，正常启动
            # 网络异常（⚠️前缀）→ 不强制弹向导，允许继续启动
            if err_msg.startswith("⚠️"):
                return
            # 密钥明确无效（❌前缀）→ 弹向导并带提示
            wizard_status = f"{err_msg} — 请重新填写密钥"

    try:
        res = _show_api_setup_wizard(initial_status=wizard_status)
        if res["key"]:
            _write_gemini_config(res["key"], res.get("base", ""))
            # 写入 setup_done 标志（同时兼容 model_downloader 的检测）
            import json
            flag = APP_ROOT / "config" / "model_setup_done.json"
            flag.write_text(json.dumps({"done": True, "version": 1}), encoding="utf-8")
        # 用户点「跳过」或关闭窗口 → 允许继续启动（功能受限，但不卡死）
    except Exception as e:
        try:
            (APP_ROOT / "logs" / "setup_error.log").write_text(
                f"Setup wizard error: {e}", encoding="utf-8"
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
    # ── Step 1: PyInstaller 冻结环境必须：在 main() 之前调用 freeze_support ──
    # 当 torch / transformers / datasets 等库通过 multiprocessing 产生子进程时，
    # 子进程会重新执行冻结的 exe。freeze_support() 检测到子进程标志后立即接管
    # 并退出，防止子进程再次执行 main() 打开新 Koto 窗口。
    import multiprocessing
    multiprocessing.freeze_support()

    # ── Step 2: Windows 全局 Mutex 单实例锁 ──────────────────────────────────
    # freeze_support() 处理 multiprocessing 工作进程，但无法拦截所有子进程类型。
    # 这里再加一道保险：用命名 Mutex 确保只有一个 Koto 主窗口实例在运行。
    # 这对所有子进程（包括 pystray、pythonnet/WebView2 产生的子进程）都有效。
    _mutex_handle = None
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes
            # 创建全局命名互斥量（不拥有它，只检测是否已存在）
            _MUTEX_NAME = "Global\\KotoMainWindowMutex_v1"
            _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
            _last_err = ctypes.windll.kernel32.GetLastError()
            if _last_err == 183:  # ERROR_ALREADY_EXISTS
                # Koto 已经在运行，将已有窗口前置并退出
                print("[Koto] 检测到已有实例在运行，退出本次启动。")
                # 尝试激活已有窗口
                try:
                    import win32gui
                    def _find_koto_window(hwnd, extra):
                        title = win32gui.GetWindowText(hwnd)
                        if "Koto" in title and win32gui.IsWindowVisible(hwnd):
                            win32gui.SetForegroundWindow(hwnd)
                            return False
                        return True
                    win32gui.EnumWindows(_find_koto_window, None)
                except Exception:
                    pass
                sys.exit(0)
        except Exception as _mutex_err:
            # Mutex 创建失败不影响程序运行，只是失去单实例保护
            print(f"[Koto] Mutex 创建失败（非致命）: {_mutex_err}")

    main()
