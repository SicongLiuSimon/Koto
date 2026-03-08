# -*- coding: utf-8 -*-
"""
GoalManager — 长期委托任务管理器
==================================
管理跨天持续执行的用户委托目标 (Goal)。

与 TaskLedger 的区别：
  TaskLedger  追踪单次 Agent 调用的执行台账（短期，一次会话内完成）
  GoalManager 管理用户交代的长期目标（可跨天、跨会话，有状态机和检查点）

生命周期:
  draft → active ↔ waiting_user / paused → completed / failed

内置模板类别:
  price_watch   监控价格 / 信息变化
  reminder      时间性提醒 + 跟进行动
  file_task     文件整理、转换、归档
  research      持续信息收集
  custom        用户自定义目标
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
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 默认 DB 路径（与 TaskLedger 同库文件）──────────────────────────────────
_DEFAULT_DB_PATH = str(
    Path(os.environ.get("KOTO_DB_DIR", Path(__file__).parent.parent.parent.parent / "config"))
    / "koto_checkpoints.sqlite"
)

# ── 单例 ─────────────────────────────────────────────────────────────────────
_manager_instance: Optional["GoalManager"] = None
_manager_lock = threading.Lock()


# ============================================================================
# 枚举 & 数据类
# ============================================================================

class GoalStatus(str, Enum):
    DRAFT         = "draft"           # 刚创建，尚未激活
    ACTIVE        = "active"          # Koto 主动追踪中
    WAITING_USER  = "waiting_user"    # 需要用户补充信息
    PAUSED        = "paused"          # 用户手动暂停
    COMPLETED     = "completed"       # 目标达成
    FAILED        = "failed"          # 失败且不再重试


TERMINAL_STATUSES = {GoalStatus.COMPLETED, GoalStatus.FAILED}

# 各类别的默认检查间隔（分钟）
_DEFAULT_INTERVALS: Dict[str, int] = {
    "price_watch": 120,
    "reminder":    30,
    "file_task":   60,
    "research":    180,
    "custom":      60,
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _minutes_later(minutes: int) -> str:
    return (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="milliseconds")


@dataclass
class GoalTask:
    """一条长期委托目标。"""
    goal_id: str
    title: str
    user_goal: str                       # 用户原始描述
    category: str = "custom"            # price_watch / reminder / file_task / research / custom

    status: GoalStatus = GoalStatus.DRAFT
    priority: str = "normal"            # urgent / high / normal / low

    created_at: str  = field(default_factory=_now_iso)
    updated_at: str  = field(default_factory=_now_iso)
    due_at: Optional[str]         = None  # 截止时间
    next_check_at: Optional[str]  = None  # 下次自动检查时间
    check_interval_minutes: int   = 60

    requires_confirmation: bool   = False  # 高风险动作前是否先向用户确认

    context_snapshot: str = "{}"   # JSON：保存任务上下文（进度、中间数据等）
    last_result: Optional[str] = None      # 最近一次执行的摘要
    progress_summary: Optional[str] = None # 总体进展摘要

    total_runs: int = 0
    session_id: Optional[str] = None
    run_on_activate: bool = True   # 激活后是否立即触发首次检查

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def get_context(self) -> Dict[str, Any]:
        try:
            return json.loads(self.context_snapshot)
        except Exception:
            return {}

    def set_context(self, ctx: Dict[str, Any]):
        self.context_snapshot = json.dumps(ctx, ensure_ascii=False)


@dataclass
class GoalRun:
    """每次执行记录（一个 GoalTask 可对应多次 GoalRun）。"""
    run_id: str           = field(default_factory=lambda: str(uuid.uuid4()))
    goal_id: str          = ""
    started_at: str       = field(default_factory=_now_iso)
    finished_at: Optional[str] = None
    outcome: str          = "pending"   # success / partial / failed / waiting_user / pending
    summary: str          = ""
    tool_calls_json: str  = "[]"        # JSON list of tool call names
    linked_task_id: Optional[str] = None  # TaskLedger task_id（如有）

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        try:
            d["tool_calls"] = json.loads(self.tool_calls_json)
        except Exception:
            d["tool_calls"] = []
        d.pop("tool_calls_json", None)
        return d


# ============================================================================
# GoalManager
# ============================================================================

class GoalManager:
    """
    长期委托目标管理器（单例）。

    用法::
        gm = get_goal_manager()

        goal = gm.create(
            title="监控 AirPods 降价",
            user_goal="每天帮我查京东 AirPods Pro 价格，低于 1400 元时提醒我",
            category="price_watch",
        )
        gm.activate(goal.goal_id)

        # 查询活跃目标
        goals = gm.list_goals(status=GoalStatus.ACTIVE)

        # 完成
        gm.complete(goal.goal_id, summary="已降价至 1380 元，已通知用户")
    """

    # ── Schema DDL ──────────────────────────────────────────────────────────
    _DDL = """
    CREATE TABLE IF NOT EXISTS koto_goals (
        goal_id               TEXT PRIMARY KEY,
        title                 TEXT NOT NULL,
        user_goal             TEXT NOT NULL,
        category              TEXT NOT NULL DEFAULT 'custom',
        status                TEXT NOT NULL DEFAULT 'draft',
        priority              TEXT NOT NULL DEFAULT 'normal',
        created_at            TEXT NOT NULL,
        updated_at            TEXT NOT NULL,
        due_at                TEXT,
        next_check_at         TEXT,
        check_interval_minutes INTEGER NOT NULL DEFAULT 60,
        requires_confirmation INTEGER NOT NULL DEFAULT 0,
        context_snapshot      TEXT NOT NULL DEFAULT '{}',
        last_result           TEXT,
        progress_summary      TEXT,
        total_runs            INTEGER NOT NULL DEFAULT 0,
        session_id            TEXT,
        run_on_activate       INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_koto_goals_status   ON koto_goals(status);
    CREATE INDEX IF NOT EXISTS idx_koto_goals_check    ON koto_goals(next_check_at);
    CREATE INDEX IF NOT EXISTS idx_koto_goals_session  ON koto_goals(session_id);

    CREATE TABLE IF NOT EXISTS koto_goal_runs (
        run_id          TEXT PRIMARY KEY,
        goal_id         TEXT NOT NULL REFERENCES koto_goals(goal_id),
        started_at      TEXT NOT NULL,
        finished_at     TEXT,
        outcome         TEXT NOT NULL DEFAULT 'pending',
        summary         TEXT NOT NULL DEFAULT '',
        tool_calls_json TEXT NOT NULL DEFAULT '[]',
        linked_task_id  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_koto_goal_runs_goal ON koto_goal_runs(goal_id);
    """

    _CHECK_INTERVAL_SECONDS = 60  # 后台循环检查间隔

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._open_conn()
        self._init_schema()
        self._bg_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._start_background_loop()
        logger.info(f"[GoalManager] ✅ 初始化完成 → {self._db_path}")

    # ── DB helpers ──────────────────────────────────────────────────────────

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self):
        self._conn.executescript(self._DDL)
        self._conn.commit()

    # ── Goal CRUD ────────────────────────────────────────────────────────────

    def create(
        self,
        title: str,
        user_goal: str,
        category: str = "custom",
        priority: str = "normal",
        due_at: Optional[str] = None,
        check_interval_minutes: Optional[int] = None,
        requires_confirmation: bool = False,
        context_snapshot: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        run_on_activate: bool = True,
    ) -> GoalTask:
        """创建一个新的长期目标（初始状态 draft）。"""
        interval = check_interval_minutes or _DEFAULT_INTERVALS.get(category, 60)
        goal = GoalTask(
            goal_id=str(uuid.uuid4()),
            title=title[:200],
            user_goal=user_goal[:2000],
            category=category,
            priority=priority,
            due_at=due_at,
            check_interval_minutes=interval,
            requires_confirmation=requires_confirmation,
            context_snapshot=json.dumps(context_snapshot or {}, ensure_ascii=False),
            session_id=session_id,
            run_on_activate=run_on_activate,
        )
        self._insert_goal(goal)
        logger.info(f"[GoalManager] 创建目标 {goal.goal_id[:8]} «{goal.title}»")
        return goal

    def get(self, goal_id: str) -> Optional[GoalTask]:
        row = self._conn.execute(
            "SELECT * FROM koto_goals WHERE goal_id = ?", (goal_id,)
        ).fetchone()
        return self._row_to_goal(row) if row else None

    def list_goals(
        self,
        status: Optional[GoalStatus] = None,
        category: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[GoalTask]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM koto_goals {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_goal(r) for r in rows]

    def count(self, status: Optional[GoalStatus] = None) -> int:
        if status:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM koto_goals WHERE status = ?", (status.value,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM koto_goals").fetchone()
        return row[0] if row else 0

    def runs_for_goal(self, goal_id: str, limit: int = 20) -> List[GoalRun]:
        rows = self._conn.execute(
            "SELECT * FROM koto_goal_runs WHERE goal_id = ? ORDER BY started_at DESC LIMIT ?",
            (goal_id, limit),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    # ── Lifecycle transitions ────────────────────────────────────────────────

    def activate(self, goal_id: str) -> bool:
        """将 draft / paused 目标设为 active，并排定首次检查时间。"""
        goal = self.get(goal_id)
        if not goal:
            return False
        if goal.status in TERMINAL_STATUSES:
            logger.warning(f"[GoalManager] 无法激活终态目标 {goal_id[:8]}")
            return False

        next_check = _now_iso() if goal.run_on_activate else _minutes_later(goal.check_interval_minutes)
        self._update(goal_id, status=GoalStatus.ACTIVE, next_check_at=next_check)
        logger.info(f"[GoalManager] 激活目标 {goal_id[:8]} «{goal.title}»，首次检查: {next_check[:19]}")
        return True

    def pause(self, goal_id: str) -> bool:
        goal = self.get(goal_id)
        if not goal or goal.status in TERMINAL_STATUSES:
            return False
        self._update(goal_id, status=GoalStatus.PAUSED, next_check_at=None)
        logger.info(f"[GoalManager] 暂停目标 {goal_id[:8]}")
        return True

    def resume(self, goal_id: str) -> bool:
        return self.activate(goal_id)

    def set_waiting_user(self, goal_id: str, reason: str = "") -> bool:
        goal = self.get(goal_id)
        if not goal:
            return False
        ctx = goal.get_context()
        ctx["waiting_reason"] = reason
        self._update(
            goal_id,
            status=GoalStatus.WAITING_USER,
            context_snapshot=json.dumps(ctx, ensure_ascii=False),
            next_check_at=None,
        )
        logger.info(f"[GoalManager] 目标 {goal_id[:8]} 等待用户确认: {reason[:80]}")
        return True

    def complete(self, goal_id: str, summary: str = "") -> bool:
        self._update(
            goal_id,
            status=GoalStatus.COMPLETED,
            last_result=summary[:2000] if summary else None,
            progress_summary=summary[:500] if summary else None,
            next_check_at=None,
        )
        logger.info(f"[GoalManager] 目标 {goal_id[:8]} 已完成: {summary[:60]}")
        return True

    def fail(self, goal_id: str, error: str = "") -> bool:
        self._update(
            goal_id,
            status=GoalStatus.FAILED,
            last_result=error[:2000] if error else None,
            next_check_at=None,
        )
        logger.info(f"[GoalManager] 目标 {goal_id[:8]} 失败: {error[:60]}")
        return True

    def delete(self, goal_id: str) -> bool:
        rows_deleted = self._conn.execute(
            "DELETE FROM koto_goals WHERE goal_id = ?", (goal_id,)
        ).rowcount
        self._conn.execute("DELETE FROM koto_goal_runs WHERE goal_id = ?", (goal_id,))
        self._conn.commit()
        return rows_deleted > 0

    def update_from_run(
        self,
        goal_id: str,
        run_outcome: str,
        summary: str,
        new_context: Optional[Dict[str, Any]] = None,
        reschedule: bool = True,
    ):
        """Agent 执行完成后更新目标状态与下次检查时间。"""
        goal = self.get(goal_id)
        if not goal:
            return

        ctx = goal.get_context()
        if new_context:
            ctx.update(new_context)

        if run_outcome == "success":
            new_status = GoalStatus.ACTIVE
        elif run_outcome == "waiting_user":
            new_status = GoalStatus.WAITING_USER
        elif run_outcome == "completed":
            new_status = GoalStatus.COMPLETED
        else:
            new_status = GoalStatus.ACTIVE  # partial/failed → stay active, retry later

        next_check = _minutes_later(goal.check_interval_minutes) if (
            reschedule and new_status == GoalStatus.ACTIVE
        ) else None

        self._update(
            goal_id,
            status=new_status,
            last_result=summary[:2000],
            progress_summary=summary[:500],
            context_snapshot=json.dumps(ctx, ensure_ascii=False),
            next_check_at=next_check,
            total_runs=goal.total_runs + 1,
        )

    # ── GoalRun CRUD ─────────────────────────────────────────────────────────

    def start_run(self, goal_id: str, linked_task_id: Optional[str] = None) -> GoalRun:
        run = GoalRun(goal_id=goal_id, linked_task_id=linked_task_id)
        self._conn.execute(
            """INSERT INTO koto_goal_runs
               (run_id, goal_id, started_at, outcome, summary, tool_calls_json, linked_task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run.run_id, run.goal_id, run.started_at, run.outcome,
             run.summary, run.tool_calls_json, run.linked_task_id),
        )
        self._conn.commit()
        return run

    def finish_run(self, run_id: str, outcome: str, summary: str,
                   tool_calls: Optional[List[str]] = None) -> bool:
        self._conn.execute(
            """UPDATE koto_goal_runs
               SET finished_at=?, outcome=?, summary=?, tool_calls_json=?
               WHERE run_id=?""",
            (_now_iso(), outcome, summary[:2000],
             json.dumps(tool_calls or [], ensure_ascii=False), run_id),
        )
        self._conn.commit()
        return True

    # ── 对外：获取待检查目标 ─────────────────────────────────────────────────

    def get_due_goals(self) -> List[GoalTask]:
        """返回所有 status=active 且 next_check_at <= now 的目标。"""
        now = _now_iso()
        rows = self._conn.execute(
            """SELECT * FROM koto_goals
               WHERE status = 'active' AND next_check_at IS NOT NULL AND next_check_at <= ?
               ORDER BY next_check_at ASC""",
            (now,),
        ).fetchall()
        return [self._row_to_goal(r) for r in rows]

    # ── 后台执行循环 ─────────────────────────────────────────────────────────

    def _start_background_loop(self):
        self._bg_thread = threading.Thread(
            target=self._background_loop,
            name="GoalManagerLoop",
            daemon=True,
        )
        self._bg_thread.start()

    def _background_loop(self):
        """每 60 秒扫描一次待检查目标，提交到 JobRunner。"""
        time.sleep(5)  # 让其他模块先启动
        while not self._stop_event.is_set():
            try:
                self._dispatch_due_goals()
            except Exception as e:
                logger.warning(f"[GoalManager] 后台循环异常（非致命）: {e}")
            self._stop_event.wait(self._CHECK_INTERVAL_SECONDS)

    def _dispatch_due_goals(self):
        due = self.get_due_goals()
        if not due:
            return
        logger.info(f"[GoalManager] 发现 {len(due)} 个待检查目标")
        for goal in due:
            # 立即将 next_check_at 拨到未来，防止重复触发
            self._update(goal.goal_id, next_check_at=_minutes_later(goal.check_interval_minutes))
            self._submit_goal_job(goal)

    def _submit_goal_job(self, goal: GoalTask):
        """将目标检查任务提交给 JobRunner；失败时降级到直接执行线程。"""
        try:
            from app.core.jobs.job_runner import get_job_runner, JobSpec
            runner = get_job_runner()
            runner.submit(JobSpec(
                job_type="goal_check",
                payload={"goal_id": goal.goal_id},
                session_id=goal.session_id or "",
                metadata={"goal_title": goal.title},
            ))
        except Exception as e:
            logger.warning(f"[GoalManager] JobRunner 不可用，降级执行: {e}")
            threading.Thread(
                target=self._execute_goal_direct,
                args=(goal,),
                daemon=True,
                name=f"GoalExec-{goal.goal_id[:8]}",
            ).start()

    def _execute_goal_direct(self, goal: GoalTask):
        """JobRunner 不可用时直接调用 UnifiedAgent 执行目标。"""
        run = self.start_run(goal.goal_id)
        try:
            result_text = self._run_agent_for_goal(goal)
            outcome = "success"
        except Exception as e:
            result_text = f"执行出错: {e}"
            outcome = "failed"
            logger.error(f"[GoalManager] 目标 {goal.goal_id[:8]} 直接执行失败: {e}")

        self.finish_run(run.run_id, outcome=outcome, summary=result_text)
        self.update_from_run(goal.goal_id, run_outcome=outcome, summary=result_text)

    # ── Agent 调用 ────────────────────────────────────────────────────────────

    def _run_agent_for_goal(self, goal: GoalTask) -> str:
        """构建目标提示词并调用 UnifiedAgent，返回结果摘要。"""
        ctx = goal.get_context()
        progress = ctx.get("progress_summary", "（首次执行）")

        prompt = (
            f"[长期目标追踪]\n"
            f"目标标题：{goal.title}\n"
            f"目标描述：{goal.user_goal}\n"
            f"当前进展：{progress}\n"
            f"上次结果：{goal.last_result or '暂无'}\n\n"
            f"请根据上述信息，执行下一步追踪，并以中文简洁汇报：\n"
            f"1. 当前状态\n"
            f"2. 本次执行内容\n"
            f"3. 下一步建议\n"
            f"4. 如目标已完成，请明确说明"
        )

        # 懒加载 UnifiedAgent，避免循环依赖
        from app.core.agent.unified_agent import UnifiedAgent
        from app.core.agent.tool_registry import ToolRegistry
        from app.core.llm.gemini import get_gemini_client

        llm = get_gemini_client()
        agent = UnifiedAgent(
            llm_provider=llm,
            tool_registry=ToolRegistry(),
            system_instruction=(
                "你是 Koto，正在追踪一个用户委托的长期目标。"
                "请以简洁、清晰的方式汇报进展，重点说明本次做了什么和下一步。"
            ),
        )

        result_parts: List[str] = []
        for step in agent.run(prompt, session_id=goal.session_id or goal.goal_id):
            if step.step_type.value == "ANSWER":
                result_parts.append(step.content)

        return "\n".join(result_parts) or "（未获得明确答案）"

    def stop(self):
        """停止后台循环（测试或关闭时使用）。"""
        self._stop_event.set()

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _insert_goal(self, g: GoalTask):
        self._conn.execute(
            """INSERT INTO koto_goals
               (goal_id, title, user_goal, category, status, priority,
                created_at, updated_at, due_at, next_check_at, check_interval_minutes,
                requires_confirmation, context_snapshot, last_result, progress_summary,
                total_runs, session_id, run_on_activate)
               VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (g.goal_id, g.title, g.user_goal, g.category, g.status.value,
             g.priority, g.created_at, g.updated_at, g.due_at, g.next_check_at,
             g.check_interval_minutes, int(g.requires_confirmation),
             g.context_snapshot, g.last_result, g.progress_summary,
             g.total_runs, g.session_id, int(g.run_on_activate)),
        )
        self._conn.commit()

    def _update(self, goal_id: str, **kwargs):
        if not kwargs:
            return
        kwargs["updated_at"] = _now_iso()
        
        # 把 GoalStatus enum 转成字符串
        if "status" in kwargs and isinstance(kwargs["status"], GoalStatus):
            kwargs["status"] = kwargs["status"].value

        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [goal_id]
        self._conn.execute(f"UPDATE koto_goals SET {cols} WHERE goal_id = ?", vals)
        self._conn.commit()

    def _row_to_goal(self, row: sqlite3.Row) -> GoalTask:
        d = dict(row)
        d["status"] = GoalStatus(d["status"])
        d["requires_confirmation"] = bool(d["requires_confirmation"])
        d["run_on_activate"] = bool(d["run_on_activate"])
        return GoalTask(**{k: v for k, v in d.items() if k in GoalTask.__dataclass_fields__})

    def _row_to_run(self, row: sqlite3.Row) -> GoalRun:
        return GoalRun(**{k: v for k, v in dict(row).items() if k in GoalRun.__dataclass_fields__})


# ============================================================================
# 单例访问
# ============================================================================

def get_goal_manager(db_path: Optional[str] = None) -> GoalManager:
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = GoalManager(db_path=db_path)
    return _manager_instance
