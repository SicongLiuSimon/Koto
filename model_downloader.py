#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Koto 本地模型下载器
检测系统硬件，推荐并下载适合的本地 AI 模型（通过 Ollama）
第一次运行时自动弹出，之后可从设置入口重新运行
"""

import os
import sys
import json
import subprocess
import threading
import platform
import time
import shutil
import socket
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# ───────── 路径解析 ─────────
if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).parent
else:
    here = Path(__file__).resolve().parent
    APP_ROOT = here.parent if here.name == "src" else here

SETUP_FLAG = APP_ROOT / "config" / "model_setup_done.json"

# ───────── 模型推荐表 ─────────
# 格式: (ollama_tag, display_name, vram_gb, ram_gb, description)
MODEL_CATALOG: List[Dict] = [
    {
        "tag": "gemma3:1b",
        "name": "Gemma 3 1B（超轻量）",
        "vram": 1.5,
        "ram": 4,
        "size_gb": 0.8,
        "desc": "极低资源消耗，适合 4GB 内存以下的老旧 PC，速度最快",
        "tier": "ultralight",
    },
    {
        "tag": "llama3.2:3b",
        "name": "LLaMA 3.2 3B（轻量）",
        "vram": 2.5,
        "ram": 6,
        "size_gb": 2.0,
        "desc": "适合 6-8GB 内存，平衡流畅度与效果，日常任务优选",
        "tier": "light",
    },
    {
        "tag": "gemma3:4b",
        "name": "Gemma 3 4B（标准）",
        "vram": 3.5,
        "ram": 8,
        "size_gb": 3.3,
        "desc": "8GB+ 内存，效果更佳，推荐大多数用户选择",
        "tier": "standard",
    },
    {
        "tag": "qwen2.5:7b",
        "name": "Qwen 2.5 7B（强力/中文优化）",
        "vram": 6,
        "ram": 12,
        "size_gb": 4.7,
        "desc": "12GB+ 内存，中文效果极佳，复杂任务首选",
        "tier": "powerful",
    },
    {
        "tag": "llama3.1:8b",
        "name": "LLaMA 3.1 8B（高性能）",
        "vram": 7,
        "ram": 16,
        "size_gb": 5.0,
        "desc": "16GB+ 内存或 NVIDIA 8GB 显卡，综合能力最强",
        "tier": "highend",
    },
    {
        "tag": "gemma3:12b",
        "name": "Gemma 3 12B（旗舰）",
        "vram": 10,
        "ram": 24,
        "size_gb": 8.1,
        "desc": "24GB 内存 / NVIDIA 12GB+ 显卡，旗舰本地体验",
        "tier": "flagship",
    },
]

OLLAMA_INSTALL_URL_WIN = "https://ollama.com/download/OllamaSetup.exe"

# ───────────────────────────────────────────
# 系统检测
# ───────────────────────────────────────────

def get_system_info() -> Dict:
    """检测系统硬件信息"""
    info: Dict = {
        "os": platform.system(),
        "os_ver": platform.version(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_cores": os.cpu_count() or 1,
        "ram_gb": 0.0,
        "free_disk_gb": 0.0,
        "gpu_name": "未检测到独立显卡",
        "gpu_vram_gb": 0.0,
        "has_nvidia": False,
        "has_amd": False,
        "has_intel_gpu": False,
        "ollama_installed": False,
        "ollama_running": False,
    }

    # RAM
    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        info["free_disk_gb"] = round(psutil.disk_usage(str(APP_ROOT)).free / (1024 ** 3), 1)
    except Exception:
        try:
            result = subprocess.run(
                ["wmic", "computersystem", "get", "TotalPhysicalMemory"],
                capture_output=True, text=True, timeout=5
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip().isdigit()]
            if lines:
                info["ram_gb"] = round(int(lines[0]) / (1024 ** 3), 1)
        except Exception:
            pass

    # NVIDIA GPU via nvidia-smi
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                info["gpu_name"] = parts[0].strip()
                info["gpu_vram_gb"] = round(float(parts[1].strip()) / 1024, 1)
                info["has_nvidia"] = True
    except Exception:
        pass

    # AMD / Intel GPU (WMI on Windows)
    if not info["has_nvidia"] and platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get",
                 "Name,AdapterRAM", "/format:csv"],
                capture_output=True, text=True, timeout=8
            )
            for line in result.stdout.strip().splitlines():
                low = line.lower()
                if "amd" in low or "radeon" in low:
                    parts = line.split(",")
                    info["gpu_name"] = parts[-1].strip() if parts else "AMD GPU"
                    try:
                        vram = int(parts[-2].strip())
                        info["gpu_vram_gb"] = round(vram / (1024 ** 3), 1)
                    except Exception:
                        pass
                    info["has_amd"] = True
                    break
                elif "intel" in low and ("hd" in low or "uhd" in low or "iris" in low or "arc" in low):
                    parts = line.split(",")
                    info["gpu_name"] = parts[-1].strip() if parts else "Intel GPU"
                    try:
                        vram = int(parts[-2].strip())
                        info["gpu_vram_gb"] = round(vram / (1024 ** 3), 1)
                    except Exception:
                        info["gpu_vram_gb"] = 0.0
                    info["has_intel_gpu"] = True
        except Exception:
            pass

    # Ollama detection
    ollama_path = shutil.which("ollama")
    info["ollama_installed"] = ollama_path is not None

    if info["ollama_installed"]:
        try:
            sock = socket.create_connection(("127.0.0.1", 11434), timeout=2)
            sock.close()
            info["ollama_running"] = True
        except Exception:
            pass

    return info


def recommend_models(info: Dict) -> List[Dict]:
    """根据硬件推荐模型列表（从适合到最优）"""
    ram = info["ram_gb"]
    vram = info["gpu_vram_gb"]
    effective = max(ram, vram * 1.5)  # GPU 加速可抵消部分 RAM 需求

    recommended = []
    for m in MODEL_CATALOG:
        if effective >= m["ram"] or vram >= m["vram"]:
            recommended.append(m)
    if not recommended:
        recommended = [MODEL_CATALOG[0]]  # 兜底最小模型
    return recommended


# ───────────────────────────────────────────
# Ollama 操作
# ───────────────────────────────────────────

def is_ollama_running() -> bool:
    try:
        sock = socket.create_connection(("127.0.0.1", 11434), timeout=2)
        sock.close()
        return True
    except Exception:
        return False


def start_ollama_server(log_callback=None) -> bool:
    """启动 Ollama 服务（后台）"""
    if is_ollama_running():
        return True
    try:
        if log_callback:
            log_callback("正在启动 Ollama 服务...")
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
        )
        for _ in range(30):
            time.sleep(1)
            if is_ollama_running():
                if log_callback:
                    log_callback("✅ Ollama 服务已启动")
                return True
        return False
    except Exception as e:
        if log_callback:
            log_callback(f"❌ 启动 Ollama 失败: {e}")
        return False


def pull_model(tag: str, progress_callback=None, log_callback=None) -> bool:
    """拉取 Ollama 模型，实时回报进度"""
    try:
        proc = subprocess.Popen(
            ["ollama", "pull", tag],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
        )
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if log_callback:
                log_callback(line)
            # 解析进度 "pulling xx%"
            if "%" in line and progress_callback:
                parts = line.split()
                for p in parts:
                    if p.endswith("%"):
                        try:
                            pct = float(p.rstrip("%"))
                            progress_callback(pct)
                        except ValueError:
                            pass
        proc.wait()
        return proc.returncode == 0
    except FileNotFoundError:
        if log_callback:
            log_callback("❌ 找不到 ollama 命令，请先安装 Ollama")
        return False
    except Exception as e:
        if log_callback:
            log_callback(f"❌ 拉取模型失败: {e}")
        return False


def save_setup_result(model_tag: str, mode: str = "local"):
    """保存安装结果到配置"""
    SETUP_FLAG.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "done": True,
        "mode": mode,
        "model": model_tag,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(SETUP_FLAG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 同步写入 user_settings.json
    settings_path = APP_ROOT / "config" / "user_settings.json"
    try:
        settings = {}
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        settings["local_model"] = model_tag
        settings["model_mode"] = mode
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def is_setup_done() -> bool:
    return SETUP_FLAG.exists()


# ───────────────────────────────────────────
# Tkinter GUI
# ───────────────────────────────────────────

def run_downloader_gui(on_complete=None):
    """运行模型下载器 GUI（阻塞直到关闭）"""
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext

    ACCENT = "#5865F2"
    BG = "#1a1a2e"
    CARD = "#16213e"
    TEXT = "#e0e0e0"
    GREEN = "#57F287"
    YELLOW = "#FEE75C"
    RED = "#ED4245"

    root = tk.Tk()
    root.title("Koto - 首次设置 · 本地模型")
    root.geometry("820x680")
    root.configure(bg=BG)
    root.resizable(False, False)

    # ── 状态变量 ──
    selected_model = tk.StringVar(value="")
    skip_local = tk.BooleanVar(value=False)
    _cancel_flag = threading.Event()
    _completed = [False]

    # ─── 顶部标题 ───
    hdr = tk.Frame(root, bg=ACCENT, height=56)
    hdr.pack(fill="x")
    hdr.pack_propagate(False)
    tk.Label(hdr, text="🗾  Koto 言 - 本地模型设置", font=("Segoe UI", 16, "bold"),
             bg=ACCENT, fg="white").pack(side="left", padx=20, pady=12)
    tk.Label(hdr, text="仅需设置一次", font=("Segoe UI", 10),
             bg=ACCENT, fg="#ccc").pack(side="right", padx=20)

    # ─── 主体两栏 ───
    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=18, pady=14)

    left = tk.Frame(body, bg=BG, width=380)
    left.pack(side="left", fill="both", padx=(0, 10))
    left.pack_propagate(False)

    right = tk.Frame(body, bg=BG)
    right.pack(side="right", fill="both", expand=True)

    # ─── 左：系统信息 ───
    tk.Label(left, text="系统检测", font=("Segoe UI", 11, "bold"),
             bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 6))

    info_frame = tk.Frame(left, bg=CARD, relief="flat", bd=0)
    info_frame.pack(fill="x", pady=(0, 12))

    sysinfo_text = tk.Text(info_frame, height=9, width=42,
                            bg=CARD, fg=TEXT, font=("Consolas", 9),
                            relief="flat", state="disabled", wrap="word",
                            padx=10, pady=8)
    sysinfo_text.pack(fill="x")

    def update_sysinfo(txt: str):
        sysinfo_text.config(state="normal")
        sysinfo_text.delete("1.0", "end")
        sysinfo_text.insert("end", txt)
        sysinfo_text.config(state="disabled")

    update_sysinfo("⏳ 正在检测系统...")

    # ─── 左：模型选择 ───
    tk.Label(left, text="推荐模型", font=("Segoe UI", 11, "bold"),
             bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 6))

    model_list_frame = tk.Frame(left, bg=BG)
    model_list_frame.pack(fill="both", expand=True)

    _radio_buttons: List = []

    def populate_models(candidates: List[Dict]):
        for w in model_list_frame.winfo_children():
            w.destroy()
        _radio_buttons.clear()
        if candidates:
            selected_model.set(candidates[-1]["tag"])  # 默认选最高推荐
        for m in reversed(candidates):
            tier_color = {
                "flagship": "#ff7675",
                "highend": "#fd79a8",
                "powerful": ACCENT,
                "standard": GREEN,
                "light": YELLOW,
                "ultralight": TEXT,
            }.get(m["tier"], TEXT)
            row = tk.Frame(model_list_frame, bg=CARD, pady=4, padx=8)
            row.pack(fill="x", pady=3)
            rb = tk.Radiobutton(
                row, text=m["name"],
                variable=selected_model, value=m["tag"],
                bg=CARD, fg=tier_color,
                selectcolor=BG,
                activebackground=CARD, activeforeground=tier_color,
                font=("Segoe UI", 9, "bold"),
            )
            rb.pack(anchor="w")
            tk.Label(row, text=f"  {m['desc']}\n  下载大小: ~{m['size_gb']} GB | 需内存: {m['ram']} GB",
                     bg=CARD, fg="#aaa", font=("Segoe UI", 8), justify="left",
                     wraplength=330).pack(anchor="w")
            _radio_buttons.append(rb)

        # 仅使用云端 Gemini API
        sep = tk.Frame(model_list_frame, bg=CARD, pady=4, padx=8)
        sep.pack(fill="x", pady=3)
        rb_skip = tk.Radiobutton(
            sep, text="☁️  仅使用 Gemini 云端 API（不下载本地模型）",
            variable=selected_model, value="__cloud__",
            bg=CARD, fg="#74b9ff",
            selectcolor=BG,
            activebackground=CARD, activeforeground="#74b9ff",
            font=("Segoe UI", 9),
        )
        rb_skip.pack(anchor="w")
        tk.Label(sep, text="  需要网络 + Gemini API Key，功能完整无限制",
                 bg=CARD, fg="#aaa", font=("Segoe UI", 8)).pack(anchor="w")

    # ─── 右：日志 & 进度 ───
    tk.Label(right, text="安装日志", font=("Segoe UI", 11, "bold"),
             bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 6))

    log_box = scrolledtext.ScrolledText(right, height=18, bg=CARD, fg=TEXT,
                                         font=("Consolas", 9), relief="flat",
                                         state="disabled", wrap="word")
    log_box.pack(fill="both", expand=True, pady=(0, 10))

    prog_var = tk.DoubleVar(value=0)
    prog_label = tk.StringVar(value="等待开始...")
    tk.Label(right, textvariable=prog_label, bg=BG, fg=TEXT,
             font=("Segoe UI", 9)).pack(anchor="w")
    prog_bar = ttk.Progressbar(right, variable=prog_var, maximum=100, length=340,
                                mode="determinate")
    prog_bar.pack(fill="x", pady=(2, 0))
    style = ttk.Style()
    style.theme_use("default")
    style.configure("TProgressbar", troughcolor=CARD, background=ACCENT, thickness=14)

    def log(msg: str, color: str = TEXT):
        def _do():
            log_box.config(state="normal")
            log_box.insert("end", msg + "\n")
            log_box.see("end")
            log_box.config(state="disabled")
        root.after(0, _do)

    def set_prog(val: float, label: str = ""):
        root.after(0, lambda: prog_var.set(val))
        if label:
            root.after(0, lambda: prog_label.set(label))

    # ─── 底部按钮 ───
    btn_frame = tk.Frame(root, bg=BG)
    btn_frame.pack(fill="x", padx=18, pady=12)

    btn_start = tk.Button(btn_frame, text="▶  开始安装", font=("Segoe UI", 11, "bold"),
                           bg=ACCENT, fg="white", relief="flat",
                           padx=28, pady=8, cursor="hand2")
    btn_start.pack(side="right", padx=(8, 0))

    btn_skip = tk.Button(btn_frame, text="跳过，稍后设置", font=("Segoe UI", 10),
                          bg="#555", fg=TEXT, relief="flat",
                          padx=16, pady=8, cursor="hand2")
    btn_skip.pack(side="right")

    status_label = tk.Label(btn_frame, text="", bg=BG, fg=GREEN,
                             font=("Segoe UI", 10))
    status_label.pack(side="left")

    # ─── 后台检测系统 ───
    _sys_info: Dict = {}

    def _detect_thread():
        info = get_system_info()
        _sys_info.update(info)

        gpu_line = f"显卡: {info['gpu_name']}"
        if info["gpu_vram_gb"] > 0:
            gpu_line += f" ({info['gpu_vram_gb']} GB VRAM)"

        # GPU 加速提示
        accel_hint = ""
        if info["has_nvidia"]:
            accel_hint = "  ✅ NVIDIA GPU — 支持 CUDA 加速\n"
        elif info["has_amd"]:
            accel_hint = "  ⚠️  AMD GPU — 可启用 ROCm 加速（实验性）\n"
        elif info["has_intel_gpu"]:
            accel_hint = "  ℹ️  Intel GPU — 仅 CPU 推理\n"
        else:
            accel_hint = "  ℹ️  无独立显卡 — 仅 CPU 推理\n"

        ollama_line = ("✅ Ollama 已安装" if info["ollama_installed"]
                       else "⚠️  Ollama 未安装（将自动安装）")

        disk_line = f"可用磁盘: {info['free_disk_gb']} GB" if info["free_disk_gb"] > 0 else "可用磁盘: 未知"

        txt = (
            f"CPU:  {info['cpu'][:38]}\n"
            f"核心: {info['cpu_cores']} 核\n"
            f"内存: {info['ram_gb']} GB\n"
            f"{gpu_line}\n"
            f"{accel_hint}"
            f"{disk_line}\n"
            f"系统: Windows {platform.release()}\n"
            f"{ollama_line}"
        )
        root.after(0, lambda: update_sysinfo(txt))

        candidates = recommend_models(info)
        # 磁盘空间不足时过滤模型
        if info["free_disk_gb"] > 0:
            candidates = [m for m in candidates if info["free_disk_gb"] >= m["size_gb"] + 1.0] or [MODEL_CATALOG[0]]
        root.after(0, lambda: populate_models(candidates))
        root.after(0, lambda: log(f"✅ 系统检测完成 — 内存 {info['ram_gb']} GB，"
                                    f"推荐 {len(candidates)} 个可用模型"))

    threading.Thread(target=_detect_thread, daemon=True).start()

    # ─── 安装流程（在线程中运行） ───
    def _install_thread():
        tag = selected_model.get()
        if not tag:
            root.after(0, lambda: messagebox.showwarning("提示", "请先选择一个模型"))
            root.after(0, lambda: btn_start.config(state="normal"))
            return

        if tag == "__cloud__":
            log("☁️  已选择仅使用云端 Gemini API 模式")
            set_prog(100, "云端模式无需下载")
            save_setup_result("__cloud__", mode="cloud")
            _completed[0] = True
            root.after(0, lambda: status_label.config(
                text="✅ 设置完成！正在启动 Koto...", fg=GREEN))
            root.after(0, lambda: btn_start.config(state="disabled"))
            time.sleep(1.5)
            root.after(0, root.destroy)
            return

        # ── 0. 磁盘空间快速检查 ──
        model_size_gb = next(
            (m["size_gb"] for m in MODEL_CATALOG if m["tag"] == tag), 2.0
        )
        free_gb = _sys_info.get("free_disk_gb", 0)
        if free_gb > 0 and free_gb < model_size_gb + 1.0:
            msg = (
                f"磁盘空间不足！\n\n"
                f"模型 {tag} 需要约 {model_size_gb + 1.0:.1f} GB 可用空间。\n"
                f"当前可用: {free_gb:.1f} GB。\n\n"
                "请释放磁盘空间后重试。"
            )
            root.after(0, lambda: messagebox.showerror("磁盘空间不足", msg))
            root.after(0, lambda: btn_start.config(state="normal"))
            return

        # ── 1. 安装 / 验证 Ollama ──
        set_prog(5, "检查 Ollama...")
        if not _sys_info.get("ollama_installed"):
            log("📥 Ollama 未安装，正在下载安装包...")
            set_prog(10, "下载 Ollama 安装程序...")
            installer_path = APP_ROOT / "config" / "OllamaSetup.exe"
            try:
                def _reporthook(count, block_size, total_size):
                    if total_size > 0:
                        pct = 10 + int(count * block_size / total_size * 20)
                        set_prog(min(pct, 30), f"下载 Ollama... {min(pct-10,20)*5}%")
                urllib.request.urlretrieve(
                    OLLAMA_INSTALL_URL_WIN, str(installer_path), _reporthook)
                log("✅ 下载完成，正在静默安装 Ollama...")
                set_prog(32, "安装 Ollama...")
                result = subprocess.run(
                    [str(installer_path), "/S"],
                    timeout=120
                )
                if result.returncode != 0:
                    log("⚠️  安装程序返回非零，尝试继续...")
                set_prog(38, "等待 Ollama 就绪...")
                time.sleep(5)
            except Exception as e:
                log(f"❌ 自动安装失败: {e}")
                log("📌 请手动从 https://ollama.com/download 安装 Ollama 后重试")
                root.after(0, lambda: messagebox.showerror(
                    "安装失败",
                    "无法自动安装 Ollama。\n"
                    "请手动访问 https://ollama.com/download 下载安装，\n"
                    "然后重新运行本设置程序。"
                ))
                root.after(0, lambda: btn_start.config(state="normal"))
                return

        # ── 2. 启动 Ollama 服务 ──
        set_prog(40, "启动 Ollama 服务...")
        ok = start_ollama_server(log_callback=log)
        if not ok:
            log("❌ 无法启动 Ollama 服务，请检查安装")
            root.after(0, lambda: btn_start.config(state="normal"))
            return

        # ── 3. 拉取模型 ──
        set_prog(45, f"正在下载模型 {tag}（可能需要几分钟）...")
        log(f"📥 开始下载: {tag}")

        def _prog_cb(pct):
            mapped = 45 + pct * 0.50
            set_prog(mapped, f"下载模型 {tag}... {pct:.0f}%")

        success = pull_model(
            tag,
            progress_callback=_prog_cb,
            log_callback=log,
        )

        if not success:
            log(f"❌ 模型 {tag} 下载失败")
            root.after(0, lambda: btn_start.config(state="normal"))
            return

        # ── 4. 保存配置 ──
        set_prog(96, "保存配置...")
        save_setup_result(tag, mode="local")
        set_prog(100, "✅ 安装完成！")
        log(f"✅ 模型 {tag} 安装成功")
        log("🚀 正在启动 Koto...")
        _completed[0] = True
        root.after(0, lambda: status_label.config(
            text="✅ 安装完成！正在启动 Koto...", fg=GREEN))
        root.after(0, lambda: btn_start.config(state="disabled"))
        time.sleep(1.5)
        root.after(0, root.destroy)

    def on_start():
        btn_start.config(state="disabled")
        status_label.config(text="安装中...", fg=YELLOW)
        threading.Thread(target=_install_thread, daemon=True).start()

    def on_skip():
        if messagebox.askyesno(
            "跳过设置",
            "跳过后 Koto 将以云端模式运行（需要 API Key）。\n"
            "您可以在设置中随时配置本地模型。\n\n确定要跳过吗？"
        ):
            save_setup_result("__cloud__", mode="cloud")
            _completed[0] = True
            root.destroy()

    btn_start.config(command=on_start)
    btn_skip.config(command=on_skip)

    root.mainloop()
    return _completed[0]


# ─── CLI 入口（供 koto_setup.py 调用）───
def maybe_run_setup():
    """如果尚未设置，运行下载器 GUI；返回 True 表示可以继续启动 Koto"""
    if is_setup_done():
        return True
    result = run_downloader_gui()
    return True  # 无论如何都继续（用户可跳过）


if __name__ == "__main__":
    run_downloader_gui()
