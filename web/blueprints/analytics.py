"""
Analytics blueprint — behavior monitoring, suggestions, insights.

Routes:
  POST   /api/behavior/log-event         — Log user behavior event
  GET    /api/behavior/recent-events      — Get recent events
  GET    /api/behavior/top-files          — Get most used files
  GET    /api/behavior/work-patterns      — Get work pattern analysis
  GET    /api/behavior/stats              — Get behavior statistics
  POST   /api/suggestions/generate        — Generate smart suggestions
  GET    /api/suggestions/pending         — Get pending suggestions
  POST   /api/suggestions/dismiss         — Dismiss a suggestion
  POST   /api/suggestions/apply           — Apply a suggestion
  GET    /api/suggestions/stats           — Get suggestion statistics
  POST   /api/insights/generate-weekly    — Generate weekly report
  POST   /api/insights/generate-monthly   — Generate monthly report
  GET    /api/insights/latest             — Get latest report
  POST   /api/insights/export-markdown    — Export report as markdown
"""

from flask import Blueprint, jsonify, request

analytics_bp = Blueprint("analytics", __name__)


# ── Lazy imports to avoid circular dependencies ──────────────


def _get_behavior_monitor():
    from web.app import get_behavior_monitor
    return get_behavior_monitor()


def _get_suggestion_engine():
    from web.app import get_suggestion_engine
    return get_suggestion_engine()


def _get_insight_reporter():
    from web.app import get_insight_reporter
    return get_insight_reporter()


def _get_trigger_system():
    from web.app import get_trigger_system
    return get_trigger_system()


# ═══════════════════════════════════════════════════
# 行为监控 API
# ═══════════════════════════════════════════════════


@analytics_bp.route("/api/behavior/log-event", methods=["POST"])
def behavior_log_event():
    """记录用户行为事件"""
    try:
        data = request.json or {}
        event_type = data.get("event_type")
        file_path = data.get("file_path")
        session_id = data.get("session_id")
        event_data = data.get("event_data")
        duration_ms = data.get("duration_ms")
        user_id = data.get("user_id", "default")
        auto_trigger = data.get("auto_trigger", True)

        if not event_type:
            return jsonify({"error": "缺少事件类型"}), 400

        monitor = _get_behavior_monitor()
        event_id = monitor.log_event(
            event_type=event_type,
            file_path=file_path,
            session_id=session_id,
            event_data=event_data,
            duration_ms=duration_ms,
        )

        decision_payload = None
        triggered = False
        if auto_trigger:
            trigger_system = _get_trigger_system()
            decision = trigger_system.evaluate_interaction_need(user_id)
            if decision and decision.should_interact:
                trigger_system.execute_interaction(decision, user_id)
                triggered = True
                decision_payload = {
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

        return jsonify(
            {
                "success": True,
                "event_id": event_id,
                "triggered": triggered,
                "decision": decision_payload,
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/behavior/recent-events", methods=["GET"])
def behavior_recent_events():
    """获取最近的事件"""
    try:
        limit = request.args.get("limit", 50, type=int)
        event_type = request.args.get("event_type")

        monitor = _get_behavior_monitor()
        events = monitor.get_recent_events(limit=limit, event_type=event_type)

        return jsonify({"success": True, "events": events})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/behavior/top-files", methods=["GET"])
def behavior_top_files():
    """获取最常用的文件"""
    try:
        limit = request.args.get("limit", 10, type=int)

        monitor = _get_behavior_monitor()
        files = monitor.get_frequently_used_files(limit=limit)

        return jsonify({"success": True, "files": files})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/behavior/work-patterns", methods=["GET"])
def behavior_work_patterns():
    """获取工作模式分析"""
    try:
        monitor = _get_behavior_monitor()
        patterns = monitor.get_work_patterns()

        return jsonify(patterns)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/behavior/stats", methods=["GET"])
def behavior_stats():
    """获取行为统计"""
    try:
        monitor = _get_behavior_monitor()
        stats = monitor.get_statistics()

        return jsonify(stats)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════
# 智能建议 API
# ═══════════════════════════════════════════════════


@analytics_bp.route("/api/suggestions/generate", methods=["POST"])
def suggestions_generate():
    """生成智能建议"""
    try:
        data = request.json or {}
        force_regenerate = data.get("force_regenerate", False)

        engine = _get_suggestion_engine()
        suggestions = engine.generate_suggestions(force_regenerate=force_regenerate)

        return jsonify(
            {"success": True, "suggestions": suggestions, "count": len(suggestions)}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/suggestions/pending", methods=["GET"])
def suggestions_pending():
    """获取待处理的建议"""
    try:
        limit = request.args.get("limit", 10, type=int)

        engine = _get_suggestion_engine()
        suggestions = engine.get_pending_suggestions(limit=limit)

        return jsonify(
            {"success": True, "suggestions": suggestions, "count": len(suggestions)}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/suggestions/dismiss", methods=["POST"])
def suggestions_dismiss():
    """拒绝建议"""
    try:
        data = request.json or {}
        suggestion_id = data.get("suggestion_id")
        feedback = data.get("feedback")

        if not suggestion_id:
            return jsonify({"error": "缺少建议ID"}), 400

        engine = _get_suggestion_engine()
        engine.dismiss_suggestion(suggestion_id, feedback=feedback)

        return jsonify({"success": True, "message": "建议已拒绝"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/suggestions/apply", methods=["POST"])
def suggestions_apply():
    """应用建议"""
    try:
        data = request.json or {}
        suggestion_id = data.get("suggestion_id")
        feedback = data.get("feedback")

        if not suggestion_id:
            return jsonify({"error": "缺少建议ID"}), 400

        engine = _get_suggestion_engine()
        engine.apply_suggestion(suggestion_id, feedback=feedback)

        return jsonify({"success": True, "message": "建议已应用"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/suggestions/stats", methods=["GET"])
def suggestions_stats():
    """获取建议统计"""
    try:
        engine = _get_suggestion_engine()
        stats = engine.get_statistics()

        return jsonify(stats)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════
# 洞察报告 API
# ═══════════════════════════════════════════════════


@analytics_bp.route("/api/insights/generate-weekly", methods=["POST"])
def insights_generate_weekly():
    """生成周报"""
    try:
        reporter = _get_insight_reporter()
        report = reporter.generate_weekly_report()

        return jsonify({"success": True, "report": report})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/insights/generate-monthly", methods=["POST"])
def insights_generate_monthly():
    """生成月报"""
    try:
        reporter = _get_insight_reporter()
        report = reporter.generate_monthly_report()

        return jsonify({"success": True, "report": report})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/insights/latest", methods=["GET"])
def insights_latest():
    """获取最新报告"""
    try:
        report_type = request.args.get("type", "weekly")

        reporter = _get_insight_reporter()
        report = reporter.get_latest_report(report_type=report_type)

        if report:
            return jsonify({"success": True, "report": report})
        else:
            return jsonify({"success": False, "message": "暂无报告"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/insights/export-markdown", methods=["POST"])
def insights_export_markdown():
    """导出报告为Markdown"""
    try:
        data = request.json or {}
        report = data.get("report")
        output_path = data.get("output_path", "workspace/report.md")

        if not report:
            return jsonify({"error": "缺少报告数据"}), 400

        reporter = _get_insight_reporter()
        saved_path = reporter.export_report_markdown(report, output_path)

        return jsonify({"success": True, "file_path": saved_path})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
