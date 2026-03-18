"""
Proactive features blueprint — notifications, dialogue monitoring, and context awareness.

Routes:
  GET    /api/notifications/unread        — Get unread notifications
  POST   /api/notifications/mark-read     — Mark a notification as read
  POST   /api/notifications/dismiss       — Dismiss a notification
  GET    /api/notifications/stats         — Get notification statistics
  GET/POST /api/notifications/preferences — Get or update notification preferences
  POST   /api/dialogue/start-monitoring   — Start proactive dialogue monitoring
  POST   /api/dialogue/stop-monitoring    — Stop proactive dialogue monitoring
  POST   /api/dialogue/trigger            — Manually trigger a dialogue
  GET    /api/dialogue/history            — Get dialogue history
  POST   /api/context/detect              — Detect current work context
  GET    /api/context/current             — Get current context
  GET    /api/context/history             — Get context history
  GET    /api/context/statistics          — Get context statistics
  GET    /api/context/predict             — Predict next context
"""

import logging

from flask import Blueprint, jsonify, request

_logger = logging.getLogger("koto.routes.proactive")

proactive_bp = Blueprint("proactive", __name__)


# ---------------------------------------------------------------------------
# Lazy imports — avoid circular dependency with app.py
# ---------------------------------------------------------------------------


def _get_notification_manager():
    from web.app import get_notification_manager

    return get_notification_manager()


def _get_proactive_dialogue():
    from web.app import get_proactive_dialogue

    return get_proactive_dialogue()


def _get_context_awareness():
    from web.app import get_context_awareness

    return get_context_awareness()


def _get_trigger_system():
    from web.app import get_trigger_system

    return get_trigger_system()


# ==================== 通知管理 API ====================


@proactive_bp.route("/api/notifications/unread", methods=["GET"])
def get_unread_notifications():
    """获取未读通知"""
    try:
        user_id = request.args.get("user_id", "default")
        limit = int(request.args.get("limit", 50))

        manager = _get_notification_manager()
        notifications = manager.get_unread_notifications(user_id, limit)

        return jsonify(
            {
                "success": True,
                "notifications": notifications,
                "count": len(notifications),
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/notifications/mark-read", methods=["POST"])
def mark_notification_read():
    """标记通知已读"""
    try:
        data = request.json or {}
        notification_id = data.get("notification_id")
        user_id = data.get("user_id", "default")

        if not notification_id:
            return jsonify({"error": "缺少notification_id"}), 400

        manager = _get_notification_manager()
        manager.mark_as_read(notification_id, user_id)

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/notifications/dismiss", methods=["POST"])
def dismiss_notification():
    """忽略通知"""
    try:
        data = request.json or {}
        notification_id = data.get("notification_id")
        user_id = data.get("user_id", "default")

        if not notification_id:
            return jsonify({"error": "缺少notification_id"}), 400

        manager = _get_notification_manager()
        manager.dismiss_notification(notification_id, user_id)

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/notifications/stats", methods=["GET"])
def get_notification_stats():
    """获取通知统计"""
    try:
        user_id = request.args.get("user_id", "default")
        days = int(request.args.get("days", 7))

        manager = _get_notification_manager()
        stats = manager.get_notification_stats(user_id, days)

        return jsonify({"success": True, "stats": stats})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/notifications/preferences", methods=["GET", "POST"])
def notification_preferences():
    """获取或设置通知偏好"""
    try:
        user_id = request.args.get("user_id", "default")
        manager = _get_notification_manager()

        if request.method == "GET":
            prefs = manager.get_user_preferences(user_id)
            return jsonify({"success": True, "preferences": prefs})

        else:  # POST
            data = request.json or {}
            manager.update_user_preferences(user_id, data)
            return jsonify({"success": True})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== 主动对话 API ====================


@proactive_bp.route("/api/dialogue/start-monitoring", methods=["POST"])
def start_dialogue_monitoring():
    """启动主动对话监控"""
    try:
        data = request.json or {}
        check_interval = data.get("check_interval", 300)  # 默认5分钟

        engine = _get_proactive_dialogue()
        engine.start_monitoring(check_interval)

        return jsonify({"success": True, "message": "主动对话监控已启动"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/dialogue/stop-monitoring", methods=["POST"])
def stop_dialogue_monitoring():
    """停止主动对话监控"""
    try:
        engine = _get_proactive_dialogue()
        engine.stop_monitoring()

        return jsonify({"success": True, "message": "主动对话监控已停止"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/dialogue/trigger", methods=["POST"])
def trigger_dialogue():
    """手动触发对话"""
    try:
        data = request.json or {}
        user_id = data.get("user_id", "default")
        scene_type = data.get("scene_type")
        context = data.get("context", {})

        if not scene_type:
            return jsonify({"error": "缺少scene_type"}), 400

        engine = _get_proactive_dialogue()
        engine.manual_trigger(user_id, scene_type, **context)

        return jsonify({"success": True, "message": f"已触发{scene_type}对话"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/dialogue/history", methods=["GET"])
def get_dialogue_history():
    """获取对话历史"""
    try:
        user_id = request.args.get("user_id", "default")
        limit = int(request.args.get("limit", 50))

        engine = _get_proactive_dialogue()
        history = engine.get_dialogue_history(user_id, limit)

        return jsonify({"success": True, "history": history, "count": len(history)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== 情境感知 API ====================


@proactive_bp.route("/api/context/detect", methods=["POST"])
def detect_context():
    """检测当前工作场景"""
    try:
        data = request.json or {}
        user_id = data.get("user_id", "default")

        system = _get_context_awareness()
        context = system.detect_context(user_id)

        return jsonify({"success": True, "context": context})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/context/current", methods=["GET"])
def get_current_context():
    """获取当前场景"""
    try:
        system = _get_context_awareness()
        context = system.get_current_context()

        return jsonify({"success": True, "context": context})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/context/history", methods=["GET"])
def get_context_history():
    """获取场景历史"""
    try:
        user_id = request.args.get("user_id", "default")
        days = int(request.args.get("days", 7))

        system = _get_context_awareness()
        history = system.get_context_history(user_id, days)

        return jsonify({"success": True, "history": history, "count": len(history)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/context/statistics", methods=["GET"])
def get_context_statistics():
    """获取场景统计"""
    try:
        user_id = request.args.get("user_id", "default")
        days = int(request.args.get("days", 30))

        system = _get_context_awareness()
        stats = system.get_context_statistics(user_id, days)

        return jsonify({"success": True, "statistics": stats})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@proactive_bp.route("/api/context/predict", methods=["GET"])
def predict_next_context():
    """预测下一个场景"""
    try:
        user_id = request.args.get("user_id", "default")

        system = _get_context_awareness()
        prediction = system.predict_next_context(user_id)

        return jsonify({"success": True, "prediction": prediction})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
