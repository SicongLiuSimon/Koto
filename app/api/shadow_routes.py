# -*- coding: utf-8 -*-
"""Koto Shadow Routes — 影子追踪 REST API
=======================================
挂载前缀: /api/shadow

端点列表:
  GET  /api/shadow/status           — 启用状态 + 统计摘要
  POST /api/shadow/toggle           — 开启 / 关闭
  GET  /api/shadow/observations     — 全部观察数据（调试用）
  GET  /api/shadow/pending          — 待展示的主动消息（轮询）
  GET  /api/shadow/stream           — SSE 实时推送（EventSource 长连接）
  POST /api/shadow/dismiss/<id>     — 关闭单条消息
  POST /api/shadow/dismiss-all      — 全部关闭
  POST /api/shadow/tick             — 手动触发一次主动检查（测试用）
  POST /api/shadow/reset            — 清空观察数据
"""

from __future__ import annotations

import json as _json
import logging
import queue as _queue

from flask import Blueprint, Response, jsonify, request, stream_with_context

logger = logging.getLogger(__name__)

shadow_bp = Blueprint("shadow", __name__, url_prefix="/api/shadow")


def _ok(data=None, **kw):
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(kw)
    return jsonify(body)


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


# ── helpers ───────────────────────────────────────────────────────────────────


def _get_watcher():
    from app.core.monitoring.shadow_watcher import get_shadow_watcher

    return get_shadow_watcher()


def _get_agent():
    from app.core.agent.proactive_agent import get_proactive_agent

    return get_proactive_agent()


# ── 端点 ──────────────────────────────────────────────────────────────────────


@shadow_bp.get("/status")
def shadow_status():
    """启用状态 + 观察摘要。"""
    try:
        obs = _get_watcher().get_observations()
        topics = sorted(obs.get("topics", {}).items(), key=lambda x: -x[1])[:5]
        open_tasks = _get_watcher().get_open_tasks()
        pending = _get_agent().pending()

        # 对话风格摘要
        cs = obs.get("conversation_style", {})
        cs_samples = cs.get("samples", 0)
        style_summary = {
            "avg_query_len": cs.get("avg_query_len", 0),
            "polite_ratio": round(cs.get("polite_ratio", 0.5), 2),
            "context_ratio": round(cs.get("context_ratio", 0.0), 2),
            "explicit_pref_ratio": round(cs.get("explicit_pref_ratio", 0.0), 2),
            "multistep_ratio": round(cs.get("multistep_ratio", 0.0), 2),
            "samples": cs_samples,
        } if cs_samples > 0 else None

        # 任务风格摘要：top-3 任务类型 + top-3 输出格式
        ts = obs.get("task_style", {})
        task_types_top = sorted(ts.get("task_types", {}).items(), key=lambda x: -x[1])[:3]
        output_fmt_top = sorted(ts.get("output_format", {}).items(), key=lambda x: -x[1])[:3]
        task_summary = {
            "top_task_types": [{"type": k, "count": v} for k, v in task_types_top],
            "top_output_formats": [{"format": k, "count": v} for k, v in output_fmt_top],
            "samples": ts.get("samples", 0),
        } if ts.get("samples", 0) > 0 else None

        return _ok({
            "enabled": obs.get("enabled", True),
            "total_observations": obs.get("total_observations", 0),
            "last_seen": obs.get("last_seen"),
            "streak_days": obs.get("streak", {}).get("days", 0),
            "top_topics": [{"topic": k, "count": v} for k, v in topics],
            "style_summary": style_summary,
            "task_summary": task_summary,
            "open_tasks_count": len(open_tasks),
            "pending_messages": len(pending),
        })
    except Exception as exc:
        logger.exception("[shadow/status] error")
        return _err(str(exc), 500)


@shadow_bp.post("/toggle")
def shadow_toggle():
    """
    Body: { "enabled": true | false }
    或者 Body 为空时自动反转当前状态。
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        watcher = _get_watcher()
        if "enabled" in body:
            new_state = bool(body["enabled"])
        else:
            new_state = not watcher.enabled
        watcher.set_enabled(new_state)
        return _ok({"enabled": new_state})
    except Exception as exc:
        logger.exception("[shadow/toggle] error")
        return _err(str(exc), 500)


@shadow_bp.get("/observations")
def shadow_observations():
    """完整观察数据（适合调试面板）。"""
    try:
        obs = _get_watcher().get_observations()
        return _ok(obs)
    except Exception as exc:
        return _err(str(exc), 500)


@shadow_bp.get("/pending")
def shadow_pending():
    """前端轮询获取待展示的主动消息。"""
    try:
        msgs = _get_agent().pending()
        return _ok(msgs)
    except Exception as exc:
        return _err(str(exc), 500)


@shadow_bp.get("/stream")
def shadow_stream():
    """
    SSE 实时推送端点。客户端连接后，新的主动消息会在入队时立即推送，无需轮询。

    前端用法:
        const es = new EventSource('/api/shadow/stream');
        es.onmessage = (e) => {
            const msg = JSON.parse(e.data);
            // msg.type: greeting | follow_up | suggestion | reminder | failed_retry
            showProactiveNotification(msg);
        };

    协议:
      - 数据帧:   data: <JSON 消息体>\n\n
      - 心跳帧:   : keepalive\n\n  （每 30 秒发送一次，防止连接超时）
    """
    agent = _get_agent()
    sub_q = agent.subscribe_sse()

    def _generate():
        try:
            while True:
                try:
                    msg = sub_q.get(timeout=30)
                    yield f"data: {_json.dumps(msg, ensure_ascii=False)}\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            agent.unsubscribe_sse(sub_q)

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@shadow_bp.post("/dismiss/<msg_id>")
def shadow_dismiss(msg_id: str):
    """用户关闭单条主动消息。"""
    try:
        _get_agent().dismiss(msg_id)
        return _ok({"dismissed": msg_id})
    except Exception as exc:
        return _err(str(exc), 500)


@shadow_bp.post("/dismiss-all")
def shadow_dismiss_all():
    """全部关闭。"""
    try:
        _get_agent().dismiss_all()
        return _ok({"dismissed": "all"})
    except Exception as exc:
        return _err(str(exc), 500)


@shadow_bp.post("/tick")
def shadow_tick():
    """
    手动触发一次主动消息检查（测试 / 调试用）。
    可传 { "force": true } 跳过冷却时间检测。
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        force = bool(body.get("force", False))

        agent = _get_agent()
        if force:
            # 清空冷却记录强制触发
            agent._last_type_time.clear()

        # 尝试获取 LLM 函数（可选）
        llm_fn = None
        try:
            from google.genai import types as _types

            from web.app import client

            def _llm(prompt: str) -> str:
                resp = client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents=prompt,
                    config=_types.GenerateContentConfig(
                        temperature=0.7, max_output_tokens=80
                    ),
                )
                return resp.text or ""

            llm_fn = _llm
        except Exception:
            pass  # run without LLM

        agent.tick(llm_fn=llm_fn)
        pending = agent.pending()
        return _ok({"pending_count": len(pending), "messages": pending})
    except Exception as exc:
        logger.exception("[shadow/tick] error")
        return _err(str(exc), 500)


@shadow_bp.get("/open-tasks")
def shadow_open_tasks():
    """列出开放任务列表。"""
    try:
        tasks = _get_watcher().get_open_tasks()
        return _ok(tasks)
    except Exception as exc:
        return _err(str(exc), 500)


@shadow_bp.post("/dismiss-task/<task_id>")
def shadow_dismiss_task(task_id: str):
    """标记某个开放任务为已完成。"""
    try:
        _get_watcher().dismiss_task(task_id)
        return _ok({"dismissed_task": task_id})
    except Exception as exc:
        return _err(str(exc), 500)


@shadow_bp.post("/reset")
def shadow_reset():
    """清空所有观察数据（保留 enabled 状态）。"""
    try:
        _get_watcher().reset()
        return _ok({"reset": True})
    except Exception as exc:
        return _err(str(exc), 500)
