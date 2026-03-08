# -*- coding: utf-8 -*-
"""
Koto Task Ledger
================
持久化任务台账，基于与 CheckpointManager 共用的 SQLite 数据库。

功能：
  - 每次 Agent 执行自动创建 TaskRecord
  - 记录任务全生命周期（步骤、工具调用、耗时、结果摘要）
  - 跨会话查询："列出今天的任务"、"任务 X 执行到哪了"
  - 支持外部注入取消请求（interrupt_task / cancel_task）
  - 与 ProgressBus 联动，状态变更自动广播

表结构（只读兼容，不侵入现有 checkpoints* 表）：
  koto_tasks        — 任务主表
  koto_task_steps   — 步骤明细表
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 默认路径（与 CheckpointManager 同库文件）────────────────────────────────
_DEFAULT_DB_PATH = str(
    Path(os.environ.get("KOTO_DB_DIR", Path(__file__).parent.parent.parent.parent / "config"))
    / "koto_checkpoints.sqlite"
)

# ── 单例 ─────────────────────────────────────────────────────────────────────
_ledger_instance: Optional["TaskLedger"] = None
_ledger_lock = threading.Lock()


# ============================================================================
# 枚举 & 数据类
# ============================================================================

class TaskStatus(str, Enum):
    PENDING    = "pending"      # 已创建，未开始
    RUNNING    = "running"      # 执行中
    WAITING    = "waiting"      # 等待人工确认（Human-in-loop）
    COMPLETED  = "completed"    # 成功完成
    FAILED     = "failed"       # 执行失败
    CANCELLED  = "cancelled"    # 被取消
    RETRYING   = "retrying"     # 重试中


@dataclass
class TaskRecord:
    """任务台账条目（对应 koto_tasks 表的一行）"""
    task_id: str
    session_id: str
    user_input: str
    status: TaskStatus = TaskStatus.PENDING

    # 分类/来源
    task_type: Optional[str] = None       # "agent"/"ppt"/"code"/"scheduler"…
    skill_id: Optional[str] = None
    source: str = "agent"                 # 发起来源

    # 时间戳
    created_at: str = field(default_factory=lambda: _now_iso())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # 执行情况
    step_count: int = 0
    tool_calls: int = 0                   # 累计工具调用次数
    retry_count: int = 0
    error: Optional[str] = None
    result_summary: Optional[str] = None  # 最终答案的前 500 字

    # 控制标志
    interrupt_requested: bool = False     # 外部要求暂停（Human-in-loop）
    cancel_requested: bool = False        # 外部要求取消

    # 额外元数据（JSON 字符串存储）
    metadata: str = "{}"

    # ── 非持久化字段 ─────────────────────────────────────────────────────
    steps: List["StepRecord"] = field(default_factory=list, compare=False, repr=False)

    @property
    def elapsed_seconds(self) -> Optional[float]:
        if not self.started_at:
            return None
        end = self.completed_at or _now_iso()
        try:
            fmt = "%Y-%m-%dT%H:%M:%S.%f"
            s = datetime.strptime(self.started_at[:26], fmt)
            e = datetime.strptime(end[:26], fmt)
            return (e - s).total_seconds()
        except Exception:
            return None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["elapsed_seconds"] = self.elapsed_seconds
        d.pop("steps", None)
        return d


@dataclass
class StepRecord:
    """单步骤记录（对应 koto_task_steps 表的一行）"""
    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    task_id: str = ""
    step_index: int = 0
    step_type: str = ""           # THOUGHT / ACTION / OBSERVATION / ANSWER / ERROR
    content: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[str] = None   # JSON str
    observation: Optional[str] = None
    created_at: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# 工具函数
# ============================================================================

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


# ============================================================================
# TaskLedger
# ============================================================================

class TaskLedger:
    """
    持久化任务台账。

    使用示例::

        ledger = get_ledger()

        # 创建一条新任务
        task = ledger.create(
            session_id="sess-abc",
            user_input="帮我写一份周报",
            task_type="agent",
        )

        # 更新状态
        ledger.mark_running(task.task_id)

        # 追加步骤
        ledger.add_step(task.task_id, step_type="THOUGHT", content="…")

        # 完成
        ledger.mark_completed(task.task_id, result_summary="已完成周报")

        # 查询
        tasks = ledger.list_tasks(session_id="sess-abc", limit=20)

        # 取消
        ledger.cancel_task(task.task_id)
    """

    # ── Schema ───────────────────────────────────────────────────────────────
    _DDL = """
    CREATE TABLE IF NOT EXISTS koto_tasks (
        task_id           TEXT PRIMARY KEY,
        session_id        TEXT NOT NULL,
        user_input        TEXT NOT NULL,
        status            TEXT NOT NULL DEFAULT 'pending',
        task_type         TEXT,
        skill_id          TEXT,
        source            TEXT NOT NULL DEFAULT 'agent',
        created_at        TEXT NOT NULL,
        started_at        TEXT,
        completed_at      TEXT,
        step_count        INTEGER NOT NULL DEFAULT 0,
        tool_calls        INTEGER NOT NULL DEFAULT 0,
        retry_count       INTEGER NOT NULL DEFAULT 0,
        error             TEXT,
        result_summary    TEXT,
        interrupt_requested INTEGER NOT NULL DEFAULT 0,
        cancel_requested    INTEGER NOT NULL DEFAULT 0,
        metadata          TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_koto_tasks_session  ON koto_tasks(session_id);
    CREATE INDEX IF NOT EXISTS idx_koto_tasks_status   ON koto_tasks(status);
    CREATE INDEX IF NOT EXISTS idx_koto_tasks_created  ON koto_tasks(created_at);

    CREATE TABLE IF NOT EXISTS koto_task_steps (
        step_id     TEXT PRIMARY KEY,
        task_id     TEXT NOT NULL REFERENCES koto_tasks(task_id),
        step_index  INTEGER NOT NULL,
        step_type   TEXT NOT NULL,
        content     TEXT NOT NULL,
        tool_name   TEXT,
        tool_args   TEXT,
        observation TEXT,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_koto_steps_task  ON koto_task_steps(task_id);
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._open_conn()
        self._init_schema()
        logger.info(f"[TaskLedger] ✅ 初始化完成 → {self._db_path}")

    # ── 底层 DB ──────────────────────────────────────────────────────────────

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self):
        self._conn.executescript(self._DDL)
        self._conn.commit()

    # ── 任务 CRUD ─────────────────────────────────────────────────────────────

    def create(
        self,
        session_id: str,
        user_input: str,
        task_type: Optional[str] = None,
        skill_id: Optional[str] = None,
        source: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        """创建新任务记录，返回 TaskRecord 对象。"""
        task = TaskRecord(
            task_id=str(uuid.uuid4()),
            session_id=session_id,
            user_input=user_input[:1000],  # 限制长度
            task_type=task_type,
            skill_id=skill_id,
            source=source,
            metadata=json.dumps(metadata or {}, ensure_ascii=False),
        )
        self._conn.execute(
            """
            INSERT INTO koto_tasks
              (task_id, session_id, user_input, status, task_type, skill_id,
               source, created_at, step_count, tool_calls, retry_count,
               interrupt_requested, cancel_requested, metadata)
            VALUES
              (:task_id, :session_id, :user_input, :status, :task_type, :skill_id,
               :source, :created_at, 0, 0, 0, 0, 0, :metadata)
            """,
            {
                "task_id": task.task_id,
                "session_id": task.session_id,
                "user_input": task.user_input,
                "status": task.status.value,
                "task_type": task.task_type,
                "skill_id": task.skill_id,
                "source": task.source,
                "created_at": task.created_at,
                "metadata": task.metadata,
            },
        )
        self._conn.commit()
        logger.debug(f"[TaskLedger] 创建任务 {task.task_id[:8]} session={session_id[:8]}")
        return task

    def get(self, task_id: str, include_steps: bool = False) -> Optional[TaskRecord]:
        """按 task_id 查询。include_steps=True 时同时加载步骤列表。"""
        row = self._conn.execute(
            "SELECT * FROM koto_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        rec = self._row_to_record(row)
        if include_steps:
            rec.steps = self._get_steps(task_id)
        return rec

    def _row_to_record(self, row: sqlite3.Row) -> TaskRecord:
        d = dict(row)
        d["status"] = TaskStatus(d["status"])
        d["interrupt_requested"] = bool(d["interrupt_requested"])
        d["cancel_requested"] = bool(d["cancel_requested"])
        d.pop("steps", None)
        return TaskRecord(**{k: v for k, v in d.items() if k in TaskRecord.__dataclass_fields__})

    def list_tasks(
        self,
        session_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        source: Optional[str] = None,
        date_from: Optional[str] = None,   # ISO 日期前缀，如 "2026-03-04"
        limit: int = 50,
        offset: int = 0,
    ) -> List[TaskRecord]:
        """多条件查询任务列表，按创建时间倒序。"""
        clauses, params = [], []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if date_from:
            clauses.append("created_at >= ?")
            params.append(date_from)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM koto_tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(
        self,
        session_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        date_from: Optional[str] = None,
    ) -> int:
        clauses, params = [], []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if date_from:
            clauses.append("created_at >= ?")
            params.append(date_from)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM koto_tasks {where}", params
        ).fetchone()
        return row[0] if row else 0

    # ── 状态变更 ──────────────────────────────────────────────────────────────

    def _update_fields(self, task_id: str, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [task_id]
        self._conn.execute(
            f"UPDATE koto_tasks SET {sets} WHERE task_id = ?", vals
        )
        self._conn.commit()

    def mark_running(self, task_id: str):
        self._update_fields(task_id, status=TaskStatus.RUNNING.value, started_at=_now_iso())
        self._notify_status_change(task_id, TaskStatus.RUNNING)

    def mark_completed(self, task_id: str, result_summary: Optional[str] = None):
        self._update_fields(
            task_id,
            status=TaskStatus.COMPLETED.value,
            completed_at=_now_iso(),
            result_summary=(result_summary or "")[:500],
        )
        self._notify_status_change(task_id, TaskStatus.COMPLETED)

    def mark_failed(self, task_id: str, error: str):
        self._update_fields(
            task_id,
            status=TaskStatus.FAILED.value,
            completed_at=_now_iso(),
            error=str(error)[:1000],
        )
        self._notify_status_change(task_id, TaskStatus.FAILED)

    def mark_cancelled(self, task_id: str):
        self._update_fields(
            task_id,
            status=TaskStatus.CANCELLED.value,
            completed_at=_now_iso(),
        )
        self._notify_status_change(task_id, TaskStatus.CANCELLED)

    def mark_waiting(self, task_id: str, reason: str = "human_in_loop"):
        """标记为等待人工确认。"""
        row = self._conn.execute(
            "SELECT metadata FROM koto_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        meta = json.loads(row["metadata"]) if row else {}
        meta["waiting_reason"] = reason
        self._update_fields(
            task_id,
            status=TaskStatus.WAITING.value,
            metadata=json.dumps(meta, ensure_ascii=False),
        )
        self._notify_status_change(task_id, TaskStatus.WAITING)

    def increment_retries(self, task_id: str):
        self._conn.execute(
            "UPDATE koto_tasks SET retry_count = retry_count + 1, status = ? WHERE task_id = ?",
            (TaskStatus.RETRYING.value, task_id),
        )
        self._conn.commit()

    # ── 中断 / 取消控制 ───────────────────────────────────────────────────────

    def request_interrupt(self, task_id: str):
        """请求暂停任务，等待人工确认后可 resume。"""
        self._update_fields(task_id, interrupt_requested=1)
        logger.info(f"[TaskLedger] ⏸ 打断请求 → task {task_id[:8]}")

    def resume_task(self, task_id: str):
        """恢复被 interrupt 的任务。"""
        self._update_fields(
            task_id,
            interrupt_requested=0,
            status=TaskStatus.RUNNING.value,
        )
        logger.info(f"[TaskLedger] ▶ 恢复任务 → task {task_id[:8]}")

    def cancel_task(self, task_id: str):
        """请求取消任务（执行线程应在下次检查时停止）。"""
        self._update_fields(task_id, cancel_requested=1)
        logger.info(f"[TaskLedger] ✖ 取消请求 → task {task_id[:8]}")

    def is_cancel_requested(self, task_id: str) -> bool:
        """供执行线程轮询检查。"""
        row = self._conn.execute(
            "SELECT cancel_requested FROM koto_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return bool(row["cancel_requested"]) if row else False

    def is_interrupt_requested(self, task_id: str) -> bool:
        row = self._conn.execute(
            "SELECT interrupt_requested FROM koto_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return bool(row["interrupt_requested"]) if row else False

    # ── 步骤记录 ──────────────────────────────────────────────────────────────

    def add_step(
        self,
        task_id: str,
        step_type: str,
        content: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        observation: Optional[str] = None,
    ) -> StepRecord:
        """追加一条步骤记录，同时更新任务的 step_count / tool_calls 计数。"""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) AS max_idx FROM koto_task_steps WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        step_index = (row["max_idx"] + 1) if row else 0

        step = StepRecord(
            task_id=task_id,
            step_index=step_index,
            step_type=step_type,
            content=content[:2000],
            tool_name=tool_name,
            tool_args=json.dumps(tool_args, ensure_ascii=False) if tool_args else None,
            observation=(observation or "")[:2000] if observation else None,
        )
        self._conn.execute(
            """
            INSERT INTO koto_task_steps
              (step_id, task_id, step_index, step_type, content,
               tool_name, tool_args, observation, created_at)
            VALUES
              (:step_id, :task_id, :step_index, :step_type, :content,
               :tool_name, :tool_args, :observation, :created_at)
            """,
            asdict(step),
        )
        # 更新计数器
        if step_type == "ACTION":
            self._conn.execute(
                "UPDATE koto_tasks SET step_count = step_count + 1, tool_calls = tool_calls + 1 WHERE task_id = ?",
                (task_id,),
            )
        else:
            self._conn.execute(
                "UPDATE koto_tasks SET step_count = step_count + 1 WHERE task_id = ?",
                (task_id,),
            )
        self._conn.commit()
        return step

    def _get_steps(self, task_id: str) -> List[StepRecord]:
        rows = self._conn.execute(
            "SELECT * FROM koto_task_steps WHERE task_id = ? ORDER BY step_index ASC",
            (task_id,),
        ).fetchall()
        return [StepRecord(**dict(r)) for r in rows]

    def get_steps(self, task_id: str) -> List[StepRecord]:
        return self._get_steps(task_id)

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def get_stats(self, date_from: Optional[str] = None) -> Dict[str, Any]:
        """返回任务运行统计摘要。"""
        where = f"WHERE created_at >= '{date_from}'" if date_from else ""
        rows = self._conn.execute(
            f"""
            SELECT status, COUNT(*) AS cnt
            FROM koto_tasks {where}
            GROUP BY status
            """
        ).fetchall()
        by_status = {r["status"]: r["cnt"] for r in rows}
        total = sum(by_status.values())
        avg_row = self._conn.execute(
            f"""
            SELECT AVG(
                (JULIANDAY(COALESCE(completed_at, datetime('now'))) - JULIANDAY(started_at)) * 86400
            ) AS avg_sec
            FROM koto_tasks
            {where}
            WHERE started_at IS NOT NULL
            """
        ).fetchone()
        return {
            "total": total,
            "by_status": by_status,
            "avg_duration_seconds": round(avg_row["avg_sec"] or 0, 2),
        }

    # ── 清理 ──────────────────────────────────────────────────────────────────

    def purge_old(self, keep_days: int = 30):
        """删除超过 keep_days 天的已完成/已取消任务及其步骤。"""
        cutoff = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cutoff_iso = cutoff.isoformat()
        self._conn.execute(
            """
            DELETE FROM koto_task_steps WHERE task_id IN (
                SELECT task_id FROM koto_tasks
                WHERE status IN ('completed', 'cancelled', 'failed')
                  AND created_at < ?
            )
            """,
            (cutoff_iso,),
        )
        deleted = self._conn.execute(
            """
            DELETE FROM koto_tasks
            WHERE status IN ('completed', 'cancelled', 'failed')
              AND created_at < ?
            """,
            (cutoff_iso,),
        ).rowcount
        self._conn.commit()
        logger.info(f"[TaskLedger] 清理 {deleted} 条历史任务（>{keep_days}天）")
        return deleted

    # ── ProgressBus 联动 ──────────────────────────────────────────────────────

    def _notify_status_change(self, task_id: str, new_status: TaskStatus):
        """当状态变化时，尝试通知 ProgressBus（懒加载，避免循环依赖）。"""
        try:
            from app.core.tasks.progress_bus import get_progress_bus, ProgressEvent
            bus = get_progress_bus()
            task = self.get(task_id)
            bus.publish(ProgressEvent(
                task_id=task_id,
                session_id=task.session_id if task else "",
                event_type="status_change",
                status=new_status.value,
                message=f"任务状态：{new_status.value}",
                progress=_STATUS_PROGRESS.get(new_status, 0),
            ))
        except Exception as e:
            logger.debug(f"[TaskLedger] _notify_status_change 跳过: {e}")


# 状态对应默认进度值
_STATUS_PROGRESS: Dict[TaskStatus, int] = {
    TaskStatus.PENDING:   0,
    TaskStatus.RUNNING:   10,
    TaskStatus.WAITING:   50,
    TaskStatus.RETRYING:  30,
    TaskStatus.COMPLETED: 100,
    TaskStatus.FAILED:    0,
    TaskStatus.CANCELLED: 0,
}


# ============================================================================
# 单例访问
# ============================================================================

def get_ledger(db_path: Optional[str] = None) -> TaskLedger:
    """返回全局 TaskLedger 单例（线程安全）。"""
    global _ledger_instance
    with _ledger_lock:
        if _ledger_instance is None:
            _ledger_instance = TaskLedger(db_path)
    return _ledger_instance
