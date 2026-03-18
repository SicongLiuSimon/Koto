"""
Execution & trigger management blueprint.

Routes:
  POST   /api/execution/authorize        — Authorize task execution
  POST   /api/execution/revoke           — Revoke task authorization
  POST   /api/execution/execute          — Execute a task
  POST   /api/execution/queue            — Queue a task
  GET    /api/execution/history          — Get execution history
  GET    /api/execution/statistics       — Get execution statistics
  POST   /api/execution/start-processor  — Start the auto-execution processor
  POST   /api/execution/stop-processor   — Stop the auto-execution processor
  POST   /api/triggers/evaluate          — Evaluate proactive interaction need
  POST   /api/triggers/start             — Start proactive interaction monitoring
  POST   /api/triggers/stop              — Stop proactive interaction monitoring
  GET    /api/triggers/stats             — Get trigger statistics
  GET    /api/triggers/list              — List triggers
  POST   /api/triggers/update            — Update trigger configuration
  GET    /api/triggers/params/<id>       — Get trigger parameters
  POST   /api/triggers/params/<id>       — Update trigger parameters
  POST   /api/triggers/feedback          — Submit trigger feedback
"""

import logging

from flask import Blueprint, jsonify, request

_logger = logging.getLogger("koto.routes.execution")

execution_bp = Blueprint("execution", __name__)


# ---------------------------------------------------------------------------
# Lazy helpers – break circular imports with web.app
# ---------------------------------------------------------------------------


def _get_auto_execution():
    from web.app import get_auto_execution

    return get_auto_execution()


def _get_trigger_system():
    from web.app import get_trigger_system

    return get_trigger_system()


# ==================== 自动执行引擎 API ====================


@execution_bp.route("/api/execution/authorize", methods=["POST"])
def authorize_task_execution():
    """授权任务执行"""
    try:
        data = request.json or {}
        user_id = data.get("user_id", "default")
        task_type = data.get("task_type")
        auto_execute = data.get("auto_execute", False)
        max_executions_per_day = data.get("max_executions_per_day", 10)
        expires_days = data.get("expires_days", 30)

        if not task_type:
            return jsonify({"error": "缺少task_type"}), 400

        engine = _get_auto_execution()
        engine.authorize_task(
            user_id, task_type, auto_execute, max_executions_per_day, expires_days
        )

        return jsonify({"success": True, "message": f"已授权{task_type}任务"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/execution/revoke", methods=["POST"])
def revoke_task_authorization():
    """撤销任务授权"""
    try:
        data = request.json or {}
        user_id = data.get("user_id", "default")
        task_type = data.get("task_type")

        if not task_type:
            return jsonify({"error": "缺少task_type"}), 400

        engine = _get_auto_execution()
        engine.revoke_authorization(user_id, task_type)

        return jsonify({"success": True, "message": f"已撤销{task_type}任务授权"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/execution/execute", methods=["POST"])
def execute_task():
    """执行任务"""
    try:
        data = request.json or {}
        user_id = data.get("user_id", "default")
        task_type = data.get("task_type")
        params = data.get("params", {})
        force = data.get("force", False)

        if not task_type:
            return jsonify({"error": "缺少task_type"}), 400

        engine = _get_auto_execution()
        result = engine.execute_task(user_id, task_type, params, force)

        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/execution/queue", methods=["POST"])
def queue_task():
    """任务加入队列"""
    try:
        data = request.json or {}
        user_id = data.get("user_id", "default")
        task_type = data.get("task_type")
        params = data.get("params", {})
        priority = data.get("priority", 5)

        if not task_type:
            return jsonify({"error": "缺少task_type"}), 400

        engine = _get_auto_execution()
        task_id = engine.queue_task(user_id, task_type, params, priority)

        return jsonify({"success": True, "task_id": task_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/execution/history", methods=["GET"])
def get_execution_history():
    """获取执行历史"""
    try:
        user_id = request.args.get("user_id", "default")
        limit = int(request.args.get("limit", 50))

        engine = _get_auto_execution()
        history = engine.get_execution_history(user_id, limit)

        return jsonify({"success": True, "history": history, "count": len(history)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/execution/statistics", methods=["GET"])
def get_execution_statistics():
    """获取执行统计"""
    try:
        user_id = request.args.get("user_id", "default")
        days = int(request.args.get("days", 30))

        engine = _get_auto_execution()
        stats = engine.get_statistics(user_id, days)

        return jsonify({"success": True, "statistics": stats})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/execution/start-processor", methods=["POST"])
def start_execution_processor():
    """启动自动执行处理器"""
    try:
        data = request.json or {}
        interval = data.get("interval", 60)  # 默认1分钟

        engine = _get_auto_execution()
        engine.start_queue_processor(interval)

        return jsonify({"success": True, "message": "自动执行处理器已启动"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/execution/stop-processor", methods=["POST"])
def stop_execution_processor():
    """停止自动执行处理器"""
    try:
        engine = _get_auto_execution()
        engine.stop_queue_processor()

        return jsonify({"success": True, "message": "自动执行处理器已停止"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== 主动交互触发系统 API ====================


@execution_bp.route("/api/triggers/evaluate", methods=["POST"])
def triggers_evaluate():
    """评估是否需要主动交互"""
    try:
        data = request.json or {}
        user_id = data.get("user_id", "default")
        execute = data.get("execute", True)

        system = _get_trigger_system()
        decision = system.evaluate_interaction_need(user_id)

        if decision and decision.should_interact and execute:
            system.execute_interaction(decision, user_id)

        decision_payload = None
        if decision:
            decision_payload = {
                "should_interact": decision.should_interact,
                "interaction_type": decision.interaction_type.value,
                "priority": decision.priority,
                "reason": decision.reason,
                "content": decision.content,
                "scores": {
                    "urgency": decision.urgency_score,
                    "importance": decision.importance_score,
                    "disturbance": decision.disturbance_cost,
                    "final": decision.final_score,
                },
            }

        return jsonify({"success": True, "decision": decision_payload})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/triggers/start", methods=["POST"])
def triggers_start():
    """启动主动交互监控"""
    try:
        data = request.json or {}
        user_id = data.get("user_id", "default")
        interval = data.get("interval", 300)

        system = _get_trigger_system()
        system.start_monitoring(check_interval=interval, user_id=user_id)

        return jsonify(
            {"success": True, "message": "主动交互触发系统已启动", "interval": interval}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/triggers/stop", methods=["POST"])
def triggers_stop():
    """停止主动交互监控"""
    try:
        system = _get_trigger_system()
        system.stop_monitoring()

        return jsonify({"success": True, "message": "主动交互触发系统已停止"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/triggers/stats", methods=["GET"])
def triggers_stats():
    """获取触发统计"""
    try:
        days = int(request.args.get("days", 7))

        system = _get_trigger_system()
        stats = system.get_trigger_statistics(days)

        return jsonify({"success": True, "stats": stats})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/triggers/list", methods=["GET"])
def triggers_list():
    """获取触发器列表"""
    try:
        system = _get_trigger_system()
        triggers = system.list_triggers()

        return jsonify({"success": True, "triggers": triggers})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/triggers/update", methods=["POST"])
def triggers_update():
    """更新触发器配置"""
    try:
        data = request.json or {}
        trigger_id = data.get("trigger_id")

        if not trigger_id:
            return jsonify({"error": "缺少trigger_id"}), 400

        enabled = data.get("enabled")
        priority = data.get("priority")
        cooldown_minutes = data.get("cooldown_minutes")
        threshold_value = data.get("threshold_value")
        parameters = data.get("parameters")

        system = _get_trigger_system()
        ok = system.update_trigger_config(
            trigger_id,
            enabled=enabled,
            priority=priority,
            cooldown_minutes=cooldown_minutes,
            threshold_value=threshold_value,
        )

        if not ok:
            return jsonify({"error": "触发器不存在"}), 404

        # 如果提供了参数，更新参数
        if parameters is not None:
            system.update_trigger_params(trigger_id, parameters)

        return jsonify({"success": True, "message": "触发器配置已更新"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/triggers/params/<trigger_id>", methods=["GET"])
def get_trigger_params(trigger_id):
    """获取触发器参数"""
    try:
        system = _get_trigger_system()
        params = system.get_trigger_params(trigger_id)

        return jsonify(
            {"success": True, "trigger_id": trigger_id, "parameters": params}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/triggers/params/<trigger_id>", methods=["POST"])
def update_trigger_params_endpoint(trigger_id):
    """更新触发器参数"""
    try:
        data = request.json or {}
        parameters = data.get("parameters", {})

        system = _get_trigger_system()
        ok = system.update_trigger_params(trigger_id, parameters)

        if not ok:
            return jsonify({"error": "触发器不存在"}), 404

        return jsonify(
            {
                "success": True,
                "message": "触发器参数已更新",
                "parameters": system.get_trigger_params(trigger_id),
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@execution_bp.route("/api/triggers/feedback", methods=["POST"])
def triggers_feedback():
    """提交触发反馈"""
    try:
        data = request.json or {}
        trigger_id = data.get("trigger_id")
        feedback = data.get("feedback")
        response_time_seconds = data.get("response_time_seconds", 0)

        if not trigger_id or not feedback:
            return jsonify({"error": "缺少trigger_id或feedback"}), 400

        system = _get_trigger_system()
        system.record_user_feedback(trigger_id, feedback, response_time_seconds)

        return jsonify({"success": True, "message": "反馈已记录"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
