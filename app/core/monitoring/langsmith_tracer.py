# -*- coding: utf-8 -*-
"""
Koto LangSmith Tracer
=====================
可选的 LangSmith 可观测性集成。
当 LANGCHAIN_API_KEY 和 LANGCHAIN_TRACING_V2=true 设置后自动激活。

功能：
  - 追踪每次 LangGraph 执行的完整 token 消耗、延迟、节点顺序
  - 记录 Agent 中间推理步骤（thought / tool_call / observation）
  - 在 LangSmith Dashboard 中可视化完整 DAG 执行路径
  - 支持 A/B 评估（feedback API）

激活方式（在 config/gemini_config.env 中添加）：
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_API_KEY=lsv2_...        # 从 https://smith.langchain.com 获取
    LANGCHAIN_PROJECT=Koto            # 项目名称（可选，默认 "Koto"）
    LANGCHAIN_ENDPOINT=https://api.smith.langchain.com   # 可选(企业私有部署)

无需任何代码更改 —— LangChain/LangGraph 自动检测环境变量。
此文件只做启动检测和状态日志。

用法（在 server.py / koto_app.py 启动时调用）：
    from app.core.monitoring.langsmith_tracer import init_langsmith
    init_langsmith()
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_initialized = False
_status: str = "disabled"   # disabled | active | error


def init_langsmith() -> str:
    """
    检测 LangSmith 环境变量并初始化 tracing。
    返回状态字符串: 'active' | 'disabled' | 'error'

    LangSmith 无需任何额外 SDK，langchain-core 已内置；
    只需设置环境变量，所有 LangChain / LangGraph 调用自动上传 trace。
    """
    global _initialized, _status

    if _initialized:
        return _status

    _initialized = True

    api_key = os.environ.get("LANGCHAIN_API_KEY", "")
    tracing = os.environ.get("LANGCHAIN_TRACING_V2", "").lower()
    project = os.environ.get("LANGCHAIN_PROJECT", "Koto")

    if not api_key or tracing != "true":
        logger.info(
            "[LangSmith] ℹ️ Tracing 未启用（未检测到 LANGCHAIN_API_KEY + LANGCHAIN_TRACING_V2=true）\n"
            "  如需启用：在 config/gemini_config.env 中添加：\n"
            "    LANGCHAIN_TRACING_V2=true\n"
            "    LANGCHAIN_API_KEY=lsv2_..."
        )
        _status = "disabled"
        return _status

    try:
        # 验证 langsmith 包（langchain-core 已包含 tracer）
        import langsmith  # type: ignore
        client = langsmith.Client(api_key=api_key)
        logger.info(
            f"[LangSmith] ✅ Tracing 已激活\n"
            f"  Project : {project}\n"
            f"  Endpoint: {os.environ.get('LANGCHAIN_ENDPOINT', 'https://api.smith.langchain.com')}\n"
            f"  Dashboard: https://smith.langchain.com/projects/p/{project}"
        )
        _status = "active"
    except ImportError:
        # langsmith package not explicitly installed, but env vars still work via langchain-core
        logger.info(
            f"[LangSmith] ✅ Tracing 环境变量已设置（project={project}）\n"
            f"  注: 安装 `pip install langsmith` 可获得额外调试功能"
        )
        _status = "active"
    except Exception as exc:
        logger.warning(f"[LangSmith] ⚠️ 初始化失败: {exc}")
        _status = "error"

    return _status


def get_status() -> dict:
    """返回当前 tracing 状态（用于健康检查 API）。"""
    return {
        "status": _status,
        "enabled": _status == "active",
        "project": os.environ.get("LANGCHAIN_PROJECT", "Koto"),
        "tracing_v2": os.environ.get("LANGCHAIN_TRACING_V2", "false"),
    }


def add_feedback(
    run_id: str,
    score: float,
    comment: Optional[str] = None,
    key: str = "user_rating",
) -> bool:
    """
    向 LangSmith 提交用户反馈（用于 RLHF / 评估）。

    参数:
        run_id  : LangSmith run ID（从 trace callback 中获取）
        score   : 0.0 ~ 1.0（1.0 = 满意）
        comment : 可选的文字反馈
        key     : 反馈维度名称（默认 "user_rating"）

    返回: True = 提交成功
    """
    if _status != "active":
        return False
    try:
        import langsmith  # type: ignore
        client = langsmith.Client()
        client.create_feedback(
            run_id=run_id,
            key=key,
            score=score,
            comment=comment or "",
        )
        logger.debug(f"[LangSmith] 反馈已提交: run_id={run_id}, score={score}")
        return True
    except Exception as exc:
        logger.warning(f"[LangSmith] 提交反馈失败: {exc}")
        return False
