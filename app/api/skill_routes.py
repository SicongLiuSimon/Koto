"""
skill_routes.py — Skill CRUD & MCP 导出 API Blueprint
======================================================
挂载前缀: /api/skills

端点列表:
  GET    /api/skills                  列出所有 Skill（支持 tag/search 过滤）
  POST   /api/skills                  创建自定义 Skill
  GET    /api/skills/<id>             获取单个 Skill 详情
  PUT    /api/skills/<id>             更新 Skill
  DELETE /api/skills/<id>             删除 Skill（仅自定义）
  POST   /api/skills/<id>/enable      启用 / 禁用 Skill
  POST   /api/skills/<id>/record      从会话提取 Skill（触发 SkillRecorder）
  GET    /api/skills/mcp              以 MCP 工具格式导出所有启用的 Skill
  GET    /api/skills/stats            每个 Skill 的调用成本统计
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys as _sys
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

skill_bp = Blueprint("skills", __name__, url_prefix="/api/skills")

# ── 路径 ──────────────────────────────────────────────────────────────────────


def _get_base_dir() -> Path:
    if getattr(_sys, 'frozen', False):
        return Path(_sys.executable).parent
    return Path(__file__).resolve().parents[2]


_BASE_DIR = _get_base_dir()
_SKILLS_DIR = str(_BASE_DIR / "config" / "skills")


# ── 懒加载辅助 ────────────────────────────────────────────────────────────────

def _sm():
    from app.core.skills.skill_manager import SkillManager
    return SkillManager


def _schema():
    from app.core.skills.skill_schema import SkillDefinition, InputVariable, OutputSpec
    return SkillDefinition, InputVariable, OutputSpec


def _recorder():
    from app.core.skills.skill_recorder import SkillRecorder
    return SkillRecorder


def _binding_manager():
    from app.core.skills.skill_trigger_binding import get_skill_binding_manager
    return get_skill_binding_manager()


def _tracer():
    from app.core.learning.shadow_tracer import ShadowTracer
    return ShadowTracer


def _token_tracker():
    import sys
    _wb = str(_BASE_DIR / "web")
    if _wb not in sys.path:
        sys.path.insert(0, _wb)
    import token_tracker
    return token_tracker


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skills  —  列出所有 Skill
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("", methods=["GET"])
def list_skills():
    """
    查询参数:
      tag     - 按 tag 过滤 (可多次传入)
      search  - 按 name/description 模糊搜索
      enabled - "true"/"false" 过滤启用状态
    """
    tag_filter = request.args.getlist("tag")
    search = request.args.get("search", "").strip().lower()
    enabled_filter = request.args.get("enabled")

    try:
        sm = _sm()
        all_skills = sm.list_skills()  # 返回 List[Dict]，含 name/category/icon/enabled 等 UI 字段

        # 过滤
        result = []
        for s in all_skills:
            if tag_filter and not any(t in s.get("tags", []) for t in tag_filter):
                continue
            if search and search not in s.get("name", "").lower() and search not in s.get("description", "").lower():
                continue
            if enabled_filter == "true" and not s.get("enabled", False):
                continue
            if enabled_filter == "false" and s.get("enabled", False):
                continue
            result.append(s)

        return jsonify({
            "success": True,
            "count": len(result),
            "skills": result,
        })
    except Exception as e:
        logger.error(f"[skills] list error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skills  —  创建自定义 Skill
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("", methods=["POST"])
def create_skill():
    """
    请求体 (JSON):
    {
      "id": str (可选，自动从 name 生成),
      "name": str,
      "description": str,
      "system_prompt": str,
      "tags": [str, ...],
      "input_variables": [{"name": str, "description": str, "required": bool, "example": str}, ...],
      "output_spec": {"format": str, "max_length": int}  (可选)
    }
    """
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "error": "name 不能为空"}), 400

    try:
        SkillDefinition, InputVariable, OutputSpec = _schema()

        # 构建输入变量
        raw_inputs = data.get("input_variables", [])
        if not raw_inputs:
            raw_inputs = [{"name": "input", "description": "用户输入", "required": True}]
        input_vars = [
            InputVariable(
                name=iv.get("name", "input"),
                description=iv.get("description", ""),
                required=iv.get("required", True),
                example=iv.get("example", ""),
                type=iv.get("type", "string"),
            )
            for iv in raw_inputs
        ]

        # 输出规格
        raw_out = data.get("output_spec", {})
        out_spec = OutputSpec(
            format=raw_out.get("format", "text"),
            max_chars=int(raw_out.get("max_length", raw_out.get("max_chars", 4000))),
        )

        # Skill ID
        from app.core.skills.skill_recorder import _make_skill_id
        skill_id = data.get("id") or _make_skill_id(name)

        sd = SkillDefinition(
            id=skill_id,
            name=name,
            icon=data.get("icon", "🤖"),
            category=data.get("category", "custom"),
            description=data.get("description", ""),
            version="1.0.0",
            author=data.get("author", "user"),
            tags=data.get("tags", ["general"]),
            input_variables=input_vars,
            system_prompt_template=data.get("system_prompt", f"你是一个专注于「{name}」任务的 AI 助手。"),
            output_spec=out_spec,
        )

        from app.core.skills.skill_recorder import SkillRecorder
        sid = SkillRecorder.save_and_register(sd, overwrite=False)
        return jsonify({"success": True, "skill_id": sid, "skill": sd.to_dict()}), 201

    except FileExistsError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        logger.error(f"[skills] create error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skills/mcp  —  MCP 工具导出（注意：路由顺序在 <id> 之前）
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/mcp", methods=["GET"])
def export_mcp_tools():
    """导出所有启用 Skill 的 MCP 兼容工具描述列表。"""
    try:
        sm = _sm()
        tools = sm.list_mcp_tools()  # 只返回启用的
        return jsonify({
            "success": True,
            "schema_version": "1.0",
            "tools": tools,
            "count": len(tools),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skills/stats  —  每 Skill 的成本统计
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/stats", methods=["GET"])
def skill_stats():
    """聚合 token 成本 + 影子记录数量 per skill。"""
    try:
        tt = _token_tracker()
        token_stats = tt.get_skill_stats()

        try:
            tracer = _tracer()
            trace_counts = tracer.get_counts()
        except Exception:
            trace_counts = {}

        merged = {}
        all_ids = set(list(token_stats.keys()) + list(trace_counts.keys()))
        for sid in all_ids:
            ts = token_stats.get(sid, {})
            merged[sid] = {
                "total_calls": ts.get("total_calls", 0),
                "total_tokens": ts.get("total_tokens", 0),
                "cost_cny": ts.get("cost_cny", 0.0),
                "approved_traces": trace_counts.get(sid, 0),
            }

        return jsonify({"success": True, "stats": merged})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skills/<id>  —  获取单个 Skill
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/<skill_id>", methods=["GET"])
def get_skill(skill_id: str):
    try:
        sm = _sm()
        sd = sm.get_definition(skill_id)
        if sd is None:
            return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404
        return jsonify({"success": True, "skill": sd.to_dict()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# PUT /api/skills/<id>  —  更新 Skill
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/<skill_id>", methods=["PUT"])
def update_skill(skill_id: str):
    """
    支持部分更新：只需传入要修改的字段。
    可更新: name, description, system_prompt, tags, input_variables, output_spec, examples
    """
    data = request.json or {}
    skill_file = os.path.join(_SKILLS_DIR, f"{skill_id}.json")

    if not os.path.exists(skill_file):
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在或非自定义 Skill"}), 404

    try:
        with open(skill_file, "r", encoding="utf-8") as f:
            existing = json.load(f)

        # 允许更新的字段
        updatable = ["name", "description", "system_prompt", "tags", "input_variables",
                     "output_spec", "examples", "enabled", "author"]
        for field in updatable:
            if field in data:
                existing[field] = data[field]

        with open(skill_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        # 重新注册到 SkillManager
        SkillDefinition, _, _ = _schema()
        sd = SkillDefinition.from_dict(existing)
        _sm().register_custom(sd)

        return jsonify({"success": True, "skill": existing})
    except Exception as e:
        logger.error(f"[skills] update error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/skills/<id>  —  删除 Skill（仅自定义）
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/<skill_id>", methods=["DELETE"])
def delete_skill(skill_id: str):
    skill_file = os.path.join(_SKILLS_DIR, f"{skill_id}.json")
    if not os.path.exists(skill_file):
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在或非自定义 Skill"}), 404

    try:
        os.remove(skill_file)
        # 从 SkillManager registry 移除（如果支持）
        try:
            sm = _sm()
            if hasattr(sm, "_def_registry"):
                sm._def_registry.pop(skill_id, None)
        except Exception:
            pass
        return jsonify({"success": True, "deleted": skill_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skills/<id>/enable  —  启用 / 禁用 Skill
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/<skill_id>/enable", methods=["POST"])
def toggle_skill(skill_id: str):
    """
    请求体: { "enabled": true | false }
    """
    data = request.json or {}
    enabled = data.get("enabled", True)
    skill_file = os.path.join(_SKILLS_DIR, f"{skill_id}.json")

    if not os.path.exists(skill_file):
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    try:
        with open(skill_file, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing["enabled"] = bool(enabled)
        with open(skill_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        return jsonify({"success": True, "skill_id": skill_id, "enabled": enabled})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skills/<id>/record  —  从会话自动提取 Skill
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/<skill_id>/toggle", methods=["POST"])
def toggle_skill_v2(skill_id: str):
    """
    前端专用：启用 / 禁用 Skill（内置 + 自定义均支持）。
    请求体: { "enabled": true | false }
    """
    data = request.json or {}
    enabled = bool(data.get("enabled", True))
    try:
        sm = _sm()
        ok = sm.set_enabled(skill_id, enabled)
        if not ok:
            return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404
        return jsonify({"success": True, "skill_id": skill_id, "enabled": enabled})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@skill_bp.route("/<skill_id>/prompt", methods=["POST"])
def save_skill_prompt(skill_id: str):
    """
    前端专用：保存用户自定义的 Skill Prompt。
    请求体: { "prompt": str }
    """
    data = request.json or {}
    prompt = data.get("prompt", "")
    try:
        sm = _sm()
        ok = sm.update_prompt(skill_id, prompt)
        if not ok:
            return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404
        return jsonify({"success": True, "skill_id": skill_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@skill_bp.route("/<skill_id>/reset", methods=["POST"])
def reset_skill_prompt(skill_id: str):
    """
    前端专用：将 Skill Prompt 恢复为内置默认值。
    """
    try:
        sm = _sm()
        ok = sm.reset_prompt(skill_id)
        if not ok:
            return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404
        return jsonify({"success": True, "skill_id": skill_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@skill_bp.route("/<skill_id>/record", methods=["POST"])
def record_from_session(skill_id: str):
    """
    从对话会话自动提取/更新 SkillDefinition。

    请求体:
    {
      "session_id": str,
      "skill_name": str (可选，默认用 skill_id),
      "description": str (可选),
      "overwrite": bool (默认 false)
    }
    """
    data = request.json or {}
    session_id = data.get("session_id", "")
    skill_name = data.get("skill_name") or skill_id
    description = data.get("description", "")
    overwrite = data.get("overwrite", False)

    if not session_id:
        return jsonify({"success": False, "error": "session_id 不能为空"}), 400

    try:
        SkillRecorder = _recorder()
        sd = SkillRecorder.from_conversation(
            session_id=session_id,
            skill_name=skill_name,
            description=description,
        )
        # 强制使用传入的 skill_id
        sd.id = skill_id

        saved_id = SkillRecorder.save_and_register(sd, overwrite=overwrite)
        return jsonify({
            "success": True,
            "skill_id": saved_id,
            "skill": sd.to_dict(),
            "source_session": session_id,
        })
    except FileExistsError as e:
        return jsonify({"success": False, "error": str(e), "hint": "传 overwrite:true 强制覆盖"}), 409
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error(f"[skills/record] 错误: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skills/bindings  —  列出技能绑定
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/bindings", methods=["GET"])
def list_bindings():
    """列出所有技能绑定，支持按 skill_id / binding_type 过滤。"""
    skill_id = request.args.get("skill_id")
    binding_type = request.args.get("binding_type")

    try:
        bindings = _binding_manager().list_bindings(
            skill_id=skill_id,
            binding_type=binding_type,
        )
        return jsonify({
            "success": True,
            "count": len(bindings),
            "bindings": [binding.to_dict() for binding in bindings],
        })
    except Exception as e:
        logger.error(f"[skills/bindings] list error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@skill_bp.route("/bindings/bootstrap", methods=["POST"])
def bootstrap_bindings():
    """Seed curated built-in bindings for first-run automation."""
    data = request.json or {}
    force = bool(data.get("force", False))

    try:
        result = _binding_manager().ensure_recommended_bindings(force=force)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"[skills/bindings/bootstrap] error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skills/<id>/bindings/intent  —  创建意图绑定
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/<skill_id>/bindings/intent", methods=["POST"])
def bind_skill_intent(skill_id: str):
    """创建一个基于关键词匹配的技能意图绑定。"""
    data = request.json or {}
    patterns = data.get("patterns") or data.get("intent_patterns") or []
    patterns = [str(pattern).strip() for pattern in patterns if str(pattern).strip()]
    if not patterns:
        return jsonify({"success": False, "error": "patterns 不能为空"}), 400

    try:
        sm = _sm()
        if not sm.get_definition(skill_id):
            return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

        binding = _binding_manager().bind_intent(
            skill_id=skill_id,
            intent_patterns=patterns,
            auto_disable_after_turns=int(data.get("auto_disable_after_turns", 3)),
        )
        return jsonify({"success": True, "binding": binding.to_dict()}), 201
    except Exception as e:
        logger.error(f"[skills/bindings/intent] error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skills/<id>/bindings/trigger  —  创建触发器绑定
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/<skill_id>/bindings/trigger", methods=["POST"])
def bind_skill_trigger(skill_id: str):
    """创建一个调度触发器绑定，并同步注册到 TriggerRegistry。"""
    data = request.json or {}
    trigger_type = str(data.get("trigger_type") or data.get("type") or "").strip()
    if not trigger_type:
        return jsonify({"success": False, "error": "trigger_type 不能为空"}), 400

    try:
        sm = _sm()
        if not sm.get_definition(skill_id):
            return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

        binding = _binding_manager().bind_trigger(
            skill_id=skill_id,
            trigger_type=trigger_type,
            trigger_config=data.get("config") or {},
            mode=data.get("mode", "execute"),
            job_payload=data.get("job_payload") or {},
            name=data.get("name"),
        )
        return jsonify({"success": True, "binding": binding.to_dict()}), 201
    except Exception as e:
        logger.error(f"[skills/bindings/trigger] error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skills/bindings/<id>/toggle  —  启用 / 禁用绑定
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/bindings/<binding_id>/toggle", methods=["POST"])
def toggle_binding(binding_id: str):
    data = request.json or {}
    enabled = bool(data.get("enabled", True))

    try:
        manager = _binding_manager()
        binding = manager.get(binding_id)
        if not binding:
            return jsonify({"success": False, "error": f"Binding '{binding_id}' 不存在"}), 404

        manager.enable(binding_id, enabled)
        updated = manager.get(binding_id)
        return jsonify({"success": True, "binding": updated.to_dict() if updated else None})
    except Exception as e:
        logger.error(f"[skills/bindings/toggle] error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/skills/bindings/<id>  —  删除绑定
# ══════════════════════════════════════════════════════════════════════════════

@skill_bp.route("/bindings/<binding_id>", methods=["DELETE"])
def delete_binding(binding_id: str):
    try:
        removed = _binding_manager().remove(binding_id)
        if not removed:
            return jsonify({"success": False, "error": f"Binding '{binding_id}' 不存在"}), 404
        return jsonify({"success": True, "deleted": binding_id})
    except Exception as e:
        logger.error(f"[skills/bindings/delete] error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
