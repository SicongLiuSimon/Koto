"""
work_file_library.py — Koto 工作文件库

专注于 Word / Excel / PPT / PDF 等办公文件，轻量级本地索引。
- 自动扫描用户常用位置（桌面、文档、下载），无需全盘扫描
- SQLite 持久化，重启后直接复用
- 支持用户自定义监控文件夹
- 搜索结果按文件类型分组（Word文档 / Excel表格 / PPT演示 / PDF文档）
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

# ── 工作文件扩展名 → 分类 ─────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

WORK_FILE_TYPES: Dict[str, str] = {
    ".doc":  "Word文档",
    ".docx": "Word文档",
    ".xls":  "Excel表格",
    ".xlsx": "Excel表格",
    ".ppt":  "PPT演示",
    ".pptx": "PPT演示",
    ".pdf":  "PDF文档",
}

_CATEGORY_ICONS: Dict[str, str] = {
    "Word文档":  "📝",
    "Excel表格": "📊",
    "PPT演示":   "📑",
    "PDF文档":   "📄",
}

# 扫描时跳过的目录名（小写）
_SKIP_DIRS = {
    "__pycache__", ".git", ".svn", "node_modules",
    ".venv", "venv", "env", "site-packages",
    "$recycle.bin", "recycler", "system volume information",
    "temp", "tmp", "cache", "appdata",
    "windows", "program files", "program files (x86)",
}


def _get_common_locations() -> List[str]:
    """获取用户常用文件位置（跨系统）"""
    home = Path.home()
    candidates: List[Path] = []

    if os.name == "nt":  # Windows
        # 从 USERPROFILE / 环境变量获取标准目录
        userprofile = os.environ.get("USERPROFILE", str(home))
        candidates = [
            Path(userprofile) / "Desktop",
            Path(userprofile) / "桌面",
            Path(userprofile) / "Documents",
            Path(userprofile) / "文档",
            Path(userprofile) / "Downloads",
            Path(userprofile) / "下载",
            Path(userprofile) / "OneDrive" / "Documents",
            Path(userprofile) / "OneDrive" / "桌面",
            Path(userprofile) / "OneDrive" / "Desktop",
        ]
    else:  # macOS / Linux
        candidates = [
            home / "Desktop",
            home / "Documents",
            home / "Downloads",
        ]

    return [str(p) for p in candidates if p.exists() and p.is_dir()]


# ── WorkFileLibrary ───────────────────────────────────────────────────────────

class WorkFileLibrary:
    """工作文件库（单例，线程安全）"""

    _lock: threading.RLock = threading.RLock()
    _scan_thread: Optional[threading.Thread] = None
    _scan_status: Dict[str, Any] = {
        "running": False, "scanned": 0, "indexed": 0, "done": False, "error": None
    }

    def __init__(self):
        self._db_path: str = self._resolve_db_path()
        self._init_db()

    # ── Paths ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_db_path() -> str:
        root = Path(__file__).parent.parent
        config_dir = root / "config"
        config_dir.mkdir(exist_ok=True)
        return str(config_dir / "work_file_library.db")

    # ── Database ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS work_files (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    path        TEXT    UNIQUE NOT NULL,
                    name        TEXT    NOT NULL,
                    name_lower  TEXT    NOT NULL,
                    ext         TEXT    NOT NULL,
                    category    TEXT    NOT NULL,
                    size        INTEGER DEFAULT 0,
                    mtime       REAL    DEFAULT 0,
                    indexed_at  REAL    DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_name_lower ON work_files(name_lower);
                CREATE INDEX IF NOT EXISTS idx_category   ON work_files(category);
                CREATE INDEX IF NOT EXISTS idx_ext        ON work_files(ext);
                CREATE INDEX IF NOT EXISTS idx_mtime      ON work_files(mtime);

                CREATE TABLE IF NOT EXISTS watch_folders (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    path         TEXT    UNIQUE NOT NULL,
                    added_at     REAL    DEFAULT 0,
                    last_scanned REAL    DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

    # ── Status ────────────────────────────────────────────────────────────────

    def is_indexed(self) -> bool:
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS cnt FROM work_files").fetchone()
                return (row["cnt"] or 0) > 0
        except Exception:
            return False

    def count(self) -> int:
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS cnt FROM work_files").fetchone()
                return row["cnt"] or 0
        except Exception:
            return 0

    def get_scan_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._scan_status)

    def get_stats(self) -> Dict[str, Any]:
        try:
            with self._conn() as conn:
                total = conn.execute("SELECT COUNT(*) AS cnt FROM work_files").fetchone()["cnt"]
                cats = conn.execute(
                    "SELECT category, COUNT(*) AS cnt FROM work_files GROUP BY category ORDER BY cnt DESC"
                ).fetchall()
                last_scan_row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'last_scan'"
                ).fetchone()
                return {
                    "total": total or 0,
                    "categories": {r["category"]: r["cnt"] for r in cats},
                    "last_scan": float(last_scan_row["value"]) if last_scan_row else None,
                    "scan_status": self.get_scan_status(),
                }
        except Exception:
            return {"total": 0, "categories": {}, "last_scan": None, "scan_status": {}}

    # ── Watch Folders ─────────────────────────────────────────────────────────

    def add_watch_folder(self, folder_path: str) -> bool:
        try:
            resolved = str(Path(folder_path).resolve())
            if not os.path.isdir(resolved):
                return False
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO watch_folders (path, added_at) VALUES (?, ?)",
                    (resolved, time.time())
                )
            return True
        except Exception:
            return False

    def remove_watch_folder(self, folder_path: str) -> bool:
        try:
            resolved = str(Path(folder_path).resolve())
            with self._conn() as conn:
                conn.execute("DELETE FROM watch_folders WHERE path = ?", (resolved,))
            return True
        except Exception:
            return False

    def list_watch_folders(self) -> List[Dict]:
        try:
            with self._conn() as conn:
                rows = conn.execute("SELECT * FROM watch_folders ORDER BY added_at DESC").fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def get_scan_locations(self) -> List[str]:
        """全量扫描位置 = 默认常用位置 + 用户添加"""
        locations = _get_common_locations()
        try:
            with self._conn() as conn:
                rows = conn.execute("SELECT path FROM watch_folders").fetchall()
                for row in rows:
                    p = row["path"]
                    if p not in locations and os.path.isdir(p):
                        locations.append(p)
        except Exception:
            pass
        return locations

    # ── Scan ──────────────────────────────────────────────────────────────────

    def scan_locations(
        self,
        locations: Optional[List[str]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        在后台线程中扫描指定位置（默认为常用位置）的工作文件。
        force=True 时先清空旧索引再重建。
        """
        with self._lock:
            if self._scan_status.get("running"):
                return {"started": False, "reason": "扫描已在运行中"}
            self._scan_status = {
                "running": True, "scanned": 0, "indexed": 0, "done": False, "error": None
            }

        scan_locs = locations if locations is not None else self.get_scan_locations()

        def _worker():
            scanned = 0
            indexed = 0
            batch: List[tuple] = []
            now = time.time()

            try:
                if force:
                    with self._conn() as conn:
                        conn.execute("DELETE FROM work_files")

                for loc in scan_locs:
                    if not os.path.isdir(loc):
                        continue
                    for root_dir, dirs, files in os.walk(loc, topdown=True, followlinks=False):
                        # 过滤无关子目录
                        dirs[:] = [
                            d for d in dirs
                            if not d.startswith(".")
                            and d.lower() not in _SKIP_DIRS
                        ]
                        for fname in files:
                            scanned += 1
                            # 跳过 Office 临时文件
                            if fname.startswith("~$"):
                                continue
                            _, ext = os.path.splitext(fname)
                            ext_lower = ext.lower()
                            if ext_lower not in WORK_FILE_TYPES:
                                continue
                            full_path = os.path.join(root_dir, fname)
                            try:
                                stat = os.stat(full_path)
                            except OSError:
                                continue
                            batch.append((
                                full_path,
                                fname,
                                fname.lower(),
                                ext_lower,
                                WORK_FILE_TYPES[ext_lower],
                                stat.st_size,
                                stat.st_mtime,
                                now,
                            ))
                            indexed += 1
                            if len(batch) >= 200:
                                self._batch_upsert(batch)
                                batch = []
                                with self._lock:
                                    self._scan_status["scanned"] = scanned
                                    self._scan_status["indexed"] = indexed

                if batch:
                    self._batch_upsert(batch)

                # 更新 meta
                with self._conn() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_scan', ?)",
                        (str(now),)
                    )
                    for loc in scan_locs:
                        conn.execute(
                            "UPDATE watch_folders SET last_scanned = ? WHERE path = ?",
                            (now, loc)
                        )

                with self._lock:
                    self._scan_status.update({
                        "running": False, "scanned": scanned,
                        "indexed": indexed, "done": True
                    })
                logger.info(f"[WorkFileLibrary] ✅ 扫描完成: 检查 {scanned} 文件，收录 {indexed} 个工作文件")

            except Exception as exc:
                with self._lock:
                    self._scan_status.update({
                        "running": False, "done": True, "error": str(exc)
                    })
                logger.error(f"[WorkFileLibrary] ❌ 扫描出错: {exc}")

        t = threading.Thread(target=_worker, name="WorkFileLibraryScan", daemon=True)
        self._scan_thread = t
        t.start()
        return {"started": True, "locations": scan_locs}

    def wait_for_scan(self, timeout: float = 8.0) -> bool:
        """等待当前扫描完成，最多等 timeout 秒。返回是否完成。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self._scan_status.get("running"):
                    return True
            time.sleep(0.3)
        return False

    def _batch_upsert(self, rows: List[tuple]) -> None:
        try:
            with self._conn() as conn:
                conn.executemany(
                    """INSERT OR REPLACE INTO work_files
                       (path, name, name_lower, ext, category, size, mtime, indexed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows
                )
        except Exception as exc:
            logger.warning(f"[WorkFileLibrary] ⚠️ 批量写入出错: {exc}")

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 30,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        按文件名关键词搜索工作文件。
        支持多关键词（空格分隔），所有词都须出现在文件名中。
        返回按匹配得分+修改时间排序的列表。
        """
        if not query or not query.strip():
            return []

        q = query.strip().lower()
        tokens = [t for t in re.split(r"[\s_\-\.]", q) if t]

        try:
            with self._conn() as conn:
                # 构建多关键词 LIKE 条件
                if len(tokens) > 1:
                    clauses = " AND ".join("name_lower LIKE ?" for _ in tokens)
                    params = [f"%{t}%" for t in tokens]
                else:
                    clauses = "name_lower LIKE ?"
                    params = [f"%{q}%"]

                if category:
                    clauses += " AND category = ?"
                    params.append(category)

                rows = conn.execute(
                    f"SELECT * FROM work_files WHERE {clauses} ORDER BY mtime DESC LIMIT ?",
                    params + [limit * 2]
                ).fetchall()
        except Exception as exc:
            logger.info(f"[WorkFileLibrary] 搜索出错: {exc}")
            return []

        results: List[Dict[str, Any]] = []
        for row in rows:
            path = row["path"]
            # 验证文件仍然存在（惰性清理）
            if not os.path.exists(path):
                continue

            nl = row["name_lower"]
            # 打分：完整包含 > 靠前出现 > 分散匹配
            if q in nl:
                pos = nl.index(q)
                score = 0.95 - pos * 0.005
            else:
                matched = sum(1 for t in tokens if t in nl)
                score = 0.6 * (matched / len(tokens)) if tokens else 0.5

            results.append({
                "path":       path,
                "name":       row["name"],
                "ext":        row["ext"],
                "category":   row["category"],
                "size":       row["size"],
                "size_str":   _human_size(row["size"]),
                "mtime":      row["mtime"],
                "mtime_str":  _human_time(row["mtime"]),
                "score":      round(score, 3),
            })

        results.sort(key=lambda x: (-x["score"], -x["mtime"]))
        return results[:limit]

    def get_by_category(self, category: str, limit: int = 50) -> List[Dict[str, Any]]:
        """按分类获取最近修改的文件"""
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM work_files WHERE category = ? ORDER BY mtime DESC LIMIT ?",
                    (category, limit)
                ).fetchall()
            return [
                {
                    "path":     r["path"],
                    "name":     r["name"],
                    "ext":      r["ext"],
                    "category": r["category"],
                    "size":     r["size"],
                    "size_str": _human_size(r["size"]),
                    "mtime":    r["mtime"],
                    "mtime_str": _human_time(r["mtime"]),
                }
                for r in rows if os.path.exists(r["path"])
            ]
        except Exception:
            return []

    def get_all_categories_summary(self) -> Dict[str, List[Dict]]:
        """获取所有分类的最近文件摘要（每类最多5条）"""
        summary: Dict[str, List[Dict]] = {}
        for cat in WORK_FILE_TYPES.values():
            if cat not in summary:
                files = self.get_by_category(cat, limit=5)
                if files:
                    summary[cat] = files
        return summary

    def refresh(self, folder_path: Optional[str] = None) -> Dict:
        """刷新指定文件夹（或全部）的索引"""
        locations = [folder_path] if folder_path else None
        return self.scan_locations(locations, force=bool(folder_path is None))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1_048_576:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1_048_576:.1f} MB"


def _human_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ── Category detection from user input ───────────────────────────────────────

def detect_category_from_input(text: str) -> Optional[str]:
    """从用户输入中检测意图文件类型，返回分类名或 None"""
    t = text.lower()
    if any(k in t for k in ["word", "docx", "doc", "word文件", "word文档"]):
        return "Word文档"
    if any(k in t for k in ["excel", "xlsx", "xls", "excel文件", "excel表格", "表格"]):
        return "Excel表格"
    if any(k in t for k in ["ppt", "pptx", "幻灯片", "演示文稿", "演示"]):
        return "PPT演示"
    if any(k in t for k in ["pdf", "pdf文件"]):
        return "PDF文档"
    return None


# ── Global singleton ──────────────────────────────────────────────────────────

_library: Optional[WorkFileLibrary] = None
_library_lock = threading.Lock()


def get_work_file_library() -> WorkFileLibrary:
    global _library
    if _library is None:
        with _library_lock:
            if _library is None:
                _library = WorkFileLibrary()
    return _library


# 模块导入时自动初始化（不扫描）
try:
    get_work_file_library()
except Exception:
    pass
