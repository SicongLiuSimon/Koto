# -*- coding: utf-8 -*-
"""
Koto Jobs API Blueprint
========================
挂载前缀: /api/jobs

作业管理端点：
  POST   /api/jobs                          — 提交后台作业
  GET    /api/jobs                          — 列举作业（支持过滤）
  GET    /api/jobs/<task_id>                — 作业详情（含步骤）
  GET    /api/jobs/<task_id>/stream         — SSE 实时进度流
  POST   /api/jobs/<task_id>/cancel         — 取消作业
  POST   /api/jobs/<task_id>/resume         — 恢复暂停的作业
  POST   /api/jobs/<task_id>/retry          — 重排 failed/cancelled 作业

触发器管理端点：
  GET    /api/jobs/triggers                 — 列出所有触发器
    GET    /api/jobs/triggers/templates       — 推荐触发器模板
    POST   /api/jobs/triggers/bootstrap       — 一键播种推荐触发器
  POST   /api/jobs/triggers                 — 注册触发器
  PATCH  /api/jobs/triggers/<trigger_id>    — 更新触发器（enabled / config 等）
  DELETE /api/jobs/triggers/<trigger_id>    — 删除触发器
  POST   /api/jobs/triggers/<trigger_id>/fire — 手动触发
"""
from __future__ import annotations

import json
import logging
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context

logger = logging.getLogger(__name__)

job_bp = Blueprint("jobs", __name__, url_prefix="/api/jobs")


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _ok(data=None, **kw):
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(kw)
    return jsonify(body)


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


# ============================================================================
# 作业 CRUD
# ============================================================================

@job_bp.post("")
def create_job():
    """
    提交后台作业。

    Body (JSON):
      job_type         string  必填 — 作业类型（agent_query / workflow / auto_catalog / skill_exec）
      payload          object  可选 — 传给处理器的参数（依 job_type 而定）
      session_id       string  可选 — 关联会话 ID
      max_retries      int     可选 — 失败后最多重试次数（默认 0）
      timeout_seconds  float   可选 — 超时秒数（默认 300）
      metadata         object  可选 — 附加元数据

    常见 payload 示例：
      agent_query:  { "query": "帮我写一份周报", "history": [] }
      workflow:     { "workflow_id": "daily_report", "user_input": "Q1 销售汇报", "variables": {} }
      auto_catalog: { "source_dir": "C:/Downloads" }
      skill_exec:   { "skill_id": "step_by_step", "query": "解释量子纠缠" }

    Returns:
      { ok: true, data: { task_id, job_type } }  HTTP 202
    """
    body = request.get_json(force=True, silent=True) or {}
    job_type = (body.get("job_type") or "").strip()
    if not job_type:
        return _err("job_type 不能为空")

    from app.core.jobs.job_runner import JobSpec, get_job_runner

    spec = JobSpec(
        job_type=job_type,
        payload=body.get("payload") or {},
        session_id=(body.get("session_id") or "api"),
        max_retries=int(body.get("max_retries", 0)),
        timeout_seconds=float(body.get("timeout_seconds", 300)),
        metadata=body.get("metadata"),
    )

    try:
        task_id = get_job_runner().submit(spec)
    except Exception as exc:
        logger.exception("[job_routes] submit 失败")
        return _err(str(exc), 500)

    return _ok({"task_id": task_id, "job_type": job_type}), 202


@job_bp.get("")
def list_jobs():
    """
    列举作业列表（仅 source=job_runner 的任务）。

    Query params:
      status    — pending / running / completed / failed / cancelled / waiting
      job_type  — 按 job_type 过滤
      limit     — 每页数量（默认 50，最大 200）
      offset    — 分页偏移
    """
    from app.core.tasks.task_ledger import get_ledger, TaskStatus

    ledger = get_ledger()
    status_raw = request.args.get("status")
    job_type_filter = request.args.get("job_type")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    status = None
    if status_raw:
        try:
            status = TaskStatus(status_raw)
        except ValueError:
            return _err(f"无效状态值: {status_raw!r}")

    tasks = ledger.list_tasks(
        source="job_runner", status=status, limit=limit + 50, offset=offset
    )
    if job_type_filter:
        tasks = [t for t in tasks if t.task_type == job_type_filter]
    tasks = tasks[:limit]

    total = ledger.count(source="job_runner", status=status)
    return _ok(data=[t.to_dict() for t in tasks], total=total, limit=limit, offset=offset)


@job_bp.get("/<task_id>")
def get_job(task_id: str):
    """获取作业详情（含执行步骤列表）。"""
    from app.core.tasks.task_ledger import get_ledger

    task = get_ledger().get(task_id, include_steps=True)
    if not task:
        return _err("作业不存在", 404)

    d = task.to_dict()
    d["steps"] = [s.to_dict() for s in task.steps]
    return _ok(d)


@job_bp.get("/<task_id>/stream")
def stream_job(task_id: str):
    """SSE 实时进度流（复用 ProgressBus）。"""
    from app.core.tasks.task_ledger import get_ledger
    from app.core.tasks.progress_bus import get_progress_bus

    if not get_ledger().get(task_id):
        return _err("作业不存在", 404)

    bus = get_progress_bus()

    def gen():
        yield from bus.stream_events(task_id, timeout=300, replay=True)

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@job_bp.post("/<task_id>/cancel")
def cancel_job(task_id: str):
    """请求取消作业（非即时，执行线程在下次检查时停止）。"""
    from app.core.tasks.task_ledger import get_ledger

    ledger = get_ledger()
    if not ledger.get(task_id):
        return _err("作业不存在", 404)
    ledger.cancel_task(task_id)
    return _ok({"task_id": task_id, "action": "cancel_requested"})


@job_bp.post("/<task_id>/resume")
def resume_job(task_id: str):
    """恢复被暂停（interrupt）的作业。"""
    from app.core.tasks.task_ledger import get_ledger

    ledger = get_ledger()
    task = ledger.get(task_id)
    if not task:
        return _err("作业不存在", 404)
    ledger.resume_task(task_id)
    return _ok({"task_id": task_id, "action": "resumed"})


@job_bp.post("/<task_id>/retry")
def retry_job(task_id: str):
    """将 failed / cancelled 作业重新入队。"""
    from app.core.tasks.task_ledger import get_ledger, TaskStatus
    from app.core.jobs.job_runner import JobSpec, get_job_runner

    ledger = get_ledger()
    task = ledger.get(task_id)
    if not task:
        return _err("作业不存在", 404)
    if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
        return _err(
            f"只能重试 failed/cancelled 作业，当前状态: {task.status.value}"
        )

    meta = json.loads(task.metadata or "{}")
    job_type = meta.get("job_type") or task.task_type
    if not job_type:
        return _err("无法确定 job_type，无法重试")

    try:
        payload = json.loads(task.user_input)
    except Exception:
        payload = {"raw": task.user_input}

    # 重置为 PENDING
    ledger._update_fields(
        task_id,
        status=TaskStatus.PENDING.value,
        error=None,
        cancel_requested=0,
        completed_at=None,
    )

    spec = JobSpec(
        job_type=job_type,
        payload=payload,
        session_id=task.session_id,
        skill_id=task.skill_id,
        metadata=meta,
    )
    runner = get_job_runner()
    runner._queue.put((5, time.time(), task_id, spec))
    return _ok({"task_id": task_id, "action": "requeued"})


# ============================================================================
# 触发器 CRUD
# ============================================================================

@job_bp.get("/triggers")
def list_triggers():
    """列出所有已注册的触发器。"""
    from app.core.jobs.trigger_registry import get_trigger_registry

    triggers = get_trigger_registry().list_all()
    return _ok(data=[t.to_dict() for t in triggers], total=len(triggers))


@job_bp.get("/triggers/templates")
def list_trigger_templates():
    """列出系统内置的推荐触发器模板。"""
    from app.core.jobs.trigger_registry import get_trigger_registry

    templates = get_trigger_registry().list_templates()
    return _ok(data=templates, total=len(templates))


@job_bp.post("/triggers/bootstrap")
def bootstrap_triggers():
    """播种推荐触发器模板；默认保留已存在配置。"""
    body = request.get_json(force=True, silent=True) or {}
    force = bool(body.get("force", False))

    from app.core.jobs.trigger_registry import get_trigger_registry

    try:
        result = get_trigger_registry().ensure_recommended_triggers(force=force)
    except Exception as exc:
        logger.exception("[job_routes] bootstrap_triggers 失败")
        return _err(str(exc), 500)

    return _ok(result)


@job_bp.post("/triggers")
def create_trigger():
    """
    注册触发器。

    Body (JSON):
      name          string  必填
      trigger_type  string  必填 — interval / cron / webhook / startup
      job_type      string  必填 — 提交的作业类型
      job_payload   object  可选 — 传给作业的参数
      session_id    string  可选
      enabled       bool    可选（默认 true）
      config        object  可选 — 类型相关配置
                    interval: { interval_seconds: N }
                    cron:     { time: "HH:MM" }

    Returns:
      { ok: true, data: TriggerSpec }  HTTP 201
    """
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    trigger_type = (body.get("trigger_type") or "").strip()
    job_type = (body.get("job_type") or "").strip()

    if not all([name, trigger_type, job_type]):
        return _err("name / trigger_type / job_type 均不能为空")

    from app.core.jobs.trigger_registry import TriggerSpec, get_trigger_registry

    try:
        spec = TriggerSpec(
            name=name,
            trigger_type=trigger_type,
            job_type=job_type,
            job_payload=body.get("job_payload") or {},
            session_id=body.get("session_id") or "system",
            enabled=bool(body.get("enabled", True)),
            config=body.get("config") or {},
        )
        get_trigger_registry().register(spec)
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        logger.exception("[job_routes] create_trigger 失败")
        return _err(str(exc), 500)

    return _ok(spec.to_dict()), 201


@job_bp.patch("/triggers/<trigger_id>")
def update_trigger(trigger_id: str):
    """
    更新触发器属性（支持字段：name / job_type / job_payload / config / enabled）。
    """
    body = request.get_json(force=True, silent=True) or {}
    from app.core.jobs.trigger_registry import get_trigger_registry

    reg = get_trigger_registry()
    if not reg.get(trigger_id):
        return _err("触发器不存在", 404)

    allowed = {"name", "job_type", "job_payload", "session_id", "enabled", "config"}
    updates = {k: v for k, v in body.items() if k in allowed}
    spec = reg.update(trigger_id, **updates)
    return _ok(spec.to_dict() if spec else None)


@job_bp.delete("/triggers/<trigger_id>")
def delete_trigger(trigger_id: str):
    """删除触发器。"""
    from app.core.jobs.trigger_registry import get_trigger_registry

    removed = get_trigger_registry().remove(trigger_id)
    if not removed:
        return _err("触发器不存在", 404)
    return _ok({"trigger_id": trigger_id, "removed": True})


@job_bp.post("/triggers/<trigger_id>/fire")
def fire_trigger(trigger_id: str):
    """
    手动触发一个触发器（适用于 webhook / manual 类型，其他类型也可手动触发）。

    Returns:
      { ok: true, data: { task_id, trigger_id } }  HTTP 202
    """
    from app.core.jobs.trigger_registry import get_trigger_registry

    reg = get_trigger_registry()
    spec = reg.get(trigger_id)
    if not spec:
        return _err("触发器不存在", 404)
    if not spec.enabled:
        return _err("触发器已禁用", 400)

    task_id = reg.fire(trigger_id)
    if task_id is None:
        return _err("触发失败，请检查 job_type 是否已注册", 500)

    return _ok({"task_id": task_id, "trigger_id": trigger_id}), 202
