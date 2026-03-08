# -*- coding: utf-8 -*-
"""
Koto Generic Task Planner
==========================
通用多步骤 DAG 规划器，将 interactive_planner.py 从 PPT 专用泛化为
适用于任意任务类型的规划框架。

功能：
  - 将复杂任务分解为带依赖关系的步骤 DAG
  - 单步失败可独立重试，不必重跑整个计划
  - 支持人工确认节点（require_approval=True）
  - 与 TaskLedger / ProgressBus 联动，全程可追踪
  - 内置通用规划模板（可被子类覆盖）+ LLM 动态规划两种模式

使用示例::

    from app.core.tasks.task_planner import TaskPlanner, Plan, PlanStep

    planner = TaskPlanner()

    # 方式 1：静态构建计划
    plan = Plan(task_id="xxx", original_request="帮我写周报并发邮件")
    plan.add_step(PlanStep(name="write_report", description="撰写周报内容"))
    plan.add_step(PlanStep(name="send_email", description="发送邮件", depends_on=["write_report"]))

    # 方式 2：LLM 动态规划
    plan = await planner.plan_with_llm(task_id, user_input, llm_provider)

    # 执行
    async for event in planner.execute_plan(plan, executor_fn):
        ...
"""

from __future__ import annotations

import json
import logging
import time
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional, Set

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举 & 数据类
# ============================================================================

class StepStatus(str, Enum):
    PENDING   = "pending"
    READY     = "ready"       # 依赖已满足，可执行
    RUNNING   = "running"
    WAITING   = "waiting"     # 等待人工确认
    COMPLETED = "completed"
    FAILED    = "failed"
    SKIPPED   = "skipped"     # 因上游失败被跳过


@dataclass
class PlanStep:
    """DAG 中的一个执行步骤。"""
    name: str                              # 唯一名称（同一 Plan 内）
    description: str                       # 对用户可见的描述
    step_type: str = "generic"             # 用于选择执行器（"llm"/"code"/"file"/"ppt"…）
    input_data: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)  # 依赖的步骤名列表
    require_approval: bool = False         # 执行前需要人工确认
    max_retries: int = 2
    timeout_seconds: int = 120
    allow_failure: bool = False            # True = 本步失败不阻塞后续步骤

    # 运行时字段（不参与 dict 初始化）
    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_count: int = 0
    result: Any = None
    error: Optional[str] = None
    expected_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @property
    def is_terminal(self) -> bool:
        return self.status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED)

    @property
    def elapsed_seconds(self) -> Optional[float]:
        if not self.started_at:
            return None
        end = self.completed_at or datetime.now().isoformat()
        try:
            fmt = "%Y-%m-%dT%H:%M:%S.%f"
            s = datetime.strptime(self.started_at[:26], fmt)
            e = datetime.strptime(end[:26], fmt)
            return (e - s).total_seconds()
        except Exception:
            return None


@dataclass
class Plan:
    """多步骤执行计划（DAG）。"""
    task_id: str
    original_request: str
    steps: List[PlanStep] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    status: str = "planning"               # planning / running / completed / failed
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))

    # ── 步骤操作 ──────────────────────────────────────────────────────────────

    def add_step(self, step: PlanStep) -> "Plan":
        """追加步骤（链式调用）。"""
        self.steps.append(step)
        return self

    def get_step(self, name: str) -> Optional[PlanStep]:
        return next((s for s in self.steps if s.name == name), None)

    def ready_steps(self) -> List[PlanStep]:
        """返回当前所有「依赖已满足 + 未开始」的步骤。"""
        completed_names: Set[str] = {
            s.name for s in self.steps
            if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
               or (s.allow_failure and s.status == StepStatus.FAILED)
        }
        result = []
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            if all(dep in completed_names for dep in step.depends_on):
                result.append(step)
        return result

    def is_done(self) -> bool:
        return all(s.is_terminal for s in self.steps)

    def has_blocking_failure(self) -> bool:
        return any(
            s.status == StepStatus.FAILED and not s.allow_failure
            for s in self.steps
        )

    def progress_percent(self) -> int:
        if not self.steps:
            return 0
        completed = sum(
            1 for s in self.steps
            if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
        )
        return int(completed / len(self.steps) * 100)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "original_request": self.original_request,
            "status": self.status,
            "progress": self.progress_percent(),
            "created_at": self.created_at,
            "steps": [s.to_dict() for s in self.steps],
            "context": self.context,
        }


# ============================================================================
# 内置规划模板
# ============================================================================

class PlanTemplates:
    """
    内置规划模板，覆盖常见任务类型。
    每个方法返回 Plan（不含 task_id，由调用方填充）。
    """

    @staticmethod
    def research_and_report(task_id: str, request: str) -> Plan:
        """研究 + 报告生成模板"""
        plan = Plan(task_id=task_id, original_request=request)
        plan.add_step(PlanStep(
            name="research",
            description="收集相关信息与资料",
            step_type="llm",
            expected_output="结构化研究摘要",
        ))
        plan.add_step(PlanStep(
            name="outline",
            description="生成报告大纲",
            step_type="llm",
            depends_on=["research"],
            expected_output="Markdown 大纲",
            require_approval=False,
        ))
        plan.add_step(PlanStep(
            name="write",
            description="撰写完整报告内容",
            step_type="llm",
            depends_on=["outline"],
            expected_output="完整报告文本",
            timeout_seconds=180,
        ))
        plan.add_step(PlanStep(
            name="export",
            description="导出为文档文件",
            step_type="file",
            depends_on=["write"],
            expected_output="文件路径",
        ))
        return plan

    @staticmethod
    def data_pipeline(task_id: str, request: str) -> Plan:
        """数据处理流水线模板"""
        plan = Plan(task_id=task_id, original_request=request)
        plan.add_step(PlanStep(name="load",     description="加载/读取数据源",   step_type="file"))
        plan.add_step(PlanStep(name="validate", description="验证数据质量",       step_type="code", depends_on=["load"]))
        plan.add_step(PlanStep(name="transform",description="数据清洗与转换",     step_type="code", depends_on=["validate"]))
        plan.add_step(PlanStep(name="analyze",  description="执行分析计算",       step_type="code", depends_on=["transform"]))
        plan.add_step(PlanStep(name="report",   description="生成分析报告/图表",  step_type="llm",  depends_on=["analyze"]))
        return plan

    @staticmethod
    def multi_step_task(task_id: str, request: str, steps: List[Dict[str, Any]]) -> Plan:
        """
        从 LLM 返回的步骤列表动态构建 Plan。

        steps 格式::

            [
              {"name": "step1", "description": "…", "depends_on": [], "require_approval": false},
              …
            ]
        """
        plan = Plan(task_id=task_id, original_request=request)
        for s in steps:
            plan.add_step(PlanStep(
                name=s.get("name", f"step_{uuid.uuid4().hex[:4]}"),
                description=s.get("description", ""),
                step_type=s.get("step_type", "llm"),
                depends_on=s.get("depends_on", []),
                require_approval=s.get("require_approval", False),
                allow_failure=s.get("allow_failure", False),
                timeout_seconds=s.get("timeout_seconds", 120),
                expected_output=s.get("expected_output", ""),
            ))
        return plan


# ============================================================================
# TaskPlanner
# ============================================================================

class TaskPlanner:
    """
    通用任务规划器与执行引擎。

    执行模型：
      - 单线程顺序执行（当前），可扩展为并行执行无依赖步骤
      - 每步执行前检查 human-in-loop（require_approval）
      - 每步完成后发布 ProgressEvent 到全局总线
    """

    # ── 规划 ──────────────────────────────────────────────────────────────────

    def plan_with_llm(
        self,
        task_id: str,
        user_input: str,
        llm_provider: Any,
        model_id: str = "gemini-3-flash-preview",
    ) -> Plan:
        """
        调用 LLM 将用户输入分解为步骤列表，返回 Plan。
        若 LLM 失败，回退到单步直接执行计划。
        """
        prompt = f"""你是一个任务规划助手。
将以下用户请求分解为 2-6 个有逻辑顺序的执行步骤，输出 JSON 数组。

用户请求：{user_input}

输出格式（仅 JSON，不要 markdown code block）：
[
  {{
    "name": "短名称（无空格）",
    "description": "步骤描述",
    "step_type": "llm|code|file|search",
    "depends_on": [],
    "require_approval": false,
    "allow_failure": false,
    "expected_output": "预期产出描述"
  }}
]

规则：
- 每个步骤的 depends_on 列出它依赖的步骤 name
- 不涉及复杂步骤的简单问答可只有一个步骤 "answer"
- 不要添加注释"""

        try:
            resp = llm_provider.generate_content(
                prompt=[{"role": "user", "content": prompt}],
                model=model_id,
                stream=False,
            )
            content = resp.get("content", "")
            # 提取 JSON 数组
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                steps_data = json.loads(content[start:end])
                if isinstance(steps_data, list) and steps_data:
                    return PlanTemplates.multi_step_task(task_id, user_input, steps_data)
        except Exception as e:
            logger.warning(f"[TaskPlanner] LLM 规划失败（回退单步）: {e}")

        # 回退：单步直接执行
        return Plan(
            task_id=task_id,
            original_request=user_input,
            steps=[PlanStep(
                name="execute",
                description="直接执行用户请求",
                step_type="llm",
            )]
        )

    # ── 执行 ──────────────────────────────────────────────────────────────────

    def execute_plan(
        self,
        plan: Plan,
        executor_fn: Callable[[PlanStep, Dict[str, Any]], Any],
        approval_fn: Optional[Callable[[PlanStep], bool]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        顺序执行计划中所有就绪步骤。

        Args:
            plan:         已构建的 Plan
            executor_fn:  步骤执行函数 (step, context) -> result
            approval_fn:  人工确认回调 (step) -> True/False（None 时自动通过）
            cancel_check: 取消检查函数 () -> bool

        Yields:
            每步完成后的事件字典::

                {"event": "step_start"|"step_done"|"step_failed"|"plan_done", ...}
        """
        plan.status = "running"
        results: Dict[str, Any] = {}  # step_name -> result

        self._publish_plan_event(plan, "plan_start", "计划开始执行")

        while not plan.is_done():
            # 检查取消
            if cancel_check and cancel_check():
                plan.status = "cancelled"
                yield {"event": "plan_cancelled", "task_id": plan.task_id}
                self._publish_plan_event(plan, "plan_cancelled", "计划已取消")
                return

            ready = plan.ready_steps()
            if not ready:
                if plan.has_blocking_failure():
                    plan.status = "failed"
                    yield {"event": "plan_failed", "task_id": plan.task_id, "reason": "blocking_step_failed"}
                    self._publish_plan_event(plan, "plan_failed", "计划因步骤失败中止")
                    return
                # 没有就绪步骤也没有失败 → 所有步骤已处理完
                break

            for step in ready:
                # 检查取消
                if cancel_check and cancel_check():
                    plan.status = "cancelled"
                    yield {"event": "plan_cancelled", "task_id": plan.task_id}
                    return

                # 收集依赖输出作为上下文
                step_ctx = {
                    dep: results.get(dep) for dep in step.depends_on
                }
                step_ctx.update(plan.context)

                # 人工确认
                if step.require_approval:
                    step.status = StepStatus.WAITING
                    self._publish_step_event(plan.task_id, step, "step_waiting")
                    yield {"event": "step_waiting", "step": step.to_dict()}
                    if approval_fn:
                        approved = approval_fn(step)
                    else:
                        approved = True  # 自动通过
                    if not approved:
                        step.status = StepStatus.SKIPPED
                        yield {"event": "step_skipped", "step": step.to_dict()}
                        continue

                # 执行
                step.status = StepStatus.RUNNING
                step.started_at = datetime.now().isoformat(timespec="milliseconds")
                self._publish_step_event(plan.task_id, step, "step_start")
                yield {"event": "step_start", "step": step.to_dict(), "progress": plan.progress_percent()}

                success = False
                while step.retry_count <= step.max_retries:
                    try:
                        result = executor_fn(step, step_ctx)
                        step.result = result
                        step.status = StepStatus.COMPLETED
                        step.completed_at = datetime.now().isoformat(timespec="milliseconds")
                        results[step.name] = result
                        success = True
                        break
                    except Exception as e:
                        step.error = str(e)
                        step.retry_count += 1
                        if step.retry_count <= step.max_retries:
                            logger.warning(
                                f"[TaskPlanner] 步骤 {step.name} 失败，第 {step.retry_count} 次重试: {e}"
                            )
                            time.sleep(min(2 ** step.retry_count, 30))
                        else:
                            logger.error(f"[TaskPlanner] 步骤 {step.name} 最终失败: {e}")

                if success:
                    self._publish_step_event(plan.task_id, step, "step_done")
                    yield {
                        "event": "step_done",
                        "step": step.to_dict(),
                        "progress": plan.progress_percent(),
                    }
                else:
                    step.status = StepStatus.FAILED
                    step.completed_at = datetime.now().isoformat(timespec="milliseconds")
                    self._publish_step_event(plan.task_id, step, "step_failed")
                    yield {
                        "event": "step_failed",
                        "step": step.to_dict(),
                        "progress": plan.progress_percent(),
                    }
                    if not step.allow_failure:
                        # 将后续依赖此步骤的步骤标记为跳过
                        self._skip_dependents(plan, step.name)

        # 计划完成
        if plan.has_blocking_failure():
            plan.status = "failed"
        else:
            plan.status = "completed"

        self._publish_plan_event(plan, "plan_done", f"计划{plan.status}！共 {len(plan.steps)} 步")
        yield {
            "event": "plan_done",
            "task_id": plan.task_id,
            "status": plan.status,
            "progress": plan.progress_percent(),
        }

    # ── 内部工具 ────────────────────────────────────────────────────────────

    @staticmethod
    def _skip_dependents(plan: Plan, failed_name: str):
        """递归将依赖 failed_name 的步骤设为 SKIPPED。"""
        changed = True
        while changed:
            changed = False
            skip_names = {
                s.name for s in plan.steps
                if s.status in (StepStatus.FAILED, StepStatus.SKIPPED)
            }
            for s in plan.steps:
                if s.status == StepStatus.PENDING:
                    if any(dep in skip_names for dep in s.depends_on):
                        s.status = StepStatus.SKIPPED
                        changed = True

    @staticmethod
    def _publish_step_event(task_id: str, step: PlanStep, event_type: str):
        try:
            from app.core.tasks.progress_bus import get_progress_bus, ProgressEvent
            bus = get_progress_bus()
            bus.publish(ProgressEvent(
                task_id=task_id,
                event_type=event_type,
                step_type=step.step_type.upper(),
                message=step.description,
                progress=step.retry_count,
                detail={"step_name": step.name, "status": step.status.value},
            ))
        except Exception:
            pass

    @staticmethod
    def _publish_plan_event(plan: Plan, event_type: str, message: str):
        try:
            from app.core.tasks.progress_bus import get_progress_bus, ProgressEvent
            bus = get_progress_bus()
            bus.publish(ProgressEvent(
                task_id=plan.task_id,
                event_type=event_type,
                status=plan.status,
                message=message,
                progress=plan.progress_percent(),
            ))
        except Exception:
            pass
