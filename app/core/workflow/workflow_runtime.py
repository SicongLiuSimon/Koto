# -*- coding: utf-8 -*-
"""
Koto WorkflowRuntime — 统一工作流执行入口
==========================================
将两层工作流基础设施打通：

  层 1: web/workflow_manager.py  — JSON 步骤式工作流定义 (WorkflowManager)
  层 2: app/core/workflow/langgraph_workflow.py — LangGraph DAG 执行引擎

执行策略（按优先级）：
  1. 尝试将 workflow_id / user_input 映射到 LangGraph 命名工作流
  2. 若无匹配，降级为 WorkflowManager 步骤式执行
  3. 若步骤式也找不到 workflow，返回 error

进度上报：
  若从 JobRunner 调用（task_ctx 非 None），通过 JobContext.step() 实时上报

使用示例：
    # 直接调用（同步）
    from app.core.workflow.workflow_runtime import WorkflowRuntime
    rt = WorkflowRuntime()
    result = rt.execute(
        workflow_id="daily_report",
        user_input="生成 Q1 销售汇报",
        variables={"period": "Q1 2026"},
    )
    print(result["output"])

    # 从 JobRunner 异步调用（推荐）
    from app.core.jobs.job_runner import JobSpec, get_job_runner
    task_id = get_job_runner().submit(JobSpec(
        job_type="workflow",
        payload={"workflow_id": "daily_report", "user_input": "Q1 汇报"},
    ))
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class WorkflowRuntime:
    """
    统一工作流执行入口。

    优先使用 LangGraph 引擎（DAG + 检查点续跑），
    无匹配时降级为 WorkflowManager 步骤式执行。
    """

    # ── 执行入口 ──────────────────────────────────────────────────────────────

    def execute(
        self,
        workflow_id: str,
        user_input: str = "",
        variables: Optional[Dict[str, Any]] = None,
        task_ctx=None,          # Optional[JobContext]
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        同步执行工作流，返回标准结果字典：
          { output, file_path, steps, error, workflow_type }

        Args:
            workflow_id:  工作流 ID（或自然语言描述，供 LangGraph 推断）
            user_input:   用户输入文本
            variables:    步骤式工作流的变量绑定 { varname: value }
            task_ctx:     JobContext（来自 JobRunner 时注入，用于进度上报）
            session_id:   会话 ID（LangGraph checkpoint 使用）
        """
        variables = variables or {}
        _session = session_id or (task_ctx.session_id if task_ctx else None)

        # ── 优先尝试 LangGraph ────────────────────────────────────────────────
        lg_result = self._try_langgraph(workflow_id, user_input, task_ctx, _session)
        if lg_result is not None:
            lg_result["workflow_type"] = "langgraph"
            return lg_result

        # ── 降级到步骤式执行 ───────────────────────────────────────────────────
        step_result = self._execute_steps(
            workflow_id, user_input, variables, task_ctx
        )
        step_result["workflow_type"] = "step_based"
        return step_result

    # ── LangGraph 执行 ────────────────────────────────────────────────────────

    def _try_langgraph(
        self,
        workflow_id: str,
        user_input: str,
        task_ctx,
        session_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """尝试 LangGraph 执行，返回 None 表示不适用或执行失败需降级。"""
        try:
            from app.core.workflow.langgraph_workflow import WorkflowEngine, _LG_AVAILABLE

            if not _LG_AVAILABLE:
                return None

            # 推断最佳 LangGraph 工作流
            detected = WorkflowEngine.detect_workflow(workflow_id, user_input)
            if detected == "sequential_chat":
                # 标准对话无需 LangGraph，直接降级
                return None

            if task_ctx:
                task_ctx.step(
                    "THOUGHT",
                    f"LangGraph 工作流: {detected}",
                    progress=8,
                )

            engine = WorkflowEngine()
            result = engine.run(
                workflow=detected,
                user_input=user_input,
                session_id=session_id,
            )

            if task_ctx and result.get("steps"):
                for i, step_name in enumerate(result["steps"]):
                    progress = int(10 + 80 * (i + 1) / len(result["steps"]))
                    task_ctx.step("ACTION", f"节点完成: {step_name}", progress=progress)

            return result

        except ImportError:
            return None
        except Exception as exc:
            logger.warning("[WorkflowRuntime] LangGraph 执行失败，降级: %s", exc)
            return None

    # ── 步骤式执行 ────────────────────────────────────────────────────────────

    def _execute_steps(
        self,
        workflow_id: str,
        user_input: str,
        variables: Dict[str, Any],
        task_ctx,
    ) -> Dict[str, Any]:
        """使用 WorkflowManager 定义的步骤列表顺序执行。"""
        wf = self._load_workflow(workflow_id)
        if wf is None:
            return {
                "output": "",
                "file_path": None,
                "steps": [],
                "error": f"工作流 '{workflow_id}' 不存在（LangGraph 也无法处理）",
            }

        if task_ctx:
            task_ctx.step(
                "THOUGHT",
                f"运行工作流 '{wf.name}' ({len(wf.steps)} 步)",
                progress=5,
            )

        results = []
        total = max(len(wf.steps), 1)

        for i, step in enumerate(wf.steps):
            if task_ctx and task_ctx.is_cancelled():
                break

            step_name = step.get("name") or f"step_{i + 1}"
            step_type = step.get("type", "agent")
            config = step.get("config") or {}
            progress = int(5 + 90 * i / total)

            if task_ctx:
                task_ctx.step(
                    "ACTION",
                    f"步骤 {i + 1}/{total}: {step_name}",
                    progress=progress,
                )

            step_result = self._run_single_step(
                step_type, step_name, config, user_input, variables
            )
            results.append({"step": step_name, "result": step_result})

        # 更新执行计数
        try:
            wf.execution_count = getattr(wf, "execution_count", 0) + 1
            mgr = self._get_manager()
            if mgr:
                mgr.save_workflow(wf)
        except Exception:
            pass

        output = "\n\n".join(
            f"[{r['step']}]\n{r['result']}" for r in results
        )
        return {
            "output": output,
            "file_path": None,
            "steps": [r["step"] for r in results],
            "error": None,
        }

    def _run_single_step(
        self,
        step_type: str,
        step_name: str,
        config: Dict[str, Any],
        user_input: str,
        variables: Dict[str, Any],
    ) -> str:
        """执行单个步骤，返回文本结果。"""
        # 变量替换：{{varname}} → value
        prompt = config.get("prompt") or config.get("query") or user_input
        for k, v in variables.items():
            prompt = prompt.replace(f"{{{{{k}}}}}", str(v))

        user_input_replaced = user_input
        for k, v in variables.items():
            user_input_replaced = user_input_replaced.replace(f"{{{{{k}}}}}", str(v))

        if step_type in ("agent", "tool", "ai"):
            try:
                from app.api.agent_routes import get_agent, AgentStepType

                agent = get_agent()
                final_answer = ""
                for step in agent.run(input_text=prompt, history=[]):
                    if step.step_type == AgentStepType.ANSWER:
                        final_answer = step.content or ""
                return final_answer or "(无输出)"
            except Exception as exc:
                return f"[步骤错误] {exc}"

        if step_type == "skill":
            skill_id = config.get("skill_id", "")
            try:
                from app.core.skills.skill_manager import SkillManager
                sk = SkillManager.get_definition(skill_id)
                augmented = (
                    f"{sk.system_prompt_template}\n\n{prompt}" if sk else prompt
                )
                from app.api.agent_routes import get_agent, AgentStepType

                agent = get_agent()
                for step in agent.run(input_text=augmented, history=[]):
                    if step.step_type == AgentStepType.ANSWER:
                        return step.content or "(无输出)"
            except Exception as exc:
                return f"[skill 步骤错误] {exc}"

        return f"[跳过] 不支持的步骤类型: {step_type!r}"

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    def _load_workflow(self, workflow_id: str):
        mgr = self._get_manager()
        if mgr and workflow_id:
            return mgr.load_workflow(workflow_id)
        return None

    @staticmethod
    def _get_manager():
        """懒加载 WorkflowManager（在 web/ 目录下）。"""
        try:
            web_dir = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..", "..", "web")
            )
            if web_dir not in sys.path:
                sys.path.insert(0, web_dir)
            from workflow_manager import WorkflowManager  # type: ignore

            return WorkflowManager()
        except Exception as exc:
            logger.debug("[WorkflowRuntime] WorkflowManager 不可用: %s", exc)
            return None

    # ── 流式执行（供 SSE 端点调用）────────────────────────────────────────────

    def stream(
        self,
        workflow_id: str,
        user_input: str = "",
        session_id: Optional[str] = None,
    ):
        """
        流式执行 LangGraph 工作流，yield 各节点的增量内容。
        每个 event: {"node": str, "content": str, "done": bool}

        注：仅 LangGraph 工作流支持流式输出；步骤式工作流不支持。
        """
        try:
            from app.core.workflow.langgraph_workflow import WorkflowEngine, _LG_AVAILABLE

            if not _LG_AVAILABLE:
                yield {"node": "error", "content": "LangGraph 不可用", "done": True}
                return

            detected = WorkflowEngine.detect_workflow(workflow_id, user_input)
            if detected == "sequential_chat":
                yield {
                    "node": "error",
                    "content": "sequential_chat 不支持工作流流式执行",
                    "done": True,
                }
                return

            engine = WorkflowEngine()
            yield from engine.stream(
                workflow=detected,
                user_input=user_input,
                session_id=session_id,
            )
        except Exception as exc:
            yield {"node": "error", "content": str(exc), "done": True}
