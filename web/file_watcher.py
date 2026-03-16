"""
文件夹监控模块 (Watch Mode)
使用 watchdog 监视指定文件夹，新文件出现时自动分析+归类。
支持多目录同时监控，守护线程运行，不阻塞主进程。

用法：
    watcher = get_file_watcher()
    watcher.start_watch("C:/Users/xxx/Downloads")
    watcher.stop_watch("C:/Users/xxx/Downloads")
    watcher.list_watches()          # 返回正在监控的目录列表
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import logging

# watchdog import — 软依赖，不安装时以 None 标记

logger = logging.getLogger(__name__)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
    _WATCHDOG_OK = True
except ImportError:
    _WATCHDOG_OK = False
    Observer = None
    FileSystemEventHandler = object


# 新文件出现后等待 2 秒再处理（防止文件还在复制中）
_SETTLE_SECONDS = 2.0

# 支持自动分析的扩展名
_AUTO_EXTS = {
    ".doc", ".docx", ".pdf", ".txt", ".csv",
    ".xlsx", ".xls", ".pptx", ".ppt", ".md",
    ".zip", ".rar", ".7z",
}


class _CatalogEventHandler(FileSystemEventHandler):
    """新文件事件处理器，触发自动分析+归类。"""

    def __init__(self, watch_dir: str, callback: Callable[[str], None]):
        super().__init__()
        self.watch_dir = Path(watch_dir)
        self._callback = callback
        self._pending: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_created(self, event):
        if event.is_directory:
            return
        src = Path(event.src_path)
        # 只处理支持的扩展名，跳过临时文件
        if src.suffix.lower() not in _AUTO_EXTS:
            return
        if src.name.startswith("~$") or src.name.startswith("."):
            return
        # 防抖：重置计时器（文件可能分多次写入）
        key = str(src)
        with self._lock:
            if key in self._pending:
                self._pending[key].cancel()
            t = threading.Timer(_SETTLE_SECONDS, self._process, args=[key])
            t.daemon = True
            self._pending[key] = t
            t.start()

    def _process(self, file_path: str):
        with self._lock:
            self._pending.pop(file_path, None)
        try:
            self._callback(file_path)
        except Exception as e:
            logger.warning(f"[FileWatcher] ⚠️ 处理 {file_path} 时出错: {e}")


class _WatchEntry:
    def __init__(self, path: str, observer: Any, handler: Any):
        self.path = path
        self.observer = observer
        self.handler = handler
        self.started_at = time.time()


class FileWatcher:
    """多目录文件监控器。每个监控目录独立 Observer 线程。"""

    def __init__(self):
        self._watches: Dict[str, _WatchEntry] = {}  # path → entry
        self._lock = threading.Lock()
        # 延迟注入，避免循环导入
        self._analyzer: Optional[Any] = None
        self._organizer: Optional[Any] = None
        self._organize_root: Optional[str] = None
        self._on_file_cataloged: Optional[Callable] = None

    def configure(self, analyzer, organizer, organize_root: str,
                  on_file_cataloged: Optional[Callable] = None):
        """注入依赖（FileAnalyzer, FileOrganizer, organize_root 路径）。
        可选 on_file_cataloged(result: dict) 回调，用于 UI 推送通知。
        """
        self._analyzer = analyzer
        self._organizer = organizer
        self._organize_root = organize_root
        self._on_file_cataloged = on_file_cataloged

    def start_watch(self, directory: str) -> Dict[str, Any]:
        """开始监控目录。已在监控则返回已监控状态。"""
        if not _WATCHDOG_OK:
            return {"success": False, "error": "watchdog 未安装，请先 pip install watchdog"}
        directory = str(Path(directory).resolve())
        if not Path(directory).is_dir():
            return {"success": False, "error": f"目录不存在: {directory}"}
        with self._lock:
            if directory in self._watches:
                return {"success": True, "status": "already_watching", "path": directory}
            handler = _CatalogEventHandler(directory, self._handle_new_file)
            observer = Observer()
            observer.schedule(handler, directory, recursive=False)
            observer.daemon = True
            observer.start()
            self._watches[directory] = _WatchEntry(directory, observer, handler)
            logger.info(f"[FileWatcher] 👁️ 开始监控: {directory}")
        return {"success": True, "status": "started", "path": directory}

    def stop_watch(self, directory: str) -> Dict[str, Any]:
        """停止监控目录。"""
        directory = str(Path(directory).resolve())
        with self._lock:
            entry = self._watches.pop(directory, None)
        if not entry:
            return {"success": False, "error": f"未在监控该目录: {directory}"}
        try:
            entry.observer.stop()
            entry.observer.join(timeout=3)
        except Exception:
            pass
        logger.info(f"[FileWatcher] ⛔ 已停止监控: {directory}")
        return {"success": True, "status": "stopped", "path": directory}

    def stop_all(self):
        """停止所有监控（程序退出时调用）。"""
        with self._lock:
            paths = list(self._watches.keys())
        for p in paths:
            self.stop_watch(p)

    def list_watches(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "path": e.path,
                    "started_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                                time.localtime(e.started_at)),
                    "alive": e.observer.is_alive(),
                }
                for e in self._watches.values()
            ]

    def _handle_new_file(self, file_path: str):
        """新文件落地后：分析 → 归类 → 可选回调通知。"""
        p = Path(file_path)
        logger.info(f"[FileWatcher] 📥 检测到新文件: {p.name}")

        if not self._analyzer or not self._organizer or not self._organize_root:
            logger.warning("[FileWatcher] ⚠️ 未完成 configure()，跳过自动归类")
            return

        try:
            from web.file_fields_extractor import extract_fields
        except ImportError:
            try:
                from file_fields_extractor import extract_fields
            except ImportError:
                extract_fields = None

        try:
            analysis = self._analyzer.analyze_file(file_path)
            suggested_folder = analysis.get("suggested_folder") or "other/uncategorized"

            result = self._organizer.organize_file(
                file_path,
                suggested_folder,
                auto_confirm=True,
            )

            fields = None
            if extract_fields and analysis.get("preview"):
                fields = extract_fields(p.name, analysis["preview"], p.suffix.lower())

            outcome = {
                "file_name": p.name,
                "file_path": file_path,
                "dest": result.get("dest_file", ""),
                "folder": suggested_folder,
                "industry": analysis.get("industry", ""),
                "summary": (fields or {}).get("summary", ""),
                "success": result.get("success", False),
            }

            if self._on_file_cataloged:
                try:
                    self._on_file_cataloged(outcome)
                except Exception:
                    pass

            status = "✅" if outcome["success"] else "⚠️"
            logger.info(f"[FileWatcher] {status} {p.name} → {suggested_folder}")

        except Exception as e:
            logger.error(f"[FileWatcher] ❌ 自动归类异常: {e}")


# ── 单例 ─────────────────────────────────────────────────────────────────────
_watcher_instance: Optional[FileWatcher] = None
_watcher_lock = threading.Lock()


def get_file_watcher() -> FileWatcher:
    global _watcher_instance
    with _watcher_lock:
        if _watcher_instance is None:
            _watcher_instance = FileWatcher()
    return _watcher_instance
