#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Koto 桌面应用 - 独立窗口版本
使用 pywebview 创建原生窗口，Flask 作为后端
无终端，完全独立运行
"""

import faulthandler
import os
import socket
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import psutil

KOTO_HOST = "127.0.0.1"
KOTO_PORT = int(os.environ.get("KOTO_PORT", "5000"))
FALLBACK_PORT = int(os.environ.get("KOTO_FALLBACK_PORT", "5001"))
STARTUP_TIMEOUT_SEC = int(os.environ.get("KOTO_STARTUP_TIMEOUT_SEC", "10"))

# 获取应用根目录和资源目录
if getattr(sys, "frozen", False):
    # PyInstaller打包后：
    # - APP_ROOT: exe所在目录（用于持久化数据：chats/、config/、workspace/等）
    # - BUNDLE_DIR: 临时解压目录（用于bundled资源：web/、assets/等）
    APP_ROOT = Path(sys.executable).parent
    BUNDLE_DIR = Path(sys._MEIPASS)

    # Fix pythonnet runtime path for pywebview's EdgeChromium backend in frozen environment
    # pythonnet needs to know where the Python runtime is located
    _internal_py = APP_ROOT / "internal" / "py"
    if _internal_py.exists():
        os.environ.setdefault(
            "PYTHONNET_PYDLL",
            str(
                _internal_py
                / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
            ),
        )
    # Alternative: Force pywebview to use EdgeChromium without pythonnet initialization issues
    os.environ.setdefault("PYWEBVIEW_GUI", "edgechromium")
else:
    here = Path(__file__).resolve().parent
    APP_ROOT = here.parent if here.name == "src" else here
    BUNDLE_DIR = APP_ROOT


# 图标资源目录：打包模式下在 _MEIPASS/assets/，源码模式下在 src/assets/
ASSETS_DIR = (
    BUNDLE_DIR if getattr(sys, "frozen", False) else APP_ROOT / "src"
) / "assets"

os.chdir(str(APP_ROOT))
sys.path.insert(0, str(BUNDLE_DIR))  # 确保能找到bundled的web模块

LOG_FILE = APP_ROOT / "logs" / "startup.log"
RUNTIME_LOG_FILE = (
    APP_ROOT / "logs" / f"runtime_{datetime.now().strftime('%Y%m%d')}.log"
)


class DualOutput:
    """同时输出到文件和控制台 - 使用持久文件句柄，避免每次 write 都 open/close"""

    def __init__(self, original_stream, log_file):
        self.original_stream = original_stream
        self.log_file = log_file
        self._file = None
        self._lock = threading.Lock()
        try:
            self._file = open(
                log_file, "a", encoding="utf-8", errors="ignore", buffering=1
            )
        except Exception:
            pass

    def write(self, message):
        try:
            self.original_stream.write(message)
            if self._file:
                with self._lock:
                    self._file.write(message)
        except Exception:
            pass

    def flush(self):
        try:
            self.original_stream.flush()
            if self._file:
                self._file.flush()
        except Exception:
            pass

    def close(self):
        try:
            if self._file:
                self._file.close()
                self._file = None
        except Exception:
            pass


def _redirect_output():
    """将 stdout/stderr 重定向到日志文件"""
    try:
        RUNTIME_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 写入分隔符
        with open(RUNTIME_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n\n{'='*30} New Session {datetime.now()} {'='*30}\n")

        sys.stdout = DualOutput(sys.stdout, RUNTIME_LOG_FILE)
        sys.stderr = DualOutput(sys.stderr, RUNTIME_LOG_FILE)
        print(f"Log redirected to {RUNTIME_LOG_FILE}")
    except Exception as e:
        print(f"Failed to redirect output: {e}")


_redirect_output()

# 持久化启动日志文件句柄，避免每次 _write_log 都 open/close（性能优化）
_startup_log_file = None
_startup_log_lock = threading.Lock()


def _get_startup_log():
    """懒加载并缓存启动日志文件句柄"""
    global _startup_log_file
    if _startup_log_file is None:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _startup_log_file = LOG_FILE.open("a", encoding="utf-8", buffering=1)
        except Exception:
            pass
    return _startup_log_file


def _write_log(message: str):
    """写入启动日志并同步打印，便于定位"未响应"原因"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}\n"
        f = _get_startup_log()
        if f:
            with _startup_log_lock:
                f.write(line)
        print(message)
    except Exception:
        # 日志失败不应阻塞启动
        pass


def _dump_threads(label: str = "thread-dump"):
    """将当前进程的线程栈写入日志，方便定位卡死位置"""
    try:
        f = _get_startup_log()
        if f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with _startup_log_lock:
                f.write(f"\n===== {label} {ts} =====\n")
                faulthandler.dump_traceback(file=f, all_threads=True)
    except Exception:
        traceback.print_exc()


def _terminate_stale_process_on_port(port: int, reason: str = "") -> bool:
    """如果端口被本机 pythonw 占用且无健康响应，尝试终止并释放端口
    优化：仅在端口确实被占用时才扫描网络连接，避免不必要的全量扫描"""
    killed = False
    try:
        # 快速预检：通过 socket 确认端口已被占用，否则直接跳过全量扫描
        _pre = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _pre.settimeout(0.1)
        _port_in_use = _pre.connect_ex(("127.0.0.1", port)) == 0
        _pre.close()
        if not _port_in_use:
            return False

        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.laddr
                and conn.laddr.port == port
                and conn.status == psutil.CONN_LISTEN
            ):
                pid = conn.pid
                if not pid:
                    continue
                try:
                    proc = psutil.Process(pid)
                    cmdline = " ".join(proc.cmdline()).lower()
                    if "koto_app.py" in cmdline or "pythonw" in proc.name().lower():
                        _write_log(f"⚠️ 终止占用 {port} 的进程 {pid}（{reason}）")
                        proc.kill()
                        time.sleep(0.5)  # 0.5s 通常足够进程退出
                        killed = True
                except Exception:
                    pass
    except Exception as e:
        _write_log(f"⚠️ 检查端口占用失败: {e}")
    return killed


def _wait_for_port(host: str, port: int, timeout_sec: int) -> bool:
    """等待端口监听就绪"""
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) == 0:
                sock.close()
                return True
            sock.close()
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _check_http_ok(url: str) -> bool:
    """检查 HTTP 是否可访问"""
    try:
        from urllib.request import ProxyHandler, build_opener, urlopen

        opener = build_opener(ProxyHandler({}))  # 禁用系统代理，避免被本地代理劫持误判
        with opener.open(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _find_available_port(host: str, start_port: int, max_tries: int = 20) -> int | None:
    """从 start_port 开始查找可用端口，返回端口号或 None。"""
    for port in range(start_port, start_port + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                if sock.connect_ex((host, port)) != 0:
                    return port
        except Exception:
            continue
    return None


def ensure_directories():
    """确保必要的目录存在"""
    dirs = [
        "workspace",
        "workspace/images",
        "workspace/documents",
        "workspace/code",
        "chats",
        "logs",
        "config",
    ]
    for d in dirs:
        (APP_ROOT / d).mkdir(exist_ok=True, parents=True)
    _write_log("✔ 目录检查完成")


def check_config():
    """检查配置文件"""
    config_file = APP_ROOT / "config" / "gemini_config.env"
    if not config_file.exists():
        config_file.parent.mkdir(exist_ok=True, parents=True)
        config_file.write_text(
            "# Koto Configuration\n"
            "API_KEY=your_api_key_here\n"
            "GEMINI_API_KEY=your_api_key_here\n"
        )
        _write_log("⚠️ 未检测到 gemini_config.env，已生成占位文件")
    else:
        _write_log("✔ 配置文件存在")


def ensure_dependencies():
    """检查桌面依赖是否已安装（用 find_spec 快速探测，不实际导入）"""
    import importlib.util
    missing = []
    if importlib.util.find_spec('webview') is None:
        missing.append("pywebview")
    if importlib.util.find_spec('pystray') is None or importlib.util.find_spec('PIL') is None:
        missing.append("pystray/pillow")

    if missing:
        auto_install = os.environ.get("KOTO_AUTO_INSTALL_DEPS", "0") == "1"
        if auto_install:
            os.system(
                f'"{sys.executable}" -m pip install pywebview pystray pillow --quiet'
            )
            _write_log(f"⚠️ 自动安装缺失依赖: {', '.join(missing)}")
            return True
        else:
            _write_log("⚠️ 缺少依赖: " + ", ".join(missing))
            _write_log("请先执行: pip install pywebview pystray pillow")
            return False
    _write_log("✔ 关键依赖就绪")
    return True


class VoiceAPI:
    """语音识别 API - 提供给前端调用
    注意：实际通过 Flask REST API 实现
    这个类仅作为占位符，保持兼容性
    """

    def __init__(self):
        pass

    def get_available_engines(self):
        """返回所有可用引擎（占位符）"""
        try:
            from web.voice_recognition import get_voice_recognizer

            recognizer = get_voice_recognizer()
            return recognizer.list_available_engines()
        except:
            return []


class WindowAPI:
    """窗口控制API - 提供给前端调用"""

    def __init__(self, window, base_url):
        self.window = window
        self.base_url = base_url.rstrip("/")
        self.is_mini_mode = False
        self.full_size = (1200, 800)
        self.full_pos = None
        self.mini_size = (320, 480)  # 适合高分辨率屏幕的迷你尺寸

    def switch_to_mini(self):
        """切换到迷你模式 - 固定在屏幕右下角"""
        if self.is_mini_mode:
            return {"success": True, "mode": "mini"}

        try:
            import ctypes

            user32 = ctypes.windll.user32
            # 使用真实屏幕分辨率（考虑DPI缩放）
            user32.SetProcessDPIAware()
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)

            # 保存当前位置和大小
            self.full_size = (self.window.width, self.window.height)

            # 迷你窗口：固定屏幕右侧（垂直居中）
            mini_w, mini_h = self.mini_size
            x = screen_w - mini_w - 20
            y = max(20, (screen_h - mini_h) // 2)

            # 先移动再调整大小，确保位置正确
            self.window.move(x, y)
            self.window.resize(mini_w, mini_h)
            self.window.on_top = True
            self.is_mini_mode = True

            # 加载迷你UI
            self.window.load_url(f"{self.base_url}/mini")

            return {
                "success": True,
                "mode": "mini",
                "size": [mini_w, mini_h],
                "pos": [x, y],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def switch_to_full(self):
        """切换到完整模式"""
        if not self.is_mini_mode:
            return {"success": True, "mode": "full"}

        try:
            import ctypes

            user32 = ctypes.windll.user32
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)

            # 恢复完整模式
            full_w, full_h = self.full_size
            x = (screen_w - full_w) // 2
            y = (screen_h - full_h) // 2

            self.window.on_top = False
            self.window.resize(full_w, full_h)
            self.window.move(x, y)
            self.is_mini_mode = False

            # 加载完整UI
            self.window.load_url(self.base_url)

            return {"success": True, "mode": "full"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_mode(self):
        """获取当前模式"""
        return {"mode": "mini" if self.is_mini_mode else "full"}

    def minimize(self):
        """最小化窗口"""
        self.window.minimize()

    def maximize(self):
        """最大化/还原窗口"""
        try:
            # pywebview没有直接的maximize，用toggle_fullscreen替代
            self.window.toggle_fullscreen()
        except:
            pass

    def close(self):
        """关闭窗口"""
        self.window.destroy()

    def open_url(self, url: str):
        """在系统默认浏览器中打开外部链接，防止 webview 导航离开 Koto"""
        import webbrowser
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return {"success": False, "error": "不允许的协议"}
            if not parsed.netloc:
                return {"success": False, "error": "无效的URL（缺少域名）"}
            webbrowser.open(url)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def _pre_check_syntax(filepath: str):
    """
    预检查 Python 文件语法，在 import 之前发现问题。
    返回 (True/False, error_message)
    """
    import ast

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename=filepath)
        return True, None
    except SyntaxError as e:
        error_msg = f"{e.msg} ({os.path.basename(filepath)}, line {e.lineno})"
        return False, error_msg
    except Exception as e:
        return False, str(e)


def _auto_fix_syntax(filepath: str, error_msg: str) -> bool:
    """
    尝试自动修复常见的语法错误。
    当前支持修复:
    1. f-string 表达式中的反斜杠 (Python < 3.12)
    2. 未闭合的括号/引号（简单情况）

    返回 True 如果做了修改，False 如果无法修复。
    """
    import re as re_mod
    import shutil

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return False

    fixed = False

    # 修复类型 1: f-string 表达式中的反斜杠
    # 错误信息形如: "f-string expression part cannot include a backslash"
    if "backslash" in error_msg.lower() and "f-string" in error_msg.lower():
        _write_log("🔧 检测到 f-string 反斜杠问题，尝试自动修复...")

        # 提取错误行号
        line_match = re_mod.search(r"line\s+(\d+)", error_msg)
        if line_match:
            error_line = int(line_match.group(1)) - 1  # 0-indexed
            if 0 <= error_line < len(lines):
                original_line = lines[error_line]

                # 策略: 将包含 \\n 的 f-string json.dumps 表达式拆分
                # 例如: yield f"data: {json.dumps({'message': f'xx\\nxx'})}\n\n"
                # 修复为: 先构建 msg 变量，再使用
                if "\\\\n" in original_line or "\\\\n" in original_line:
                    # 在当前行之前插入一个变量定义
                    indent = len(original_line) - len(original_line.lstrip())
                    indent_str = " " * indent

                    # 提取 json.dumps 内的 f-string 并替换 \\n 为换行变量
                    new_line = original_line.replace("\\\\n", "' + chr(10) + '")

                    if new_line != original_line:
                        lines[error_line] = new_line
                        fixed = True
                        _write_log(
                            f"  修复第 {error_line + 1} 行: 替换 f-string 中的反斜杠"
                        )

    if fixed:
        # 备份原文件
        backup_path = filepath + ".bak"
        try:
            shutil.copy2(filepath, backup_path)
            _write_log(f"  备份原文件到 {os.path.basename(backup_path)}")
        except Exception:
            pass

        # 写回修复后的文件
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(lines)
            _write_log("✅ 语法修复已写入文件")
            return True
        except Exception as e:
            _write_log(f"❌ 写入修复文件失败: {e}")
            return False

    return False


def start_flask_server():
    """在后台线程启动 Flask 服务器（带预检查和错误恢复）"""
    global KOTO_PORT
    import logging

    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    health_url = f"http://{KOTO_HOST}:{KOTO_PORT}/api/health"

    # 如果端口已被占用，先校验是否真的是可用的 Koto 服务
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        if sock.connect_ex((KOTO_HOST, KOTO_PORT)) == 0:
            sock.close()
            if _check_http_ok(health_url):
                _write_log(
                    f"ℹ️ 检测到 {KOTO_HOST}:{KOTO_PORT} 已在运行，健康检查通过，跳过内置服务启动"
                )
                return {"started": False, "already_running": True}
            else:
                _write_log(
                    f"⚠️ {KOTO_HOST}:{KOTO_PORT} 被占用但健康检查失败，尝试清理占用进程"
                )
                cleaned = _terminate_stale_process_on_port(
                    KOTO_PORT, reason="health timeout"
                )
                if cleaned:
                    _write_log("ℹ️ 已清理占用进程，继续启动内置服务")
                else:
                    alt_port = _find_available_port(KOTO_HOST, FALLBACK_PORT)
                    if alt_port is None:
                        _write_log(
                            f"⚠️ 清理失败，且未找到可用备用端口（起始 {FALLBACK_PORT}）"
                        )
                        return {
                            "started": False,
                            "already_running": False,
                            "needs_fallback": True,
                        }
                    _write_log(f"⚠️ 清理失败，自动改用可用端口 {alt_port}")
                    KOTO_PORT = alt_port
                    health_url = f"http://{KOTO_HOST}:{KOTO_PORT}/api/health"
        sock.close()
    except Exception:
        pass

    _server_error = [None]  # 用列表存储，方便在闭包中修改

    def run_server():
        try:
            # ──────── 预启动语法检查 (仅在调试模式或显式要求时启用) ────────
            # 在导入 app.py 之前先检查语法，避免大文件导入时崩溃
            # 优化：默认跳过此检查以加速启动 (12MB文件解析耗时)
            app_file = os.path.join(str(APP_ROOT), "web", "app.py")
            debug_syntax = os.environ.get("KOTO_DEBUG_SYNTAX", "0") == "1"

            if debug_syntax and os.path.exists(app_file):
                _write_log("🔍 正在执行语法预检查...")
                syntax_ok, syntax_err = _pre_check_syntax(app_file)
                if not syntax_ok:
                    _write_log(f"❌ app.py 语法检查失败: {syntax_err}")
                    # 尝试自动修复
                    fixed = _auto_fix_syntax(app_file, syntax_err)
                    if fixed:
                        _write_log("✅ 已自动修复语法错误，重新检查...")
                        syntax_ok2, syntax_err2 = _pre_check_syntax(app_file)
                        if not syntax_ok2:
                            _write_log(f"❌ 自动修复后仍有语法错误: {syntax_err2}")
                            _server_error[0] = syntax_err2
                            _start_fallback_server(syntax_err2, port=KOTO_PORT)
                            return
                        _write_log("✅ 语法修复成功！")
                    else:
                        _server_error[0] = syntax_err
                        _start_fallback_server(syntax_err, port=KOTO_PORT)
                        return
            else:
                _write_log("⚡ 快速启动：跳过语法预检查")

            # ──────── 正式启动 Flask ────────
            from web.app import app

            app.run(
                host=KOTO_HOST,
                port=KOTO_PORT,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
        except SyntaxError as e:
            error_msg = f"语法错误 ({e.filename}, 第{e.lineno}行): {e.msg}"
            _server_error[0] = error_msg
            _write_log(f"❌ Flask 服务启动失败(语法): {error_msg}")
            _start_fallback_server(error_msg, port=KOTO_PORT)
        except Exception as e:
            _server_error[0] = str(e)
            _write_log(f"❌ Flask 服务启动失败: {e}")
            # 启动一个最小的错误提示服务器
            _start_fallback_server(str(e), port=KOTO_PORT)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    _write_log("✔ Flask 后台线程已启动")
    return {
        "started": True,
        "already_running": False,
        "thread": server_thread,
        "error": _server_error[0],
    }


def _start_fallback_server(error_msg: str, port: int = KOTO_PORT):
    """当主 Flask 应用加载失败时，启动一个带诊断能力的错误恢复服务器"""
    try:
        import html
        import json as json_mod
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import parse_qs, urlparse

        safe_error = html.escape(error_msg)

        class FallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)

                # API: 获取诊断信息
                if parsed.path == "/api/diagnose":
                    self._handle_diagnose()
                    return

                # API: 尝试重启
                if parsed.path == "/api/retry":
                    self._handle_retry()
                    return

                # 主页面
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                page = self._build_error_page(safe_error)
                self.wfile.write(page.encode("utf-8"))

            def do_POST(self):
                if self.path == "/api/retry":
                    self._handle_retry()
                    return
                self.send_response(404)
                self.end_headers()

            def _handle_diagnose(self):
                """诊断当前系统状态"""
                app_file = os.path.join(str(APP_ROOT), "web", "app.py")
                info = {
                    "python_version": sys.version,
                    "app_file_exists": os.path.exists(app_file),
                    "app_file_size": (
                        os.path.getsize(app_file) if os.path.exists(app_file) else 0
                    ),
                    "error": error_msg,
                }
                # 语法检查
                if os.path.exists(app_file):
                    ok, err = _pre_check_syntax(app_file)
                    info["syntax_ok"] = ok
                    info["syntax_error"] = err

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    json_mod.dumps(info, ensure_ascii=False).encode("utf-8")
                )

            def _handle_retry(self):
                """尝试重新启动主应用"""
                result = {"success": False, "message": ""}
                app_file = os.path.join(str(APP_ROOT), "web", "app.py")

                # 1. 先检查语法
                ok, err = _pre_check_syntax(app_file)
                if not ok:
                    # 尝试自动修复
                    fixed = _auto_fix_syntax(app_file, err)
                    if fixed:
                        ok2, err2 = _pre_check_syntax(app_file)
                        if ok2:
                            result["message"] = "语法已自动修复！正在重启程序..."
                            result["success"] = True
                            result["action"] = "restart"
                        else:
                            result["message"] = f"自动修复失败，仍有错误: {err2}"
                    else:
                        result["message"] = f"无法自动修复: {err}"
                else:
                    result["message"] = "语法检查通过！正在重启程序..."
                    result["success"] = True
                    result["action"] = "restart"

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    json_mod.dumps(result, ensure_ascii=False).encode("utf-8")
                )

                # 如果修复成功，重启整个进程
                if result.get("success"):
                    _write_log("🔄 语法修复成功，准备重启进程...")

                    def _do_restart():
                        time.sleep(1)
                        _write_log("🔄 正在重启 Koto...")
                        os.execv(sys.executable, [sys.executable] + sys.argv)

                    threading.Thread(target=_do_restart, daemon=True).start()

            def _build_error_page(self, safe_err):
                return (
                    '<!DOCTYPE html><html><head><meta charset="utf-8">'
                    "<title>Koto - 启动修复</title>"
                    "<style>"
                    "*{box-sizing:border-box}"
                    "body{font-family:system-ui,-apple-system,sans-serif;display:flex;align-items:center;"
                    "justify-content:center;min-height:100vh;margin:0;background:#0f0f1a;color:#e0e0e0}"
                    ".card{background:#1a1a2e;border-radius:20px;padding:48px;max-width:680px;width:90%;"
                    "box-shadow:0 12px 48px rgba(0,0,0,.4)}"
                    "h1{color:#ff6b6b;margin:0 0 8px;font-size:28px;text-align:center}"
                    ".subtitle{color:#888;text-align:center;margin-bottom:24px;font-size:14px}"
                    ".error-box{background:#0d1b2a;border:1px solid #1b2838;border-radius:12px;"
                    'padding:20px;margin:20px 0;font-family:"Cascadia Code","Fira Code",monospace;'
                    "font-size:13px;word-break:break-all;max-height:160px;overflow:auto;"
                    "line-height:1.6;color:#ffd93d}"
                    ".actions{display:flex;gap:12px;margin:24px 0;flex-wrap:wrap}"
                    ".btn{flex:1;padding:14px 24px;border:none;border-radius:12px;font-size:15px;"
                    "font-weight:600;cursor:pointer;transition:all .2s;min-width:140px;text-align:center}"
                    ".btn-primary{background:linear-gradient(135deg,#4361ee,#3a0ca3);color:#fff}"
                    ".btn-primary:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(67,97,238,.4)}"
                    ".btn-secondary{background:#1b2838;color:#4fc3f7;border:1px solid #2a3a4a}"
                    ".btn-secondary:hover{background:#243447}"
                    ".btn:disabled{opacity:.5;cursor:not-allowed;transform:none}"
                    ".status{padding:16px;border-radius:12px;margin:16px 0;font-size:14px;"
                    "display:none;line-height:1.8}"
                    ".status.show{display:block}"
                    ".status.success{background:#1a3a2a;border:1px solid #2d6a4f;color:#52b788}"
                    ".status.error{background:#3a1a1a;border:1px solid #6a2d2d;color:#ff6b6b}"
                    ".status.info{background:#1a2a3a;border:1px solid #2d4a6a;color:#4fc3f7}"
                    ".tips{margin-top:20px;padding:20px;background:#16213e;border-radius:12px}"
                    ".tips h3{margin:0 0 12px;font-size:15px;color:#aaa}"
                    ".tips ul{margin:0;padding-left:20px;line-height:2.2}"
                    ".tips li{font-size:13px;color:#999}"
                    ".tips code{background:#0d1b2a;padding:2px 8px;border-radius:4px;font-size:12px;color:#4fc3f7}"
                    ".spinner{display:inline-block;width:16px;height:16px;border:2px solid #fff3;"
                    "border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite;"
                    "vertical-align:middle;margin-right:8px}"
                    "@keyframes spin{to{transform:rotate(360deg)}}"
                    ".footer{text-align:center;margin-top:24px;font-size:12px;color:#555}"
                    "</style></head><body>"
                    '<div class="card">'
                    "<h1>⚠️ Koto 启动遇到问题</h1>"
                    '<p class="subtitle">应用加载过程中出现错误，可尝试自动修复</p>'
                    f'<div class="error-box">{safe_err}</div>'
                    '<div id="status" class="status"></div>'
                    '<div class="actions">'
                    '<button class="btn btn-primary" id="retryBtn" onclick="handleRetry()">'
                    "🔧 自动修复并重启</button>"
                    '<button class="btn btn-secondary" id="diagnoseBtn" onclick="handleDiagnose()">'
                    "🔍 诊断检查</button>"
                    "</div>"
                    '<div class="tips">'
                    "<h3>💡 手动排查提示</h3>"
                    "<ul>"
                    "<li>🔑 检查 <code>config/gemini_config.env</code> 中的 API 密钥</li>"
                    "<li>📦 运行 <code>pip install -r requirements.txt</code></li>"
                    "<li>🔄 关闭后重新运行 <code>RunSource.bat</code></li>"
                    "<li>📋 查看 <code>logs/startup.log</code> 获取详细日志</li>"
                    "</ul></div>"
                    '<div class="footer">Koto v2.0 | 错误恢复模式</div>'
                    "</div>"
                    "<script>"
                    'const statusEl=document.getElementById("status");'
                    'const retryBtn=document.getElementById("retryBtn");'
                    'const diagnoseBtn=document.getElementById("diagnoseBtn");'
                    "function showStatus(msg,type){"
                    'statusEl.className="status show "+type;statusEl.innerHTML=msg}'
                    "async function handleRetry(){"
                    "retryBtn.disabled=true;"
                    "retryBtn.innerHTML='<span class=\"spinner\"></span>正在修复...';"
                    'showStatus("⏳ 正在检查语法并尝试自动修复...","info");'
                    'try{const r=await fetch("/api/retry",{method:"POST"});'
                    "const d=await r.json();"
                    "if(d.success){"
                    'showStatus("✅ "+d.message+"<br>页面将在 3 秒后自动刷新...","success");'
                    "setTimeout(()=>location.reload(),3000)"
                    "}else{"
                    'showStatus("❌ "+d.message,"error");'
                    'retryBtn.disabled=false;retryBtn.innerHTML="🔧 重试修复"}'
                    "}catch(e){"
                    'showStatus("❌ 请求失败: "+e.message,"error");'
                    'retryBtn.disabled=false;retryBtn.innerHTML="🔧 重试修复"}}'
                    "async function handleDiagnose(){"
                    'diagnoseBtn.disabled=true;diagnoseBtn.innerHTML="🔍 检查中...";'
                    'try{const r=await fetch("/api/diagnose");const d=await r.json();'
                    'let info="<b>📋 诊断结果</b><br>";'
                    'info+="Python: "+d.python_version+"<br>";'
                    'info+="app.py 存在: "+(d.app_file_exists?"✅":"❌")+"<br>";'
                    'info+="文件大小: "+(d.app_file_size/1024).toFixed(1)+" KB<br>";'
                    'info+="语法检查: "+(d.syntax_ok?"✅ 通过":"❌ "+d.syntax_error)+"<br>";'
                    'showStatus(info,d.syntax_ok?"success":"error")'
                    '}catch(e){showStatus("❌ 诊断失败: "+e.message,"error")}'
                    'diagnoseBtn.disabled=false;diagnoseBtn.innerHTML="🔍 诊断检查"}'
                    "</script></body></html>"
                )

            def log_message(self, fmt, *args):
                pass  # 静默日志

        server = HTTPServer((KOTO_HOST, port), FallbackHandler)
        _write_log(f"⚠️ 错误恢复服务器已启动在 http://{KOTO_HOST}:{port}")
        server.serve_forever()
    except Exception as fallback_err:
        _write_log(f"❌ 错误恢复服务器也无法启动: {fallback_err}")


def create_system_tray(window_ref=None):
    """创建系统托盘图标"""
    try:
        from PIL import Image, ImageDraw
        from pystray import Icon, Menu, MenuItem

        icon_file = ASSETS_DIR / "koto_icon.png"

        def create_icon_image():
            """创建简单的托盘图标"""
            # 创建 64x64 图标
            width, height = 64, 64
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)

            # 绘制圆形背景 - 渐变蓝色
            draw.ellipse([4, 4, 60, 60], fill=(66, 133, 244, 255))

            # 绘制内部圆形 - 白色
            draw.ellipse([16, 16, 48, 48], fill=(255, 255, 255, 255))

            # 绘制 "言" 字形状的简化版本 - 三条横线
            draw.rectangle([22, 24, 42, 28], fill=(66, 133, 244, 255))
            draw.rectangle([22, 32, 42, 36], fill=(66, 133, 244, 255))
            draw.rectangle([22, 40, 42, 44], fill=(66, 133, 244, 255))

            return image

        def on_quit(icon, item):
            """退出应用"""
            icon.stop()
            os._exit(0)

        def on_show(icon, item):
            """显示主窗口"""
            if window_ref:
                try:
                    window_ref[0].show()
                except:
                    pass

        def on_hide(icon, item):
            """隐藏主窗口"""
            if window_ref:
                try:
                    window_ref[0].hide()
                except:
                    pass

        # 创建托盘图标（优先使用自定义图标）
        if icon_file.exists():
            tray_image = Image.open(str(icon_file))
        else:
            tray_image = create_icon_image()

        icon = Icon(
            "Koto",
            tray_image,
            "Koto - AI 助手 (运行中)",
            Menu(
                MenuItem("显示窗口", on_show, default=True),
                MenuItem("隐藏窗口", on_hide),
                Menu.SEPARATOR,
                MenuItem("退出", on_quit),
            ),
        )

        return icon
    except Exception as e:
        print(f"⚠️ 系统托盘创建失败: {e}")
        return None


def _bootstrap_api_setup():
    """从 _internal/koto_setup.py 加载并执行 API 密钥向导。
    兼容旧版编译入口：写入 model_setup_done.json 防止 model_downloader 重复弹出。
    """
    import json as _json

    # 抑制旧版编译入口下次弹出 model_downloader
    _flag = APP_ROOT / "config" / "model_setup_done.json"
    if not _flag.exists():
        try:
            _flag.write_text(
                _json.dumps({"done": True, "version": 1}), encoding="utf-8"
            )
        except Exception:
            pass
    # 调用 koto_setup.py 中的 API 密钥向导
    try:
        import runpy as _runpy

        _bundle = (
            Path(sys._MEIPASS)
            if getattr(sys, "frozen", False)
            else Path(__file__).parent
        )
        _setup_py = _bundle / "koto_setup.py"
        if _setup_py.exists():
            _ns = _runpy.run_path(
                str(_setup_py)
            )  # run_name 默认非 __main__，不触发 main()
            if "_run_setup_if_needed" in _ns:
                _ns["_run_setup_if_needed"]()
    except Exception:
        pass


def main():
    """主入口 - 桌面应用模式"""
    # 初始化
    ensure_directories()
    _bootstrap_api_setup()  # API 密钥向导（便携版 / 首次启动 / 密钥失效时触发）
    check_config()
    if not ensure_dependencies():
        return
    _write_log("🚀 启动 Koto 桌面程序")

    # 设置 WebView2 持久化用户数据目录，使麦克风等权限在重启后保留
    _webview_data_dir = APP_ROOT / ".webview2_profile"
    _webview_data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(_webview_data_dir))
    _write_log(f"✔ WebView2 用户数据目录: {_webview_data_dir}")

    # 先启动后端线程，再导入 webview ——
    # 图标生成 + Flask 启动 + webview 导入 三者并行，大幅缩短启动时间
    server_info = start_flask_server() or {}

    # 图标生成（仅首次运行需要；与 Flask 启动并行完成）
    _icon_ready = threading.Event()
    ico_path = ASSETS_DIR / "koto_icon.ico"
    png_path = ASSETS_DIR / "koto_icon.png"

    def _generate_icons():
        try:
            from PIL import Image, ImageDraw
            icon_dir = ASSETS_DIR
            icon_dir.mkdir(exist_ok=True, parents=True)
            if not png_path.exists():
                width, height = 256, 256
                image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle([0, 0, 256, 256], radius=56, fill=(79, 140, 255, 255))
                draw.ellipse([48, 48, 208, 208], fill=(255, 255, 255, 255))
                draw.rectangle([72, 88, 184, 104], fill=(47, 107, 255, 255))
                draw.rectangle([72, 120, 184, 136], fill=(47, 107, 255, 255))
                draw.rectangle([72, 152, 184, 168], fill=(47, 107, 255, 255))
                image.save(str(png_path))
            if not ico_path.exists():
                image = Image.open(str(png_path))
                image.save(str(ico_path), sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
        except Exception as e:
            _write_log(f"⚠️ 生成默认图标失败: {e}")
        finally:
            _icon_ready.set()

    _icon_thread = threading.Thread(target=_generate_icons, daemon=True)
    _icon_thread.start()

    # 在 Flask 后台启动的同时导入 webview（重叠 I/O 开销）
    import webview

    _write_log("✔ 已导入 webview")

    app_url = f"http://{KOTO_HOST}:{KOTO_PORT}"
    health_url = f"{app_url}/api/health"
    if server_info.get("error"):
        _write_log(f"⚠️ Flask 线程报错: {server_info['error']}")
    if server_info.get("already_running"):
        _write_log("ℹ️ 发现已有后端运行，复用它")

    # 如果端口被外部程序占用且不健康，直接切换备用端口
    if server_info.get("needs_fallback"):
        err_msg = (
            "检测到 5000 端口被其他程序占用且响应异常，自动切换备用端口 5001。\n"
            "请关闭占用 5000 端口的程序，或使用备用端口访问。"
        )
        _write_log(err_msg)
        threading.Thread(
            target=_start_fallback_server, args=(err_msg, FALLBACK_PORT), daemon=True
        ).start()
        app_url = f"http://{KOTO_HOST}:{FALLBACK_PORT}"
    else:
        # 等待后端就绪（容忍慢启动，避免误判后错误恢复页占住主端口）
        backend_ready = _wait_for_port(KOTO_HOST, KOTO_PORT, STARTUP_TIMEOUT_SEC)
        if (
            not backend_ready
            and server_info.get("thread") is not None
            and server_info["thread"].is_alive()
        ):
            _write_log("⚠️ 后端启动较慢，延长等待健康检查（最多 15 秒）")
            for _ in range(30):
                if _check_http_ok(health_url):
                    backend_ready = True
                    break
                time.sleep(0.5)

        if not backend_ready:
            err_msg = "后端服务启动超时，请检查依赖或端口占用情况。"
            _write_log(err_msg)
            _dump_threads("wait-port-timeout")
            fallback_port = (
                _find_available_port(KOTO_HOST, FALLBACK_PORT) or FALLBACK_PORT
            )
            threading.Thread(
                target=_start_fallback_server,
                args=(err_msg, fallback_port),
                daemon=True,
            ).start()
            app_url = f"http://{KOTO_HOST}:{fallback_port}"
        else:
            _write_log("✔ 后端端口已就绪")

    # === 快速启动模式：跳过预热检查 ===
    # pywebview 内部会处理加载超时，无需提前检查
    _write_log("⚡ 快速启动：跳过预热检查，直接创建窗口")

    # === 启动后台系统监控（守护线程，桌面模式专用）===
    try:
        from app.core.monitoring.system_event_monitor import get_system_event_monitor

        _sem = get_system_event_monitor(check_interval=60)  # 每 60 秒检查一次
        if not _sem.is_running():
            _sem.start()
            _write_log("✔ 系统资源监控已启动（CPU/内存/磁盘告警）")
    except Exception as _sem_err:
        _write_log(f"⚠️ 系统监控启动失败（非致命）: {_sem_err}")

    # === 预热本地路由模型（守护线程，不阻塞窗口创建）===
    def _init_local_router_async():
        import socket as _socket

        time.sleep(3)  # 等待 Flask 和 Ollama 完全就绪
        try:
            _s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            _s.settimeout(0.5)
            _ollama_up = _s.connect_ex(("127.0.0.1", 11434)) == 0
            _s.close()
        except Exception:
            _ollama_up = False

        if not _ollama_up:
            _write_log(
                "🦙 Ollama 未运行，本地模型路由不可用（可运行 LocalModelInstaller 安装）"
            )
            return

        try:
            from app.core.routing.local_model_router import LocalModelRouter

            if LocalModelRouter.init_model():
                _write_log(f"🦙 本地路由模型已就绪: {LocalModelRouter._model_name}")
            else:
                _write_log(
                    "🦙 Ollama 运行中但无可用路由模型，请通过 LocalModelInstaller 下载模型"
                )
        except Exception as _lmr_e:
            _write_log(f"🦙 本地路由模型初始化跳过: {_lmr_e}")

    threading.Thread(target=_init_local_router_async, daemon=True).start()

    # 等待图标生成完成（通常此时已完成，因为与 Flask 等待并行进行）
    _icon_ready.wait(timeout=5)

    # 选择窗口图标（如存在）
    icon_path = None
    if ico_path.exists():
        icon_path = str(ico_path)

    # 检测屏幕分辨率，自动适配窗口大小（居中、占屏幕 88%）
    try:
        import ctypes as _ctypes
        _u32 = _ctypes.windll.user32
        _u32.SetProcessDPIAware()
        _screen_w = _u32.GetSystemMetrics(0)
        _screen_h = _u32.GetSystemMetrics(1)
        _win_w = max(1024, int(_screen_w * 0.65))
        _win_h = max(700, int(_screen_h * 0.65))
        _win_x = (_screen_w - _win_w) // 2
        _win_y = (_screen_h - _win_h) // 2
        _write_log(f"✔ 屏幕分辨率: {_screen_w}x{_screen_h}，初始窗口: {_win_w}x{_win_h} 位于 ({_win_x},{_win_y})")
    except Exception as _e:
        _win_w, _win_h = 1200, 800
        _win_x, _win_y = None, None
        _write_log(f"⚠️ 无法检测屏幕分辨率，使用默认 1200x800: {_e}")

    # 创建桌面窗口
    window = webview.create_window(
        title="Koto - AI 个人助手",
        url=app_url,
        width=_win_w,
        height=_win_h,
        x=_win_x,
        y=_win_y,
        resizable=True,
        fullscreen=False,
        min_size=(400, 300),
        confirm_close=False,
        text_select=True,
        easy_drag=False,  # 关闭拖拽模式，防止拦截点击事件
        on_top=False,  # 不置顶，让用户正常使用
    )

    if icon_path:
        _write_log(f"✔ 图标路径: {icon_path}")
    _write_log(f"✔ 创建窗口，加载 {app_url}")

    # 绑定窗口控制API
    window_api = WindowAPI(window, app_url)
    window_api.full_size = (_win_w, _win_h)  # 同步实际初始窗口尺寸
    window.expose(window_api.switch_to_mini)
    window.expose(window_api.switch_to_full)
    window.expose(window_api.get_mode)
    window.expose(window_api.minimize)
    window.expose(window_api.maximize)
    window.expose(window_api.close)
    window.expose(window_api.open_url)

    # 窗口引用（供托盘使用）
    window_ref = [window]

    # 创建系统托盘（在单独线程中）
    tray_icon = create_system_tray(window_ref)
    if tray_icon:
        tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
        tray_thread.start()
        _write_log("✔ 系统托盘线程已启动")

    # ── 启动阶段看门狗 ──────────────────────────────────
    # webview.start() 会永久阻塞主线程（这是正常行为，它就是窗口事件循环）。
    # 看门狗仅监控「窗口是否在超时内成功显示」，一旦 on_shown 触发就取消。
    # 避免之前的 bug：35秒后无条件 os._exit 杀掉正在正常运行的程序。
    _window_shown = threading.Event()

    def on_shown():
        """窗口显示后的回调"""
        _window_shown.set()  # 通知看门狗：窗口已加载
        _write_log("✔ 窗口已显示，应用正常运行中")

    def _startup_watchdog(timeout_sec: int = 45):
        """启动看门狗：如果窗口在 timeout_sec 内未显示，记录诊断信息。
        注意：只记录日志 + thread dump，不强制退出。
        强制退出会导致用户看到"闪退"，不如让窗口继续尝试加载。"""
        if _window_shown.wait(timeout=timeout_sec):
            return  # 窗口正常显示，看门狗退出
        # 超时：窗口没有显示
        _write_log(f"⚠️ 窗口在 {timeout_sec} 秒内未显示，记录诊断信息")
        _dump_threads("startup-watchdog-timeout")
        # 不调用 os._exit()，让程序继续尝试

    watchdog = threading.Thread(target=_startup_watchdog, args=(45,), daemon=True)
    watchdog.start()

    _write_log("🚀 启动 webview.start（窗口事件循环）")

    # 在启动时设置图标（仅Windows支持）
    # private_mode=False：关闭隐私模式，使麦克风等权限、Cookie 在重启后保留
    # storage_path：指定持久化用户数据目录（与前面创建的 .webview2_profile 一致）
    start_kwargs = {
        "func": on_shown,
        "debug": False,
        "private_mode": False,
        "storage_path": str(_webview_data_dir),
    }
    if icon_path:
        start_kwargs["icon"] = icon_path
        _write_log(f"✔ 设置应用图标: {icon_path}")

    webview.start(**start_kwargs)
    _write_log("ℹ️ webview.start 结束（窗口已关闭）")

    # 窗口关闭后退出
    os._exit(0)


if __name__ == "__main__":
    main()
