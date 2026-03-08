# -*- coding: utf-8 -*-
"""
Koto JobRunner — 后台作业执行器
================================
将持久化的 TaskLedger 条目转化为真正可后台执行的作业：

- 提交：submit(JobSpec) → task_id，立即入队
- 执行：线程池执行，每个作业都绑定 TaskLedger 生命周期
- 进度：通过 ProgressBus 向前端/SSE 推送实时步骤
- 控制：取消、暂停（interrupt）、恢复 — 与 TaskLedger 信号位联动
- 重试：失败时按 max_retries + 指数退避自动重排
- 崩溃恢复：启动时自动检测 PENDING/job_runner 任务并重排

作业类型 (job_type) 与内置处理器：
  "agent_query"   — 通过 UnifiedAgent 执行用户查询
  "workflow"      — 通过 WorkflowRuntime 执行命名工作流
  "auto_catalog"  — 触发自动整理任务
  "skill_exec"    — 通过指定 skill_id 执行技能

自定义处理器注册：
    runner = get_job_runner()
    runner.register_handler("my_type", lambda ctx: do_something(ctx))
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class JobSpec:
    """描述一个待提交的作业。"""
    job_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    skill_id: Optional[str] = None
    max_retries: int = 0
    timeout_seconds: float = 300.0
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class JobContext:
    """
    传给每个处理器的执行上下文。

    用法：
        def my_handler(ctx: JobContext) -> Optional[str]:
            ctx.step("THOUGHT", "开始处理...", progress=10)
            if ctx.is_cancelled(): return "已取消"
            result = do_work(ctx.payload)
            ctx.step("ANSWER", result[:200], progress=100)
            return result[:500]
    """
    task_id: str
    session_id: str
    payload: Dict[str, Any]
    ledger: Any     # TaskLedger
    bus: Any        # ProgressBus

    def is_cancelled(self) -> bool:
        """供处理器轮询：外部是否已请求取消。"""
        return self.ledger.is_cancel_requested(self.task_id)

    def is_interrupted(self) -> bool:
        """供处理器轮询：外部是否已请求暂停（Human-in-loop）。"""
        return self.ledger.is_interrupt_requested(self.task_id)

    def step(
        self,
        step_type: str,
        content: str,
        progress: int = 0,
        tool_name: Optional[str] = None,
    ):
        """
        记录一个执行步骤并广播到 ProgressBus。

        Args:
            step_type: THOUGHT / ACTION / OBSERVATION / ANSWER / ERROR
            content:   步骤文本
            progress:  0-100 进度百分比
            tool_name: 若是工具调用，传入工具名
        """
        try:
            self.ledger.add_step(
                self.task_id,
                step_type=step_type,
                content=content,
                tool_name=tool_name,
            )
        except Exception:
            pass
        try:
            self.bus.publish_step(
                task_id=self.task_id,
                session_id=self.session_id,
                step_type=step_type,
                content=content,
                progress=progress,
                tool_name=tool_name,
            )
        except Exception:
            pass


# ============================================================================
# JobRunner
# ============================================================================

class JobRunner:
    """
    后台作业执行器（线程池 + 优先队列）。

    使用示例：
        runner = get_job_runner()
        task_id = runner.submit(JobSpec(
            job_type="agent_query",
            payload={"query": "帮我写一份周报"},
            session_id="sess-abc",
        ))
        # 通过 GET /api/jobs/<task_id>/stream 跟踪进度
    """

    def __init__(self, max_workers: int = 4):
        self._max_workers = max_workers
        # (priority, timestamp, task_id, spec)
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="koto_job"
        )
        self._handlers: Dict[str, Callable[[JobContext], Optional[str]]] = {}
        self._running = False
        self._dispatcher_thread: Optional[threading.Thread] = None

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name="koto_job_dispatcher"
        )
        self._dispatcher_thread.start()
        # 延迟恢复（等 TaskLedger 初始化完成）
        threading.Thread(
            target=self._recover_stale_tasks, daemon=True, name="koto_job_recovery"
        ).start()
        logger.info("[JobRunner] ✅ 启动，max_workers=%d", self._max_workers)

    def stop(self):
        self._running = False
        self._executor.shutdown(wait=False)

    # ── 注册处理器 ────────────────────────────────────────────────────────────

    def register_handler(
        self, job_type: str, fn: Callable[[JobContext], Optional[str]]
    ):
        """
        注册自定义作业类型处理器。

        处理器签名：fn(ctx: JobContext) -> Optional[str]
        返回值为 result_summary（最多 500 字），None 也可以。
        若处理器需要报错，直接 raise 即可，JobRunner 会捕获并标记失败。
        """
        self._handlers[job_type] = fn
        logger.debug("[JobRunner] 注册处理器: %s", job_type)

    # ── 提交作业 ──────────────────────────────────────────────────────────────

    def submit(self, spec: JobSpec) -> str:
        """
        创建 TaskLedger 条目并入队。
        返回 task_id，作为后续查询/控制的唯一标识。
        """
        from app.core.tasks.task_ledger import get_ledger
        ledger = get_ledger()

        # 将 payload 序列化为 user_input（TaskLedger 的文本字段）
        user_input_str = json.dumps(spec.payload, ensure_ascii=False)[:1000]

        task = ledger.create(
            session_id=spec.session_id or "system",
            user_input=user_input_str,
            task_type=spec.job_type,
            skill_id=spec.skill_id,
            source="job_runner",
            metadata={
                "job_type": spec.job_type,
                "max_retries": spec.max_retries,
                "timeout_seconds": spec.timeout_seconds,
                **(spec.metadata or {}),
            },
        )

        self._queue.put((5, time.time(), task.task_id, spec))
        logger.info(
            "[JobRunner] 作业入队 task_id=%s type=%s", task.task_id[:8], spec.job_type
        )
        return task.task_id

    # ── 内部执行循环 ──────────────────────────────────────────────────────────

    def _dispatch_loop(self):
        while self._running:
            try:
                _, _, task_id, spec = self._queue.get(timeout=1.0)
                self._executor.submit(self._run_job, task_id, spec)
            except queue.Empty:
                continue
            except Exception as exc:
                logger.error("[JobRunner] dispatch 异常: %s", exc)

    def _run_job(self, task_id: str, spec: JobSpec):
        from app.core.tasks.task_ledger import get_ledger
        from app.core.tasks.progress_bus import get_progress_bus

        ledger = get_ledger()
        bus = get_progress_bus()

        handler = self._handlers.get(spec.job_type)
        if not handler:
            ledger.mark_failed(task_id, f"未知 job_type: {spec.job_type!r}")
            return

        ctx = JobContext(
            task_id=task_id,
            session_id=spec.session_id or "system",
            payload=spec.payload,
            ledger=ledger,
            bus=bus,
        )

        ledger.mark_running(task_id)
        try:
            result_summary = handler(ctx)
            if ledger.is_cancel_requested(task_id):
                ledger.mark_cancelled(task_id)
            else:
                ledger.mark_completed(task_id, result_summary=result_summary)
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("[JobRunner] 作业失败 task_id=%s: %s", task_id[:8], exc)

            task = ledger.get(task_id)
            if task:
                meta = json.loads(task.metadata or "{}")
                max_retries = meta.get("max_retries", 0)
                if task.retry_count < max_retries:
                    ledger.increment_retries(task_id)
                    delay = min(60, 2 ** task.retry_count)
                    logger.info(
                        "[JobRunner] %ds 后重试 task_id=%s", delay, task_id[:8]
                    )
                    def _requeue(_tid=task_id, _spec=spec, _delay=delay):
                        time.sleep(_delay)
                        self._queue.put((5, time.time(), _tid, _spec))
                    threading.Thread(target=_requeue, daemon=True).start()
                    return

            ledger.mark_failed(task_id, f"{type(exc).__name__}: {exc}\n\n{tb}"[:1000])

            # 发布运维事件
            try:
                from app.core.ops.ops_event_bus import get_ops_bus
                get_ops_bus().emit("job_failed", {
                    "task_id": task_id,
                    "job_type": spec.job_type,
                    "error": str(exc)[:300],
                })
            except Exception:
                pass

    def _recover_stale_tasks(self):
        """启动时将上次未完成的 PENDING 作业重新入队（崩溃恢复）。"""
        time.sleep(5)
        try:
            from app.core.tasks.task_ledger import get_ledger, TaskStatus
            ledger = get_ledger()
            stale = ledger.list_tasks(
                status=TaskStatus.PENDING, source="job_runner", limit=100
            )
            for task in stale:
                meta = json.loads(task.metadata or "{}")
                job_type = meta.get("job_type") or task.task_type
                if not job_type:
                    continue
                try:
                    payload = json.loads(task.user_input)
                except Exception:
                    payload = {"raw": task.user_input}
                spec = JobSpec(
                    job_type=job_type,
                    payload=payload,
                    session_id=task.session_id,
                    skill_id=task.skill_id,
                    max_retries=meta.get("max_retries", 0),
                    timeout_seconds=meta.get("timeout_seconds", 300.0),
                    metadata=meta,
                )
                self._queue.put((5, time.time(), task.task_id, spec))
                logger.info(
                    "[JobRunner] 恢复挂起任务 task_id=%s", task.task_id[:8]
                )
        except Exception as exc:
            logger.warning("[JobRunner] 崩溃恢复失败: %s", exc)


# ============================================================================
# 单例
# ============================================================================

_runner: Optional[JobRunner] = None
_runner_lock = threading.Lock()


def get_job_runner() -> JobRunner:
    """获取全局 JobRunner 单例，首次调用时初始化并注册内置处理器。"""
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                _runner = JobRunner()
                _register_builtin_handlers(_runner)
                _runner.start()
    return _runner


# ============================================================================
# 内置处理器
# ============================================================================

def _register_builtin_handlers(runner: JobRunner):
    runner.register_handler("agent_query", _handle_agent_query)
    runner.register_handler("workflow", _handle_workflow)
    runner.register_handler("auto_catalog", _handle_auto_catalog)
    runner.register_handler("skill_exec", _handle_skill_exec)
    runner.register_handler("proactive_tick", _handle_proactive_tick)


def _handle_agent_query(ctx: JobContext) -> Optional[str]:
    """通过 UnifiedAgent 执行自然语言查询。"""
    query = ctx.payload.get("query", "")
    if not query:
        raise ValueError("payload.query 不能为空")

    history = ctx.payload.get("history") or []
    ctx.step("THOUGHT", f"开始处理查询: {query[:120]}", progress=5)

    try:
        from app.api.agent_routes import get_agent
        agent = get_agent()
        final_answer = ""
        for step in agent.run(input_text=query, history=history):
            from app.api.agent_routes import AgentStepType
            step_data = step.to_dict()
            ctx.step(
                step_data.get("step_type", "THOUGHT"),
                (step_data.get("content") or "")[:300],
                progress=50,
                tool_name=step_data.get("tool_name"),
            )
            if step.step_type == AgentStepType.ANSWER:
                final_answer = step.content or ""
        ctx.step("ANSWER", final_answer[:300], progress=100)
        return final_answer[:500]
    except Exception as exc:
        raise RuntimeError(f"agent_query 执行失败: {exc}") from exc


def _handle_workflow(ctx: JobContext) -> Optional[str]:
    """通过 WorkflowRuntime 执行命名工作流。"""
    workflow_id = ctx.payload.get("workflow_id", "")
    user_input = ctx.payload.get("user_input", "")
    variables = ctx.payload.get("variables") or {}

    if not workflow_id and not user_input:
        raise ValueError("workflow_id 或 user_input 至少提供一个")

    ctx.step("THOUGHT", f"启动工作流: {workflow_id or '(auto-detect)'}", progress=5)
    try:
        from app.core.workflow.workflow_runtime import WorkflowRuntime
        rt = WorkflowRuntime()
        result = rt.execute(
            workflow_id=workflow_id,
            user_input=user_input,
            variables=variables,
            task_ctx=ctx,
        )
        summary = (result.get("output") or "")[:500]
        ctx.step("ANSWER", summary or "工作流完成", progress=100)
        return summary
    except Exception as exc:
        raise RuntimeError(f"workflow 执行失败: {exc}") from exc


def _handle_auto_catalog(ctx: JobContext) -> Optional[str]:
    """触发文件自动整理任务。"""
    source_dir = ctx.payload.get("source_dir", "")
    ctx.step("THOUGHT", f"自动整理: {source_dir or '默认目录'}", progress=5)
    try:
        from web.auto_catalog_scheduler import AutoCatalogWorker  # type: ignore
        worker = AutoCatalogWorker(source_dir=source_dir or None)
        result = worker.run_once()
        summary = str(result)[:300] if result else "整理完成"
        ctx.step("ANSWER", summary, progress=100)
        return summary
    except Exception as exc:
        raise RuntimeError(f"auto_catalog 失败: {exc}") from exc


def _handle_skill_exec(ctx: JobContext) -> Optional[str]:
    """通过技能 ID 执行对话技能（注入 skill prompt 后调用 agent）。"""
    skill_id = ctx.payload.get("skill_id") or ctx.task_id  # fallback
    query = ctx.payload.get("query", "")
    if not query:
        raise ValueError("payload.query 不能为空")

    ctx.step("THOUGHT", f"加载技能 {skill_id}", progress=5)
    try:
        from app.core.skills.skill_manager import SkillManager
        skill_def = SkillManager.get_definition(skill_id)
        if skill_def:
            base_prompt = skill_def.system_prompt_template or ""
            query = f"{base_prompt}\n\n{query}" if base_prompt else query

        ctx.step("ACTION", f"调用 agent (skill={skill_id})", progress=20)

        from app.api.agent_routes import get_agent
        agent = get_agent()
        final_answer = ""
        for step in agent.run(input_text=query, history=[]):
            from app.api.agent_routes import AgentStepType
            if step.step_type == AgentStepType.ANSWER:
                final_answer = step.content or ""

        ctx.step("ANSWER", final_answer[:300], progress=100)
        return final_answer[:500]
    except Exception as exc:
        raise RuntimeError(f"skill_exec 失败: {exc}") from exc


def _handle_proactive_tick(ctx: JobContext) -> Optional[str]:
    """触发主动交互巡检：检查是否有需要主动推送的消息。"""
    ctx.step("THOUGHT", "开始主动交互巡检", progress=10)
    try:
        # 尝试获取 LLM 函数（非必须）
        llm_fn = None
        try:
            from web.app import client
            from google.genai import types as _types

            def _llm(prompt: str) -> str:
                resp = client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents=prompt,
                    config=_types.GenerateContentConfig(
                        temperature=0.7, max_output_tokens=80
                    ),
                )
                return resp.text or ""

            llm_fn = _llm
        except Exception:
            pass

        from app.core.agent.proactive_agent import get_proactive_agent
        agent = get_proactive_agent()
        agent.tick(llm_fn=llm_fn)
        pending = agent.pending()
        summary = f"巡检完成，待推送消息 {len(pending)} 条"
        ctx.step("ANSWER", summary, progress=100)
        return summary
    except Exception as exc:
        raise RuntimeError(f"proactive_tick 失败: {exc}") from exc
