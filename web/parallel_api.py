# -*- coding: utf-8 -*-
"""
🔗 Koto 并行执行系统 - API集成

提供API端点来与并行执行系统交互：
- /api/queue/submit - 提交任务
- /api/queue/status - 查询队列状态
- /api/queue/cancel - 取消任务
- /api/monitor/dashboard - 获取监控仪表板
"""

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from .parallel_executor import (
    Priority,
    TaskType,
    cancel_task,
    get_queue_manager,
    get_resource_manager,
    get_session_tasks,
    get_task_monitor,
    get_task_status,
    submit_task,
)

logger = logging.getLogger(__name__)

# 创建蓝图
parallel_bp = Blueprint("parallel", __name__, url_prefix="/api")


# ============================================================================
# 优先级推断函数
# ============================================================================


def infer_priority(task_type: str, user_input: str) -> Priority:
    """根据任务类型和输入推断优先级"""

    # 关键词优先级标记
    critical_keywords = ["中断", "取消", "停止", "诊断", "修复", "错误"]
    high_keywords = ["打开", "启动", "执行", "运行", "代码", "文件"]
    low_keywords = ["研究", "分析", "搜索", "学习", "查询"]

    input_lower = user_input.lower()

    # 检查关键词
    if any(kw in input_lower for kw in critical_keywords):
        return Priority.CRITICAL

    if any(kw in input_lower for kw in high_keywords):
        return Priority.HIGH

    if any(kw in input_lower for kw in low_keywords):
        return Priority.LOW

    # 根据任务类型默认优先级
    task_priorities = {
        "SYSTEM_COMMAND": Priority.HIGH,
        "CODE_EXECUTION": Priority.HIGH,
        "FILE_OPERATION": Priority.HIGH,
        "IMAGE_PROCESSING": Priority.NORMAL,
        "DOCUMENT_GENERATION": Priority.LOW,
        "RESEARCH": Priority.LOW,
        "CHAT": Priority.NORMAL,
        "MULTI_STEP": Priority.NORMAL,
    }

    return task_priorities.get(task_type, Priority.NORMAL)


def infer_memory_usage(task_type: str, payload: Dict[str, Any]) -> int:
    """根据任务类型推断内存使用量（MB）"""

    estimates = {
        TaskType.CHAT.value: 100,
        TaskType.CODE_EXECUTION.value: 150,
        TaskType.FILE_OPERATION.value: 200,
        TaskType.SYSTEM_COMMAND.value: 50,
        TaskType.IMAGE_PROCESSING.value: 500,
        TaskType.DOCUMENT_GENERATION.value: 800,
        TaskType.RESEARCH.value: 300,
        TaskType.MULTI_STEP.value: 400,
    }

    base = estimates.get(task_type, 100)

    # 根据payload大小调整
    payload_size_kb = len(str(payload)) / 1024
    return int(base + payload_size_kb)


# ============================================================================
# API端点
# ============================================================================


@parallel_bp.route("/queue/submit", methods=["POST"])
def submit_queue_task():
    """
    提交任务到队列

    Request JSON:
    {
        "session_id": "sess_xxx",
        "task_type": "CHAT|CODE_EXECUTION|FILE_OPERATION|...",
        "user_input": "用户输入内容",
        "payload": {... 附加数据 ...},
        "priority": "CRITICAL|HIGH|NORMAL|LOW" (可选，自动推断)
    }

    Response:
    {
        "success": true,
        "task_id": "task_uuid",
        "message": "Task submitted successfully"
    }
    """
    try:
        data = request.get_json()

        # 验证必需字段
        session_id = data.get("session_id")
        task_type_str = data.get("task_type", "CHAT")
        user_input = data.get("user_input", "")
        payload = data.get("payload", {})

        if not session_id:
            return jsonify({"success": False, "message": "Missing session_id"}), 400

        # 转换任务类型
        try:
            task_type = TaskType[task_type_str]
        except KeyError:
            return (
                jsonify(
                    {"success": False, "message": f"Invalid task_type: {task_type_str}"}
                ),
                400,
            )

        # 推断优先级
        if "priority" in data:
            try:
                priority = Priority[data["priority"]]
            except KeyError:
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": f'Invalid priority: {data["priority"]}',
                        }
                    ),
                    400,
                )
        else:
            priority = infer_priority(task_type_str, user_input)

        # 推断内存使用
        estimated_memory = infer_memory_usage(task_type_str, payload)

        # 提交任务
        task_id = submit_task(
            session_id=session_id,
            task_type=task_type,
            priority=priority,
            user_input=user_input,
            payload=payload,
            estimated_memory=estimated_memory,
        )

        logger.info(f"[API] Task submitted: {task_id} (priority={priority.name})")

        return (
            jsonify(
                {
                    "success": True,
                    "task_id": task_id,
                    "message": "Task submitted successfully",
                    "priority": priority.name,
                }
            ),
            202,
        )  # Accepted

    except Exception as e:
        logger.error(f"[API] Error submitting task: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@parallel_bp.route("/queue/status", methods=["GET"])
def get_queue_status():
    """
    获取队列状态

    Query params:
    - session_id: (可选) 只获取该会话的任务
    - task_id: (可选) 获取特定任务的状态

    Response:
    {
        "queue_stats": {
            "total_tasks": 10,
            "pending": 3,
            "running": 2,
            ...
        },
        "tasks": [
            {
                "id": "task_xxx",
                "status": "RUNNING|PENDING|COMPLETED|FAILED",
                ...
            }
        ]
    }
    """
    try:
        queue_mgr = get_queue_manager()

        task_id = request.args.get("task_id")
        session_id = request.args.get("session_id")

        if task_id:
            # 查询单个任务
            task = get_task_status(task_id)
            if task:
                return jsonify({"success": True, "task": task.to_dict()})
            else:
                return jsonify({"success": False, "message": "Task not found"}), 404

        # 获取队列统计
        stats = queue_mgr.get_stats()

        tasks = []
        if session_id:
            # 获取会话的任务
            session_tasks = get_session_tasks(session_id)
            tasks = [t.to_dict() for t in session_tasks]

        return jsonify(
            {
                "success": True,
                "queue_stats": stats,
                "tasks": tasks,
            }
        )

    except Exception as e:
        logger.error(f"[API] Error getting queue status: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@parallel_bp.route("/queue/cancel", methods=["POST"])
def cancel_queue_task():
    """
    取消任务

    Request JSON:
    {
        "task_id": "task_uuid"
    }

    Response:
    {
        "success": true,
        "message": "Task cancelled successfully"
    }
    """
    try:
        data = request.get_json()
        task_id = data.get("task_id")

        if not task_id:
            return jsonify({"success": False, "message": "Missing task_id"}), 400

        success = cancel_task(task_id)

        if success:
            logger.info(f"[API] Task cancelled: {task_id}")
            return jsonify({"success": True, "message": "Task cancelled"})
        else:
            return jsonify({"success": False, "message": "Task not found"}), 404

    except Exception as e:
        logger.error(f"[API] Error cancelling task: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@parallel_bp.route("/monitor/dashboard", methods=["GET"])
def get_monitor_dashboard():
    """
    获取监控仪表板

    Response:
    {
        "timestamp": "2026-02-16T...",
        "queue": {... 队列统计 ...},
        "resources": {... 资源使用 ...},
        "completed_tasks": 100,
        "failed_tasks": 5,
        "avg_task_time": 12.5,
        "success_rate": 0.95
    }
    """
    try:
        monitor = get_task_monitor()
        dashboard = monitor.get_dashboard()

        return jsonify({"success": True, "dashboard": dashboard})

    except Exception as e:
        logger.error(f"[API] Error getting dashboard: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@parallel_bp.route("/resource/usage", methods=["GET"])
def get_resource_usage():
    """
    获取资源使用情况

    Response:
    {
        "concurrent_tasks": 2,
        "max_concurrent": 5,
        "memory_usage_mb": 1024,
        "memory_soft_limit_mb": 2048,
        "memory_hard_limit_mb": 3072,
        "cpu_usage_percent": 45.5,
        "api_tokens": 2.5
    }
    """
    try:
        resource_mgr = get_resource_manager()
        stats = resource_mgr.get_stats()

        return jsonify({"success": True, "resources": stats})

    except Exception as e:
        logger.error(f"[API] Error getting resource usage: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================================
# API导出
# ============================================================================


def register_parallel_api(app):
    """向Flask应用注册并行执行API"""
    app.register_blueprint(parallel_bp)
    logger.info("[PARALLEL API] Registered API endpoints")
