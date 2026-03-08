"""
distill_routes.py — LoRA 蒸馏训练 API Blueprint
=================================================
将 ``DistillManager`` 的功能暴露为 REST + SSE 接口，
供前端界面触发、监控、取消 LoRA 微调任务。

注册方式（在 web/app.py 的 _register_blueprints_deferred 中）：
    from app.api.distill_routes import distill_bp
    app.register_blueprint(distill_bp, url_prefix='/api/distill')

端点列表
--------
POST   /api/distill/submit            提交训练任务
GET    /api/distill/jobs              列出所有任务
GET    /api/distill/jobs/<job_id>     查询单个任务状态
POST   /api/distill/jobs/<job_id>/cancel  取消排队中的任务
GET    /api/distill/stream/<job_id>   SSE 实时进度流
GET    /api/distill/adapters          列出已注册的 LoRA 适配器
"""

from __future__ import annotations

import json
import os
import logging

from flask import Blueprint, Response, jsonify, request, stream_with_context

logger = logging.getLogger(__name__)

distill_bp = Blueprint("distill", __name__)


# ── 懒加载 DistillManager（避免启动时立即 import 重型依赖）─────────────────────

_manager = None


def _get_manager():
    global _manager
    if _manager is None:
        from app.core.learning.distill_manager import DistillManager
        _manager = DistillManager.instance()
    return _manager


# ═══════════════════════════════════════════════════════════════════
#  POST /api/distill/submit
# ═══════════════════════════════════════════════════════════════════

@distill_bp.route("/submit", methods=["POST"])
def submit_training():
    """
    提交 LoRA 训练任务。

    Body (JSON)::

        {
          "skill_id": "email_writer",       // 必填
          "config_override": {              // 可选：覆盖默认超参
            "num_epochs": 5,
            "lora_r": 32
          },
          "dataset_path": "/path/to.jsonl"  // 可选：指定数据集路径代替 ShadowTracer
        }

    Response::

        {"job_id": "abc12345", "status": "queued"}
    """
    body = request.get_json(silent=True) or {}
    skill_id = (body.get("skill_id") or "").strip()
    if not skill_id:
        return jsonify({"error": "skill_id is required"}), 400

    config_override = body.get("config_override") or {}
    dataset_path = body.get("dataset_path") or None

    try:
        job_id = _get_manager().submit(
            skill_id=skill_id,
            config_override=config_override,
            dataset_path=dataset_path,
        )
        return jsonify({"job_id": job_id, "status": "queued"}), 202
    except Exception as exc:
        logger.exception("[DistillAPI] submit failed")
        return jsonify({"error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
#  GET /api/distill/jobs
# ═══════════════════════════════════════════════════════════════════

@distill_bp.route("/jobs", methods=["GET"])
def list_jobs():
    """
    列出所有训练任务（按时间倒序）。
    可选 query param: ``?skill_id=email_writer`` 过滤。
    """
    skill_id = request.args.get("skill_id") or None
    try:
        jobs = _get_manager().list_jobs(skill_id=skill_id)
        return jsonify({"jobs": jobs, "count": len(jobs)})
    except Exception as exc:
        logger.exception("[DistillAPI] list_jobs failed")
        return jsonify({"error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
#  GET /api/distill/jobs/<job_id>
# ═══════════════════════════════════════════════════════════════════

@distill_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    """查询单个任务的详细状态和训练 log。"""
    job = _get_manager().get_job(job_id)
    if not job:
        return jsonify({"error": f"job '{job_id}' not found"}), 404
    return jsonify(job.to_dict())


# ═══════════════════════════════════════════════════════════════════
#  POST /api/distill/jobs/<job_id>/cancel
# ═══════════════════════════════════════════════════════════════════

@distill_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    """取消仍在排队中的任务（运行中的任务无法中途取消）。"""
    ok = _get_manager().cancel(job_id)
    if ok:
        return jsonify({"success": True, "message": f"job '{job_id}' cancelled"})
    job = _get_manager().get_job(job_id)
    if not job:
        return jsonify({"error": f"job '{job_id}' not found"}), 404
    return jsonify({"success": False, "message": f"Cannot cancel job in status '{job.status}'"}), 409


# ═══════════════════════════════════════════════════════════════════
#  GET /api/distill/stream/<job_id>  (SSE)
# ═══════════════════════════════════════════════════════════════════

@distill_bp.route("/stream/<job_id>", methods=["GET"])
def stream_progress(job_id: str):
    """
    SSE 实时进度流。前端通过 ``EventSource`` 订阅此端点即可获得训练进度。

    事件格式::

        data: {"job_id":"...","status":"running","pct":32.5,"current_loss":1.23,...}\\n\\n

    流结束条件：任务状态变为 done / failed / cancelled，或 2 小时超时。
    """
    def _generate():
        try:
            yield from _get_manager().stream_progress(job_id, timeout=7200.0)
        except GeneratorExit:
            pass  # 客户端断开
        except Exception as exc:
            err = json.dumps({"error": str(exc), "job_id": job_id})
            yield f"data: {err}\n\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # 关闭 Nginx 缓冲，保证 SSE 实时推送
        },
    )


# ═══════════════════════════════════════════════════════════════════
#  GET /api/distill/adapters
# ═══════════════════════════════════════════════════════════════════

@distill_bp.route("/adapters", methods=["GET"])
def list_adapters():
    """
    列出 ``config/adapters/`` 目录下已注册的 LoRA 适配器元数据。
    前端可据此知道哪些 Skill 已完成微调，是否可切换到本地推理。
    """
    try:
        import glob
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config", "adapters",
        )
        adapters = []
        for path in sorted(glob.glob(os.path.join(base, "*.json"))):
            try:
                with open(path, encoding="utf-8") as f:
                    meta = json.load(f)
                adapters.append(meta)
            except Exception:
                pass
        return jsonify({"adapters": adapters, "count": len(adapters)})
    except Exception as exc:
        logger.exception("[DistillAPI] list_adapters failed")
        return jsonify({"error": str(exc)}), 500
