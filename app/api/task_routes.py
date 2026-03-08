# -*- coding: utf-8 -*-
"""
Koto Task Management API Routes
=================================
提供对 TaskLedger 和 ProgressBus 的 REST / SSE 接口。

挂载到父 Flask 应用后，所有路由前缀为 /api/tasks

端点总览:
  GET    /api/tasks                        — 查询任务列表（支持筛选）
  GET    /api/tasks/stats                  — 汇总统计
  GET    /api/tasks/<task_id>              — 查询单个任务（含步骤）
  POST   /api/tasks/<task_id>/cancel       — 请求取消任务
  POST   /api/tasks/<task_id>/interrupt    — 请求暂停（Human-in-loop）
  POST   /api/tasks/<task_id>/resume       — 恢复暂停的任务
  GET    /api/tasks/<task_id>/stream       — SSE 实时进度流
  DELETE /api/tasks/<task_id>              — 删除任务记录
  POST   /api/tasks/purge                  — 清理旧任务（管理接口）
"""

from __future__ import annotations

import logging
from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.core.tasks.task_ledger import TaskStatus, get_ledger
from app.core.tasks.progress_bus import get_progress_bus

logger = logging.getLogger(__name__)

task_bp = Blueprint("tasks", __name__)


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

@task_bp.get("")
def list_tasks():
    """
    查询任务列表。

    Query params:
      session_id  — 按会话过滤
      status      — 按状态过滤（pending/running/completed/failed/cancelled/waiting）
      source      — 按来源过滤（agent/scheduler/…）
      date_from   — 起始日期前缀，如 2026-03-04
      limit       — 每页数量（默认 50，最大 200）
      offset      — 分页偏移
    """
    ledger = get_ledger()
    session_id = request.args.get("session_id") or None
    status_raw = request.args.get("status") or None
    source = request.args.get("source") or None
    date_from = request.args.get("date_from") or None
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    status = None
    if status_raw:
        try:
            status = TaskStatus(status_raw)
        except ValueError:
            return _err(f"无效状态值: {status_raw}")

    tasks = ledger.list_tasks(
        session_id=session_id,
        status=status,
        source=source,
        date_from=date_from,
        limit=limit,
        offset=offset,
    )
    total = ledger.count(session_id=session_id, status=status, date_from=date_from)
    return _ok(
        data=[t.to_dict() for t in tasks],
        total=total,
        limit=limit,
        offset=offset,
    )


@task_bp.get("/stats")
def get_stats():
    """
    返回任务执行统计信息。

    Query params:
      date_from — 统计起始日期前缀
    """
    ledger = get_ledger()
    date_from = request.args.get("date_from") or None
    stats = ledger.get_stats(date_from=date_from)
    return _ok(data=stats)


@task_bp.get("/<task_id>")
def get_task(task_id: str):
    """查询单个任务详情，包含步骤明细。"""
    ledger = get_ledger()
    task = ledger.get(task_id, include_steps=True)
    if not task:
        return _err("任务不存在", 404)
    data = task.to_dict()
    data["steps"] = [s.to_dict() for s in task.steps]
    return _ok(data=data)


@task_bp.post("/<task_id>/cancel")
def cancel_task(task_id: str):
    """
    请求取消任务。

    Agent 执行线程会在下一次步骤开始前检查取消标志并中止。
    """
    ledger = get_ledger()
    task = ledger.get(task_id)
    if not task:
        return _err("任务不存在", 404)
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        return _err(f"任务已处于终态 ({task.status.value})，无法取消")
    ledger.cancel_task(task_id)
    return _ok(message="取消请求已发送，执行线程将在下一步时停止")


@task_bp.post("/<task_id>/interrupt")
def interrupt_task(task_id: str):
    """
    请求暂停任务，进入 Human-in-Loop 状态。

    Body JSON (可选):
      {"reason": "需要确认操作"}
    """
    ledger = get_ledger()
    task = ledger.get(task_id)
    if not task:
        return _err("任务不存在", 404)
    if task.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
        return _err(f"任务当前状态为 {task.status.value}，无法打断")

    body = request.get_json(silent=True) or {}
    reason = body.get("reason", "human_in_loop")
    ledger.request_interrupt(task_id)
    ledger.mark_waiting(task_id, reason=reason)

    # 发布暂停事件到进度总线
    try:
        bus = get_progress_bus()
        from app.core.tasks.progress_bus import ProgressEvent
        bus.publish(ProgressEvent(
            task_id=task_id,
            session_id=task.session_id,
            event_type="interrupt",
            status=TaskStatus.WAITING.value,
            message=f"任务已暂停等待确认：{reason}",
            progress=0,
        ))
    except Exception:
        pass

    return _ok(message="任务已进入等待确认状态")


@task_bp.post("/<task_id>/resume")
def resume_task(task_id: str):
    """
    恢复被暂停的任务。

    Body JSON (可选):
      {"approved": true, "comment": "确认操作"}
    """
    ledger = get_ledger()
    task = ledger.get(task_id)
    if not task:
        return _err("任务不存在", 404)
    if task.status != TaskStatus.WAITING:
        return _err(f"任务当前状态为 {task.status.value}，不处于等待状态")

    body = request.get_json(silent=True) or {}
    approved = body.get("approved", True)

    if approved:
        ledger.resume_task(task_id)
        try:
            bus = get_progress_bus()
            from app.core.tasks.progress_bus import ProgressEvent
            bus.publish(ProgressEvent(
                task_id=task_id,
                session_id=task.session_id,
                event_type="resume",
                status=TaskStatus.RUNNING.value,
                message="用户已确认，任务恢复执行",
                progress=0,
            ))
        except Exception:
            pass
        return _ok(message="任务已恢复执行")
    else:
        ledger.cancel_task(task_id)
        ledger.mark_cancelled(task_id)
        return _ok(message="用户拒绝确认，任务已取消")


@task_bp.get("/<task_id>/stream")
def stream_progress(task_id: str):
    """
    SSE 实时进度流。

    客户端示例（JavaScript）::

        const es = new EventSource(`/api/tasks/${taskId}/stream`);
        es.addEventListener('progress', e => console.log(JSON.parse(e.data)));
        es.addEventListener('timeout',  () => es.close());

    Query params:
      timeout  — 最长监听秒数（默认 300，0=永不超时）
      replay   — 是否回放历史事件（默认 true）
    """
    ledger = get_ledger()
    task = ledger.get(task_id)
    if not task:
        return _err("任务不存在", 404)

    bus = get_progress_bus()
    timeout = float(request.args.get("timeout", 300))
    replay = request.args.get("replay", "true").lower() != "false"

    def generate():
        yield from bus.stream_events(task_id, timeout=timeout, replay=replay)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@task_bp.delete("/<task_id>")
def delete_task(task_id: str):
    """
    软删除任务（标记为 cancelled）并清理进度总线历史。
    实际数据库行保留，可通过 purge 接口清理。
    """
    ledger = get_ledger()
    task = ledger.get(task_id)
    if not task:
        return _err("任务不存在", 404)
    if task.status == TaskStatus.RUNNING:
        return _err("任务正在执行中，请先取消再删除")
    ledger.cancel_task(task_id)
    ledger.mark_cancelled(task_id)
    get_progress_bus().cleanup(task_id, delay=0)
    return _ok(message="任务已删除")


@task_bp.post("/purge")
def purge_tasks():
    """
    清理旧任务（管理接口）。

    Body JSON (可选):
      {"keep_days": 30}   — 保留最近 N 天的任务（默认 30）
    """
    body = request.get_json(silent=True) or {}
    keep_days = int(body.get("keep_days", 30))
    ledger = get_ledger()
    deleted = ledger.purge_old(keep_days=keep_days)
    return _ok(data={"deleted": deleted}, message=f"已清理 {deleted} 条历史任务")
