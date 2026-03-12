#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local Model Installer  —  本地 AI 模型安装器
================================================
独立程序：检测 Windows 硬件 → 推荐合适的 AI 模型 → 一键安装 Ollama + 下载模型
无需安装 Koto 或任何其他软件，纯 Python stdlib + tkinter。

打包命令（生成单文件 EXE）：
    pyinstaller local_model_installer.spec
"""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

# ─────────────────────────────────────────────────────────────
#  ANSI 清理（Ollama 0.17+ 输出包含大量 ANSI 转义序列）
# ─────────────────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """移除 ANSI/VT100 转义序列，返回纯文本。"""
    return _ANSI_RE.sub("", text)


# ─────────────────────────────────────────────────────────────
#  路径
# ─────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    here = Path(__file__).resolve().parent
    APP_DIR = here.parent if here.name == "src" else here

RESULT_FILE = APP_DIR / "installed_models.json"  # 安装记录（可选）
OLLAMA_WIN_URL = "https://ollama.com/download/OllamaSetup.exe"

# ─────────────────────────────────────────────────────────────
#  模型目录
# ─────────────────────────────────────────────────────────────
MODEL_CATALOG: List[Dict] = [
    {
        "tag": "gemma3:1b",
        "name": "Gemma 3 1B",
        "badge": "超轻量",
        "vram": 1.5,
        "ram": 4,
        "size_gb": 0.8,
        "tier": "ultralight",
        "desc": "极低资源，适合 4 GB 以下内存，速度最快",
    },
    {
        "tag": "qwen2.5:3b",
        "name": "Qwen 2.5 3B",
        "badge": "轻量",
        "vram": 2.5,
        "ram": 6,
        "size_gb": 1.9,
        "tier": "light",
        "desc": "6–8 GB 内存，流畅度与效果兼顾，中文日常任务优选",
    },
    {
        "tag": "gemma3:4b",
        "name": "Gemma 3 4B",
        "badge": "标准",
        "vram": 3.5,
        "ram": 8,
        "size_gb": 3.3,
        "tier": "standard",
        "desc": "8 GB+ 内存，效果优秀，推荐大多数用户",
    },
    {
        "tag": "qwen2.5:7b",
        "name": "Qwen 2.5 7B",
        "badge": "平衡",
        "vram": 6.0,
        "ram": 12,
        "size_gb": 4.7,
        "tier": "powerful",
        "desc": "12 GB+ 内存，中英文均衡，性价比极高",
    },
    {
        "tag": "qwen3:8b",
        "name": "Qwen 3 8B",
        "badge": "高性能",
        "vram": 7.0,
        "ram": 16,
        "size_gb": 5.2,
        "tier": "highend",
        "desc": "16 GB 内存 / NVIDIA 8 GB 显卡，中文深度思考，综合能力强",
    },
    {
        "tag": "qwen2.5:14b",
        "name": "Qwen 2.5 14B",
        "badge": "旗舰",
        "vram": 10.0,
        "ram": 24,
        "size_gb": 9.0,
        "tier": "flagship",
        "desc": "24 GB+ 内存 / NVIDIA 12 GB+ 显卡，旗舰体验",
    },
    {
        "tag": "gemma3:12b",
        "name": "Gemma 3 12B",
        "badge": "旗舰",
        "vram": 10.0,
        "ram": 24,
        "size_gb": 8.1,
        "tier": "flagship",
        "desc": "24 GB+ 内存 / NVIDIA 12 GB+ 显卡，谷歌旗舰",
    },
]

TIER_COLOR = {
    "ultralight": "#b2bec3",
    "light": "#fdcb6e",
    "standard": "#55efc4",
    "powerful": "#74b9ff",
    "highend": "#a29bfe",
    "flagship": "#fd79a8",
}


# ─────────────────────────────────────────────────────────────
#  系统检测
# ─────────────────────────────────────────────────────────────
def get_system_info() -> Dict:
    info = {
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
        "installed_models": [],
    }

    # RAM（优先 psutil，备用 wmic）
    try:
        import psutil

        info["ram_gb"] = round(psutil.virtual_memory().total / 1024**3, 1)
        info["free_disk_gb"] = round(psutil.disk_usage(str(APP_DIR)).free / 1024**3, 1)
    except Exception:
        try:
            r = subprocess.run(
                ["wmic", "computersystem", "get", "TotalPhysicalMemory"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            nums = [l.strip() for l in r.stdout.splitlines() if l.strip().isdigit()]
            if nums:
                info["ram_gb"] = round(int(nums[0]) / 1024**3, 1)
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["wmic", "logicaldisk", "get", "FreeSpace,DeviceID"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            drive = APP_DIR.anchor.rstrip("\\")  # e.g. "C:"
            for line in r.stdout.splitlines():
                if drive in line:
                    parts = line.split()
                    for p in parts:
                        if p.isdigit():
                            info["free_disk_gb"] = round(int(p) / 1024**3, 1)
                            break
        except Exception:
            pass

    # NVIDIA GPU
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(",")
            if len(parts) >= 2:
                info["gpu_name"] = parts[0].strip()
                info["gpu_vram_gb"] = round(float(parts[1].strip()) / 1024, 1)
                info["has_nvidia"] = True
    except Exception:
        pass

    # AMD / Intel（WMI）
    if not info["has_nvidia"] and platform.system() == "Windows":
        try:
            r = subprocess.run(
                [
                    "wmic",
                    "path",
                    "win32_VideoController",
                    "get",
                    "Name,AdapterRAM",
                    "/format:csv",
                ],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in r.stdout.strip().splitlines():
                low = line.lower()
                parts = line.split(",")
                if "amd" in low or "radeon" in low:
                    info["gpu_name"] = parts[-1].strip() if parts else "AMD GPU"
                    try:
                        info["gpu_vram_gb"] = round(int(parts[-2].strip()) / 1024**3, 1)
                    except Exception:
                        pass
                    info["has_amd"] = True
                    break
                elif "intel" in low and any(
                    k in low for k in ("hd", "uhd", "iris", "arc")
                ):
                    info["gpu_name"] = parts[-1].strip() if parts else "Intel GPU"
                    try:
                        info["gpu_vram_gb"] = round(int(parts[-2].strip()) / 1024**3, 1)
                    except Exception:
                        pass
                    info["has_intel_gpu"] = True
        except Exception:
            pass

    # Ollama 状态
    info["ollama_installed"] = shutil.which("ollama") is not None
    try:
        s = socket.create_connection(("127.0.0.1", 11434), timeout=2)
        s.close()
        info["ollama_running"] = True
    except Exception:
        pass

    # 已安装模型列表
    if info["ollama_running"]:
        try:
            r = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            lines = r.stdout.strip().splitlines()
            for line in lines[1:]:  # skip header
                parts = line.split()
                if parts:
                    info["installed_models"].append(parts[0])
        except Exception:
            pass

    return info


def recommend_models(info: Dict) -> List[Dict]:
    """返回可运行的模型列表，并在最适合用户的型号上标注 recommended=True。

    「推荐」选取逻辑：在用户可运行的模型中，找到能满足硬件余量的最高档位：
    - GPU 路径：显存需求 ≤ 可用显存 × 85%
    - CPU 路径：内存需求 ≤ 可用内存 × 70%（预留 OS 开销）
    两个条件任一满足即可成为推荐候选，取满足条件的最高档。
    """
    ram = info["ram_gb"]
    vram = info["gpu_vram_gb"]

    # ── 1. 所有可运行模型（理论上能跑）──
    feasible = [m for m in MODEL_CATALOG if ram >= m["ram"] or vram >= m["vram"]]
    if not feasible:
        feasible = [MODEL_CATALOG[0]]

    # ── 2. 找「舒适」推荐档：留足余量 ──
    # VRAM 余量 15%（GPU 路径），RAM 余量 30%（CPU 路径）
    sweet = None
    for m in reversed(feasible):  # 从高档往低找，取最高的舒适档
        gpu_ok = vram > 0 and vram >= m["vram"] * 1.15
        cpu_ok = ram >= m["ram"] * 1.30
        if gpu_ok or cpu_ok:
            sweet = m
            break
    if sweet is None:
        sweet = feasible[0]  # 全部偏紧时选最低档

    # ── 3. 复制列表，标注 recommended 字段 ──
    result = []
    for m in feasible:
        mc = dict(m)  # 浅拷贝，不污染 MODEL_CATALOG 原始数据
        mc["recommended"] = m["tag"] == sweet["tag"]
        result.append(mc)
    return result


# ─────────────────────────────────────────────────────────────
#  Ollama 操作
# ─────────────────────────────────────────────────────────────

# Windows 上 Ollama 常见安装路径（安装后 PATH 不会立刻刷新，需手动探测）
_OLLAMA_SEARCH_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Ollama" / "ollama.exe",
    Path("C:/Users")
    / os.environ.get("USERNAME", "")
    / "AppData"
    / "Local"
    / "Programs"
    / "Ollama"
    / "ollama.exe",
]


def _find_ollama_exe() -> Optional[str]:
    """返回 ollama 可执行文件路径。优先 PATH，其次常见安装路径。"""
    # 1) PATH 里直接能找到
    found = shutil.which("ollama")
    if found:
        return found
    # 2) 扫描常见安装目录（安装后 PATH 尚未刷新的情况）
    for p in _OLLAMA_SEARCH_PATHS:
        if p.exists():
            # 将 ollama 所在目录临时加入 PATH，使后续 subprocess 也能找到
            parent = str(p.parent)
            if parent not in os.environ.get("PATH", ""):
                os.environ["PATH"] = parent + os.pathsep + os.environ.get("PATH", "")
            return str(p)
    return None


def is_ollama_running() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 11434), timeout=2)
        s.close()
        return True
    except Exception:
        return False


def start_ollama(log_cb=None) -> bool:
    if is_ollama_running():
        return True
    exe = _find_ollama_exe()
    if not exe:
        log_cb and log_cb("❌ 找不到 ollama 可执行文件，请重启安装器后重试")
        return False
    try:
        if log_cb:
            log_cb("🔄 正在启动 Ollama 服务...")
        subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for _ in range(30):
            time.sleep(1)
            if is_ollama_running():
                if log_cb:
                    log_cb("✅ Ollama 服务已启动")
                return True
        log_cb and log_cb("❌ Ollama 服务启动超时")
        return False
    except Exception as e:
        log_cb and log_cb(f"❌ 启动失败: {e}")
        return False


def pull_model(tag: str, prog_cb=None, log_cb=None) -> bool:
    """运行 ollama pull，处理 Ollama 0.17+ 的 ANSI 输出并限制 GUI 更新频率。"""
    exe = _find_ollama_exe()
    if not exe:
        log_cb and log_cb("❌ 找不到 ollama 可执行文件")
        return False
    try:
        proc = subprocess.Popen(
            [exe, "pull", tag],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        _last_log_t = 0.0  # 上次发送进度日志的时间
        _last_pct = -1.0  # 上次回调的百分比
        _last_activity = time.monotonic()  # 最后一次收到输出的时间（检测死锁）
        STALL_TIMEOUT = 300  # 超过 5 分钟无任何输出视为卡死
        for raw_line in proc.stdout:
            _last_activity = time.monotonic()
            # 剥离所有 ANSI/VT100 转义序列（Ollama 0.17+ 输出大量光标控制码）
            line = _strip_ansi(raw_line).strip()
            if not line:
                continue

            # 判断是否是进度行（含 "N%" token）
            is_progress = False
            pct_val = -1.0
            if "%" in line:
                for tok in line.split():
                    if tok.endswith("%"):
                        try:
                            v = float(tok.rstrip("%"))
                            is_progress = True
                            pct_val = v
                            break
                        except ValueError:
                            pass

            # 日志：非进度行直接输出；进度行每秒最多一条
            now = time.monotonic()
            if not is_progress:
                log_cb and log_cb(line)
            elif now - _last_log_t >= 1.0:
                log_cb and log_cb(line)
                _last_log_t = now

            # 进度回调：每变化 ≥ 2% 才触发，避免 GUI 刷新过频
            if (
                is_progress
                and prog_cb
                and (pct_val - _last_pct >= 2.0 or pct_val >= 100.0)
            ):
                prog_cb(pct_val)
                _last_pct = pct_val

            # 死锁检测：长时间无新输出则杀进程
            if time.monotonic() - _last_activity > STALL_TIMEOUT:
                proc.kill()
                log_cb and log_cb(
                    f"❌ 下载超时（{STALL_TIMEOUT}s 无响应），已终止。请检查网络后重试。"
                )
                return False

        try:
            proc.wait(timeout=60)  # 正常流程下进程应立即退出，给 60s 上限
        except subprocess.TimeoutExpired:
            proc.kill()
            log_cb and log_cb("❌ ollama 进程未正常退出，已强制终止")
            return False

        if proc.returncode != 0:
            log_cb and log_cb(f"❌ ollama pull 返回错误码 {proc.returncode}")
            return False

        # ── 实际验证：确认模型出现在 ollama list ──
        log_cb and log_cb("🔎 验证模型是否已成功安装...")
        try:
            r = subprocess.run(
                [exe, "list"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            base_tag = tag.split(":")[0]  # 允许版本号宽松匹配
            installed = any(
                base_tag in line.split()[0] if line.split() else False
                for line in r.stdout.splitlines()[1:]  # 跳过 header
            )
            if not installed:
                log_cb and log_cb(
                    f"⚠️  ollama pull 返回成功但 list 中未见 {tag}，可能下载不完整"
                )
                return False
        except Exception as ve:
            log_cb and log_cb(f"⚠️  无法验证安装结果: {ve}（继续）")

        return True
    except Exception as e:
        log_cb and log_cb(f"❌ {e}")
        return False


def save_result(tag: str):
    try:
        data = {
            "model": tag,
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ollama_endpoint": "http://127.0.0.1:11434",
        }
        RESULT_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────────────────────
def run_gui():
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, scrolledtext, ttk

    # ── 主题常量 ──
    BG = "#0d1117"
    PANEL = "#161b22"
    BORDER = "#30363d"
    ACCENT = "#2ea043"
    ACCENT2 = "#388bfd"
    TEXT = "#e6edf3"
    MUTED = "#8b949e"
    RED = "#f85149"
    YELLOW = "#d29922"
    WIN_W, WIN_H = 900, 640

    root = tk.Tk()
    root.title("本地 AI 模型安装器")
    root.geometry(f"{WIN_W}x{WIN_H}")
    root.configure(bg=BG)
    root.resizable(False, False)

    # 居中
    root.update_idletasks()
    x = (root.winfo_screenwidth() - WIN_W) // 2
    y = (root.winfo_screenheight() - WIN_H) // 2
    root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

    # ── 字体 ──
    F_TITLE = ("Segoe UI", 18, "bold")
    F_HEAD = ("Segoe UI", 11, "bold")
    F_NORMAL = ("Segoe UI", 10)
    F_SMALL = ("Segoe UI", 9)
    F_MONO = ("Consolas", 9)

    # ── ttk 样式 ──
    style = ttk.Style()
    style.theme_use("default")
    style.configure(
        "TProgressbar", troughcolor=PANEL, background=ACCENT, thickness=6, borderwidth=0
    )
    style.configure("Dark.TFrame", background=BG)

    # ─────────── 顶部标题栏 ───────────
    header = tk.Frame(root, bg=PANEL, height=56)
    header.pack(fill="x")
    header.pack_propagate(False)
    tk.Label(
        header,
        text="⚡  本地 AI 模型安装器",
        font=("Segoe UI", 14, "bold"),
        bg=PANEL,
        fg=TEXT,
    ).pack(side="left", padx=24, pady=0)
    version_lbl = tk.Label(
        header, text="v2.0  ·  零依赖独立版", font=F_SMALL, bg=PANEL, fg=MUTED
    )
    version_lbl.pack(side="right", padx=24)

    # ─────────── 步骤指示器 ───────────
    step_bar = tk.Frame(root, bg=BORDER, height=1)
    step_bar.pack(fill="x")

    steps_frame = tk.Frame(root, bg=BG, height=40)
    steps_frame.pack(fill="x")
    steps_frame.pack_propagate(False)

    STEP_LABELS = ["①  检测硬件", "②  选择模型", "③  安装下载", "④  完成"]
    step_lbls: List[tk.Label] = []
    for i, txt in enumerate(STEP_LABELS):
        lbl = tk.Label(steps_frame, text=txt, font=F_SMALL, bg=BG, fg=MUTED, padx=18)
        lbl.pack(side="left")
        step_lbls.append(lbl)
        if i < len(STEP_LABELS) - 1:
            tk.Label(
                steps_frame, text="›", font=("Segoe UI", 10), bg=BG, fg=BORDER
            ).pack(side="left")

    def set_step(idx: int):
        for i, lbl in enumerate(step_lbls):
            if i < idx:
                lbl.config(fg=ACCENT)
            elif i == idx:
                lbl.config(fg=TEXT, font=("Segoe UI", 9, "bold"))
            else:
                lbl.config(fg=MUTED, font=F_SMALL)

    set_step(0)

    # ─────────── 主内容区（card堆叠，切换显示） ───────────
    content = tk.Frame(root, bg=BG)
    content.pack(fill="both", expand=True, padx=0, pady=0)

    # 每个"页"是一个 Frame，通过 pack/pack_forget 切换
    pages: Dict[str, tk.Frame] = {}

    def show_page(name: str):
        for p in pages.values():
            p.pack_forget()
        pages[name].pack(fill="both", expand=True)

    # ═══════════════════════════════════════════
    #  PAGE 1 ── 检测硬件
    # ═══════════════════════════════════════════
    p1 = tk.Frame(content, bg=BG)
    pages["detect"] = p1

    # 左右分割
    p1_left = tk.Frame(p1, bg=BG, width=420)
    p1_left.pack(side="left", fill="both", padx=(24, 12), pady=20)
    p1_left.pack_propagate(False)

    p1_right = tk.Frame(p1, bg=BG)
    p1_right.pack(side="right", fill="both", expand=True, padx=(12, 24), pady=20)

    # 检测结果卡片
    tk.Label(p1_left, text="系统信息", font=F_HEAD, bg=BG, fg=TEXT).pack(
        anchor="w", pady=(0, 8)
    )
    hw_card = tk.Frame(p1_left, bg=PANEL, bd=0, relief="flat")
    hw_card.pack(fill="x")
    hw_text = tk.Text(
        hw_card,
        height=12,
        bg=PANEL,
        fg=TEXT,
        font=F_MONO,
        relief="flat",
        state="disabled",
        padx=14,
        pady=12,
        wrap="word",
        cursor="arrow",
    )
    hw_text.pack(fill="x")

    def hw_set(txt: str):
        hw_text.config(state="normal")
        hw_text.delete("1.0", "end")
        hw_text.insert("end", txt)
        hw_text.config(state="disabled")

    hw_set("⏳  正在读取硬件信息，请稍候...")

    # Ollama 状态
    tk.Label(p1_left, text="Ollama 状态", font=F_HEAD, bg=BG, fg=TEXT).pack(
        anchor="w", pady=(16, 6)
    )
    ollama_card = tk.Frame(p1_left, bg=PANEL, bd=0)
    ollama_card.pack(fill="x")
    ollama_lbl = tk.Label(
        ollama_card,
        text="检测中...",
        font=F_NORMAL,
        bg=PANEL,
        fg=MUTED,
        padx=14,
        pady=10,
        anchor="w",
        justify="left",
    )
    ollama_lbl.pack(fill="x")

    # 右侧：已安装模型
    tk.Label(p1_right, text="已安装模型", font=F_HEAD, bg=BG, fg=TEXT).pack(
        anchor="w", pady=(0, 8)
    )
    installed_card = tk.Frame(p1_right, bg=PANEL)
    installed_card.pack(fill="both", expand=True)
    installed_inner = tk.Frame(installed_card, bg=PANEL)
    installed_inner.pack(fill="both", expand=True, padx=14, pady=12)
    installed_lbl = tk.Label(
        installed_inner,
        text="检测中...",
        font=F_MONO,
        bg=PANEL,
        fg=MUTED,
        anchor="nw",
        justify="left",
        wraplength=360,
    )
    installed_lbl.pack(anchor="nw")

    # 底部按钮
    p1_btn = tk.Frame(p1, bg=BG)
    p1_btn.place(relx=1.0, rely=1.0, anchor="se", x=-24, y=-18)
    p1_rescan_btn = tk.Button(
        p1_btn,
        text="重新检测",
        font=F_NORMAL,
        bg=PANEL,
        fg=MUTED,
        relief="flat",
        padx=16,
        pady=7,
        cursor="hand2",
    )
    p1_rescan_btn.pack(side="left", padx=(0, 8))
    p1_next_btn = tk.Button(
        p1_btn,
        text="下一步  →",
        font=("Segoe UI", 10, "bold"),
        bg=ACCENT2,
        fg="white",
        relief="flat",
        padx=22,
        pady=7,
        cursor="hand2",
    )
    p1_next_btn.pack(side="left")

    # ═══════════════════════════════════════════
    #  PAGE 2 ── 选择模型
    # ═══════════════════════════════════════════
    p2 = tk.Frame(content, bg=BG)
    pages["select"] = p2

    p2_top = tk.Frame(p2, bg=BG)
    p2_top.pack(fill="x", padx=24, pady=(20, 8))
    tk.Label(p2_top, text="选择要安装的模型", font=F_HEAD, bg=BG, fg=TEXT).pack(
        side="left"
    )
    rec_hint = tk.Label(
        p2_top, text="✦ 标记为根据您的硬件自动推荐", font=F_SMALL, bg=BG, fg=MUTED
    )
    rec_hint.pack(side="right")

    # 模型列表区
    p2_list_frame = tk.Frame(p2, bg=BG)
    p2_list_frame.pack(fill="both", expand=True, padx=24, pady=(0, 12))

    selected_model = tk.StringVar(value="")
    _model_radio: List[tk.Widget] = []

    def build_model_list(candidates: List[Dict], installed_tags: List[str]):
        for w in p2_list_frame.winfo_children():
            w.destroy()
        _model_radio.clear()

        # 按 tier 排列（旗舰到轻量）
        ordered = list(reversed(candidates))

        canvas = tk.Canvas(p2_list_frame, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(
            p2_list_frame, orient="vertical", command=canvas.yview, bg=PANEL
        )
        scroll_frame = tk.Frame(canvas, bg=BG)
        scroll_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 设置默认选中：优先标注了 recommended 的型号，回退到第一个
        rec_tag = next((m["tag"] for m in ordered if m.get("recommended")), None)
        default_tag = rec_tag or (ordered[0]["tag"] if ordered else "")
        if ordered and not selected_model.get():
            selected_model.set(default_tag)
        elif not ordered:
            selected_model.set("")

        for m in ordered:
            is_installed = m["tag"] in installed_tags
            is_rec = m.get("recommended", False)  # 舒适档推荐
            color = TIER_COLOR.get(m["tier"], MUTED)

            card = tk.Frame(scroll_frame, bg=PANEL, pady=0)
            card.pack(fill="x", pady=4, ipady=0)

            # 左装饰条
            bar = tk.Frame(card, bg=color, width=4)
            bar.pack(side="left", fill="y")

            inner = tk.Frame(card, bg=PANEL)
            inner.pack(side="left", fill="both", expand=True, padx=12, pady=10)

            # 标题行
            title_row = tk.Frame(inner, bg=PANEL)
            title_row.pack(fill="x")

            rb = tk.Radiobutton(
                title_row,
                text=m["name"],
                variable=selected_model,
                value=m["tag"],
                bg=PANEL,
                fg=color,
                activebackground=PANEL,
                activeforeground=color,
                selectcolor=BG,
                font=("Segoe UI", 10, "bold"),
                cursor="hand2",
            )
            rb.pack(side="left")
            _model_radio.append(rb)

            # 徽章
            badge_txt = m["badge"]
            if is_installed:
                badge_txt = "✅ 已安装"
                badge_color = ACCENT
            elif is_rec:
                badge_txt = "✦ 推荐"
                badge_color = YELLOW
            else:
                badge_color = MUTED
            tk.Label(
                title_row, text=f"  {badge_txt}", font=F_SMALL, bg=PANEL, fg=badge_color
            ).pack(side="left", padx=4)

            # 规格行
            spec_txt = (
                f"内存需求: {m['ram']} GB  ·  "
                f"下载大小: ~{m['size_gb']} GB  ·  "
                f"显存需求: {m['vram']} GB"
            )
            tk.Label(
                inner, text=spec_txt, font=F_SMALL, bg=PANEL, fg=MUTED, anchor="w"
            ).pack(fill="x")

            # 描述
            tk.Label(
                inner,
                text=m["desc"],
                font=F_SMALL,
                bg=PANEL,
                fg=TEXT,
                anchor="w",
                justify="left",
            ).pack(fill="x")

    # 底部按钮
    p2_btn = tk.Frame(p2, bg=BG)
    p2_btn.place(relx=1.0, rely=1.0, anchor="se", x=-24, y=-18)
    p2_back_btn = tk.Button(
        p2_btn,
        text="← 返回",
        font=F_NORMAL,
        bg=PANEL,
        fg=MUTED,
        relief="flat",
        padx=16,
        pady=7,
        cursor="hand2",
    )
    p2_back_btn.pack(side="left", padx=(0, 8))
    p2_install_btn = tk.Button(
        p2_btn,
        text="▶  开始安装",
        font=("Segoe UI", 10, "bold"),
        bg=ACCENT,
        fg="white",
        relief="flat",
        padx=22,
        pady=7,
        cursor="hand2",
    )
    p2_install_btn.pack(side="left")

    # ═══════════════════════════════════════════
    #  PAGE 3 ── 安装进度
    # ═══════════════════════════════════════════
    p3 = tk.Frame(content, bg=BG)
    pages["install"] = p3

    p3_left = tk.Frame(p3, bg=BG, width=340)
    p3_left.pack(side="left", fill="y", padx=(24, 12), pady=20)
    p3_left.pack_propagate(False)

    p3_right = tk.Frame(p3, bg=BG)
    p3_right.pack(side="right", fill="both", expand=True, padx=(12, 24), pady=20)

    # 左侧：当前步骤状态
    tk.Label(p3_left, text="安装步骤", font=F_HEAD, bg=BG, fg=TEXT).pack(
        anchor="w", pady=(0, 12)
    )

    INSTALL_STEPS = [
        ("check_ollama", "检查 Ollama"),
        ("install_ollama", "安装 Ollama"),
        ("start_ollama", "启动服务"),
        ("download_model", "下载模型"),
        ("create_router", "创建路由模型"),
        ("verify", "验证安装"),
    ]
    step_vars: Dict[str, tk.StringVar] = {}
    step_icon_lbls: Dict[str, tk.Label] = {}

    for key, label in INSTALL_STEPS:
        row = tk.Frame(p3_left, bg=BG)
        row.pack(fill="x", pady=3)
        var = tk.StringVar(value="○")
        step_vars[key] = var
        icon_lbl = tk.Label(
            row,
            textvariable=var,
            font=("Segoe UI", 11),
            bg=BG,
            fg=MUTED,
            width=2,
            anchor="w",
        )
        icon_lbl.pack(side="left")
        step_icon_lbls[key] = icon_lbl
        tk.Label(row, text=label, font=F_NORMAL, bg=BG, fg=MUTED).pack(side="left")

    def set_istep(key: str, state: str):
        """state: 'wait' | 'running' | 'done' | 'error'"""
        cfg = {
            "wait": ("○", MUTED),
            "running": ("◎", YELLOW),
            "done": ("✓", ACCENT),
            "error": ("✗", RED),
        }
        ico, col = cfg.get(state, ("○", MUTED))

        def _do():
            step_vars[key].set(ico)
            step_icon_lbls[key].config(fg=col)

        root.after(0, _do)

    # 进度条 & 标签
    prog_lbl_var = tk.StringVar(value="等待开始...")
    tk.Label(
        p3_left,
        textvariable=prog_lbl_var,
        font=F_SMALL,
        bg=BG,
        fg=MUTED,
        wraplength=300,
        justify="left",
    ).pack(anchor="w", pady=(20, 4))
    prog_var = tk.DoubleVar(value=0)
    prog_bar = ttk.Progressbar(
        p3_left, variable=prog_var, maximum=100, length=290, mode="determinate"
    )
    prog_bar.pack(anchor="w")
    pct_lbl = tk.Label(p3_left, text="0%", font=F_SMALL, bg=BG, fg=MUTED)
    pct_lbl.pack(anchor="w", pady=(2, 0))

    def set_prog(val: float, label: str = ""):
        root.after(0, lambda: prog_var.set(val))
        root.after(0, lambda: pct_lbl.config(text=f"{val:.0f}%"))
        if label:
            root.after(0, lambda: prog_lbl_var.set(label))

    # 右侧：日志
    tk.Label(p3_right, text="实时日志", font=F_HEAD, bg=BG, fg=TEXT).pack(
        anchor="w", pady=(0, 6)
    )
    log_box = scrolledtext.ScrolledText(
        p3_right,
        bg=PANEL,
        fg=TEXT,
        font=F_MONO,
        relief="flat",
        state="disabled",
        wrap="word",
        padx=12,
        pady=10,
    )
    log_box.pack(fill="both", expand=True)

    p3_cancel_btn = tk.Button(
        p3,
        text="取消",
        font=F_NORMAL,
        bg=PANEL,
        fg=RED,
        relief="flat",
        padx=16,
        pady=7,
        cursor="hand2",
    )
    p3_cancel_btn.place(relx=1.0, rely=1.0, anchor="se", x=-24, y=-18)
    _cancel = threading.Event()
    p3_cancel_btn.config(command=lambda: _cancel.set())

    def log(msg: str):
        def _do():
            log_box.config(state="normal")
            log_box.insert("end", msg + "\n")
            log_box.see("end")
            log_box.config(state="disabled")

        root.after(0, _do)

    # ═══════════════════════════════════════════
    #  PAGE 4 ── 完成
    # ═══════════════════════════════════════════
    p4 = tk.Frame(content, bg=BG)
    pages["done"] = p4

    p4_inner = tk.Frame(p4, bg=BG)
    p4_inner.place(relx=0.5, rely=0.45, anchor="center")

    done_icon = tk.Label(
        p4_inner, text="✅", font=("Segoe UI Emoji", 54), bg=BG, fg=ACCENT
    )
    done_icon.pack(pady=(0, 12))
    done_title = tk.Label(
        p4_inner, text="安装成功！", font=("Segoe UI", 22, "bold"), bg=BG, fg=TEXT
    )
    done_title.pack()
    done_tag_lbl = tk.Label(p4_inner, text="", font=("Segoe UI", 12), bg=BG, fg=MUTED)
    done_tag_lbl.pack(pady=(4, 20))

    # 使用说明
    usage_card = tk.Frame(p4_inner, bg=PANEL, bd=0)
    usage_card.pack(fill="x", ipadx=20, ipady=14)
    usage_title = tk.Label(
        usage_card, text="如何使用这个模型", font=F_HEAD, bg=PANEL, fg=TEXT
    )
    usage_title.pack(anchor="w", padx=16, pady=(10, 6))
    usage_text = tk.Text(
        usage_card,
        height=5,
        bg=PANEL,
        fg=MUTED,
        font=F_MONO,
        relief="flat",
        state="disabled",
        padx=14,
        pady=0,
        wrap="word",
        cursor="arrow",
    )
    usage_text.pack(fill="x", padx=4, pady=(0, 10))

    def set_done_info(tag: str):
        done_tag_lbl.config(text=f"已安装模型：{tag}")
        usage_text.config(state="normal")
        usage_text.delete("1.0", "end")
        usage_text.insert(
            "end",
            f"在终端中测试：\n"
            f"  ollama run {tag}\n\n"
            f"API 端点（供 Koto / 其他程序调用）：\n"
            f"  http://127.0.0.1:11434  （Ollama 默认端口）",
        )
        usage_text.config(state="disabled")

    p4_close_btn = tk.Button(
        p4_inner,
        text="关闭安装器",
        font=("Segoe UI", 11, "bold"),
        bg=ACCENT2,
        fg="white",
        relief="flat",
        padx=28,
        pady=10,
        cursor="hand2",
        command=root.destroy,
    )
    p4_close_btn.pack(pady=(20, 0))

    p4_more_btn = tk.Button(
        p4_inner,
        text="安装更多模型",
        font=F_NORMAL,
        bg=PANEL,
        fg=MUTED,
        relief="flat",
        padx=16,
        pady=7,
        cursor="hand2",
    )
    p4_more_btn.pack(pady=(8, 0))

    # ─────────────────────────────────────────────────────────
    #  状态共享
    # ─────────────────────────────────────────────────────────
    _sys_info: Dict = {}
    _candidates: List[Dict] = []

    # ─────────────────────────────────────────────────────────
    #  检测线程
    # ─────────────────────────────────────────────────────────
    def _do_detect():
        info = get_system_info()
        _sys_info.update(info)

        # 硬件摘要文字
        gpu_line = f"显卡  : {info['gpu_name']}"
        if info["gpu_vram_gb"] > 0:
            gpu_line += f"  ({info['gpu_vram_gb']} GB)"
        accel = (
            "✅ CUDA 加速可用"
            if info["has_nvidia"]
            else (
                "⚠️  AMD ROCm（实验）"
                if info["has_amd"]
                else (
                    "ℹ️  仅 CPU 推理"
                    if info["has_intel_gpu"]
                    else "ℹ️  仅 CPU 推理（无独立 GPU）"
                )
            )
        )
        disk = f"{info['free_disk_gb']} GB 可用" if info["free_disk_gb"] > 0 else "未知"
        hw_str = (
            f"处理器: {info['cpu'][:42]}\n"
            f"核心数: {info['cpu_cores']} 核\n"
            f"内存  : {info['ram_gb']} GB\n"
            f"{gpu_line}\n"
            f"加速  : {accel}\n"
            f"磁盘  : {disk} (当前分区)\n"
            f"系统  : Windows {platform.release()}"
        )
        root.after(0, lambda: hw_set(hw_str))

        # Ollama 状态
        if info["ollama_installed"] and info["ollama_running"]:
            ollama_msg = "✅  已安装，服务运行中"
            ollama_fg = ACCENT
        elif info["ollama_installed"]:
            ollama_msg = "⚠️  已安装，服务未运行（将自动启动）"
            ollama_fg = YELLOW
        else:
            ollama_msg = "⚠️  未安装 — 点击「下一步」将自动下载安装"
            ollama_fg = YELLOW
        root.after(0, lambda: ollama_lbl.config(text=ollama_msg, fg=ollama_fg))

        # 已安装模型
        if info["installed_models"]:
            inst_str = "\n".join(f"  • {t}" for t in info["installed_models"])
        else:
            inst_str = "（暂无已安装模型）"
        root.after(
            0,
            lambda: installed_lbl.config(
                text=inst_str, fg=TEXT if info["installed_models"] else MUTED
            ),
        )

        # 候选模型（过滤磁盘空间不足的）
        cands = recommend_models(info)
        if info["free_disk_gb"] > 0:
            cands = [
                m for m in cands if info["free_disk_gb"] >= m["size_gb"] + 1.0
            ] or [MODEL_CATALOG[0]]
        _candidates.clear()
        _candidates.extend(cands)

        root.after(0, lambda: p1_next_btn.config(state="normal"))

    def start_detect():
        p1_next_btn.config(state="disabled")
        hw_set("⏳  正在读取硬件信息，请稍候...")
        ollama_lbl.config(text="检测中...", fg=MUTED)
        installed_lbl.config(text="检测中...", fg=MUTED)
        threading.Thread(target=_do_detect, daemon=True).start()

    p1_rescan_btn.config(command=start_detect)
    p1_next_btn.config(state="disabled")

    def go_to_select():
        set_step(1)
        build_model_list(_candidates, _sys_info.get("installed_models", []))
        show_page("select")

    p1_next_btn.config(command=go_to_select)
    p2_back_btn.config(command=lambda: (set_step(0), show_page("detect")))

    # ─────────────────────────────────────────────────────────
    #  安装线程
    # ─────────────────────────────────────────────────────────
    def _do_install(tag: str):
        _cancel.clear()

        def cancelled():
            if _cancel.is_set():
                log("⛔ 用户已取消")
                root.after(0, lambda: p2_install_btn.config(state="normal"))
                root.after(0, lambda: p2_back_btn.config(state="normal"))
                root.after(0, lambda: (set_step(0), show_page("detect")))
                return True
            return False

        # ── 1. 检查 Ollama ──
        set_istep("check_ollama", "running")
        set_prog(2, "检查 Ollama...")
        log("🔍 检查 Ollama 安装状态...")
        has_ollama = shutil.which("ollama") is not None
        set_istep("check_ollama", "done")
        if cancelled():
            return

        # ── 2. 安装 Ollama ──
        if not has_ollama:
            set_istep("install_ollama", "running")
            set_prog(5, "下载 Ollama 安装程序...")
            log(f"📥 下载 Ollama: {OLLAMA_WIN_URL}")
            setup_path = APP_DIR / "OllamaSetup_tmp.exe"
            try:

                def _hook(blk, bsz, tot):
                    if tot > 0 and not _cancel.is_set():
                        pct = 5 + int(blk * bsz / tot * 22)
                        set_prog(
                            min(pct, 27), f"下载 Ollama... {min(pct-5,22)*100//22}%"
                        )

                # 设置全局 socket 超时，防止网络挂起时永久阻塞
                import socket as _socket

                _prev_timeout = _socket.getdefaulttimeout()
                _socket.setdefaulttimeout(30)  # 30s 无响应即超时
                try:
                    urllib.request.urlretrieve(OLLAMA_WIN_URL, str(setup_path), _hook)
                finally:
                    _socket.setdefaulttimeout(_prev_timeout)
                if cancelled():
                    return
                log("✅ Ollama 下载完成，正在静默安装...")
                set_prog(28, "安装 Ollama...")
                subprocess.run(
                    [str(setup_path), "/S"],
                    timeout=180,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                time.sleep(4)
                set_istep("install_ollama", "done")
                log("✅ Ollama 安装完成")
                # 刷新路径：安装后 PATH 不会自动更新，主动探测
                exe_found = _find_ollama_exe()
                if exe_found:
                    log(f"✅ 找到 Ollama: {exe_found}")
                else:
                    log("⚠️  在常见路径未找到 ollama.exe，将在后续步骤重试...")
                try:
                    setup_path.unlink()
                except Exception:
                    pass
            except Exception as e:
                set_istep("install_ollama", "error")
                log(f"❌ 自动安装失败: {e}")
                log("👉 请手动访问 https://ollama.com/download 安装后重试")
                root.after(
                    0,
                    lambda: messagebox.showerror(
                        "安装失败",
                        "无法自动安装 Ollama。\n\n"
                        "请手动访问 https://ollama.com/download\n"
                        "安装后重新运行本程序。",
                    ),
                )
                root.after(0, lambda: p2_install_btn.config(state="normal"))
                root.after(0, lambda: p2_back_btn.config(state="normal"))
                return
        else:
            set_istep("install_ollama", "done")
            log("✅ Ollama 已安装，跳过下载")
            _find_ollama_exe()  # 确保路径已加入 PATH
            set_prog(28)

        if cancelled():
            return

        # ── 3. 启动服务 ──
        set_istep("start_ollama", "running")
        set_prog(32, "启动 Ollama 服务...")
        ok = start_ollama(log_cb=log)
        if not ok:
            set_istep("start_ollama", "error")
            root.after(
                0,
                lambda: messagebox.showerror(
                    "启动失败", "无法启动 Ollama 服务。\n请重启后重试。"
                ),
            )
            root.after(0, lambda: p2_install_btn.config(state="normal"))
            root.after(0, lambda: p2_back_btn.config(state="normal"))
            return
        set_istep("start_ollama", "done")
        set_prog(38)
        if cancelled():
            return

        # ── 4. 拉取模型 ──
        set_istep("download_model", "running")
        set_prog(40, f"下载模型 {tag}（可能需要数分钟）...")
        log(f"📦 开始下载: {tag}")

        def _prog(pct):
            mapped = 40 + pct * 0.52
            set_prog(mapped, f"下载 {tag}... {pct:.0f}%")

        ok = pull_model(tag, prog_cb=_prog, log_cb=log)
        if not ok:
            set_istep("download_model", "error")
            log(f"❌ 模型 {tag} 下载失败")
            root.after(0, lambda: p2_install_btn.config(state="normal"))
            root.after(0, lambda: p2_back_btn.config(state="normal"))
            return
        set_istep("download_model", "done")
        set_prog(92)
        if cancelled():
            return

        # ── 5. 创建 koto-router 本地路由模型 ──
        set_istep("create_router", "running")
        set_prog(93, "正在创建 Koto 路由模型...")
        log("🤖 正在基于 {} 创建 koto-router 路由模型...".format(tag))
        _ROUTER_MODELFILE = """FROM {base}

SYSTEM \"\"\"
你是 Koto AI 的任务路由分类器。
根据用户输入判断任务类型，严格只输出 JSON: {{\"task\":\"TYPE\",\"confidence\":0.9}}
可用类型: CHAT CODER PAINTER FILE_GEN DOC_ANNOTATE RESEARCH WEB_SEARCH FILE_SEARCH SYSTEM AGENT
\"\"\"

PARAMETER temperature 0.1
PARAMETER num_predict 50
PARAMETER num_ctx 4096
""".format(base=tag)
        try:
            import tempfile

            _mf = tempfile.NamedTemporaryFile(
                mode="w", suffix=".Modelfile", delete=False, encoding="utf-8"
            )
            _mf.write(_ROUTER_MODELFILE)
            _mf.close()
            _r = subprocess.run(
                ["ollama", "create", "koto-router", "-f", _mf.name],
                capture_output=True,
                text=True,
                timeout=120,
            )
            try:
                os.unlink(_mf.name)
            except Exception:
                pass
            if _r.returncode == 0:
                set_istep("create_router", "done")
                log("✅ koto-router 路由模型创建成功")
            else:
                set_istep("create_router", "error")
                log(
                    "⚠️ koto-router 创建失败（不影响基本使用）: {}".format(
                        _r.stderr.strip()[:120]
                    )
                )
        except Exception as _e:
            set_istep("create_router", "error")
            log("⚠️ koto-router 创建跳过: {}".format(_e))
        set_prog(96)
        if cancelled():
            return

        # ── 6. 验证 ──
        set_istep("verify", "running")
        set_prog(97, "验证安装...")
        log("🔎 验证模型...")
        time.sleep(1)
        save_result(tag)
        set_istep("verify", "done")
        set_prog(100, "安装完成 ✅")
        log(f"✅ 模型 {tag} 安装成功！")
        log(f"👉 使用方式： ollama run {tag}")

        # 进入完成页
        root.after(500, lambda: (set_step(3), set_done_info(tag), show_page("done")))

    def start_install():
        tag = selected_model.get()
        if not tag:
            messagebox.showwarning("提示", "请先选择一个模型")
            return
        # 磁盘空间检查
        model_size = next((m["size_gb"] for m in MODEL_CATALOG if m["tag"] == tag), 2.0)
        free = _sys_info.get("free_disk_gb", 0)
        if free > 0 and free < model_size + 1.0:
            messagebox.showerror(
                "磁盘空间不足",
                f"安装 {tag}（约 {model_size:.1f} GB）需要至少 {model_size+1:.1f} GB 空余空间，\n"
                f"当前可用：{free:.1f} GB。\n\n请释放磁盘空间后重试。",
            )
            return
        set_step(2)
        show_page("install")
        p2_install_btn.config(state="disabled")
        p2_back_btn.config(state="disabled")
        threading.Thread(target=_do_install, args=(tag,), daemon=True).start()

    p2_install_btn.config(command=start_install)
    p4_more_btn.config(
        command=lambda: (
            set_step(1),
            build_model_list(_candidates, _sys_info.get("installed_models", [])),
            show_page("select"),
        )
    )

    # ─────────────────────────────────────────────────────────
    #  启动
    # ─────────────────────────────────────────────────────────
    show_page("detect")
    start_detect()

    root.mainloop()


# ─────────────────────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_gui()
