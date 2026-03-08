# -*- coding: utf-8 -*-
"""
rating_store.py — 双轨评分存储（用户打分 + 模型自评）
=======================================================

架构：
  workspace/rating_store/ratings.db
  ├── user_ratings   — 用户对每条回复的 1-5 星评分 + 文字反馈
  └── model_evals    — 模型对自身回复的多维度自评分

key 概念：msg_id = MD5(session_name + user_input[:120])
  → 稳定的跨模块消息指纹，无需给 SSE 流打 UUID

对外接口
--------
  rs = RatingStore()
  rs.save_user_rating(msg_id, stars, comment, session_name, user_input, ai_response)
  rs.save_model_eval(msg_id, scores_dict, session_name, task_type)
  rs.combined_score(msg_id) -> float | None   # 0.0~1.0，两轨融合
  rs.get_high_quality_samples(min_combined=0.75) -> List[dict]
  rs.get_stats() -> dict
  RatingStore.make_msg_id(session_name, user_input) -> str
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

# ── 路径 ─────────────────────────────────────────────────────────────────────
import sys as _sys

def _base_dir() -> Path:
    if getattr(_sys, "frozen", False):
        return Path(_sys.executable).parent
    return Path(__file__).resolve().parents[3]

_DB_PATH = _base_dir() / "workspace" / "rating_store" / "ratings.db"

# 用户评分→质量分换算表（1-5星 → 0.0-1.0）
_STAR_TO_QUALITY = {1: 0.0, 2: 0.25, 3: 0.55, 4: 0.82, 5: 1.0}

# 模型自评维度权重
_EVAL_WEIGHTS = {
    "accuracy":        0.35,
    "helpfulness":     0.30,
    "personalization": 0.20,
    "task_completion": 0.15,
}


def _make_id(session_name: str, user_input: str) -> str:
    """稳定的消息指纹（MD5）"""
    key = f"{session_name}::{user_input[:120]}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


class RatingStore:
    """双轨评分数据库。线程安全（WAL模式）。"""

    _lock = threading.Lock()

    def __init__(self, db_path: Path = _DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── 连接管理 ──────────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
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
                CREATE TABLE IF NOT EXISTS user_ratings (
                    msg_id        TEXT PRIMARY KEY,
                    stars         INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
                    comment       TEXT    DEFAULT '',
                    session_name  TEXT    DEFAULT '',
                    user_input    TEXT    DEFAULT '',
                    ai_response   TEXT    DEFAULT '',
                    task_type     TEXT    DEFAULT 'CHAT',
                    created_at    TEXT    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_evals (
                    msg_id            TEXT PRIMARY KEY,
                    accuracy          REAL DEFAULT 0.0,
                    helpfulness       REAL DEFAULT 0.0,
                    personalization   REAL DEFAULT 0.0,
                    task_completion   REAL DEFAULT 0.0,
                    overall           REAL DEFAULT 0.0,
                    reasoning         TEXT DEFAULT '',
                    session_name      TEXT DEFAULT '',
                    task_type         TEXT DEFAULT 'CHAT',
                    created_at        TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_ur_stars ON user_ratings(stars);
                CREATE INDEX IF NOT EXISTS idx_me_overall ON model_evals(overall);
            """)

    # ── 写入 ─────────────────────────────────────────────────────────────────

    @staticmethod
    def make_msg_id(session_name: str, user_input: str) -> str:
        return _make_id(session_name, user_input)

    def save_user_rating(
        self,
        msg_id: str,
        stars: int,
        comment: str = "",
        session_name: str = "",
        user_input: str = "",
        ai_response: str = "",
        task_type: str = "CHAT",
    ) -> bool:
        """保存用户评分（1-5星）。重复提交覆盖旧值。"""
        stars = max(1, min(5, int(stars)))
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO user_ratings
                        (msg_id, stars, comment, session_name, user_input, ai_response, task_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(msg_id) DO UPDATE SET
                        stars=excluded.stars,
                        comment=excluded.comment,
                        created_at=excluded.created_at
                """, (msg_id, stars, comment, session_name,
                      user_input[:2000], ai_response[:2000], task_type, now))
        logger.info(f"[RatingStore] ⭐ user_rating saved: {msg_id[:8]}… stars={stars}")
        return True

    def save_model_eval(
        self,
        msg_id: str,
        scores: Dict[str, float],
        session_name: str = "",
        task_type: str = "CHAT",
        reasoning: str = "",
    ) -> bool:
        """保存模型自评分（各维度 0.0~1.0 + 加权 overall）。"""
        overall = sum(
            scores.get(dim, 0.0) * w for dim, w in _EVAL_WEIGHTS.items()
        )
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO model_evals
                        (msg_id, accuracy, helpfulness, personalization,
                         task_completion, overall, reasoning, session_name, task_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(msg_id) DO UPDATE SET
                        accuracy=excluded.accuracy, helpfulness=excluded.helpfulness,
                        personalization=excluded.personalization,
                        task_completion=excluded.task_completion,
                        overall=excluded.overall, reasoning=excluded.reasoning,
                        created_at=excluded.created_at
                """, (
                    msg_id,
                    scores.get("accuracy",        0.0),
                    scores.get("helpfulness",      0.0),
                    scores.get("personalization",  0.0),
                    scores.get("task_completion",  0.0),
                    overall,
                    reasoning[:1000],
                    session_name, task_type, now,
                ))
        logger.info(f"[RatingStore] 🤖 model_eval saved: {msg_id[:8]}… overall={overall:.2f}")
        return True

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def combined_score(self, msg_id: str) -> Optional[float]:
        """
        综合评分（0.0~1.0）。
        两轨都有时：user_quality * 0.55 + model_overall * 0.45
        只有一轨时：返回该轨分数。
        """
        with self._conn() as conn:
            ur = conn.execute(
                "SELECT stars FROM user_ratings WHERE msg_id=?", (msg_id,)
            ).fetchone()
            me = conn.execute(
                "SELECT overall FROM model_evals WHERE msg_id=?", (msg_id,)
            ).fetchone()

        user_q = _STAR_TO_QUALITY.get(ur["stars"]) if ur else None
        model_o = me["overall"] if me else None

        if user_q is not None and model_o is not None:
            return round(user_q * 0.55 + model_o * 0.45, 4)
        if user_q is not None:
            return round(user_q, 4)
        if model_o is not None:
            return round(model_o, 4)
        return None

    def get_high_quality_samples(
        self,
        min_combined: float = 0.75,
        limit: int = 2000,
    ) -> List[Dict[str, Any]]:
        """
        导出高质量样本（供 TrainingDataBuilder 使用）。
        仅返回「用户评分高 OR 模型自评高」且两轨均有记录的条目。
        """
        rows: List[Dict[str, Any]] = []
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT
                    ur.msg_id,
                    ur.stars,
                    ur.comment,
                    ur.session_name,
                    ur.user_input,
                    ur.ai_response,
                    ur.task_type,
                    me.accuracy,
                    me.helpfulness,
                    me.personalization,
                    me.task_completion,
                    me.overall   AS model_overall,
                    me.reasoning
                FROM user_ratings ur
                JOIN model_evals me ON ur.msg_id = me.msg_id
                ORDER BY (ur.stars * 0.55 + me.overall * 0.45) DESC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                d = dict(row)
                user_q = _STAR_TO_QUALITY.get(d["stars"], 0.0)
                combined = round(user_q * 0.55 + d["model_overall"] * 0.45, 4)
                if combined >= min_combined:
                    d["combined_score"] = combined
                    rows.append(d)
        return rows

    def get_low_quality_samples(
        self,
        max_combined: float = 0.30,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """低分样本 — 用于负例分析或人工重标。"""
        rows: List[Dict[str, Any]] = []
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT ur.msg_id, ur.stars, ur.comment, ur.user_input,
                       ur.ai_response, ur.task_type, me.overall AS model_overall
                FROM user_ratings ur
                JOIN model_evals me ON ur.msg_id = me.msg_id
                WHERE ur.stars <= 2
                ORDER BY ur.stars ASC, me.overall ASC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                d = dict(row)
                user_q = _STAR_TO_QUALITY.get(d["stars"], 0.0)
                d["combined_score"] = round(user_q * 0.55 + d["model_overall"] * 0.45, 4)
                if d["combined_score"] <= max_combined:
                    rows.append(d)
        return rows

    def get_stats(self) -> Dict[str, Any]:
        """统计摘要（供 /api/ratings/stats 路由使用）。"""
        with self._conn() as conn:
            ur_row = conn.execute(
                "SELECT COUNT(*) AS n, AVG(stars) AS avg_stars FROM user_ratings"
            ).fetchone()
            me_row = conn.execute(
                "SELECT COUNT(*) AS n, AVG(overall) AS avg_overall FROM model_evals"
            ).fetchone()
            dist = conn.execute(
                "SELECT stars, COUNT(*) AS cnt FROM user_ratings GROUP BY stars"
            ).fetchall()
            hq = conn.execute("""
                SELECT COUNT(*) AS n FROM user_ratings ur
                JOIN model_evals me ON ur.msg_id = me.msg_id
                WHERE (ur.stars * 0.55 + me.overall * 0.45) >= 0.75
            """).fetchone()

        return {
            "user_ratings": {
                "total":     ur_row["n"],
                "avg_stars": round(ur_row["avg_stars"] or 0, 2),
                "distribution": {str(r["stars"]): r["cnt"] for r in dist},
            },
            "model_evals": {
                "total":       me_row["n"],
                "avg_overall": round(me_row["avg_overall"] or 0, 3),
            },
            "high_quality_paired": hq["n"],
        }

    def user_rating_for(self, msg_id: str) -> Optional[Dict[str, Any]]:
        """获取单条用户评分详情。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_ratings WHERE msg_id=?", (msg_id,)
            ).fetchone()
        return dict(row) if row else None

    def model_eval_for(self, msg_id: str) -> Optional[Dict[str, Any]]:
        """获取单条模型自评详情。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_evals WHERE msg_id=?", (msg_id,)
            ).fetchone()
        return dict(row) if row else None


# ── 全局单例 ─────────────────────────────────────────────────────────────────
_instance: Optional[RatingStore] = None


def get_rating_store() -> RatingStore:
    global _instance
    if _instance is None:
        _instance = RatingStore()
    return _instance
