# -*- coding: utf-8 -*-
"""
goal_job_handler — 向 JobRunner 注册 goal_check 处理器
=======================================================
当 GoalManager 后台循环发现待检查目标时，会向 JobRunner 提交
job_type="goal_check" 的作业，由本模块中的处理器负责执行。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def register_goal_handler(runner) -> None:
    """将 goal_check 处理器注册到 JobRunner 实例上。"""
    runner.register_handler("goal_check", _handle_goal_check)
    logger.info("[GoalJobHandler] ✅ goal_check 处理器已注册")


def _handle_goal_check(ctx) -> Optional[str]:
    """
    goal_check 处理器。

    ctx.payload 预期格式::
        {"goal_id": "<uuid>"}

    执行流程：
    1. 从 GoalManager 加载目标
    2. 启动一条 GoalRun 记录
    3. 构建 Agent 提示词并调用 UnifiedAgent
    4. 解析执行结果，判断目标是否已完成
    5. 更新 GoalTask 状态与下次检查时间
    """
    goal_id: str = ctx.payload.get("goal_id", "")
    if not goal_id:
        return "❌ goal_check: 缺少 goal_id"

    from app.core.goal.goal_manager import get_goal_manager, GoalStatus

    gm = get_goal_manager()
    goal = gm.get(goal_id)
    if not goal:
        return f"❌ goal_check: 未找到目标 {goal_id[:8]}"
    if goal.status not in (GoalStatus.ACTIVE,):
        return f"⚠️ 目标 {goal.title[:40]} 状态为 {goal.status.value}，跳过执行"

    # ── 启动 GoalRun ──────────────────────────────────────────────────────────
    run = gm.start_run(goal_id, linked_task_id=ctx.task_id)

    ctx.step("THOUGHT", f"[GoalCheck] 开始追踪目标: «{goal.title}»", progress=5)

    # ── 构建提示词 ────────────────────────────────────────────────────────────
    g_ctx = goal.get_context()
    progress_note = g_ctx.get("progress_summary", "（首次执行）")

    prompt = (
        f"[长期目标追踪]\n"
        f"目标标题：{goal.title}\n"
        f"目标描述：{goal.user_goal}\n"
        f"当前进展：{progress_note}\n"
        f"上次结果：{goal.last_result or '暂无'}\n\n"
        f"请执行下一步追踪，并以中文简洁汇报：\n"
        f"1. 当前状态\n"
        f"2. 本次执行内容\n"
        f"3. 下一步建议\n"
        f"如目标已完成，在回复最后一行单独写：STATUS:COMPLETED\n"
        f"如需用户提供信息，最后一行单独写：STATUS:WAITING_USER:<原因>"
    )

    # ── 调用 UnifiedAgent ─────────────────────────────────────────────────────
    result_text = ""
    tool_call_names = []
    try:
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

        answer_parts = []
        for step in agent.run(prompt, session_id=goal.session_id or goal_id):
            step_type = step.step_type.value if hasattr(step.step_type, "value") else str(step.step_type)
            if step_type == "ACTION" and step.content:
                tool_call_names.append(step.content[:80])
                ctx.step("ACTION", step.content[:200], tool_name=getattr(step, "tool_name", None))
            elif step_type == "ANSWER":
                answer_parts.append(step.content)
                ctx.step("ANSWER", step.content[:200], progress=90)
            elif ctx.is_cancelled():
                break

        result_text = "\n".join(answer_parts) or "（未获得明确答案）"

    except Exception as e:
        result_text = f"执行异常: {e}"
        logger.error(f"[GoalJobHandler] 目标 {goal_id[:8]} Agent 执行失败: {e}")

    # ── 解析特殊状态标记 ──────────────────────────────────────────────────────
    outcome = "success"
    waiting_reason = ""
    summary = result_text

    lines = result_text.strip().splitlines()
    last_line = lines[-1].strip() if lines else ""

    if last_line.startswith("STATUS:COMPLETED"):
        outcome = "completed"
        summary = "\n".join(lines[:-1]).strip() or result_text
        ctx.step("ANSWER", "🎉 目标已完成", progress=100)
    elif last_line.startswith("STATUS:WAITING_USER"):
        parts = last_line.split(":", 2)
        waiting_reason = parts[2] if len(parts) > 2 else "需要用户补充信息"
        outcome = "waiting_user"
        summary = "\n".join(lines[:-1]).strip() or result_text
        ctx.step("THOUGHT", f"⏸️ 等待用户: {waiting_reason}", progress=80)

    # ── 完成 GoalRun ──────────────────────────────────────────────────────────
    gm.finish_run(run.run_id, outcome=outcome, summary=summary, tool_calls=tool_call_names)

    # ── 更新 GoalTask ─────────────────────────────────────────────────────────
    if outcome == "completed":
        gm.complete(goal_id, summary=summary)
    elif outcome == "waiting_user":
        gm.set_waiting_user(goal_id, reason=waiting_reason)
    else:
        new_ctx = dict(g_ctx)
        new_ctx["progress_summary"] = summary[:500]
        gm.update_from_run(goal_id, run_outcome=outcome, summary=summary, new_context=new_ctx)

    ctx.step("ANSWER", f"✅ 目标追踪完成: {summary[:150]}", progress=100)
    return summary[:500]
