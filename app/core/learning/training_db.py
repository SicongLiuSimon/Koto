# -*- coding: utf-8 -*-
"""
training_db.py — Koto 本地模型训练数据库（SQLite）
===================================================

在本机维护一个持久化的 SQLite 数据库，专门用于积累、管理和优化
koto-router 任务分类模型的训练数据。

特性
----
- 持久化存储所有训练样本（去重、版本化、质量追踪）
- 多来源合并：合成数据 / 聊天历史 / 用户反馈 / 人工标注
- 人工纠错：用户可为任意样本指定正确标签（优先于自动标签）
- 增量更新：只追加新样本，不重复导入
- 一键导出 JSONL + 重建 koto-router
- 自动触发：新样本积累到阈值后自动重建

数据库路径：workspace/training_db/koto_training.db

用法
----
  python -m app.core.learning.training_db            # 完整流程：采集→导出→重建
  python -m app.core.learning.training_db --stats    # 仅查看统计
  python -m app.core.learning.training_db --export   # 仅导出 JSONL
  python -m app.core.learning.training_db --rebuild  # 仅重建 koto-router
  python -m app.core.learning.training_db --correct "输入文本" CORRECT_TASK
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 路径 ──────────────────────────────────────────────────────────────────────
def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[3]

_BASE  = _base_dir()
_DB_DIR  = _BASE / "workspace" / "training_db"
_DB_PATH = _DB_DIR / "koto_training.db"
_OUT_DIR = _BASE / "workspace" / "training_data"

# ── 常量 ──────────────────────────────────────────────────────────────────────
VALID_TASKS = {
    "CHAT", "CODER", "PAINTER", "FILE_GEN", "DOC_ANNOTATE",
    "RESEARCH", "WEB_SEARCH", "FILE_SEARCH", "SYSTEM", "AGENT",
}

# 积累多少新样本后自动触发重建（0 = 不自动触发）
AUTO_REBUILD_THRESHOLD = 50

_ROUTER_SYSTEM = (
    "你是 Koto AI 的任务路由分类器。"
    "根据用户输入判断任务类型，严格只输出 JSON: {\"task\":\"TYPE\",\"confidence\":0.9}\n"
    "可用类型: CHAT CODER PAINTER FILE_GEN DOC_ANNOTATE RESEARCH WEB_SEARCH FILE_SEARCH SYSTEM AGENT"
)

# ══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DBSample:
    """数据库中的一条训练样本"""
    user_input:  str
    task_type:   str
    confidence:  float      = 0.90
    source:      str        = "synthetic"   # synthetic / chat_history / shadow_trace / manual
    quality:     float      = 0.90
    # 人工纠错字段
    corrected_task: Optional[str] = None    # 若人工标注了正确分类，此处非空
    corrected_by:   str           = ""      # "user" / "admin"
    # 元数据
    notes:       str        = ""
    # 自动生成
    sample_hash: str        = field(default="", init=False)
    created_at:  str        = field(default="", init=False)

    def __post_init__(self):
        self.sample_hash = _hash(self.user_input)
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def effective_task(self) -> str:
        """实际使用的任务类型（人工纠错优先）"""
        return self.corrected_task if self.corrected_task else self.task_type

    @property
    def effective_confidence(self) -> float:
        return 0.99 if self.corrected_task else self.confidence

    def to_ollama_jsonl(self) -> str:
        answer = json.dumps(
            {"task": self.effective_task, "confidence": self.effective_confidence},
            ensure_ascii=False
        )
        return json.dumps({
            "messages": [
                {"role": "system",    "content": _ROUTER_SYSTEM},
                {"role": "user",      "content": self.user_input},
                {"role": "assistant", "content": answer},
            ]
        }, ensure_ascii=False)


def _hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
# 数据库管理
# ══════════════════════════════════════════════════════════════════════════════

class TrainingDB:
    """SQLite 训练数据库管理器"""

    _lock = threading.Lock()

    def __init__(self, db_path: Path = _DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_hash     TEXT    NOT NULL UNIQUE,
                    user_input      TEXT    NOT NULL,
                    task_type       TEXT    NOT NULL,
                    confidence      REAL    DEFAULT 0.90,
                    source          TEXT    DEFAULT 'synthetic',
                    quality         REAL    DEFAULT 0.90,
                    corrected_task  TEXT,
                    corrected_by    TEXT    DEFAULT '',
                    notes           TEXT    DEFAULT '',
                    created_at      TEXT    NOT NULL,
                    updated_at      TEXT    NOT NULL,
                    exported_at     TEXT,
                    active          INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS build_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    built_at        TEXT    NOT NULL,
                    total_samples   INTEGER DEFAULT 0,
                    new_samples     INTEGER DEFAULT 0,
                    export_path     TEXT,
                    ollama_success  INTEGER DEFAULT 0,
                    notes           TEXT    DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS pending_corrections (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_input      TEXT    NOT NULL,
                    predicted_task  TEXT    NOT NULL,
                    correct_task    TEXT,
                    session_id      TEXT    DEFAULT '',
                    created_at      TEXT    NOT NULL,
                    resolved        INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_samples_hash   ON samples(sample_hash);
                CREATE INDEX IF NOT EXISTS idx_samples_source ON samples(source);
                CREATE INDEX IF NOT EXISTS idx_samples_task   ON samples(task_type);
                CREATE INDEX IF NOT EXISTS idx_samples_active ON samples(active);
            """)

    # ── 写入 ─────────────────────────────────────────────────────────────────

    def upsert(self, sample: DBSample) -> Tuple[bool, str]:
        """
        插入样本（已存在则根据质量决定是否更新）。
        返回 (inserted: bool, action: str)
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self._conn() as conn:
                existing = conn.execute(
                    "SELECT id, quality, corrected_task FROM samples WHERE sample_hash=?",
                    (sample.sample_hash,)
                ).fetchone()

                if existing:
                    # 不覆盖人工纠错
                    if existing["corrected_task"]:
                        return False, "skipped_has_correction"
                    # 仅在质量更高时更新
                    if sample.quality > existing["quality"]:
                        conn.execute("""
                            UPDATE samples SET
                                task_type=?, confidence=?, source=?, quality=?,
                                notes=?, updated_at=?
                            WHERE sample_hash=?
                        """, (sample.task_type, sample.confidence, sample.source,
                              sample.quality, sample.notes, now, sample.sample_hash))
                        return False, "updated"
                    return False, "skipped_lower_quality"

                conn.execute("""
                    INSERT INTO samples
                        (sample_hash, user_input, task_type, confidence, source,
                         quality, corrected_task, corrected_by, notes, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (sample.sample_hash, sample.user_input, sample.task_type,
                      sample.confidence, sample.source, sample.quality,
                      sample.corrected_task, sample.corrected_by, sample.notes,
                      sample.created_at, now))
                return True, "inserted"

    def upsert_batch(self, samples: List[DBSample], verbose: bool = False) -> Dict[str, int]:
        """批量插入，返回统计"""
        counts = {"inserted": 0, "updated": 0, "skipped": 0}
        for s in samples:
            _, action = self.upsert(s)
            if action == "inserted":
                counts["inserted"] += 1
            elif action == "updated":
                counts["updated"] += 1
            else:
                counts["skipped"] += 1
        if verbose:
            print(f"  插入: {counts['inserted']}  更新: {counts['updated']}  跳过: {counts['skipped']}")
        return counts

    def correct_label(self, user_input: str, correct_task: str,
                      corrected_by: str = "user", notes: str = "") -> bool:
        """为指定输入打上人工纠错标签（优先级最高）"""
        if correct_task not in VALID_TASKS:
            raise ValueError(f"无效任务类型: {correct_task}，可用: {VALID_TASKS}")
        h = _hash(user_input)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self._conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM samples WHERE sample_hash=?", (h,)
                ).fetchone()
                if existing:
                    conn.execute("""
                        UPDATE samples SET
                            corrected_task=?, corrected_by=?, notes=?, updated_at=?
                        WHERE sample_hash=?
                    """, (correct_task, corrected_by, notes, now, h))
                    return True
                else:
                    # 新建一条人工标注样本
                    s = DBSample(user_input=user_input, task_type=correct_task,
                                 confidence=0.99, source="manual", quality=0.99,
                                 corrected_task=correct_task, corrected_by=corrected_by,
                                 notes=notes)
                    self.upsert(s)
                    return True

    def log_prediction(self, user_input: str, predicted_task: str,
                       session_id: str = "") -> int:
        """
        记录一次模型预测（用于后续纠错分析）。
        返回记录的 ID，用于后续调用 resolve_correction()。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO pending_corrections
                    (user_input, predicted_task, session_id, created_at)
                VALUES (?,?,?,?)
            """, (user_input, predicted_task, session_id, now))
            return cur.lastrowid

    def resolve_correction(self, correction_id: int, correct_task: Optional[str]):
        """
        解决一条待纠错记录。
        correct_task=None 表示预测正确，无需纠错；
        correct_task=X    表示正确分类是 X，写入 samples 表。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT user_input, predicted_task FROM pending_corrections WHERE id=?",
                (correction_id,)
            ).fetchone()
            if not row:
                return
            conn.execute(
                "UPDATE pending_corrections SET correct_task=?, resolved=1 WHERE id=?",
                (correct_task, correction_id)
            )
            if correct_task and correct_task != row["predicted_task"]:
                self.correct_label(row["user_input"], correct_task, corrected_by="user")

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def get_all_active(self, min_quality: float = 0.7) -> List[DBSample]:
        """获取所有活跃样本"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM samples
                WHERE active=1 AND quality >= ?
                ORDER BY quality DESC, created_at ASC
            """, (min_quality,)).fetchall()
        result = []
        for r in rows:
            s = DBSample(
                user_input=r["user_input"],
                task_type=r["task_type"],
                confidence=r["confidence"],
                source=r["source"],
                quality=r["quality"],
                corrected_task=r["corrected_task"],
                corrected_by=r["corrected_by"] or "",
                notes=r["notes"] or "",
            )
            s.sample_hash = r["sample_hash"]
            s.created_at  = r["created_at"]
            result.append(s)
        return result

    def stats(self) -> Dict[str, Any]:
        with self._conn() as conn:
            total   = conn.execute("SELECT COUNT(*) FROM samples WHERE active=1").fetchone()[0]
            by_task = {r[0]: r[1] for r in conn.execute(
                "SELECT COALESCE(corrected_task, task_type), COUNT(*) FROM samples "
                "WHERE active=1 GROUP BY COALESCE(corrected_task, task_type)"
            ).fetchall()}
            by_src  = {r[0]: r[1] for r in conn.execute(
                "SELECT source, COUNT(*) FROM samples WHERE active=1 GROUP BY source"
            ).fetchall()}
            corrected = conn.execute(
                "SELECT COUNT(*) FROM samples WHERE active=1 AND corrected_task IS NOT NULL"
            ).fetchone()[0]
            last_build = conn.execute(
                "SELECT built_at, total_samples FROM build_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            pending_corrections = conn.execute(
                "SELECT COUNT(*) FROM pending_corrections WHERE resolved=0"
            ).fetchone()[0]
        return {
            "total":               total,
            "by_task":             dict(sorted(by_task.items())),
            "by_source":           by_src,
            "manually_corrected":  corrected,
            "pending_corrections": pending_corrections,
            "last_build":          dict(last_build) if last_build else None,
            "db_path":             str(self.db_path),
        }

    def get_unexported_count(self) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM samples WHERE active=1 AND exported_at IS NULL"
            ).fetchone()[0]

    # ── 导出 ─────────────────────────────────────────────────────────────────

    def export_jsonl(self, output_dir: Path = _OUT_DIR,
                     min_quality: float = 0.7) -> Path:
        """导出全量 JSONL 训练文件"""
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = output_dir / f"koto_routing_db_{ts}.jsonl"

        samples = self.get_all_active(min_quality=min_quality)
        if not samples:
            raise RuntimeError("数据库中没有可导出的样本")

        lines = [s.to_ollama_jsonl() for s in samples]
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 标记已导出
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            conn.execute(
                "UPDATE samples SET exported_at=? WHERE active=1 AND exported_at IS NULL",
                (now,)
            )

        print(f"  导出 {len(samples)} 条样本 → {out_path.name}")
        return out_path

    def rebuild_koto_router(self, base_model: str = "auto",
                             output_dir: Path = _OUT_DIR) -> bool:
        """
        导出 JSONL + 生成 Modelfile + 重建 koto-router 模型。
        返回是否成功。
        """
        print("\n[TrainingDB] 🔧 开始重建 koto-router...")

        # 检测可用底座模型
        if base_model == "auto":
            base_model = _detect_best_base_model()
        if not base_model:
            print("[TrainingDB] ⚠️ Ollama 未运行或无可用模型，跳过重建")
            return False

        # 导出 JSONL
        jsonl_path = self.export_jsonl(output_dir=output_dir)

        # 生成 Modelfile
        modelfile_content = _make_modelfile(base_model)
        mf_path = output_dir / "Koto_Router.Modelfile"
        mf_path.write_text(modelfile_content, encoding="utf-8")

        # 运行 ollama create
        print(f"[TrainingDB] 🤖 正在基于 {base_model} 创建 koto-router...")
        result = subprocess.run(
            ["ollama", "create", "koto-router", "-f", str(mf_path)],
            capture_output=True, text=True, timeout=180
        )
        success = result.returncode == 0

        # 记录构建历史
        samples = self.get_all_active()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO build_history
                    (built_at, total_samples, export_path, ollama_success, notes)
                VALUES (?,?,?,?,?)
            """, (now, len(samples), str(jsonl_path), int(success),
                  f"base={base_model}"))

        if success:
            print(f"[TrainingDB] ✅ koto-router 重建成功（{len(samples)} 条样本）")
        else:
            print(f"[TrainingDB] ❌ koto-router 重建失败: {result.stderr.strip()[:200]}")

        return success


# ══════════════════════════════════════════════════════════════════════════════
# 数据采集器
# ══════════════════════════════════════════════════════════════════════════════

class DataHarvester:
    """
    多源数据采集器，将各类来源的数据导入 TrainingDB。

    来源：
    1. SyntheticDataGenerator  — 黄金标准合成样本（每次运行更新）
    2. chats/*.json            — 聊天历史（带 task 标签的对话）
    3. workspace/shadow_traces — ShadowTracer 记录的高质量样本
    4. pending_corrections     — 用户纠错记录（最高优先级）
    """

    def __init__(self, db: TrainingDB):
        self.db = db

    def harvest_all(self, verbose: bool = True) -> Dict[str, int]:
        total = {"inserted": 0, "updated": 0, "skipped": 0}

        def _merge(d):
            for k in total:
                total[k] += d.get(k, 0)

        if verbose: print("\n[Harvester] 📥 开始采集数据...")

        # 1. 合成数据
        r = self.harvest_synthetic(verbose=verbose)
        _merge(r)

        # 2. 聊天历史
        r = self.harvest_chat_history(verbose=verbose)
        _merge(r)

        # 3. Shadow Traces
        r = self.harvest_shadow_traces(verbose=verbose)
        _merge(r)

        if verbose:
            print(f"[Harvester] ✅ 采集完毕 — 新增: {total['inserted']}  "
                  f"更新: {total['updated']}  跳过: {total['skipped']}")
        return total

    def harvest_synthetic(self, verbose: bool = True) -> Dict[str, int]:
        try:
            from app.core.learning.synthetic_data_generator import SyntheticDataGenerator
            gold = SyntheticDataGenerator.generate_all(shuffle=False)
            samples = [
                DBSample(user_input=inp, task_type=task, confidence=conf,
                         source="synthetic_gold", quality=0.96)
                for inp, task, conf in gold
            ]
            r = self.db.upsert_batch(samples, verbose=False)
            if verbose:
                print(f"  [合成数据]   {len(samples)} 条 → "
                      f"新增 {r['inserted']} 更新 {r['updated']} 跳过 {r['skipped']}")
            return r
        except Exception as e:
            if verbose: print(f"  [合成数据] ⚠️ 失败: {e}")
            return {"inserted": 0, "updated": 0, "skipped": 0}

    def harvest_chat_history(self, verbose: bool = True) -> Dict[str, int]:
        chats_dir = _BASE / "chats"
        if not chats_dir.exists():
            return {"inserted": 0, "updated": 0, "skipped": 0}

        # 尝试加载本地路由器用于重分类无标签条目
        _router = None
        try:
            from app.core.routing.local_model_router import LocalModelRouter
            _router = LocalModelRouter
        except Exception:
            pass

        samples = []
        for f in chats_dir.glob("*.json"):
            try:
                turns = json.loads(f.read_text(encoding="utf-8"))
                i = 0
                while i < len(turns) - 1:
                    u, m = turns[i], turns[i+1]
                    i += 2
                    if u.get("role") != "user" or m.get("role") != "model":
                        continue
                    parts = u.get("parts", [])
                    user_text = str(parts[0]).strip() if parts else ""
                    task = m.get("task") or ""
                    if not user_text or len(user_text) < 3:
                        continue
                    if task not in VALID_TASKS:
                        # 尝试用路由器重新分类
                        if _router is not None:
                            try:
                                rtask, conf_str, _ = _router.classify(user_text, timeout=5.0)
                                task = rtask if rtask in VALID_TASKS else "CHAT"
                            except Exception:
                                task = "CHAT"
                        else:
                            task = "CHAT"
                    samples.append(DBSample(
                        user_input=user_text, task_type=task,
                        confidence=0.85, source="chat_history", quality=0.80,
                        notes=f"from:{f.stem}"
                    ))
            except Exception:
                pass

        r = self.db.upsert_batch(samples, verbose=False)
        if verbose:
            print(f"  [聊天历史]   {len(samples)} 条 → "
                  f"新增 {r['inserted']} 更新 {r['updated']} 跳过 {r['skipped']}")
        return r

    def harvest_shadow_traces(self, verbose: bool = True) -> Dict[str, int]:
        shadow_dir = _BASE / "workspace" / "shadow_traces"
        if not shadow_dir.exists():
            return {"inserted": 0, "updated": 0, "skipped": 0}

        samples = []
        for f in shadow_dir.glob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    user_text = rec.get("user_input", "")
                    task      = rec.get("task_type") or "CHAT"
                    feedback  = rec.get("feedback", "")
                    if not user_text or len(user_text) < 3:
                        continue
                    if task not in VALID_TASKS:
                        task = "CHAT"
                    quality = 0.92 if feedback in ("thumbs_up", "workflow_complete") else 0.78
                    samples.append(DBSample(
                        user_input=user_text, task_type=task,
                        confidence=0.90, source="shadow_trace", quality=quality,
                        notes=f"feedback:{feedback}"
                    ))
                except Exception:
                    pass

        r = self.db.upsert_batch(samples, verbose=False)
        if verbose:
            print(f"  [Shadow记录] {len(samples)} 条 → "
                  f"新增 {r['inserted']} 更新 {r['updated']} 跳过 {r['skipped']}")
        return r


# ══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def _detect_best_base_model() -> Optional[str]:
    """检测 Ollama 中最适合做底座的模型"""
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code != 200:
            return None
        tags = [m["name"] for m in resp.json().get("models", [])]
        preferred = ["qwen3:8b", "qwen3:4b", "qwen3:1.7b", "qwen2.5:7b",
                     "qwen2.5:3b", "llama3.1:8b", "llama3.2:3b", "gemma3:4b"]
        for p in preferred:
            if any(p in t for t in tags):
                return p
        return tags[0] if tags else None
    except Exception:
        return None


def _make_modelfile(base_model: str) -> str:
    return f"""FROM {base_model}

SYSTEM \"\"\"
你是 Koto AI 的任务路由分类器。
根据用户输入判断任务类型，严格只输出 JSON: {{"task":"TYPE","confidence":0.9}}

可用类型:
- CHAT       : 知识问答、概念解释、日常对话、翻译、短文本
- CODER      : 编写/调试代码、数据可视化图表
- PAINTER    : 生成/创作图片、壁纸、头像、海报
- FILE_GEN   : 生成 Word/PPT/Excel/PDF 文件
- DOC_ANNOTATE: 修改/润色/批注/校对已有文档或代码
- RESEARCH   : 深入/全面/系统研究某主题
- WEB_SEARCH : 需要实时信息（今天/目前/近况/最新/局势/动态/行情）
- FILE_SEARCH: 在电脑上搜索/定位文件
- SYSTEM     : 操作系统级操作（打开/关闭应用、截图、关机）
- AGENT      : 跨应用自动化（发消息/邮件、设提醒、浏览器操作）

时效性信号词 → WEB_SEARCH: 目前/近况/近期/如今/当下/局势/战况/动态/现状/进展/行情/走势
\"\"\"

PARAMETER temperature 0.1
PARAMETER num_predict 50
PARAMETER num_ctx 4096
"""


# ══════════════════════════════════════════════════════════════════════════════
# 公共 API（供 TrainingDataBuilder 和外部模块调用）
# ══════════════════════════════════════════════════════════════════════════════

_default_db: Optional[TrainingDB] = None

def get_db() -> TrainingDB:
    global _default_db
    if _default_db is None:
        _default_db = TrainingDB()
    return _default_db


def full_pipeline(verbose: bool = True) -> Dict[str, Any]:
    """
    完整流水线：采集数据 → 导入数据库 → 导出 JSONL → 重建 koto-router
    """
    db = get_db()
    harvester = DataHarvester(db)

    # 采集
    harvest_result = harvester.harvest_all(verbose=verbose)

    # 统计
    s = db.stats()
    if verbose:
        _print_stats(s)

    # 重建（仅当有足够样本）
    rebuilt = False
    if s["total"] >= 10:
        rebuilt = db.rebuild_koto_router()

    return {
        "harvest": harvest_result,
        "stats":   s,
        "rebuilt": rebuilt,
    }


def _print_stats(s: Dict[str, Any]):
    print(f"\n{'='*62}")
    print(f"  Koto 训练数据库统计  (总计: {s['total']} 条)")
    print(f"{'='*62}")
    for task, count in s["by_task"].items():
        bar = "█" * (count // 3)
        print(f"  {task:<15} {count:>4} 条  {bar}")
    print(f"{'─'*62}")
    print(f"  来源分布:")
    for src, count in s["by_source"].items():
        print(f"    {src:<25} {count:>4} 条")
    print(f"  人工纠错样本: {s['manually_corrected']} 条")
    print(f"  待解决纠错:   {s['pending_corrections']} 条")
    if s["last_build"]:
        print(f"  上次重建: {s['last_build']['built_at']} "
              f"({s['last_build']['total_samples']} 条)")
    print(f"  数据库路径: {s['db_path']}")
    print(f"{'='*62}\n")


# ══════════════════════════════════════════════════════════════════════════════
# ShadowTracer 钩子（新增：每条用户交互自动记录到 DB）
# ══════════════════════════════════════════════════════════════════════════════

def auto_record_interaction(user_input: str, task_type: str,
                            confidence: float = 0.85,
                            feedback: Optional[str] = None):
    """
    由 UnifiedAgent / SmartDispatcher 调用，自动将每次交互记录进数据库。
    质量由置信度和用户反馈综合决定。仅插入，不阻塞主流程（后台线程执行）。
    """
    def _run():
        try:
            if task_type not in VALID_TASKS or len(user_input.strip()) < 3:
                return
            quality = min(confidence + 0.05, 0.99) if feedback == "thumbs_up" else confidence
            sample = DBSample(
                user_input=user_input.strip(),
                task_type=task_type,
                confidence=confidence,
                source="live_interaction",
                quality=quality,
                notes=f"feedback:{feedback or 'none'}",
            )
            db = get_db()
            inserted, _ = db.upsert(sample)

            # 达到阈值自动重建
            if inserted and AUTO_REBUILD_THRESHOLD > 0:
                unexported = db.get_unexported_count()
                if unexported >= AUTO_REBUILD_THRESHOLD:
                    logger.info(f"[TrainingDB] 达到自动重建阈值 ({unexported} 条)，触发重建...")
                    db.rebuild_koto_router()
        except Exception as e:
            logger.debug(f"[TrainingDB] auto_record 失败（忽略）: {e}")

    threading.Thread(target=_run, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Koto 训练数据库管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m app.core.learning.training_db              # 完整流程
  python -m app.core.learning.training_db --stats      # 查看统计
  python -m app.core.learning.training_db --export     # 仅导出 JSONL
  python -m app.core.learning.training_db --rebuild    # 仅重建模型
  python -m app.core.learning.training_db --correct "目前伊朗战事" WEB_SEARCH
        """
    )
    parser.add_argument("--stats",   action="store_true", help="查看数据库统计")
    parser.add_argument("--export",  action="store_true", help="导出 JSONL")
    parser.add_argument("--rebuild", action="store_true", help="重建 koto-router")
    parser.add_argument("--harvest", action="store_true", help="仅采集数据（不重建）")
    parser.add_argument("--correct", nargs=2, metavar=("INPUT", "TASK"),
                        help="人工纠错: --correct '输入文本' TASK_TYPE")
    args = parser.parse_args()

    db = get_db()

    if args.correct:
        text, task = args.correct
        if task not in VALID_TASKS:
            print(f"❌ 无效任务类型: {task}\n可用: {sorted(VALID_TASKS)}")
            sys.exit(1)
        ok = db.correct_label(text, task, corrected_by="user")
        print(f"✅ 已标注: '{text[:50]}' → {task}")
        sys.exit(0)

    if args.stats:
        _print_stats(db.stats())
        sys.exit(0)

    if args.harvest:
        DataHarvester(db).harvest_all(verbose=True)
        _print_stats(db.stats())
        sys.exit(0)

    if args.export:
        p = db.export_jsonl()
        print(f"✅ 已导出: {p}")
        sys.exit(0)

    if args.rebuild:
        ok = db.rebuild_koto_router()
        sys.exit(0 if ok else 1)

    # 默认：完整流水线
    result = full_pipeline(verbose=True)
    sys.exit(0 if result["rebuilt"] else 0)
