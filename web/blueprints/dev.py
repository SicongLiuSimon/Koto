"""
Dev / debug and RAG blueprint.

Routes:
  GET    /workflow-dag                                   — Workflow DAG visualization page
  GET    /api/dev/graph-mermaid                          — Mermaid DAG markup for a workflow/agent
  GET    /api/dev/checkpoint-info                        — Checkpoint DB info
  GET    /api/dev/checkpoints/<thread_id>                — List checkpoints for a thread
  DELETE /api/dev/checkpoints/<thread_id>                — Delete all checkpoints for a thread
  POST   /api/rag/ingest                                 — Index file or text into vector store
  POST   /api/rag/query                                  — Retrieve relevant chunks (optionally with LLM answer)
  GET    /api/rag/stats                                  — RAG index statistics
  DELETE /api/rag/clear                                  — Clear the entire RAG vector store
  POST   /api/response/rate                              — User star-rating for AI responses
  GET    /api/auto-catalog/status                        — Auto-catalog scheduler status
  POST   /api/auto-catalog/enable                        — Enable auto-catalog
  POST   /api/auto-catalog/disable                       — Disable auto-catalog
  POST   /api/auto-catalog/run-now                       — Trigger a manual catalog run
  GET    /api/auto-catalog/backup-manifest/<filename>    — Download a backup manifest file
  GET    /api/token-stats                                — Token usage statistics
  POST   /api/token-stats/reset                          — Reset token statistics
"""
import logging
import os

from flask import Blueprint, jsonify, request, send_file, send_from_directory

_logger = logging.getLogger("koto.routes.dev")

dev_bp = Blueprint("dev", __name__)


# ── Auto-catalog routes ───────────────────────────────────────────────────────


@dev_bp.route("/api/auto-catalog/status", methods=["GET"])
def auto_catalog_status():
    """获取自动归纳状态"""
    try:
        from auto_catalog_scheduler import get_auto_catalog_scheduler

        scheduler = get_auto_catalog_scheduler()

        return jsonify(
            {
                "success": True,
                "enabled": scheduler.is_auto_catalog_enabled(),
                "schedule_time": scheduler.get_catalog_schedule(),
                "source_directories": scheduler.get_source_directories(),
                "backup_directory": scheduler.get_backup_directory(),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/auto-catalog/enable", methods=["POST"])
def auto_catalog_enable():
    """启用自动归纳"""
    try:
        from auto_catalog_scheduler import get_auto_catalog_scheduler

        scheduler = get_auto_catalog_scheduler()

        data = request.json or {}
        schedule_time = data.get("schedule_time", "02:00")
        source_dirs = data.get("source_directories")

        scheduler.enable_auto_catalog(schedule_time, source_dirs)

        return jsonify(
            {
                "success": True,
                "message": f"自动归纳已启用，每日 {schedule_time} 执行",
                "schedule_time": schedule_time,
                "source_directories": scheduler.get_source_directories(),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/auto-catalog/disable", methods=["POST"])
def auto_catalog_disable():
    """禁用自动归纳"""
    try:
        from auto_catalog_scheduler import get_auto_catalog_scheduler

        scheduler = get_auto_catalog_scheduler()

        scheduler.disable_auto_catalog()

        return jsonify({"success": True, "message": "自动归纳已禁用"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/auto-catalog/run-now", methods=["POST"])
def auto_catalog_run_now():
    """立即执行一次归纳（手动触发）"""
    try:
        from auto_catalog_scheduler import get_auto_catalog_scheduler

        scheduler = get_auto_catalog_scheduler()

        result = scheduler.manual_catalog_now()

        return jsonify(
            {
                "success": result.get("success", False),
                "total_files": result.get("total_files", 0),
                "organized_count": result.get("organized_count", 0),
                "backed_up_count": result.get("backed_up_count", 0),
                "errors": result.get("errors", []),
                "report_path": result.get("report_path", ""),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/auto-catalog/backup-manifest/<path:filename>", methods=["GET"])
def get_backup_manifest(filename):
    """下载备份清单文件"""
    try:
        from auto_catalog_scheduler import get_auto_catalog_scheduler

        scheduler = get_auto_catalog_scheduler()

        backup_dir = scheduler.get_backup_directory()
        return send_from_directory(backup_dir, filename, as_attachment=False)
    except Exception as e:
        return jsonify({"error": str(e)}), 404


# ── Token usage statistics ────────────────────────────────────────────────────


@dev_bp.route("/api/token-stats", methods=["GET"])
def api_token_stats():
    """返回 Token 用量统计（今日 / 本月 / 按模型 / 近 7 天）"""
    try:
        from token_tracker import get_stats

        return jsonify(get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/token-stats/reset", methods=["POST"])
def api_token_stats_reset():
    """重置统计数据。Body: {"period": "today" | "month" | "all"}"""
    try:
        from token_tracker import reset_stats

        period = (request.json or {}).get("period", "all")
        return jsonify(reset_stats(period))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── LangGraph workflow visualization & dev tools ──────────────────────────────


@dev_bp.route("/workflow-dag")
def workflow_dag_page():
    """工作流 DAG 可视化页面"""
    html_path = os.path.join(os.path.dirname(__file__), os.pardir, "static", "workflow_dag.html")
    try:
        return send_file(html_path)
    except Exception as e:
        return f"<h3>Error: {e}</h3>", 500


@dev_bp.route("/api/dev/graph-mermaid", methods=["GET"])
def api_dev_graph_mermaid():
    """
    返回指定工作流 / Agent 的 Mermaid DAG 图标记。

    参数:
        workflow : 工作流名称  (research_and_document | multi_agent_ppt | react_agent)
        type     : 类型        (workflow | agent)
    """
    wf = request.args.get("workflow", "react_agent")
    wf_type = request.args.get("type", "agent")
    try:
        if wf_type == "agent" or wf == "react_agent":
            from app.core.agent.factory import create_langgraph_agent

            agent = create_langgraph_agent()
            mermaid_code = agent.get_graph_mermaid()
            node_count = mermaid_code.count("\n    ") if mermaid_code else 0
            edge_count = (
                mermaid_code.count("-->") + mermaid_code.count("-.->")
                if mermaid_code
                else 0
            )
        else:
            from app.core.workflow.langgraph_workflow import WorkflowEngine

            engine = WorkflowEngine()
            mermaid_code = engine.get_graph_mermaid(wf)
            node_count = mermaid_code.count("\n    ") if mermaid_code else 0
            edge_count = (
                mermaid_code.count("-->") + mermaid_code.count("-.->")
                if mermaid_code
                else 0
            )

        return jsonify(
            {
                "success": True,
                "workflow": wf,
                "type": wf_type,
                "mermaid": mermaid_code,
                "node_count": max(node_count, 0),
                "edge_count": max(edge_count, 0),
            }
        )
    except Exception as e:
        import traceback

        return (
            jsonify(
                {"success": False, "error": str(e), "traceback": traceback.format_exc()}
            ),
            500,
        )


@dev_bp.route("/api/dev/checkpoint-info", methods=["GET"])
def api_dev_checkpoint_info():
    """返回检查点数据库信息（类型 / 会话数 / 快照总数）。"""
    try:
        from app.core.agent.checkpoint_manager import CheckpointManager

        return jsonify(CheckpointManager.get_db_info())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/dev/checkpoints/<thread_id>", methods=["GET"])
def api_dev_list_checkpoints(thread_id):
    """列出某会话的检查点快照列表。"""
    try:
        from app.core.agent.checkpoint_manager import CheckpointManager

        snapshots = CheckpointManager.list_checkpoints(thread_id)
        return jsonify(
            {"thread_id": thread_id, "snapshots": snapshots, "count": len(snapshots)}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/dev/checkpoints/<thread_id>", methods=["DELETE"])
def api_dev_delete_checkpoints(thread_id):
    """删除某会话的全部检查点（用于清除对话历史）。"""
    try:
        from app.core.agent.checkpoint_manager import CheckpointManager

        ok = CheckpointManager.delete_thread(thread_id)
        return jsonify({"success": ok, "thread_id": thread_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── RAG vector retrieval API ─────────────────────────────────────────────────


@dev_bp.route("/api/rag/ingest", methods=["POST"])
def api_rag_ingest():
    """
    索引文件或文本到向量库。

    请求体 (JSON):
        { "file_path": "/abs/path/to/doc.pdf" }
        或
        { "text": "要索引的文本内容...", "source": "my_doc" }

    返回:
        { "success": true, "chunks_added": 42, "stats": {...} }
    """
    try:
        from app.core.services.rag_service import get_rag_service

        data = request.get_json(force=True) or {}
        rag = get_rag_service()

        if "file_path" in data:
            fp = data["file_path"]
            if not os.path.isabs(fp):
                fp = os.path.join(os.getcwd(), fp)
            if not os.path.exists(fp):
                return jsonify({"error": f"文件不存在: {fp}"}), 400
            count = rag.index_file(fp)
        elif "text" in data:
            count = rag.index_text(data["text"], source=data.get("source", "api_input"))
        else:
            return jsonify({"error": "请提供 file_path 或 text 字段"}), 400

        return jsonify({"success": True, "chunks_added": count, "stats": rag.stats()})
    except Exception as e:
        _logger.exception("[RAG /ingest] error")
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/rag/query", methods=["POST"])
def api_rag_query():
    """
    检索向量库，返回相关文本片段。

    请求体 (JSON):
        {
          "question": "Koto 支持哪些文件格式？",
          "k": 5,
          "answer": true        // 可选：true = 同时生成 LLM 答案
        }

    返回（仅检索）:
        { "chunks": [...], "count": 3 }

    返回（含答案）:
        { "answer": "...", "sources": [...], "chunks": [...], "context_used": true }
    """
    try:
        from app.core.services.rag_service import get_rag_service

        data = request.get_json(force=True) or {}
        question = data.get("question", "").strip()
        if not question:
            return jsonify({"error": "question 字段不能为空"}), 400

        k = int(data.get("k", 5))
        want_answer = data.get("answer", False)
        rag = get_rag_service()

        if want_answer:
            result = rag.rag_answer(question, k=k)
            return jsonify(result)
        else:
            chunks = rag.retrieve(question, k=k)
            return jsonify({"chunks": chunks, "count": len(chunks)})
    except Exception as e:
        _logger.exception("[RAG /query] error")
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/rag/stats", methods=["GET"])
def api_rag_stats():
    """
    返回 RAG 索引统计信息。

    返回:
        {
          "initialized": true,
          "doc_count": 312,
          "index_dir": "config/rag_index",
          "index_size_mb": 2.4,
          "embedding_model": "GoogleGenerativeAIEmbeddings"
        }
    """
    try:
        from app.core.services.rag_service import get_rag_service

        rag = get_rag_service()
        return jsonify(rag.stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dev_bp.route("/api/rag/clear", methods=["DELETE"])
def api_rag_clear():
    """清空 RAG 向量库（删除所有索引数据）。"""
    try:
        import app.core.services.rag_service as _rag_mod
        from app.core.services.rag_service import get_rag_service

        rag = get_rag_service()
        ok = rag.clear()
        # 重置单例，下次 get_rag_service() 将重建
        _rag_mod._rag_instance = None
        return jsonify({"success": ok, "message": "向量库已清空"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── User rating API ──────────────────────────────────────────────────────────


@dev_bp.route("/api/response/rate", methods=["POST"])
def api_response_rate():
    """
    接收用户对 AI 回复的星级评分。

    请求体:
      msg_id       str   — MD5 消息指纹（由后端 done 事件下发）
      stars        int   — 1~5 星
      comment      str   — 可选文字反馈
      session_name str   — 会话名
      user_input   str   — 用户原始输入（前 500 字）
      ai_response  str   — AI 回复文本（前 500 字）
      task_type    str   — 任务类型，默认 CHAT
    """
    data = request.json or {}
    msg_id = data.get("msg_id", "")
    stars = int(data.get("stars", 0))
    comment = (data.get("comment") or "").strip()
    session_name = data.get("session_name", "default")
    user_input = data.get("user_input", "")
    ai_response = data.get("ai_response", "")
    task_type = data.get("task_type", "CHAT")

    if not (1 <= stars <= 5):
        return jsonify({"success": False, "error": "stars 必须在 1~5 之间"}), 400

    # ── 1. 存入 RatingStore ────────────────────────────────────────────────
    try:
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore()
        rs.save_user_rating(
            msg_id=msg_id,
            stars=stars,
            comment=comment,
            session_name=session_name,
            user_input=user_input,
            ai_response=ai_response,
        )
    except Exception as e:
        _logger.warning(f"[ResponseRate] ⚠️ RatingStore 保存失败: {e}")

    # ── 2. 高评分（≥4 星）→ ShadowTracer 记录优质样本，推进飞轮 ──────────
    trace_id = None
    if stars >= 4 and user_input and ai_response:
        try:
            from app.core.learning.shadow_tracer import ShadowTracer

            trace_id = ShadowTracer.record_approved(
                session_id=session_name,
                user_input=user_input,
                ai_response=ai_response,
                skill_id=None,
                task_type=task_type,
                model_used="",
                metadata={"stars": stars, "comment": comment, "source": "user_rating"},
            )
            _logger.debug(
                f"[ResponseRate] ⭐ {stars}星 → ShadowTracer 记录 trace_id={trace_id}"
            )
        except Exception as e:
            _logger.warning(f"[ResponseRate] ⚠️ ShadowTracer 记录失败: {e}")

    return jsonify({
        "success": True,
        "msg_id": msg_id,
        "stars": stars,
        "trace_id": trace_id,
        "flywheel": trace_id is not None,
    })
