# -*- coding: utf-8 -*-
"""
macro_routes.py — 宏录制主动建议 API
======================================
挂载前缀: /api/macro

端点列表：
  GET  /api/macro/pending           前端轮询，获取待确认的宏建议
  POST /api/macro/confirm/<id>      用户确认 → 创建专属 Skill 按钮
                                      body: { "name": "按钮名称" }
  POST /api/macro/dismiss/<id>      用户忽略建议
  GET  /api/macro/history           全部建议（调试用）
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

macro_bp = Blueprint("macro", __name__, url_prefix="/api/macro")


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _get_recorder():
    from app.core.monitoring.macro_recorder import get_macro_recorder
    return get_macro_recorder()


def _ok(data=None, **kw):
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(kw)
    return jsonify(body)


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/macro/pending
# ══════════════════════════════════════════════════════════════════════════════

@macro_bp.get("/pending")
def macro_pending():
    """返回所有 status='pending' 的宏建议（前端每次响应后轻量轮询）。"""
    try:
        items = _get_recorder().pending()
        return _ok(items)
    except Exception as exc:
        logger.exception("[macro/pending] error")
        return _err(str(exc), 500)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/macro/confirm/<id>
# ══════════════════════════════════════════════════════════════════════════════

@macro_bp.post("/confirm/<suggestion_id>")
def macro_confirm(suggestion_id: str):
    """
    用户确认宏建议，将其固化为一个专属 Skill 按钮。

    Body (JSON):
        { "name": "一键总结文档" }

    Returns:
        { "ok": true, "data": { "skill_id": "macro_xxxx" } }
    """
    try:
        body       = request.get_json(force=True, silent=True) or {}
        skill_name = (body.get("name") or "").strip()

        if not skill_name:
            return _err("name 不能为空")
        if len(skill_name) > 50:
            return _err("name 不能超过 50 个字符")

        skill_id = _get_recorder().confirm(suggestion_id, skill_name)
        if skill_id is None:
            return _err("未找到该建议，或已被处理", 404)

        return _ok({"skill_id": skill_id})
    except Exception as exc:
        logger.exception("[macro/confirm] error")
        return _err(str(exc), 500)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/macro/dismiss/<id>
# ══════════════════════════════════════════════════════════════════════════════

@macro_bp.post("/dismiss/<suggestion_id>")
def macro_dismiss(suggestion_id: str):
    """用户忽略宏建议（不再就此模式打扰）。"""
    try:
        ok = _get_recorder().dismiss(suggestion_id)
        if not ok:
            return _err("未找到该建议，或已被处理", 404)
        return _ok()
    except Exception as exc:
        logger.exception("[macro/dismiss] error")
        return _err(str(exc), 500)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/macro/history
# ══════════════════════════════════════════════════════════════════════════════

@macro_bp.get("/history")
def macro_history():
    """全部宏建议（含已确认/已忽略），供调试面板查看。"""
    try:
        recorder  = _get_recorder()
        with recorder._lock:
            all_items = [s.to_dict() for s in recorder._suggestions]
        return _ok(all_items)
    except Exception as exc:
        logger.exception("[macro/history] error")
        return _err(str(exc), 500)
