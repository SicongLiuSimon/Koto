"""
file_scanner.py — Koto 全盘文件扫描器

功能:
  - 枚举所有磁盘分区，后台线程扫描所有文件
  - 持久化索引 (config/file_index.json)
  - 基于文件名的模糊搜索 (difflib + 首字母匹配)
  - 按扩展分类：文档 / 图片 / 视频 / 音频 / 代码 / 压缩包 / 其他
  - open_file(path) — 用系统默认程序打开文件
  - 提供进度回调供 SSE 传输

使用示例:
    from web.file_scanner import FileScanner
import logging

logger = logging.getLogger(__name__)

    FileScanner.start_scan()                        # 后台扫描
    results = FileScanner.search("报告 2025", limit=10)
    FileScanner.open_file(results[0]["path"])
"""

from __future__ import annotations

import difflib
import json
import os
import re
import string
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ─── Configuration ────────────────────────────────────────────────────────────

# 系统/无意义目录 — 跳过，不扫描
_SKIP_DIRS_WIN = {
    "windows",
    "$recycle.bin",
    "recycler",
    "system volume information",
    "programdata",
    "program files",
    "program files (x86)",
    "appdata",
    "localappdata",
    "users\\default",
    "users\\public\\documents\\my music",
    "boot",
    "recovery",
    "perflogs",
    "__pycache__",
    ".git",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "site-packages",
    "dist-packages",
}

_SKIP_EXT = {
    ".sys",
    ".dll",
    ".exe",
    ".pdb",
    ".cab",
    ".msi",
    ".ocx",
    ".drv",
    ".ini",
    ".lnk",
    ".tmp",
    ".log",
    ".dat",
    ".bak",
    ".swp",
    ".swo",
    ".DS_Store",
}

# 文件分类
_CATEGORY = {
    "文档": {
        ".doc",
        ".docx",
        ".pdf",
        ".txt",
        ".md",
        ".rtf",
        ".odt",
        ".wps",
        ".ppt",
        ".pptx",
        ".odp",
        ".xls",
        ".xlsx",
        ".ods",
        ".csv",
        ".html",
        ".htm",
        ".epub",
        ".mobi",
    },
    "图片": {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".tif",
        ".tiff",
        ".ico",
        ".heic",
        ".raw",
        ".cr2",
    },
    "视频": {
        ".mp4",
        ".avi",
        ".mkv",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".ts",
        ".rmvb",
    },
    "音频": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".ape"},
    "代码": {
        ".py",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".cs",
        ".go",
        ".rs",
        ".php",
        ".rb",
        ".swift",
        ".kt",
        ".r",
        ".scala",
        ".sh",
        ".bat",
        ".ps1",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".sql",
        ".css",
        ".scss",
    },
    "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".iso"},
}

# 最大单次扫描文件数（防止内存爆炸）
_MAX_FILES = 2_000_000
# 扫描线程在每处理 N 个文件后释放 GIL，减少 CPU 占用
_YIELD_EVERY = 5_000


# ─── Data Model ───────────────────────────────────────────────────────────────


@dataclass
class FileEntry:
    path: str  # 绝对路径
    name: str  # 文件名（原始大小写）
    name_lower: str  # 文件名小写（用于搜索）
    ext: str  # 扩展名（小写，含圆点）
    size: int  # 字节数
    mtime: float  # 修改时间戳
    category: str  # 文件类别


def _classify(ext: str) -> str:
    for cat, exts in _CATEGORY.items():
        if ext in exts:
            return cat
    return "其他"


# ─── FileScanner ──────────────────────────────────────────────────────────────


class FileScanner:
    """全盘文件扫描与搜索引擎（单例，线程安全）"""

    # index: path_lower -> FileEntry
    _index: Dict[str, FileEntry] = {}
    _lock = threading.RLock()
    _scan_thread: Optional[threading.Thread] = None

    _status: Dict[str, Any] = {
        "running": False,
        "paused": False,
        "finished": False,
        "scanned": 0,
        "indexed": 0,
        "total_estimate": 0,
        "current_dir": "",
        "start_time": None,
        "end_time": None,
        "error": None,
    }

    # 索引持久化路径
    _INDEX_PATH: Optional[str] = None

    # ── Init / Index Path ──────────────────────────────────────────────────────

    @classmethod
    def _get_index_path(cls) -> str:
        if cls._INDEX_PATH:
            return cls._INDEX_PATH
        # 查找 config/ 目录
        root = Path(__file__).parent.parent
        config_dir = root / "config"
        config_dir.mkdir(exist_ok=True)
        cls._INDEX_PATH = str(config_dir / "file_index.json")
        return cls._INDEX_PATH

    @classmethod
    def _load_index(cls) -> None:
        """从磁盘加载上次扫描的索引"""
        path = cls._get_index_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            loaded: Dict[str, FileEntry] = {}
            for pl, d in raw.items():
                try:
                    loaded[pl] = FileEntry(**d)
                except Exception:
                    pass
            with cls._lock:
                cls._index = loaded
            logger.info(f"[FileScanner] 📂 已加载历史索引 {len(loaded):,} 个文件")
        except Exception as e:
            logger.warning(f"[FileScanner] ⚠️ 索引加载失败: {e}")

    @classmethod
    def _save_index(cls) -> None:
        """将内存索引写回磁盘"""
        path = cls._get_index_path()
        try:
            with cls._lock:
                data = {k: asdict(v) for k, v in cls._index.items()}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            logger.info(f"[FileScanner] 💾 索引已保存 {len(data):,} 个文件 → {path}")
        except Exception as e:
            logger.warning(f"[FileScanner] ⚠️ 索引保存失败: {e}")

    # ── Scan ───────────────────────────────────────────────────────────────────

    @classmethod
    def get_drives(cls) -> List[str]:
        """枚举可用磁盘分区（Windows）"""
        if sys.platform != "win32":
            return ["/"]
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
        return drives

    @classmethod
    def start_scan(
        cls,
        drives: Optional[List[str]] = None,
        on_progress: Optional[Callable[[Dict], None]] = None,
    ) -> bool:
        """
        启动后台全盘扫描。
        如果正在扫描中则返回 False。
        """
        with cls._lock:
            if cls._status["running"]:
                return False  # 已经在扫描

        if drives is None:
            drives = cls.get_drives()

        def _run():
            cls._scanner_worker(drives, on_progress)

        t = threading.Thread(target=_run, name="KotoFileScanner", daemon=True)
        cls._scan_thread = t
        with cls._lock:
            cls._status = {
                "running": True,
                "paused": False,
                "finished": False,
                "scanned": 0,
                "indexed": 0,
                "total_estimate": 0,
                "current_dir": "",
                "start_time": time.time(),
                "end_time": None,
                "error": None,
            }
        t.start()
        logger.info(f"[FileScanner] 🚀 开始扫描 {drives}")
        return True

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        with cls._lock:
            s = dict(cls._status)
        s["indexed_count"] = len(cls._index)
        if s["start_time"] and s["running"]:
            s["elapsed"] = round(time.time() - s["start_time"], 1)
        elif s["start_time"] and s["end_time"]:
            s["elapsed"] = round(s["end_time"] - s["start_time"], 1)
        else:
            s["elapsed"] = 0
        return s

    @classmethod
    def _scanner_worker(
        cls,
        drives: List[str],
        on_progress: Optional[Callable[[Dict], None]] = None,
    ) -> None:
        """后台扫描工作线程"""
        new_index: Dict[str, FileEntry] = {}
        scanned = 0
        skipped = 0

        try:
            for drive in drives:
                for root_dir, dirs, files in os.walk(
                    drive, topdown=True, followlinks=False
                ):
                    # 跳过系统目录（原地修改 dirs 阻止 os.walk 递归进入）
                    dirs[:] = [
                        d
                        for d in dirs
                        if d.lower() not in _SKIP_DIRS_WIN and not d.startswith(".")
                    ]

                    # 更新当前目录状态
                    with cls._lock:
                        cls._status["current_dir"] = root_dir

                    for fname in files:
                        scanned += 1
                        if scanned > _MAX_FILES:
                            break

                        _, ext = os.path.splitext(fname)
                        ext_lower = ext.lower()
                        if ext_lower in _SKIP_EXT:
                            skipped += 1
                            continue

                        full_path = os.path.join(root_dir, fname)
                        try:
                            stat = os.stat(full_path)
                        except OSError:
                            skipped += 1
                            continue

                        entry = FileEntry(
                            path=full_path,
                            name=fname,
                            name_lower=fname.lower(),
                            ext=ext_lower,
                            size=stat.st_size,
                            mtime=stat.st_mtime,
                            category=_classify(ext_lower),
                        )
                        new_index[full_path.lower()] = entry

                        # 每 N 个文件刷新进度
                        if scanned % _YIELD_EVERY == 0:
                            with cls._lock:
                                cls._status["scanned"] = scanned
                                cls._status["indexed"] = len(new_index)
                            if on_progress:
                                try:
                                    on_progress(cls.get_status())
                                except Exception:
                                    pass
                            time.sleep(0.01)  # 让出 CPU

                    if scanned > _MAX_FILES:
                        break

            # 将新索引替换到内存
            with cls._lock:
                cls._index = new_index
                cls._status["running"] = False
                cls._status["finished"] = True
                cls._status["scanned"] = scanned
                cls._status["indexed"] = len(new_index)
                cls._status["end_time"] = time.time()

            cls._save_index()
            logger.info(
                f"[FileScanner] ✅ 扫描完成: {scanned:,} 已检查 / {len(new_index):,} 已索引"
            )

        except Exception as e:
            with cls._lock:
                cls._status["running"] = False
                cls._status["error"] = str(e)
            logger.error(f"[FileScanner] ❌ 扫描错误: {e}")

    # ── Search ─────────────────────────────────────────────────────────────────

    @classmethod
    def search(
        cls,
        query: str,
        limit: int = 12,
        ext_filter: Optional[List[str]] = None,
        category_filter: Optional[str] = None,
        min_score: float = 0.35,
    ) -> List[Dict[str, Any]]:
        """
        模糊搜索文件名。
        返回格式: [{path, name, ext, size, mtime, category, score}, ...]
        score 范围 0-1（越高越匹配）。
        """
        if not query or not query.strip():
            return []

        q = query.strip().lower()
        q_tokens = re.split(r"[\s_\-\.]+", q)

        with cls._lock:
            entries = list(cls._index.values())

        if not entries:
            # 尝试从磁盘加载
            cls._load_index()
            with cls._lock:
                entries = list(cls._index.values())

        results: List[Dict[str, Any]] = []

        for entry in entries:
            # 扩展名过滤
            if ext_filter and entry.ext not in ext_filter:
                continue
            if category_filter and entry.category != category_filter:
                continue

            name_no_ext = re.sub(r"\.[^.]+$", "", entry.name_lower)

            # --- 打分策略 ---
            score = 0.0

            # 1. 精确包含（最高优先）
            if q in entry.name_lower:
                score = 0.95

            # 2. 全部 token 都包含在文件名中
            elif q_tokens and all(tok in entry.name_lower for tok in q_tokens):
                score = 0.88 - 0.02 * max(0, len(name_no_ext) - len(q))

            # 3. 大部分 token 包含
            elif q_tokens:
                matched = sum(1 for t in q_tokens if t in entry.name_lower)
                ratio = matched / len(q_tokens)
                if ratio >= 0.5:
                    score = 0.6 * ratio

            # 4. 序列相似度（较慢，仅对分较低的时候用）
            if score < 0.5:
                seq = difflib.SequenceMatcher(
                    None, q, name_no_ext, autojunk=False
                ).ratio()
                if seq > score:
                    score = seq * 0.8  # 序列相似度打九折

            if score < min_score:
                continue

            results.append(
                {
                    "path": entry.path,
                    "name": entry.name,
                    "ext": entry.ext,
                    "size": entry.size,
                    "size_str": _human_size(entry.size),
                    "mtime": entry.mtime,
                    "mtime_str": _human_time(entry.mtime),
                    "category": entry.category,
                    "score": round(score, 3),
                }
            )

        # 排序：score 高 → 修改时间新
        results.sort(key=lambda r: (-r["score"], -r["mtime"]))
        return results[:limit]

    # ── Open ───────────────────────────────────────────────────────────────────

    @classmethod
    def open_file(cls, path: str) -> Dict[str, Any]:
        """用系统默认程序打开文件"""
        if not path or not os.path.exists(path):
            return {"success": False, "error": f"文件不存在: {path}"}
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["open", path])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", path])
            return {
                "success": True,
                "path": path,
                "name": os.path.basename(path),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Stats ──────────────────────────────────────────────────────────────────

    @classmethod
    def stats(cls) -> Dict[str, Any]:
        """索引统计摘要"""
        with cls._lock:
            entries = list(cls._index.values())
        total = len(entries)
        by_cat: Dict[str, int] = {}
        for e in entries:
            by_cat[e.category] = by_cat.get(e.category, 0) + 1
        return {
            "total": total,
            "by_category": by_cat,
            "status": cls.get_status(),
        }

    @classmethod
    def is_indexed(cls) -> bool:
        """是否已有索引数据"""
        with cls._lock:
            return len(cls._index) > 0

    @classmethod
    def ensure_loaded(cls) -> None:
        """若无内存索引，尝试从磁盘加载"""
        if not cls.is_indexed():
            cls._load_index()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


def _human_time(ts: float) -> str:
    import datetime

    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "未知"


# ─── Extract search query from natural language ───────────────────────────────

_STRIP_PREFIXES = re.compile(
    r"^(帮我|请|能不能帮我|你能|麻烦)?"
    r"(找|找一下|找找|搜索|搜一下|查找|打开|打开一下|定位|帮我找|帮我打开|"
    r"open|find|search|locate|look for)\s*",
    re.IGNORECASE,
)
_STRIP_SUFFIXES = re.compile(r"(文件|这个文件|那个文件|的文件|\.?\s*$)", re.IGNORECASE)


def extract_query_from_input(text: str) -> str:
    """
    从自然语言中提取文件搜索关键词。
    例:
      "帮我找一下 报告2025" -> "报告2025"
      "打开 我的简历.docx" -> "我的简历.docx"
    """
    t = text.strip()
    t = _STRIP_PREFIXES.sub("", t).strip()
    # 去掉末尾的"文件"等后缀（但保留扩展名）
    if not re.search(r"\.\w{1,5}$", t):  # 没有扩展名才去后缀
        t = _STRIP_SUFFIXES.sub("", t).strip()
    return t or text.strip()


# ─── Intent detection ─────────────────────────────────────────────────────────

_DISK_SEARCH_KEYWORDS = [
    "找文件",
    "帮我找",
    "打开文件",
    "帮我打开",
    "找一下",
    "找找",
    "搜索文件",
    "定位文件",
    "在哪里",
    "查找文件",
    "哪个文件",
    "找到这个文件",
    "打开一下",
    "找出来",
    "定位一下",
    "show me",
    "open file",
    "find file",
    "find the file",
    "where is",
    "locate file",
    # 列举/归纳/浏览类
    "列出",
    "列举",
    "归纳",
    "列一下",
    "有哪些",
    "看看有什么",
    "显示文件",
    # 扫描类
    "扫描我的电脑",
    "扫描电脑",
    "扫描磁盘",
    "扫描硬盘",
    "全盘扫描",
    "开始扫描",
    "scan my",
    "start scan",
    "全盘搜索",
    # 文件夹监控类
    "监控文件夹",
    "监控目录",
    "开始监控",
    "停止监控",
    "正在监控",
    "监控列表",
    # 文件内容读取/问答类
    "提取字段",
    "提取信息",
    "关键信息",
    "合同信息",
    "解读这个",
    "分析这个文件",
]


def is_disk_search_intent(text: str) -> bool:
    """判断用户输入是否是全盘文件搜索意图"""
    tl = text.lower()
    # 明确的 Windows 路径（如 C:\xxx）= 始终视为磁盘搜索
    if re.search(r"[a-z]:[/\\]", tl):
        return True
    return any(kw in tl for kw in _DISK_SEARCH_KEYWORDS)


# ─── Auto-load on import ──────────────────────────────────────────────────────

# 模块导入时自动加载已有磁盘索引（不扫描，只加载缓存）
try:
    FileScanner.ensure_loaded()
except Exception:
    pass
