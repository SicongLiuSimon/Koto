# -*- coding: utf-8 -*-
"""
Koto Interactive Planner
========================
通用多步骤任务规划器的高层门面（Facade）。

向下桥接 app.core.tasks.task_planner（通用 DAG 引擎），
向上为调用方暴露简洁、领域友好的接口。

兼容性：保留旧 TaskPlanStep / TaskPlan 数据类（供 PPT 等旧代码使用），
同时提供基于新引擎的 InteractivePlanner.create_plan() 通用入口。
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# 旧数据类（向后兼容）
# ============================================================================


@dataclass
class TaskPlanStep:
    step_id: int
    step_type: (
        str  # "research", "outline", "content_gen", "image_gen", "review", "assembly"
    )
    description: str
    input_data: Dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""
    status: str = "pending"  # pending, running, completed, failed
    result: Optional[Any] = None


@dataclass
class TaskPlan:
    task_id: str
    original_request: str
    steps: List[TaskPlanStep]
    context: Dict[str, Any] = field(default_factory=dict)
    status: str = "planning"


# ============================================================================
# InteractivePlanner — 通用规划门面
# ============================================================================


class InteractivePlanner:
    """
    任务规划门面，提供：
      - 领域特定计划构建（PPT、研究报告、数据管道……）
      - LLM 动态规划（调用 create_plan_with_llm）
      - 统一执行入口（execute，代理到 TaskPlanner.execute_plan）

    与旧代码的兼容：
      create_ppt_plan()  返回旧 TaskPlan（PPT 流水线专用）
      to_new_plan()      将旧 TaskPlan 转换为新 Plan 对象
    """

    # ── PPT 专用（向后兼容保留）────────────────────────────────────────────────

    @staticmethod
    def create_ppt_plan(user_input: str) -> TaskPlan:
        """
        构建 PPT 生成的旧式 TaskPlan，供 web/ppt_pipeline.py 等调用。

        阶段：研究 → 大纲 → 内容扩展 → 组装
        """
        steps = [
            TaskPlanStep(
                step_id=1,
                step_type="research",
                description="Analyze user request and gather background context",
                input_data={"query": user_input},
                expected_output="Context summary and key themes",
            ),
            TaskPlanStep(
                step_id=2,
                step_type="outline",
                description="Design PPT structure (Slides, Titles, Layouts)",
                input_data={"context_ref": 1},
                expected_output="JSON outline with slide titles and types",
            ),
            TaskPlanStep(
                step_id=3,
                step_type="content_expansion",
                description="Generate detailed content for each slide",
                input_data={"outline_ref": 2},
                expected_output="Full markdown/JSON content for all slides",
            ),
            TaskPlanStep(
                step_id=4,
                step_type="assembly",
                description="Compile content into final PPTX file",
                input_data={"content_ref": 3},
                expected_output="PPTX File path",
            ),
        ]
        return TaskPlan(
            task_id=f"ppt_{uuid.uuid4().hex[:8]}",
            original_request=user_input,
            steps=steps,
        )

    # ── 通用规划（新引擎）──────────────────────────────────────────────────────

    @staticmethod
    def create_plan(
        user_input: str,
        plan_type: str = "auto",
        task_id: Optional[str] = None,
    ):
        """
        构建通用 Plan 对象（新引擎 app.core.tasks.task_planner.Plan）。

        Args:
            user_input: 用户原始需求
            plan_type:  "ppt" | "research_report" | "data_pipeline" | "auto"
                        auto = 单步直接执行（等效旧行为）
            task_id:    指定 task_id（None 时自动生成）

        Returns:
            app.core.tasks.task_planner.Plan
        """
        from app.core.tasks.task_planner import PlanTemplates

        _tid = task_id or str(uuid.uuid4())

        if plan_type == "ppt":
            # 将旧 PPT TaskPlan 转换为新 Plan
            old = InteractivePlanner.create_ppt_plan(user_input)
            return InteractivePlanner.to_new_plan(old, task_id=_tid)
        elif plan_type == "research_report":
            return PlanTemplates.research_and_report(_tid, user_input)
        elif plan_type == "data_pipeline":
            return PlanTemplates.data_pipeline(_tid, user_input)
        else:
            # auto: 单步占位，实际执行通过 create_plan_with_llm 升级
            from app.core.tasks.task_planner import Plan, PlanStep

            plan = Plan(task_id=_tid, original_request=user_input)
            plan.add_step(
                PlanStep(
                    name="execute",
                    description=user_input[:100],
                    step_type="llm",
                )
            )
            return plan

    @staticmethod
    def create_plan_with_llm(
        user_input: str,
        llm_provider: Any,
        task_id: Optional[str] = None,
        model_id: str = "gemini-3-flash-preview",
    ):
        """
        使用 LLM 动态拆解用户需求为步骤 DAG。

        Args:
            user_input:    用户输入
            llm_provider:  LLMProvider 实例
            task_id:       指定 task_id
            model_id:      用于规划的模型

        Returns:
            app.core.tasks.task_planner.Plan
        """
        from app.core.tasks.task_planner import TaskPlanner

        _tid = task_id or str(uuid.uuid4())
        planner = TaskPlanner()
        return planner.plan_with_llm(_tid, user_input, llm_provider, model_id=model_id)

    @staticmethod
    def create_plan_with_context(
        user_input: str,
        llm_provider: Any,
        task_id: Optional[str] = None,
        model_id: str = "gemini-3-flash-preview",
        tool_registry: Any = None,
        history: Optional[list] = None,
    ):
        """
        增强版 LLM 规划：自动注入可用工具列表和会话历史摘要，
        生成工具感知、上下文感知的步骤 DAG（减少 token 消耗，提高精度）。

        Args:
            tool_registry: ToolRegistry 实例（提取可用工具名）
            history:       消息历史列表（生成会话上下文摘要）
        """
        from app.core.tasks.task_planner import TaskPlanner

        _tid = task_id or str(uuid.uuid4())
        planner = TaskPlanner()
        return planner.plan_with_context(
            task_id=_tid,
            user_input=user_input,
            llm_provider=llm_provider,
            model_id=model_id,
            tool_registry=tool_registry,
            history=history,
        )

    @staticmethod
    def execute(
        plan,
        executor_fn,
        approval_fn=None,
        cancel_check=None,
        llm_provider: Any = None,
        replan_model_id: str = "gemini-3-flash-preview",
    ) -> Generator[Dict[str, Any], None, None]:
        """
        执行 Plan（新引擎格式）。

        Args:
            plan:             app.core.tasks.task_planner.Plan
            executor_fn:      (step, context) -> result
            approval_fn:      (step) -> bool（人工确认回调，None=自动通过）
            cancel_check:     () -> bool（取消检查）
            llm_provider:     可选，提供后启用再规划能力
            replan_model_id:  再规划使用的模型 ID

        Yields:
            步骤事件字典 {"event": "step_done"|"step_failed"|"replan"|"plan_done", ...}
        """
        from app.core.tasks.task_planner import TaskPlanner

        planner = TaskPlanner()
        yield from planner.execute_plan(
            plan,
            executor_fn=executor_fn,
            approval_fn=approval_fn,
            cancel_check=cancel_check,
            llm_provider=llm_provider,
            replan_model_id=replan_model_id,
        )

    # ── 互转工具 ──────────────────────────────────────────────────────────────

    @staticmethod
    def to_new_plan(old_plan: TaskPlan, task_id: Optional[str] = None):
        """将旧 TaskPlan 转换为新 Plan 对象。"""
        from app.core.tasks.task_planner import Plan, PlanStep

        _tid = task_id or old_plan.task_id
        new_plan = Plan(task_id=_tid, original_request=old_plan.original_request)
        prev_name: Optional[str] = None
        for s in old_plan.steps:
            name = s.step_type or f"step_{s.step_id}"
            new_plan.add_step(
                PlanStep(
                    name=name,
                    description=s.description,
                    step_type=s.step_type,
                    input_data=s.input_data,
                    expected_output=s.expected_output,
                    depends_on=[prev_name] if prev_name else [],
                )
            )
            prev_name = name
        return new_plan

    @staticmethod
    def to_old_plan(new_plan) -> TaskPlan:
        """将新 Plan 对象转换为旧 TaskPlan（向后兼容）。"""
        steps = []
        for idx, s in enumerate(new_plan.steps, start=1):
            steps.append(
                TaskPlanStep(
                    step_id=idx,
                    step_type=s.step_type,
                    description=s.description,
                    input_data=s.input_data,
                    expected_output=s.expected_output,
                    status=(
                        s.status.value if hasattr(s.status, "value") else str(s.status)
                    ),
                    result=s.result,
                )
            )
        return TaskPlan(
            task_id=new_plan.task_id,
            original_request=new_plan.original_request,
            steps=steps,
            status=new_plan.status,
        )
