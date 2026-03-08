# -*- coding: utf-8 -*-
"""
FileRegistry — Koto 统一文件元数据注册表
==========================================
解决的核心问题：
  FileScanner / FileIndexer / ArchiveSearchEngine / RAGService / upload 各存各的，
  Agent 找文件无从下手，同一文件可能被多个引擎重复索引。

设计原则：
  - 所有模块在索引/接收一个文件后，都应调用 FileRegistry.register() 注册
  - 以 file_hash (md5) 去重；同一内容多路径 → 保留所有路径但不重复内容
  - SQLite FTS5 支持中英文全文搜索
  - origin_session / origin_goal 追踪文件来源

表 koto_file_registry 字段说明:
  file_id           UUID 主键
  path              绝对路径（UNIQUE 约束）
  name              文件名
  ext               扩展名（小写，含点）
  category          文档 / 图片 / 视频 / 音频 / 代码 / 压缩包 / 其他
  file_hash         MD5（用于去重）
  size_bytes        文件大小
  mtime             最后修改时间（UNIX 时间戳）
  source            manual / scanner / organizer / upload / watcher
  content_preview   提取的文本内容（最多 3000 字，供 FTS 和摘要）
  origin_session_id 来源会话 ID（可选）
  origin_goal_id    来源长期目标 ID（可选）
  indexed_at        首次入库时间
  updated_at        最近更新时间
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 默认路径 ─────────────────────────────────────────────────────────────────
_DEFAULT_DB_PATH = str(
    Path(os.environ.get("KOTO_DB_DIR", Path(__file__).parent.parent.parent.parent / "config"))
    / "koto_checkpoints.sqlite"
)

# ── 单例 ─────────────────────────────────────────────────────────────────────
_registry_instance: Optional["FileRegistry"] = None
_registry_lock = threading.Lock()

# ── 文件分类表 ────────────────────────────────────────────────────────────────
_EXT_CATEGORY: Dict[str, str] = {}
for _cat, _exts in {
    "文档": {".doc", ".docx", ".pdf", ".txt", ".md", ".rtf", ".odt",
              ".wps", ".ppt", ".pptx", ".odp", ".xls", ".xlsx", ".ods",
              ".csv", ".html", ".htm", ".epub"},
    "图片": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
              ".tif", ".tiff", ".heic"},
    "视频": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "音频": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "代码": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go",
              ".rs", ".php", ".rb", ".swift", ".sh", ".bat", ".ps1",
              ".json", ".xml", ".yaml", ".yml", ".sql", ".css"},
    "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"},
}.items():
    for _e in _exts:
        _EXT_CATEGORY[_e] = _cat


def _classify(ext: str) -> str:
    return _EXT_CATEGORY.get(ext.lower(), "其他")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _file_hash(path: str, chunk: int = 65536) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                h.update(buf)
    except Exception:
        return ""
    return h.hexdigest()


def _extract_text_preview(path: str, max_chars: int = 3000) -> str:
    """
    提取文件文本内容前 max_chars 字符。
    支持 txt/md/csv/json/py/代码 直接读取；pdf/docx/xlsx/pptx 通过专用库读取。
    """
    ext = Path(path).suffix.lower()
    try:
        # 纯文本类
        if ext in {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
                   ".html", ".htm", ".py", ".js", ".ts", ".sql", ".sh",
                   ".bat", ".ps1", ".cs", ".java", ".go", ".rs", ".css"}:
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    text = Path(path).read_text(encoding=enc)
                    return text[:max_chars]
                except UnicodeDecodeError:
                    continue
            return ""

        if ext == ".pdf":
            try:
                import PyPDF2
                with open(path, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    parts = []
                    for page in reader.pages[:8]:
                        parts.append(page.extract_text() or "")
                    return "\n".join(parts)[:max_chars]
            except Exception:
                return ""

        if ext == ".docx":
            try:
                from docx import Document
                doc = Document(path)
                return "\n".join(p.text for p in doc.paragraphs)[:max_chars]
            except Exception:
                return ""

        if ext in {".xlsx", ".xls"}:
            try:
                import openpyxl
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                rows = []
                ws = wb.active
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i > 200:
                        break
                    rows.append("\t".join(str(c or "") for c in row))
                return "\n".join(rows)[:max_chars]
            except Exception:
                return ""

    except Exception:
        pass
    return ""


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class FileEntry:
    file_id: str
    path: str
    name: str
    ext: str
    category: str
    file_hash: str
    size_bytes: int
    mtime: float
    source: str                      # manual / scanner / organizer / upload / watcher
    content_preview: str = ""
    origin_session_id: Optional[str] = None
    origin_goal_id: Optional[str] = None
    indexed_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self, include_preview: bool = False) -> Dict[str, Any]:
        d = asdict(self)
        if not include_preview:
            d.pop("content_preview", None)
        return d

    @property
    def snippet(self) -> str:
        """返回前 200 字作为摘要片段。"""
        return (self.content_preview or "")[:200].replace("\n", " ").strip()


# ============================================================================
# FileRegistry
# ============================================================================

class FileRegistry:
    """
    统一文件元数据注册表（单例）。

    基本用法::
        reg = get_file_registry()
        entry = reg.register("/path/to/file.pdf", source="upload", session_id="sess-abc")
        results = reg.search("报价单 2026")
        entry = reg.get_by_path("/path/to/file.pdf")
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS koto_file_registry (
        file_id           TEXT PRIMARY KEY,
        path              TEXT NOT NULL UNIQUE,
        name              TEXT NOT NULL,
        ext               TEXT NOT NULL,
        category          TEXT NOT NULL DEFAULT '其他',
        file_hash         TEXT NOT NULL DEFAULT '',
        size_bytes        INTEGER NOT NULL DEFAULT 0,
        mtime             REAL NOT NULL DEFAULT 0,
        source            TEXT NOT NULL DEFAULT 'manual',
        content_preview   TEXT NOT NULL DEFAULT '',
        origin_session_id TEXT,
        origin_goal_id    TEXT,
        indexed_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_kfr_hash      ON koto_file_registry(file_hash);
    CREATE INDEX IF NOT EXISTS idx_kfr_category  ON koto_file_registry(category);
    CREATE INDEX IF NOT EXISTS idx_kfr_source    ON koto_file_registry(source);
    CREATE INDEX IF NOT EXISTS idx_kfr_name      ON koto_file_registry(name);
    CREATE INDEX IF NOT EXISTS idx_kfr_updated   ON koto_file_registry(updated_at);

    CREATE VIRTUAL TABLE IF NOT EXISTS koto_file_fts
    USING fts5(
        file_id UNINDEXED,
        name,
        content_preview,
        content='koto_file_registry',
        content_rowid='rowid',
        tokenize='unicode61'
    );

    CREATE TRIGGER IF NOT EXISTS kfr_fts_insert
    AFTER INSERT ON koto_file_registry BEGIN
        INSERT INTO koto_file_fts(rowid, file_id, name, content_preview)
        VALUES (new.rowid, new.file_id, new.name, new.content_preview);
    END;

    CREATE TRIGGER IF NOT EXISTS kfr_fts_delete
    AFTER DELETE ON koto_file_registry BEGIN
        INSERT INTO koto_file_fts(koto_file_fts, rowid, file_id, name, content_preview)
        VALUES ('delete', old.rowid, old.file_id, old.name, old.content_preview);
    END;

    CREATE TRIGGER IF NOT EXISTS kfr_fts_update
    AFTER UPDATE ON koto_file_registry BEGIN
        INSERT INTO koto_file_fts(koto_file_fts, rowid, file_id, name, content_preview)
        VALUES ('delete', old.rowid, old.file_id, old.name, old.content_preview);
        INSERT INTO koto_file_fts(rowid, file_id, name, content_preview)
        VALUES (new.rowid, new.file_id, new.name, new.content_preview);
    END;
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._open_conn()
        self._init_schema()
        logger.info(f"[FileRegistry] ✅ 初始化完成 → {self._db_path}")

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    _DDL_EXTRA = """
    CREATE TABLE IF NOT EXISTS koto_file_op_log (
        op_id       TEXT PRIMARY KEY,
        op_type     TEXT NOT NULL,
        src_path    TEXT NOT NULL,
        dst_path    TEXT,
        meta        TEXT,
        timestamp   TEXT NOT NULL,
        undone      INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_kfol_ts ON koto_file_op_log(timestamp);

    CREATE TABLE IF NOT EXISTS koto_file_tags (
        path        TEXT NOT NULL,
        tag         TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (path, tag)
    );
    CREATE INDEX IF NOT EXISTS idx_kft_tag ON koto_file_tags(tag);

    CREATE TABLE IF NOT EXISTS koto_file_favorites (
        path        TEXT NOT NULL PRIMARY KEY,
        added_at    TEXT NOT NULL
    );
    """

    def _init_schema(self):
        self._conn.executescript(self._DDL)
        self._conn.executescript(self._DDL_EXTRA)
        self._conn.commit()

    # ── 注册 ──────────────────────────────────────────────────────────────────

    def register(
        self,
        path: str,
        source: str = "manual",
        session_id: Optional[str] = None,
        goal_id: Optional[str] = None,
        extract_content: bool = True,
    ) -> Optional[FileEntry]:
        """
        注册一个文件到 FileRegistry。

        - 如果 path 已存在，更新元数据（mtime / hash / content_preview）
        - 如果 hash 相同且 mtime 未变，跳过内容重解析（快速更新）
        - extract_content=False 时跳过文本提取（扫描大量文件时设为 False）
        """
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None

        stat = p.stat()
        mtime = stat.st_mtime
        size = stat.st_size
        ext = p.suffix.lower()
        name = p.name
        category = _classify(ext)

        # 快速路径：路径和 mtime 都没变，直接返回
        existing = self.get_by_path(path)
        if existing and abs(existing.mtime - mtime) < 1.0 and existing.file_hash:
            return existing

        # 计算 hash（用于去重检测，不阻止注册）
        fhash = _file_hash(path)

        # 提取内容
        content_preview = ""
        if extract_content:
            try:
                content_preview = _extract_text_preview(path)
            except Exception:
                pass

        now = _now_iso()

        if existing:
            # 更新
            self._conn.execute(
                """UPDATE koto_file_registry
                   SET file_hash=?, size_bytes=?, mtime=?, content_preview=?, updated_at=?
                   WHERE path=?""",
                (fhash, size, mtime, content_preview, now, path),
            )
            self._conn.commit()
            return self.get_by_path(path)
        else:
            # 插入
            file_id = str(uuid.uuid4())
            entry = FileEntry(
                file_id=file_id, path=path, name=name, ext=ext,
                category=category, file_hash=fhash, size_bytes=size,
                mtime=mtime, source=source,
                content_preview=content_preview,
                origin_session_id=session_id,
                origin_goal_id=goal_id,
            )
            self._conn.execute(
                """INSERT INTO koto_file_registry
                   (file_id, path, name, ext, category, file_hash, size_bytes, mtime,
                    source, content_preview, origin_session_id, origin_goal_id,
                    indexed_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entry.file_id, entry.path, entry.name, entry.ext, entry.category,
                 entry.file_hash, entry.size_bytes, entry.mtime, entry.source,
                 entry.content_preview, entry.origin_session_id, entry.origin_goal_id,
                 entry.indexed_at, entry.updated_at),
            )
            self._conn.commit()
            logger.debug(f"[FileRegistry] 注册文件 {name} [{category}] hash={fhash[:8]}")
            return entry

    def batch_register(
        self,
        paths: List[str],
        source: str = "scanner",
        extract_content: bool = False,
    ) -> Tuple[int, int]:
        """批量注册，返回 (新增数, 更新数)。extract_content 默认 False（快速扫描）。"""
        added, updated = 0, 0
        for path in paths:
            try:
                existing = self.get_by_path(path)
                entry = self.register(path, source=source, extract_content=extract_content)
                if entry:
                    if existing:
                        updated += 1
                    else:
                        added += 1
            except Exception as e:
                logger.debug(f"[FileRegistry] batch_register skip {path}: {e}")
        return added, updated

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get_by_path(self, path: str) -> Optional[FileEntry]:
        row = self._conn.execute(
            "SELECT * FROM koto_file_registry WHERE path = ?", (path,)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def get_by_id(self, file_id: str) -> Optional[FileEntry]:
        row = self._conn.execute(
            "SELECT * FROM koto_file_registry WHERE file_id = ?", (file_id,)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def get_duplicates(self) -> List[List[FileEntry]]:
        """返回内容相同（hash 相同）的文件组列表。"""
        rows = self._conn.execute(
            """SELECT file_hash FROM koto_file_registry
               WHERE file_hash != ''
               GROUP BY file_hash HAVING COUNT(*) > 1"""
        ).fetchall()
        groups = []
        for row in rows:
            dup_rows = self._conn.execute(
                "SELECT * FROM koto_file_registry WHERE file_hash = ?", (row[0],)
            ).fetchall()
            groups.append([self._row_to_entry(r) for r in dup_rows])
        return groups

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 20,
    ) -> List[FileEntry]:
        """
        统一搜索：
        1. 先用 FTS5 全文搜索 name + content_preview
        2. 再用 LIKE 文件名模糊匹配补充（去重）
        3. 可选按 category / source 过滤
        """
        seen_ids: set = set()
        results: List[FileEntry] = []

        # — FTS 搜索 —
        try:
            fts_rows = self._conn.execute(
                """SELECT r.* FROM koto_file_registry r
                   INNER JOIN koto_file_fts f ON r.rowid = f.rowid
                   WHERE koto_file_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (self._escape_fts(query), limit * 2),
            ).fetchall()
            for row in fts_rows:
                entry = self._row_to_entry(row)
                if self._filter_ok(entry, category, source) and entry.file_id not in seen_ids:
                    results.append(entry)
                    seen_ids.add(entry.file_id)
        except Exception as e:
            logger.debug(f"[FileRegistry] FTS 搜索出错: {e}")

        # — 文件名 LIKE 模糊补充 —
        if len(results) < limit:
            like_q = f"%{query}%"
            clauses = ["name LIKE ?"]
            params: List[Any] = [like_q]
            if category:
                clauses.append("category = ?")
                params.append(category)
            if source:
                clauses.append("source = ?")
                params.append(source)
            params.append(limit - len(results))
            like_rows = self._conn.execute(
                f"SELECT * FROM koto_file_registry WHERE {' AND '.join(clauses)} LIMIT ?",
                params,
            ).fetchall()
            for row in like_rows:
                entry = self._row_to_entry(row)
                if entry.file_id not in seen_ids:
                    results.append(entry)
                    seen_ids.add(entry.file_id)

        return results[:limit]

    def list_recent(
        self,
        days: int = 7,
        category: Optional[str] = None,
        limit: int = 30,
    ) -> List[FileEntry]:
        """列出最近 N 天索引的文件。"""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        clauses = ["indexed_at >= ?"]
        params: List[Any] = [cutoff]
        if category:
            clauses.append("category = ?")
            params.append(category)
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM koto_file_registry WHERE {' AND '.join(clauses)} ORDER BY indexed_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count(self, category: Optional[str] = None, source: Optional[str] = None) -> int:
        clauses, params = [], []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._conn.execute(
            f"SELECT COUNT(*) FROM koto_file_registry {where}", params
        ).fetchone()[0]

    def stats(self) -> Dict[str, Any]:
        """返回按类别分组的统计摘要。"""
        rows = self._conn.execute(
            "SELECT category, COUNT(*) as cnt, SUM(size_bytes) as total_size "
            "FROM koto_file_registry GROUP BY category"
        ).fetchall()
        by_cat = {r["category"]: {"count": r["cnt"], "size_bytes": r["total_size"] or 0}
                  for r in rows}
        total = sum(v["count"] for v in by_cat.values())
        return {"total": total, "by_category": by_cat}

    def delete(self, path: str) -> bool:
        rows = self._conn.execute(
            "DELETE FROM koto_file_registry WHERE path = ?", (path,)
        ).rowcount
        self._conn.commit()
        return rows > 0

    # ── 路径更新（重命名 / 移动后同步注册表）────────────────────────────────────

    def update_path(self, old_path: str, new_path: str) -> bool:
        """文件被重命名或移动后，同步更新注册表中的路径与文件名。"""
        rows = self._conn.execute(
            "UPDATE koto_file_registry SET path=?, name=?, updated_at=? WHERE path=?",
            (new_path, Path(new_path).name, _now_iso(), old_path),
        ).rowcount
        if rows:
            self._conn.execute(
                "UPDATE koto_file_tags SET path=? WHERE path=?", (new_path, old_path)
            )
            self._conn.execute(
                "UPDATE koto_file_favorites SET path=? WHERE path=?", (new_path, old_path)
            )
        self._conn.commit()
        return rows > 0

    # ── 操作日志（撤销支持）──────────────────────────────────────────────────

    def log_op(
        self,
        op_type: str,
        src_path: str,
        dst_path: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """记录一次文件操作，返回 op_id。"""
        import json as _json

        op_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO koto_file_op_log (op_id, op_type, src_path, dst_path, meta, timestamp)"
            " VALUES (?,?,?,?,?,?)",
            (op_id, op_type, src_path, dst_path, _json.dumps(meta or {}), _now_iso()),
        )
        self._conn.commit()
        return op_id

    def get_op_log(self, limit: int = 20) -> List[Dict[str, Any]]:
        """返回最近 N 条操作记录（含撤销状态）。"""
        import json as _json

        rows = self._conn.execute(
            "SELECT * FROM koto_file_op_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["meta"] = _json.loads(d.get("meta") or "{}")
            except Exception:
                d["meta"] = {}
            result.append(d)
        return result

    def pop_last_undoable_op(self) -> Optional[Dict[str, Any]]:
        """取出最近一条未撤销操作，并标记为已撤销。用于 undo 功能。"""
        import json as _json

        row = self._conn.execute(
            "SELECT * FROM koto_file_op_log WHERE undone=0 ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["meta"] = _json.loads(d.get("meta") or "{}")
        except Exception:
            d["meta"] = {}
        self._conn.execute(
            "UPDATE koto_file_op_log SET undone=1 WHERE op_id=?", (d["op_id"],)
        )
        self._conn.commit()
        return d

    # ── 标签系统 ─────────────────────────────────────────────────────────────

    def add_tag(self, path: str, tag: str) -> bool:
        """为文件添加一个标签。"""
        tag = tag.strip()
        if not tag:
            return False
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO koto_file_tags (path, tag, created_at) VALUES (?,?,?)",
                (path, tag, _now_iso()),
            )
            self._conn.commit()
            return True
        except Exception:
            return False

    def remove_tag(self, path: str, tag: str) -> bool:
        """移除文件的某个标签。"""
        rows = self._conn.execute(
            "DELETE FROM koto_file_tags WHERE path=? AND tag=?", (path, tag)
        ).rowcount
        self._conn.commit()
        return rows > 0

    def get_tags(self, path: str) -> List[str]:
        """获取一个文件的所有标签。"""
        rows = self._conn.execute(
            "SELECT tag FROM koto_file_tags WHERE path=? ORDER BY tag", (path,)
        ).fetchall()
        return [r["tag"] for r in rows]

    def list_by_tag(self, tag: str, limit: int = 50) -> List[str]:
        """列出拥有指定标签的所有文件路径。"""
        rows = self._conn.execute(
            "SELECT path FROM koto_file_tags WHERE tag=? ORDER BY created_at DESC LIMIT ?",
            (tag, limit),
        ).fetchall()
        return [r["path"] for r in rows]

    def list_all_tags(self) -> List[Dict[str, Any]]:
        """列出所有标签及其使用次数。"""
        rows = self._conn.execute(
            "SELECT tag, COUNT(*) as cnt FROM koto_file_tags GROUP BY tag ORDER BY cnt DESC"
        ).fetchall()
        return [{"tag": r["tag"], "count": r["cnt"]} for r in rows]

    def clear_tags(self, path: str) -> int:
        """清除文件的所有标签，返回被删除的数量。"""
        rows = self._conn.execute(
            "DELETE FROM koto_file_tags WHERE path=?", (path,)
        ).rowcount
        self._conn.commit()
        return rows

    # ── 收藏夹 ───────────────────────────────────────────────────────────────

    def add_favorite(self, path: str) -> bool:
        """将文件加入收藏夹。"""
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO koto_file_favorites (path, added_at) VALUES (?,?)",
                (path, _now_iso()),
            )
            self._conn.commit()
            return True
        except Exception:
            return False

    def remove_favorite(self, path: str) -> bool:
        """从收藏夹移除文件。"""
        rows = self._conn.execute(
            "DELETE FROM koto_file_favorites WHERE path=?", (path,)
        ).rowcount
        self._conn.commit()
        return rows > 0

    def list_favorites(self) -> List[str]:
        """返回所有收藏的文件路径列表。"""
        rows = self._conn.execute(
            "SELECT path FROM koto_file_favorites ORDER BY added_at DESC"
        ).fetchall()
        return [r["path"] for r in rows]

    # ── 磁盘分析辅助 ─────────────────────────────────────────────────────────

    def list_large_files(self, min_bytes: int = 10 * 1024 * 1024, limit: int = 20) -> List[FileEntry]:
        """返回注册表中大于 min_bytes 字节的文件，按大小降序。"""
        rows = self._conn.execute(
            "SELECT * FROM koto_file_registry WHERE size_bytes >= ? ORDER BY size_bytes DESC LIMIT ?",
            (min_bytes, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def list_old_files(self, days_old: int = 180, limit: int = 20) -> List[FileEntry]:
        """返回注册表中修改时间超过 days_old 天的文件，按 mtime 升序（最旧优先）。"""
        from datetime import timedelta

        cutoff_ts = (datetime.now() - timedelta(days=days_old)).timestamp()
        rows = self._conn.execute(
            "SELECT * FROM koto_file_registry WHERE mtime <= ? ORDER BY mtime ASC LIMIT ?",
            (cutoff_ts, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _escape_fts(query: str) -> str:
        """将用户输入转义为 FTS5 安全查询字符串（支持多词）。"""
        words = query.strip().split()
        if not words:
            return '""'
        return " ".join(f'"{w}"' for w in words)

    @staticmethod
    def _filter_ok(entry: FileEntry, category: Optional[str], source: Optional[str]) -> bool:
        if category and entry.category != category:
            return False
        if source and entry.source != source:
            return False
        return True

    def _row_to_entry(self, row: sqlite3.Row) -> FileEntry:
        d = dict(row)
        return FileEntry(**{k: v for k, v in d.items() if k in FileEntry.__dataclass_fields__})


# ============================================================================
# 单例访问
# ============================================================================

def get_file_registry(db_path: Optional[str] = None) -> FileRegistry:
    global _registry_instance
    if _registry_instance is None:
        with _registry_lock:
            if _registry_instance is None:
                _registry_instance = FileRegistry(db_path=db_path)
    return _registry_instance
