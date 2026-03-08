# -*- coding: utf-8 -*-
"""
FileWatcher — Koto 目录轮询监控器
===================================
无需 watchdog 等第三方依赖，纯 Python 轮询实现。

功能：
  - 每 30 秒（可配）扫描监控目录列表
  - 检测新增/修改的文件 → 注册到 FileRegistry（含内容提取）
  - 检测已删除文件 → 从 FileRegistry 软删除
  - 监控目录从 user_settings.json 读取，运行时可动态更新

监控目录配置 (user_settings.json)::
  {
    "file_watcher": {
      "enabled": true,
      "watch_dirs": ["C:/Users/me/Downloads", "C:/Users/me/Desktop"],
      "interval_seconds": 30,
      "max_file_size_mb": 50,
      "skip_extensions": [".tmp", ".part", ".crdownload"]
    }
  }
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS_PATH = str(
    Path(__file__).parent.parent.parent.parent / "config" / "user_settings.json"
)

_DEFAULT_SKIP_EXTS: Set[str] = {
    ".tmp", ".part", ".crdownload", ".download",
    ".~lock", ".swp", ".lnk", ".db", ".db-shm", ".db-wal",
}

_watcher_instance: Optional["FileWatcher"] = None
_watcher_lock = threading.Lock()


class FileWatcher:
    """
    后台目录监控器。

    用法::
        watcher = get_file_watcher()
        watcher.start()          # 启动后台线程
        watcher.add_dir("C:/Downloads")
        watcher.stop()           # 优雅停止
    """

    def __init__(self, settings_path: Optional[str] = None):
        self._settings_path = settings_path or _DEFAULT_SETTINGS_PATH
        self._cfg: Dict = {}
        self._reload_config()

        # mtime 快照：path → mtime
        self._snapshot: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ── 配置 ─────────────────────────────────────────────────────────────────

    def _reload_config(self):
        try:
            cfg_path = Path(self._settings_path)
            if cfg_path.exists():
                raw = json.loads(cfg_path.read_text(encoding="utf-8"))
                self._cfg = raw.get("file_watcher", {})
        except Exception as e:
            logger.warning(f"[FileWatcher] 读取配置失败: {e}")
            self._cfg = {}

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.get("enabled", False))

    @property
    def watch_dirs(self) -> List[str]:
        return list(self._cfg.get("watch_dirs", []))

    @property
    def interval(self) -> int:
        return max(10, int(self._cfg.get("interval_seconds", 30)))

    @property
    def max_file_size_bytes(self) -> int:
        return int(self._cfg.get("max_file_size_mb", 50)) * 1024 * 1024

    @property
    def skip_exts(self) -> Set[str]:
        user_skip = {e.lower() for e in self._cfg.get("skip_extensions", [])}
        return _DEFAULT_SKIP_EXTS | user_skip

    def add_dir(self, directory: str):
        """动态添加一个监控目录（不持久化到磁盘）。"""
        dirs = self._cfg.get("watch_dirs", [])
        if directory not in dirs:
            dirs.append(directory)
            self._cfg["watch_dirs"] = dirs
            logger.info(f"[FileWatcher] 添加监控目录: {directory}")

    def remove_dir(self, directory: str):
        dirs = self._cfg.get("watch_dirs", [])
        if directory in dirs:
            dirs.remove(directory)
            self._cfg["watch_dirs"] = dirs

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        if not self.enabled and not self.watch_dirs:
            logger.info("[FileWatcher] 未配置监控目录，跳过启动")
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="koto-file-watcher"
        )
        self._thread.start()
        logger.info(f"[FileWatcher] 🚀 启动，监控 {len(self.watch_dirs)} 个目录，interval={self.interval}s")

    def stop(self):
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[FileWatcher] 已停止")

    # ── 核心循环 ──────────────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._reload_config()
                self._scan_all()
            except Exception as e:
                logger.error(f"[FileWatcher] 扫描异常: {e}")
            self._stop_event.wait(self.interval)

    def _scan_all(self):
        from app.core.file.file_registry import get_file_registry

        reg = get_file_registry()
        dirs = self.watch_dirs
        if not dirs:
            return

        current_paths: Set[str] = set()
        new_or_modified: List[str] = []

        for d in dirs:
            d_path = Path(d)
            if not d_path.is_dir():
                continue
            try:
                for p in d_path.rglob("*"):
                    if not p.is_file():
                        continue
                    if p.suffix.lower() in self.skip_exts:
                        continue
                    # 跳过隐藏文件
                    if any(part.startswith(".") for part in p.parts):
                        continue
                    # 跳过超大文件
                    try:
                        if p.stat().st_size > self.max_file_size_bytes:
                            continue
                        mtime = p.stat().st_mtime
                    except OSError:
                        continue

                    path_str = str(p)
                    current_paths.add(path_str)

                    with self._lock:
                        old_mtime = self._snapshot.get(path_str)

                    if old_mtime is None or abs(old_mtime - mtime) > 0.5:
                        new_or_modified.append(path_str)
                        with self._lock:
                            self._snapshot[path_str] = mtime

            except PermissionError:
                pass
            except Exception as e:
                logger.debug(f"[FileWatcher] 扫描目录 {d} 错误: {e}")

        # 注册新增/修改文件（内容提取仅对文档/代码类型）
        for path_str in new_or_modified:
            try:
                ext = Path(path_str).suffix.lower()
                extract = ext in {
                    ".txt", ".md", ".pdf", ".docx", ".xlsx",
                    ".py", ".js", ".json", ".csv", ".html"
                }
                entry = reg.register(path_str, source="watcher", extract_content=extract)
                # Phase 1-C: 对 category 为"其他"的文件，用 FileAnalyzer 异步回填分类
                if entry and entry.category == "其他":
                    self._enrich_category_async(path_str)
            except Exception as e:
                logger.debug(f"[FileWatcher] 注册失败 {path_str}: {e}")

        # 检测并移除已删除的文件
        with self._lock:
            deleted = [p for p in list(self._snapshot.keys())
                       if p not in current_paths and not Path(p).exists()]
        for path_str in deleted:
            try:
                reg.delete(path_str)
                with self._lock:
                    self._snapshot.pop(path_str, None)
                logger.debug(f"[FileWatcher] 移除已删除文件: {path_str}")
            except Exception as e:
                logger.debug(f"[FileWatcher] 移除失败 {path_str}: {e}")

        if new_or_modified:
            logger.info(
                f"[FileWatcher] 本次扫描: {len(new_or_modified)} 个文件新增/更新，"
                f"{len(deleted)} 个已删除"
            )

    def scan_once(self, directory: str) -> int:
        """立即同步扫描一个目录，返回注册成功的文件数（外部调用）。"""
        from app.core.file.file_registry import get_file_registry

        reg = get_file_registry()
        count = 0
        d_path = Path(directory)
        if not d_path.is_dir():
            return 0
        for p in d_path.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in self.skip_exts:
                continue
            try:
                entry = reg.register(str(p), source="scanner", extract_content=False)
                if entry:
                    count += 1
            except Exception:
                pass
        return count

    def _enrich_category_async(self, path_str: str):
        """后台调用 FileAnalyzer 对"其他"类文件回填行业分类，写回 category 字段。"""
        def _run():
            try:
                from app.core.file.file_registry import get_file_registry
                try:
                    from web.file_analyzer import FileAnalyzer
                except ImportError:
                    from file_analyzer import FileAnalyzer  # type: ignore
                analyzer = FileAnalyzer()
                name = Path(path_str).name
                result = analyzer.analyze_file(path_str)
                industry = result.get("industry") or result.get("category") or ""
                if not industry or industry == "other":
                    return
                reg = get_file_registry()
                reg._conn.execute(
                    "UPDATE koto_file_registry SET category=?, updated_at=? WHERE path=?",
                    (industry, __import__("datetime").datetime.now().isoformat(timespec="milliseconds"), path_str),
                )
                reg._conn.commit()
                logger.debug(f"[FileWatcher] 分类回填 {name} → {industry}")
            except Exception as e:
                logger.debug(f"[FileWatcher] 分类回填失败 {path_str}: {e}")
        threading.Thread(target=_run, daemon=True).start()


# ============================================================================
# 单例访问
# ============================================================================

def get_file_watcher(settings_path: Optional[str] = None) -> FileWatcher:
    global _watcher_instance
    if _watcher_instance is None:
        with _watcher_lock:
            if _watcher_instance is None:
                _watcher_instance = FileWatcher(settings_path=settings_path)
    return _watcher_instance
