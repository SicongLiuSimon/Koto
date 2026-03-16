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
import threading
import time
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
    PENDING = "pending"
    READY = "ready"  # 依赖已满足，可执行
    RUNNING = "running"
    WAITING = "waiting"  # 等待人工确认
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # 因上游失败被跳过


@dataclass
class StepResult:
    """
    步骤执行结果的结构化封装。

    规划层通过 ``summary`` / ``key_facts`` 向后续步骤传递精炼的上下文，
    避免将完整原始输出直接填入 prompt（大幅降低 token 消耗）。
    ``replan_hint`` 允许执行层在发现计划偏差时向规划层反馈再规划信号。
    """
    full_output: str                    # 完整原始输出（保留备查）
    summary: str = ""                   # 压缩摘要（≤300 字），用于后续步骤的上下文注入
    key_facts: List[str] = field(default_factory=list)  # 提炼出的关键事实列表
    replan_hint: str = ""               # 非空时触发再规划："后续步骤应改为…"
    structured: Optional[Dict[str, Any]] = None  # 可选结构化数据（JSON）

    def context_text(self) -> str:
        """生成适合注入到后续步骤 prompt 的精简上下文文本。"""
        parts: List[str] = []
        if self.summary:
            parts.append(self.summary)
        if self.key_facts:
            parts.append("关键事实：" + "；".join(self.key_facts[:5]))
        return "\n".join(parts) or self.full_output[:500]


@dataclass
class StepResult:
    """
    步骤执行结果的结构化封装。

    规划层通过 ``summary`` / ``key_facts`` 向后续步骤传递精炼的上下文，
    避免将完整原始输出直接填入 prompt（大幅降低 token 消耗）。
    ``replan_hint`` 允许执行层在发现计划偏差时向规划层反馈再规划信号。
    """
    full_output: str                    # 完整原始输出（保留备查）
    summary: str = ""                   # 压缩摘要（≤300 字），用于后续步骤的上下文注入
    key_facts: List[str] = field(default_factory=list)  # 提炼出的关键事实列表
    replan_hint: str = ""               # 非空时触发再规划："后续步骤应改为…"
    structured: Optional[Dict[str, Any]] = None  # 可选结构化数据（JSON）

    def context_text(self) -> str:
        """生成适合注入到后续步骤 prompt 的精简上下文文本。"""
        parts: List[str] = []
        if self.summary:
            parts.append(self.summary)
        if self.key_facts:
            parts.append("关键事实：" + "；".join(self.key_facts[:5]))
        return "\n".join(parts) or self.full_output[:500]


@dataclass
class PlanStep:
    """DAG 中的一个执行步骤。"""

    name: str  # 唯一名称（同一 Plan 内）
    description: str  # 对用户可见的描述
    step_type: str = "generic"  # 用于选择执行器（"llm"/"code"/"file"/"ppt"…）
    input_data: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)  # 依赖的步骤名列表
    require_approval: bool = False  # 执行前需要人工确认
    max_retries: int = 2
    timeout_seconds: int = 120
    allow_failure: bool = False  # True = 本步失败不阻塞后续步骤

    # ── 规划层填充的执行指导（v2 新增，均有默认值保持向后兼容） ────────────────
    executor_prompt: str = ""              # 执行器应使用的具体指令（替代模糊的 description）
    context_keys: List[str] = field(default_factory=list)  # 明确声明需要哪些上游结果（空=所有依赖）
    result_schema: str = ""               # 预期输出格式描述，用于引导执行器和验收
    success_criteria: str = ""            # 判断本步成功的标准
    suggested_tools: List[str] = field(default_factory=list)  # 建议执行器使用的工具名列表

    # ── 规划层填充的执行指导（v2 新增，均有默认值保持向后兼容） ────────────────
    executor_prompt: str = ""              # 执行器应使用的具体指令（替代模糊的 description）
    context_keys: List[str] = field(default_factory=list)  # 明确声明需要哪些上游结果（空=所有依赖）
    result_schema: str = ""               # 预期输出格式描述，用于引导执行器和验收
    success_criteria: str = ""            # 判断本步成功的标准
    suggested_tools: List[str] = field(default_factory=list)  # 建议执行器使用的工具名列表

    # 运行时字段（不参与 dict 初始化）
    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_count: int = 0
    result: Any = None                     # 兼容旧代码的原始结果；优先使用 step_result
    step_result: Optional["StepResult"] = None  # 结构化结果（v2）
    error: Optional[str] = None
    expected_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        # step_result 是非原始类型，asdict 已深拷贝但需排除 dataclass 内嵌
        # 用简单字典替代，保持可序列化
        if self.step_result is not None:
            d["step_result"] = {
                "summary": self.step_result.summary,
                "key_facts": self.step_result.key_facts,
                "replan_hint": self.step_result.replan_hint,
            }
        else:
            d.pop("step_result", None)
        return d

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            StepStatus.COMPLETED,
            StepStatus.FAILED,
            StepStatus.SKIPPED,
        )

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
    status: str = "planning"  # planning / running / completed / failed
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="milliseconds")
    )

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
            s.name
            for s in self.steps
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
            s.status == StepStatus.FAILED and not s.allow_failure for s in self.steps
        )

    def progress_percent(self) -> int:
        if not self.steps:
            return 0
        completed = sum(
            1
            for s in self.steps
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
        plan.add_step(
            PlanStep(
                name="research",
                description="收集相关信息与资料",
                step_type="llm",
                expected_output="结构化研究摘要",
            )
        )
        plan.add_step(
            PlanStep(
                name="outline",
                description="生成报告大纲",
                step_type="llm",
                depends_on=["research"],
                expected_output="Markdown 大纲",
                require_approval=False,
            )
        )
        plan.add_step(
            PlanStep(
                name="write",
                description="撰写完整报告内容",
                step_type="llm",
                depends_on=["outline"],
                expected_output="完整报告文本",
                timeout_seconds=180,
            )
        )
        plan.add_step(
            PlanStep(
                name="export",
                description="导出为文档文件",
                step_type="file",
                depends_on=["write"],
                expected_output="文件路径",
            )
        )
        return plan

    @staticmethod
    def data_pipeline(task_id: str, request: str) -> Plan:
        """数据处理流水线模板"""
        plan = Plan(task_id=task_id, original_request=request)
        plan.add_step(
            PlanStep(name="load", description="加载/读取数据源", step_type="file")
        )
        plan.add_step(
            PlanStep(
                name="validate",
                description="验证数据质量",
                step_type="code",
                depends_on=["load"],
            )
        )
        plan.add_step(
            PlanStep(
                name="transform",
                description="数据清洗与转换",
                step_type="code",
                depends_on=["validate"],
            )
        )
        plan.add_step(
            PlanStep(
                name="analyze",
                description="执行分析计算",
                step_type="code",
                depends_on=["transform"],
            )
        )
        plan.add_step(
            PlanStep(
                name="report",
                description="生成分析报告/图表",
                step_type="llm",
                depends_on=["analyze"],
            )
        )
        return plan

    @staticmethod
    def multi_step_task(
        task_id: str, request: str, steps: List[Dict[str, Any]]
    ) -> Plan:
        """
        从 LLM 返回的步骤列表动态构建 Plan。

        steps 格式（v2 扩展，所有新字段均为可选以保持向后兼容）::

            [
              {
                "name": "step1",
                "description": "…",
                "depends_on": [],
                "require_approval": false,
                "executor_prompt": "具体执行指令",
                "context_keys": [],
                "result_schema": "输出格式说明",
                "success_criteria": "成功判定标准",
                "suggested_tools": ["web_search"]
              }
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
                # v2 新字段
                executor_prompt=s.get("executor_prompt", ""),
                context_keys=s.get("context_keys", []),
                result_schema=s.get("result_schema", ""),
                success_criteria=s.get("success_criteria", ""),
                suggested_tools=s.get("suggested_tools", []),
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
        available_tools: Optional[List[str]] = None,
        session_context: str = "",
    ) -> Plan:
        """
        调用 LLM 将用户输入分解为步骤列表，返回 Plan。

        v2 增强：
        - ``available_tools``  将工具名列表注入 prompt，让规划模型生成工具感知的步骤
        - ``session_context``  注入对话历史摘要，避免重复收集已知信息
        - 输出 schema 扩展至包含 executor_prompt / context_keys / result_schema /
          success_criteria / suggested_tools，大幅减少执行层猜测成本
        若 LLM 失败，回退到单步直接执行计划。
        """
        # ── 构建工具说明段落 ────────────────────────────────────────────────
        tools_section = ""
        if available_tools:
            tools_list = "\n".join(f"  - {t}" for t in available_tools[:40])
            tools_section = f"""
## 可用工具（在 suggested_tools 中引用时请使用此列表中的名称）
{tools_list}
"""

        # ── 构建历史上下文段落 ───────────────────────────────────────────────
        context_section = ""
        if session_context.strip():
            context_section = f"""
## 已知上下文（来自当前会话）
{session_context.strip()[:800]}
"""

        prompt = f"""你是 Koto 的任务规划模块。你的职责是将用户请求拆解为高质量、可执行的步骤 DAG。
{context_section}{tools_section}
## 用户请求
{user_input}

## 输出规范
输出**纯 JSON 数组**（不允许 markdown 代码块、注释或多余文字）。每个步骤字段如下：

```json
[
  {{
    "name": "snake_case_短名称",
    "description": "面向用户的简洁步骤说明（≤50字）",
    "step_type": "llm|code|file|search|tool",
    "depends_on": ["前置步骤name列表"],
    "require_approval": false,
    "allow_failure": false,
    "executor_prompt": "执行器应直接使用的具体指令（包含所有必要约束，≤200字）",
    "context_keys": ["本步骤需要从哪些前置步骤名获取结果，空=自动继承所有依赖"],
    "result_schema": "期望输出的结构描述（如：JSON对象含title/content字段；或Markdown列表）",
    "success_criteria": "如何判断本步骤成功完成（一句话）",
    "suggested_tools": ["建议调用的工具名，仅使用可用工具列表中的名称"],
    "expected_output": "预期产出物描述",
    "timeout_seconds": 120
  }}
]
```

## 规划规则
1. 步骤数量：简单任务 1 步，复杂任务 2-6 步，切勿过度分解
2. depends_on：严格声明数据依赖，形成正确 DAG（无环）
3. executor_prompt：必须自包含，执行器只能看到本字段 + context_keys 中的上游输出，**不会**重新看到用户原始请求
4. context_keys：精确声明所需的上游步骤名，避免传递冗余大文本到后续步骤
5. suggested_tools：只填写可用工具列表中存在的名称；不涉及工具则留空数组
6. 简单问答（无需工具或文件）：单步骤 answer，step_type=llm
7. 禁止输出注释"""

        try:
            resp = llm_provider.generate_content(
                prompt=[{"role": "user", "content": prompt}],
                model=model_id,
                stream=False,
            )
            content = resp.get("content", "")
            # 提取 JSON 数组（兼容模型在输出中包裹 code block 的情况）
            # 优先找裸数组，退而取 ```json ... ```
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                steps_data = json.loads(content[start:end])
                if isinstance(steps_data, list) and steps_data:
                    plan = PlanTemplates.multi_step_task(task_id, user_input, steps_data)
                    logger.info(
                        "[TaskPlanner] ✅ LLM 规划完成：%d 步（工具感知=%s）",
                        len(plan.steps), bool(available_tools),
                    )
                    return plan
        except Exception as e:
            logger.warning("[TaskPlanner] LLM 规划失败（回退单步）: %s", e)

        # 回退：单步直接执行
        return Plan(
            task_id=task_id,
            original_request=user_input,
            steps=[PlanStep(
                name="execute",
                description="直接执行用户请求",
                step_type="llm",
                executor_prompt=user_input,
            )]
        )

    def plan_with_context(
        self,
        task_id: str,
        user_input: str,
        llm_provider: Any,
        model_id: str = "gemini-3-flash-preview",
        tool_registry: Any = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> "Plan":
        """
        便捷方法：自动从 ToolRegistry 提取工具列表 + 从 history 生成会话摘要，
        然后调用 plan_with_llm。

        Args:
            tool_registry: ToolRegistry 实例，用于提取可用工具名称列表。
            history:       消息历史列表，用于生成简短的会话上下文摘要。
        """
        # 提取工具名列表
        available_tools: List[str] = []
        if tool_registry is not None:
            try:
                defs = tool_registry.get_definitions()
                available_tools = [d.get("name", "") for d in defs if d.get("name")]
            except Exception as _te:
                logger.debug("[TaskPlanner] 无法提取工具列表: %s", _te)

        # 生成会话上下文摘要（取最近 4 轮 user/model 消息，不含 function 角色）
        session_context = ""
        if history:
            relevant = [
                m for m in history
                if m.get("role") in ("user", "model") and m.get("content")
            ][-8:]  # 最近 4 轮（user+model 各算 1 条）
            if relevant:
                session_context = "\n".join(
                    f"[{m['role']}] {str(m['content'])[:200]}"
                    for m in relevant
                )

        return self.plan_with_llm(
            task_id=task_id,
            user_input=user_input,
            llm_provider=llm_provider,
            model_id=model_id,
            available_tools=available_tools,
            session_context=session_context,
        )

    def plan_from_skill(
        self,
        skill_id: str,
        task_id: str,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Plan]:
        """
        从 Skill 的 plan_template 构建确定性 Plan。

        当 Skill 定义了 plan_template 时，直接用模板生成 Plan，
        而不依赖 LLM 动态规划，保证关键任务（如文档标注）的执行步骤稳定可靠。

        Args:
            skill_id  : Skill ID
            task_id   : 任务 ID
            user_input: 用户原始请求
            context   : 附加上下文（写入 plan.context）

        Returns:
            Plan if skill has plan_template, None otherwise.
            返回 None 时调用方应降级到 plan_with_llm()。
        """
        try:
            from app.core.skills.skill_capability import SkillCapabilityRegistry
            template = SkillCapabilityRegistry.get_plan_template(skill_id)
        except Exception as e:
            logger.warning("[TaskPlanner] plan_from_skill() 无法加载模板: %s", e)
            return None

        if not template:
            return None

        plan = PlanTemplates.multi_step_task(task_id, user_input, template)
        plan.context["skill_id"] = skill_id
        if context:
            plan.context.update(context)

        logger.info(
            "[TaskPlanner] 📋 从 Skill '%s' 构建计划: %d 步",
            skill_id, len(plan.steps),
        )
        return plan

    # ── 执行 ──────────────────────────────────────────────────────────────────

    def execute_plan(
        self,
        plan: Plan,
        executor_fn: Callable[[PlanStep, Dict[str, Any]], Any],
        approval_fn: Optional[Callable[[PlanStep], bool]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        llm_provider: Any = None,
        replan_model_id: str = "gemini-3-flash-preview",
    ) -> Generator[Dict[str, Any], None, None]:
        """
        顺序执行计划中所有就绪步骤。

        v2 增强：
        - 选择性上下文注入：根据 step.context_keys 过滤上游结果，避免无关大文本污染 prompt
        - StepResult 感知：优先使用结构化摘要（summary/key_facts）而非完整原始输出向后续步骤传递
        - 再规划信号：每步完成后检查 step.step_result.replan_hint；若非空，调用
          replan_remaining() 动态修改剩余步骤（需要 llm_provider）
        - 执行前将 executor_prompt / suggested_tools 写入 step.input_data，
          供 executor_fn 使用（向后兼容：旧 executor_fn 可忽略这些字段）

        Args:
            plan:            已构建的 Plan
            executor_fn:     步骤执行函数 (step, context) -> result
            approval_fn:     人工确认回调 (step) -> True/False（None 时自动通过）
            cancel_check:    取消检查函数 () -> bool
            llm_provider:    可选，用于再规划的 LLM 实例
            replan_model_id: 再规划时使用的模型 ID

        Yields:
            每步完成后的事件字典::

                {"event": "step_start"|"step_done"|"step_failed"|"replan"|"plan_done", ...}
        """
        plan.status = "running"
        # step_name -> 精简上下文文本（优先 StepResult.context_text，否则 str(result)[:500]）
        results: Dict[str, Any] = {}
        result_texts: Dict[str, str] = {}

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
                    yield {
                        "event": "plan_failed",
                        "task_id": plan.task_id,
                        "reason": "blocking_step_failed",
                    }
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

                # ── 选择性上下文注入（v2） ────────────────────────────────────
                # 若 step.context_keys 非空，只传该列表指定的上游结果；
                # 否则传递所有 depends_on 的上游摘要（兼容旧行为）
                _ctx_keys = step.context_keys if step.context_keys else step.depends_on
                step_ctx: Dict[str, Any] = {}
                for key in _ctx_keys:
                    # 优先使用 StepResult 摘要文本，避免大块原始输出污染上下文
                    if key in result_texts:
                        step_ctx[key] = result_texts[key]
                    elif key in results:
                        step_ctx[key] = str(results[key])[:500]
                step_ctx.update(plan.context)

                # 把执行指导字段写入 input_data，executor_fn 可自行决定是否读取
                if step.executor_prompt:
                    step.input_data.setdefault("executor_prompt", step.executor_prompt)
                if step.suggested_tools:
                    step.input_data.setdefault("suggested_tools", step.suggested_tools)
                if step.result_schema:
                    step.input_data.setdefault("result_schema", step.result_schema)
                if step.success_criteria:
                    step.input_data.setdefault("success_criteria", step.success_criteria)

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
                yield {
                    "event": "step_start",
                    "step": step.to_dict(),
                    "progress": plan.progress_percent(),
                }

                success = False
                while step.retry_count <= step.max_retries:
                    try:
                        result = executor_fn(step, step_ctx)
                        step.result = result
                        step.status = StepStatus.COMPLETED
                        step.completed_at = datetime.now().isoformat(
                            timespec="milliseconds"
                        )
                        results[step.name] = result

                        # ── 提取/构建 StepResult 用于精简上下文传递 ──────────
                        if isinstance(result, StepResult):
                            step.step_result = result
                            result_texts[step.name] = result.context_text()
                        else:
                            raw_str = str(result)
                            # 若未提供结构化结果，用原始输出前500字作为摘要
                            result_texts[step.name] = raw_str[:500]

                        success = True
                        break
                    except Exception as e:
                        step.error = str(e)
                        step.retry_count += 1
                        if step.retry_count <= step.max_retries:
                            logger.warning(
                                "[TaskPlanner] 步骤 %s 失败，第 %d 次重试: %s",
                                step.name, step.retry_count, e,
                            )
                            time.sleep(min(2**step.retry_count, 30))
                        else:
                            logger.error("[TaskPlanner] 步骤 %s 最终失败: %s", step.name, e)

                if success:
                    self._publish_step_event(plan.task_id, step, "step_done")
                    yield {
                        "event": "step_done",
                        "step": step.to_dict(),
                        "progress": plan.progress_percent(),
                    }

                    # ── 再规划信号检查（v2） ────────────────────────────────
                    _replan_hint = (
                        step.step_result.replan_hint
                        if step.step_result else ""
                    )
                    if _replan_hint and llm_provider:
                        logger.info(
                            "[TaskPlanner] 🔄 步骤 '%s' 触发再规划: %s",
                            step.name, _replan_hint[:100],
                        )
                        changed = self.replan_remaining(
                            plan=plan,
                            llm_provider=llm_provider,
                            model_id=replan_model_id,
                            replan_hint=_replan_hint,
                            completed_summary="\n".join(
                                f"[{k}] {v[:200]}" for k, v in result_texts.items()
                            ),
                        )
                        if changed:
                            yield {
                                "event": "replan",
                                "task_id": plan.task_id,
                                "trigger_step": step.name,
                                "hint": _replan_hint,
                                "remaining_steps": [
                                    s.to_dict() for s in plan.steps
                                    if s.status == StepStatus.PENDING
                                ],
                            }
                else:
                    step.status = StepStatus.FAILED
                    step.completed_at = datetime.now().isoformat(
                        timespec="milliseconds"
                    )
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

        self._publish_plan_event(
            plan, "plan_done", f"计划{plan.status}！共 {len(plan.steps)} 步"
        )
        yield {
            "event": "plan_done",
            "task_id": plan.task_id,
            "status": plan.status,
            "progress": plan.progress_percent(),
        }

    # ── 再规划 ────────────────────────────────────────────────────────────────

    def replan_remaining(
        self,
        plan: Plan,
        llm_provider: Any,
        model_id: str = "gemini-3-flash-preview",
        replan_hint: str = "",
        completed_summary: str = "",
    ) -> bool:
        """
        根据执行过程中发现的新信息，动态修改计划中尚未执行的步骤。

        调用时机：某步骤执行结果包含 ``replan_hint`` 非空字符串，表明后续步骤
        应该进行调整（如发现用户意图有误、发现了更高效的执行路径、数据格式与预期不符）。

        实现策略：
        1. 提取所有 PENDING 步骤作为"待修改步骤清单"
        2. 将原始请求、已完成摘要、再规划提示一起送给 LLM
        3. LLM 输出修订后的步骤数组（格式与 plan_with_llm 相同）
        4. 按名称匹配并原地更新 PENDING 步骤的字段；添加新步骤；删除 LLM 未返回的步骤

        Args:
            plan:              当前执行中的 Plan（直接修改）
            llm_provider:      LLM 实例
            model_id:          规划模型 ID
            replan_hint:       触发再规划的具体原因/建议
            completed_summary: 已完成步骤的摘要文本

        Returns:
            True 表示计划被成功修改，False 表示未做变更（LLM 失败或无差异）。
        """
        pending_steps = [s for s in plan.steps if s.status == StepStatus.PENDING]
        if not pending_steps:
            return False

        pending_desc = "\n".join(
            f"  {i+1}. [{s.name}] {s.description}" for i, s in enumerate(pending_steps)
        )

        prompt = f"""你是 Koto 的任务再规划模块。执行过程中发现实际情况与计划存在偏差，需要修订剩余步骤。

## 原始用户请求
{plan.original_request}

## 已完成步骤摘要
{completed_summary or "（无）"}

## 再规划原因
{replan_hint}

## 当前待执行步骤（需要修订）
{pending_desc}

## 输出规范
输出**纯 JSON 数组**，仅包含修订后需要执行的步骤（可增删改，但不能包含已完成的步骤）。
字段与原始规划格式完全相同：name, description, step_type, depends_on, executor_prompt,
context_keys, result_schema, success_criteria, suggested_tools, expected_output。
若当前计划已足够，原样输出即可。禁止添加注释。"""

        try:
            resp = llm_provider.generate_content(
                prompt=[{"role": "user", "content": prompt}],
                model=model_id,
                stream=False,
            )
            content = resp.get("content", "")
            start = content.find("[")
            end = content.rfind("]") + 1
            if start < 0 or end <= start:
                return False
            updated_steps_data: List[Dict[str, Any]] = json.loads(content[start:end])
            if not isinstance(updated_steps_data, list):
                return False
        except Exception as e:
            logger.warning("[TaskPlanner] 再规划 LLM 调用失败: %s", e)
            return False

        # ── 应用再规划结果 ───────────────────────────────────────────────────
        updated_names = {s.get("name") for s in updated_steps_data if s.get("name")}
        changed = False

        # 删除：LLM 未返回的 PENDING 步骤（被取消）
        for step in list(pending_steps):
            if step.name not in updated_names:
                step.status = StepStatus.SKIPPED
                logger.info("[TaskPlanner] 再规划：跳过步骤 '%s'", step.name)
                changed = True

        # 更新/添加
        existing_names = {s.name for s in plan.steps}
        for sd in updated_steps_data:
            s_name = sd.get("name", "")
            if not s_name:
                continue
            existing = plan.get_step(s_name)
            if existing and existing.status == StepStatus.PENDING:
                # 原地更新可修改字段
                existing.description = sd.get("description", existing.description)
                existing.executor_prompt = sd.get("executor_prompt", existing.executor_prompt)
                existing.context_keys = sd.get("context_keys", existing.context_keys)
                existing.result_schema = sd.get("result_schema", existing.result_schema)
                existing.success_criteria = sd.get("success_criteria", existing.success_criteria)
                existing.suggested_tools = sd.get("suggested_tools", existing.suggested_tools)
                existing.depends_on = sd.get("depends_on", existing.depends_on)
                changed = True
            elif s_name not in existing_names:
                # 新增步骤
                plan.add_step(PlanStep(
                    name=s_name,
                    description=sd.get("description", ""),
                    step_type=sd.get("step_type", "llm"),
                    depends_on=sd.get("depends_on", []),
                    executor_prompt=sd.get("executor_prompt", ""),
                    context_keys=sd.get("context_keys", []),
                    result_schema=sd.get("result_schema", ""),
                    success_criteria=sd.get("success_criteria", ""),
                    suggested_tools=sd.get("suggested_tools", []),
                    expected_output=sd.get("expected_output", ""),
                ))
                changed = True
                logger.info("[TaskPlanner] 再规划：新增步骤 '%s'", s_name)

        if changed:
            logger.info("[TaskPlanner] ✅ 再规划完成，计划已更新")
        return changed

    # ── 内部工具 ────────────────────────────────────────────────────────────

    @staticmethod
    def _skip_dependents(plan: Plan, failed_name: str):
        """递归将依赖 failed_name 的步骤设为 SKIPPED。"""
        changed = True
        while changed:
            changed = False
            skip_names = {
                s.name
                for s in plan.steps
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
            from app.core.tasks.progress_bus import ProgressEvent, get_progress_bus

            bus = get_progress_bus()
            bus.publish(
                ProgressEvent(
                    task_id=task_id,
                    event_type=event_type,
                    step_type=step.step_type.upper(),
                    message=step.description,
                    progress=step.retry_count,
                    detail={"step_name": step.name, "status": step.status.value},
                )
            )
        except Exception:
            pass

    @staticmethod
    def _publish_plan_event(plan: Plan, event_type: str, message: str):
        try:
            from app.core.tasks.progress_bus import ProgressEvent, get_progress_bus

            bus = get_progress_bus()
            bus.publish(
                ProgressEvent(
                    task_id=plan.task_id,
                    event_type=event_type,
                    status=plan.status,
                    message=message,
                    progress=plan.progress_percent(),
                )
            )
        except Exception:
            pass
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
