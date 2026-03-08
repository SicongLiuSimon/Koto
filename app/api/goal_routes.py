# -*- coding: utf-8 -*-
"""
Koto Goal Management API
=========================
挂载前缀: /api/goals

端点总览:
  POST   /api/goals                        — 创建长期目标
  GET    /api/goals                        — 查询目标列表
  GET    /api/goals/stats                  — 统计摘要
  GET    /api/goals/<goal_id>              — 查询单个目标（含最近执行记录）
  PATCH  /api/goals/<goal_id>              — 更新目标（标题 / 描述 / 优先级等）
  POST   /api/goals/<goal_id>/activate     — 激活目标（draft/paused → active）
  POST   /api/goals/<goal_id>/pause        — 暂停目标
  POST   /api/goals/<goal_id>/resume       — 恢复目标（paused → active）
  POST   /api/goals/<goal_id>/complete     — 手动标记完成
  POST   /api/goals/<goal_id>/confirm      — 用户确认后恢复 waiting_user 目标
  DELETE /api/goals/<goal_id>              — 删除目标
  GET    /api/goals/<goal_id>/runs         — 查询目标所有执行记录
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

from app.core.goal.goal_manager import GoalStatus, get_goal_manager

logger = logging.getLogger(__name__)

goal_bp = Blueprint("goals", __name__)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _ok(data=None, **kwargs):
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(kwargs)
    return jsonify(body)


def _err(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


# ============================================================================
# 路由
# ============================================================================

@goal_bp.post("")
def create_goal():
    """
    创建长期目标。

    请求体 (JSON):
      title*              必填，目标简短标题
      user_goal*          必填，用户完整描述
      category            可选，默认 custom
      priority            可选，默认 normal
      due_at              可选，ISO 时间字符串
      check_interval_minutes  可选，默认按 category 决定
      requires_confirmation   可选，bool，默认 false
      session_id          可选，关联当前会话
      auto_activate       可选，bool，true 则立即激活，默认 false
    """
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    user_goal = (data.get("user_goal") or "").strip()

    if not title:
        return _err("title 不能为空")
    if not user_goal:
        return _err("user_goal 不能为空")

    gm = get_goal_manager()
    goal = gm.create(
        title=title,
        user_goal=user_goal,
        category=data.get("category", "custom"),
        priority=data.get("priority", "normal"),
        due_at=data.get("due_at"),
        check_interval_minutes=data.get("check_interval_minutes"),
        requires_confirmation=bool(data.get("requires_confirmation", False)),
        session_id=data.get("session_id"),
        run_on_activate=bool(data.get("run_on_activate", True)),
    )

    if data.get("auto_activate"):
        gm.activate(goal.goal_id)
        goal = gm.get(goal.goal_id)

    return _ok(goal.to_dict()), 201


@goal_bp.get("")
def list_goals():
    """
    查询目标列表。

    Query params:
      status    过滤状态（draft/active/waiting_user/paused/completed/failed）
      category  过滤类别
      session_id 过滤会话
      limit     默认 50
      offset    默认 0
    """
    gm = get_goal_manager()
    status_raw = request.args.get("status")
    category = request.args.get("category")
    session_id = request.args.get("session_id")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    status = None
    if status_raw:
        try:
            status = GoalStatus(status_raw)
        except ValueError:
            return _err(f"无效状态: {status_raw}")

    goals = gm.list_goals(
        status=status, category=category, session_id=session_id,
        limit=limit, offset=offset,
    )
    return _ok([g.to_dict() for g in goals], total=len(goals))


@goal_bp.get("/stats")
def goal_stats():
    """返回各状态目标的数量统计。"""
    gm = get_goal_manager()
    stats = {s.value: gm.count(status=s) for s in GoalStatus}
    stats["total"] = sum(stats.values())
    return _ok(stats)


@goal_bp.get("/<goal_id>")
def get_goal(goal_id: str):
    """查询单个目标，同时返回最近 10 次执行记录。"""
    gm = get_goal_manager()
    goal = gm.get(goal_id)
    if not goal:
        return _err("目标不存在", 404)
    runs = gm.runs_for_goal(goal_id, limit=10)
    data = goal.to_dict()
    data["recent_runs"] = [r.to_dict() for r in runs]
    return _ok(data)


@goal_bp.patch("/<goal_id>")
def update_goal(goal_id: str):
    """
    更新目标字段（仅允许在非终态时修改）。

    可更新字段: title, user_goal, category, priority,
                due_at, check_interval_minutes, requires_confirmation
    """
    gm = get_goal_manager()
    goal = gm.get(goal_id)
    if not goal:
        return _err("目标不存在", 404)

    data = request.get_json(silent=True) or {}
    allowed = {
        "title", "user_goal", "category", "priority",
        "due_at", "check_interval_minutes", "requires_confirmation",
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return _err("没有可更新的字段")

    if "requires_confirmation" in updates:
        updates["requires_confirmation"] = int(bool(updates["requires_confirmation"]))

    gm._update(goal_id, **updates)
    return _ok(gm.get(goal_id).to_dict())


@goal_bp.post("/<goal_id>/activate")
def activate_goal(goal_id: str):
    """激活目标（draft 或 paused → active）。"""
    gm = get_goal_manager()
    if not gm.get(goal_id):
        return _err("目标不存在", 404)
    success = gm.activate(goal_id)
    if not success:
        return _err("无法激活（目标可能已处于终态）")
    return _ok(gm.get(goal_id).to_dict())


@goal_bp.post("/<goal_id>/pause")
def pause_goal(goal_id: str):
    """暂停目标。"""
    gm = get_goal_manager()
    if not gm.get(goal_id):
        return _err("目标不存在", 404)
    gm.pause(goal_id)
    return _ok(gm.get(goal_id).to_dict())


@goal_bp.post("/<goal_id>/resume")
def resume_goal(goal_id: str):
    """恢复已暂停的目标。"""
    gm = get_goal_manager()
    if not gm.get(goal_id):
        return _err("目标不存在", 404)
    gm.resume(goal_id)
    return _ok(gm.get(goal_id).to_dict())


@goal_bp.post("/<goal_id>/complete")
def complete_goal(goal_id: str):
    """手动标记目标为完成。"""
    gm = get_goal_manager()
    if not gm.get(goal_id):
        return _err("目标不存在", 404)
    data = request.get_json(silent=True) or {}
    gm.complete(goal_id, summary=data.get("summary", "用户手动标记完成"))
    return _ok(gm.get(goal_id).to_dict())


@goal_bp.post("/<goal_id>/confirm")
def confirm_goal(goal_id: str):
    """
    用户回复了 waiting_user 状态的目标，补充信息后恢复追踪。

    请求体:
      user_reply   用户补充的信息
    """
    gm = get_goal_manager()
    goal = gm.get(goal_id)
    if not goal:
        return _err("目标不存在", 404)
    if goal.status != GoalStatus.WAITING_USER:
        return _err(f"目标当前状态为 {goal.status.value}，无需确认")

    data = request.get_json(silent=True) or {}
    user_reply = (data.get("user_reply") or "").strip()
    if not user_reply:
        return _err("user_reply 不能为空")

    # 把用户回复存入 context，然后重新激活
    ctx = goal.get_context()
    ctx["user_confirmation"] = user_reply
    ctx.pop("waiting_reason", None)
    gm._update(goal_id, context_snapshot=__import__("json").dumps(ctx, ensure_ascii=False))
    gm.activate(goal_id)
    return _ok(gm.get(goal_id).to_dict())


@goal_bp.delete("/<goal_id>")
def delete_goal(goal_id: str):
    """删除目标及其所有执行记录。"""
    gm = get_goal_manager()
    deleted = gm.delete(goal_id)
    if not deleted:
        return _err("目标不存在", 404)
    return _ok({"deleted": goal_id})


@goal_bp.get("/<goal_id>/runs")
def list_goal_runs(goal_id: str):
    """查询目标的所有执行记录，按时间倒序，最多返回 50 条。"""
    gm = get_goal_manager()
    if not gm.get(goal_id):
        return _err("目标不存在", 404)
    limit = min(int(request.args.get("limit", 20)), 50)
    runs = gm.runs_for_goal(goal_id, limit=limit)
    return _ok([r.to_dict() for r in runs])
