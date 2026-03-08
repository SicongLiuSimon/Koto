# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║       Koto  ─  Skill Marketplace API Blueprint                   ║
╚══════════════════════════════════════════════════════════════════╝

挂载前缀: /api/skillmarket

端点列表
────────
  GET    /api/skillmarket/catalog           获取完整 Skill 目录（内置+自定义）
  GET    /api/skillmarket/library           用户 Skill 库（已安装/已创建）
  GET    /api/skillmarket/featured          推荐精选 Skill 列表
  GET    /api/skillmarket/search            搜索 Skill（名称/描述/标签/作者）

  POST   /api/skillmarket/auto-build        用自然语言描述自动生成 Skill
  POST   /api/skillmarket/preview-prompt    实时预览生成的 Prompt（不保存）
  POST   /api/skillmarket/from-session      从对话会话提取 Skill 风格

  POST   /api/skillmarket/install           安装一个 Skill（来自 JSON body 或 .kotosk）
  POST   /api/skillmarket/uninstall/<id>    卸载自定义 Skill
  POST   /api/skillmarket/toggle/<id>       启用 / 禁用 Skill
  PUT    /api/skillmarket/edit/<id>         编辑 Skill（名称/描述/prompt）
  POST   /api/skillmarket/duplicate/<id>    克隆一个 Skill

  GET    /api/skillmarket/export/<id>       导出单个 Skill 为 .kotosk 文件
  GET    /api/skillmarket/export-pack       批量导出多个 Skill 为 .kotosk 包
  POST   /api/skillmarket/import            从上传的 .kotosk 文件导入 Skill

  POST   /api/skillmarket/rate/<id>         对 Skill 评分（本地统计）
  GET    /api/skillmarket/stats             全局使用统计
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request, send_file

logger = logging.getLogger(__name__)

marketplace_bp = Blueprint("skillmarket", __name__, url_prefix="/api/skillmarket")

# ── 路径常量 ──────────────────────────────────────────────────────────────────
import sys as _sys
_BASE_DIR = (Path(_sys.executable).parent if getattr(_sys, 'frozen', False)
             else Path(__file__).resolve().parents[2])   # project root
_SKILLS_DIR   = _BASE_DIR / "config" / "skills"
_RATINGS_FILE = _BASE_DIR / "config" / "skill_ratings.json"
_PACKS_DIR    = _BASE_DIR / "config" / "skill_packs"

_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
_PACKS_DIR.mkdir(parents=True, exist_ok=True)


# ── 懒加载辅助 ────────────────────────────────────────────────────────────────
def _sm():
    from app.core.skills.skill_manager import SkillManager
    return SkillManager

def _schema():
    from app.core.skills.skill_schema import SkillDefinition, InputVariable, OutputSpec
    return SkillDefinition, InputVariable, OutputSpec

def _auto_builder():
    from app.core.skills.skill_auto_builder import SkillAutoBuilder, SkillPackager
    return SkillAutoBuilder, SkillPackager

def _recorder():
    from app.core.skills.skill_recorder import SkillRecorder
    return SkillRecorder


# ── 评分持久化 ────────────────────────────────────────────────────────────────
def _load_ratings() -> Dict[str, Any]:
    if _RATINGS_FILE.exists():
        try:
            return json.loads(_RATINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_ratings(data: Dict):
    _RATINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _get_skill_rating(skill_id: str) -> Dict:
    ratings = _load_ratings()
    return ratings.get(skill_id, {"avg": 0.0, "count": 0, "votes": []})


# ── Skill 富化（为前端添加 rating、is_builtin、is_installed 等字段）────────────
def _enrich_skill(skill_dict: Dict, is_builtin: bool = False) -> Dict:
    skill_id = skill_dict.get("id", "")
    rating = _get_skill_rating(skill_id)
    is_installed = (
        is_builtin
        or (_SKILLS_DIR / f"{skill_id}.json").exists()
    )
    return {
        **skill_dict,
        "is_builtin": is_builtin,
        "is_installed": is_installed,
        "rating": rating.get("avg", 0.0),
        "rating_count": rating.get("count", 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/catalog
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/catalog", methods=["GET"])
def get_catalog():
    """
    返回完整 Skill 目录。
    查询参数:
      category - 按分类过滤 (behavior/style/domain/custom)
      tag      - 按标签过滤（可多次传入）
      author   - 按作者过滤
    """
    category_filter = request.args.get("category", "").strip().lower()
    tag_filter      = request.args.getlist("tag")
    author_filter   = request.args.get("author", "").strip().lower()

    try:
        sm = _sm()
        sm._ensure_init()

        all_skills: List[Dict] = []

        # 内置 Skill（从 SkillManager 读取）
        for skill_id, skill_def in sm._def_registry.items():
            d = skill_def.to_dict()
            # 同步启用状态
            leg = sm._registry.get(skill_id, {})
            d["enabled"] = leg.get("enabled", skill_def.enabled)
            all_skills.append(_enrich_skill(d, is_builtin=(d.get("author") == "builtin")))

        # 安全过滤
        result = []
        for s in all_skills:
            if category_filter and s.get("category", "") != category_filter:
                continue
            if tag_filter:
                skill_tags = [t.lower() for t in s.get("tags", [])]
                if not any(t.lower() in skill_tags for t in tag_filter):
                    continue
            if author_filter and s.get("author", "").lower() != author_filter:
                continue
            result.append(s)

        # 按分类排序：behavior > style > domain > custom
        cat_order = {"behavior": 0, "style": 1, "domain": 2, "custom": 3}
        result.sort(key=lambda x: cat_order.get(x.get("category", ""), 99))

        return jsonify({
            "success": True,
            "total": len(result),
            "skills": result,
        })
    except Exception as e:
        logger.exception("[skillmarket/catalog]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/library
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/library", methods=["GET"])
def get_library():
    """返回用户自己创建/安装的 Skill 库"""
    try:
        skills = []
        for skill_file in sorted(_SKILLS_DIR.glob("*.json")):
            try:
                data = json.loads(skill_file.read_text(encoding="utf-8"))
                enriched = _enrich_skill(data, is_builtin=False)
                enriched["file_name"] = skill_file.name
                skills.append(enriched)
            except Exception as e:
                logger.warning(f"[library] 解析 {skill_file.name} 失败: {e}")

        return jsonify({
            "success": True,
            "total": len(skills),
            "skills": skills,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/featured
# ══════════════════════════════════════════════════════════════════════════════

# 精选推荐列表（静态配置 + 动态评分加权）
_FEATURED_IDS = [
    "step_by_step", "teaching_mode", "strict_mode",
    "code_best_practices", "creative_writing", "concise_mode",
    "professional_tone", "emoji_assist", "data_analysis",
]

@marketplace_bp.route("/featured", methods=["GET"])
def get_featured():
    """返回精选推荐 Skill 列表"""
    try:
        sm = _sm()
        sm._ensure_init()

        featured = []
        for skill_id in _FEATURED_IDS:
            skill_def = sm._def_registry.get(skill_id)
            if skill_def:
                d = skill_def.to_dict()
                leg = sm._registry.get(skill_id, {})
                d["enabled"] = leg.get("enabled", skill_def.enabled)
                featured.append(_enrich_skill(d, is_builtin=True))

        return jsonify({"success": True, "skills": featured})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/search
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/search", methods=["GET"])
def search_skills():
    """
    全文搜索 Skill（名称/描述/标签/作者/intent_description）。
    查询参数: q=<搜索词>
    """
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify({"success": False, "error": "参数 q 不能为空"}), 400

    try:
        sm = _sm()
        sm._ensure_init()

        results = []
        for skill_id, skill_def in sm._def_registry.items():
            d = skill_def.to_dict()
            # 计算匹配度
            score = 0
            search_fields = [
                (d.get("name", ""), 3),
                (d.get("description", ""), 2),
                (" ".join(d.get("tags", [])), 1),
                (d.get("author", ""), 1),
                (d.get("intent_description", ""), 1),
            ]
            for text, weight in search_fields:
                if q in text.lower():
                    score += weight
            if score > 0:
                leg = sm._registry.get(skill_id, {})
                d["enabled"] = leg.get("enabled", skill_def.enabled)
                enriched = _enrich_skill(d, is_builtin=(d.get("author") == "builtin"))
                enriched["_score"] = score
                results.append(enriched)

        results.sort(key=lambda x: x.get("_score", 0), reverse=True)
        for r in results:
            r.pop("_score", None)

        return jsonify({"success": True, "query": q, "total": len(results), "skills": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/auto-build
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/auto-build", methods=["POST"])
def auto_build():
    """
    用自然语言描述自动生成 Skill 并保存。

    请求体:
    {
      "name": str,                 技能名称（必填）
      "description": str,          风格描述（必填）
      "icon": str,                 emoji 图标（可选，默认🎭）
      "category": str,             分类（可选，默认 style）
      "author": str,               作者（可选，默认 user）
      "tags": [str, ...],          标签（可选）
      "enabled": bool,             是否立即启用（可选，默认 false）
      "save": bool,                是否保存到 Skill 库（默认 true）
      "formality": float,          手动覆盖维度（0-1，可选）
      "verbosity": float,
      "empathy": float,
      "structure": float,
      "creativity": float,
      "positivity": float,
      "proactivity": float,
      "humor": float,
      "domain": str,
            "personalize": bool,        是否读取 user_profile/memory 做个性化（可选，默认 true）
    }
    """
    data = request.json or {}

    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()

    if not name:
        return jsonify({"success": False, "error": "name 不能为空"}), 400
    if not description:
        return jsonify({"success": False, "error": "description 不能为空"}), 400

    try:
        SkillAutoBuilder, SkillPackager = _auto_builder()
        personalize = bool(data.get("personalize", True))
        personalization_context = (
            SkillAutoBuilder.load_personalization_context()
            if personalize else None
        )
        personalization_applied = personalize and bool(
            (personalization_context or {}).get("communication_style")
            or (personalization_context or {}).get("memory_hints")
        )

        # 检查是否有手动维度覆盖
        manual_dims = {
            "formality", "verbosity", "empathy", "structure",
            "creativity", "positivity", "proactivity", "humor",
        }
        has_manual = any(k in data for k in manual_dims)

        if has_manual:
            skill = SkillAutoBuilder.from_style_config(
                name=name,
                description=description,
                formality=float(data.get("formality", 0.5)),
                verbosity=float(data.get("verbosity", 0.5)),
                empathy=float(data.get("empathy", 0.5)),
                structure=float(data.get("structure", 0.5)),
                creativity=float(data.get("creativity", 0.3)),
                positivity=float(data.get("positivity", 0.6)),
                proactivity=float(data.get("proactivity", 0.4)),
                humor=float(data.get("humor", 0.2)),
                domain=data.get("domain", "general"),
                icon=data.get("icon", "🎛️"),
                category=data.get("category", "style"),
                author=data.get("author", "user"),
                enabled=bool(data.get("enabled", False)),
                personalize=personalize,
                personalization_context=personalization_context,
            )
        elif data.get("use_ai", False):
            # AI 生成模式：调用 Gemini，失败自动降级为规则引擎
            skill = SkillAutoBuilder.from_ai_description(
                name=name,
                description=description,
                icon=data.get("icon", "🎭"),
                category=data.get("category", "style"),
                author=data.get("author", "user"),
                tags=data.get("tags"),
                enabled=bool(data.get("enabled", False)),
                personalize=personalize,
                personalization_context=personalization_context,
            )
        else:
            skill = SkillAutoBuilder.from_style_description(
                name=name,
                description=description,
                icon=data.get("icon", "🎭"),
                category=data.get("category", "style"),
                author=data.get("author", "user"),
                tags=data.get("tags"),
                enabled=bool(data.get("enabled", False)),
                personalize=personalize,
                personalization_context=personalization_context,
            )

        # 保存到 Skill 库
        if data.get("save", True):
            SkillRecorder = _recorder()
            overwrite = data.get("overwrite", False)
            try:
                SkillRecorder.save_and_register(skill, overwrite=overwrite)
            except FileExistsError:
                return jsonify({
                    "success": False,
                    "error": f"Skill '{skill.id}' 已存在，传 overwrite:true 覆盖",
                    "skill_id": skill.id,
                }), 409

        return jsonify({
            "success": True,
            "skill_id": skill.id,
            "skill": skill.to_dict(),
            "saved": data.get("save", True),
            "personalization_applied": personalization_applied,
        }), 201

    except Exception as e:
        logger.exception("[skillmarket/auto-build]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/preview-prompt
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/preview-prompt", methods=["POST"])
def preview_prompt():
    """
    实时预览自动生成的 Prompt（不保存 Skill）。
    前端可在用户输入时实时调用此接口展示预览。
    """
    data = request.json or {}
    name = (data.get("name") or "未命名技能").strip()
    description = (data.get("description") or "").strip()

    try:
        SkillAutoBuilder, _ = _auto_builder()
        personalize = bool(data.get("personalize", True))
        personalization_context = (
            SkillAutoBuilder.load_personalization_context()
            if personalize else None
        )
        personalization_applied = personalize and bool(
            (personalization_context or {}).get("communication_style")
            or (personalization_context or {}).get("memory_hints")
        )
        result = SkillAutoBuilder.preview_prompt(
            name=name,
            description=description,
            formality=float(data.get("formality", 0.5)),
            verbosity=float(data.get("verbosity", 0.5)),
            empathy=float(data.get("empathy", 0.5)),
            structure=float(data.get("structure", 0.5)),
            creativity=float(data.get("creativity", 0.3)),
            positivity=float(data.get("positivity", 0.6)),
            proactivity=float(data.get("proactivity", 0.4)),
            humor=float(data.get("humor", 0.2)),
            domain=data.get("domain", "general"),
            personalize=personalize,
            personalization_context=personalization_context,
        )
        return jsonify({"success": True, "personalization_applied": personalization_applied, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/from-session
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/from-session", methods=["POST"])
def from_session():
    """
    从对话会话自动提取 Skill 风格。
    请求体: { "session_id": str, "name": str, "description": str, "save": bool }
    """
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    name = (data.get("name") or "").strip()

    if not session_id:
        return jsonify({"success": False, "error": "session_id 不能为空"}), 400
    if not name:
        return jsonify({"success": False, "error": "name 不能为空"}), 400

    try:
        SkillAutoBuilder, _ = _auto_builder()
        skill = SkillAutoBuilder.from_conversation_history(
            session_id=session_id,
            name=name,
            description=data.get("description", ""),
            icon=data.get("icon", "💬"),
            category=data.get("category", "style"),
            author=data.get("author", "user"),
        )

        if data.get("save", True):
            SkillRecorder = _recorder()
            try:
                SkillRecorder.save_and_register(skill, overwrite=data.get("overwrite", False))
            except FileExistsError:
                return jsonify({
                    "success": False,
                    "error": f"Skill '{skill.id}' 已存在，传 overwrite:true 覆盖",
                }), 409

        return jsonify({
            "success": True,
            "skill_id": skill.id,
            "skill": skill.to_dict(),
            "source_session": session_id,
        }), 201

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.exception("[skillmarket/from-session]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/install
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/install", methods=["POST"])
def install_skill():
    """
    安装一个 Skill。支持两种方式：
    1. JSON body 包含完整 SkillDefinition
    2. multipart/form-data 上传 .kotosk 文件（自动解包）
    """
    try:
        SkillDefinition, _, _ = _schema()
        SkillRecorder = _recorder()

        # 方式 1：JSON body
        if request.is_json:
            data = request.json or {}
            if not data.get("id") or not data.get("name"):
                return jsonify({"success": False, "error": "id 和 name 不能为空"}), 400
            skill = SkillDefinition.from_dict(data)
            overwrite = data.pop("_overwrite", False)
            sid = SkillRecorder.save_and_register(skill, overwrite=overwrite)
            return jsonify({"success": True, "skill_id": sid, "skill": skill.to_dict()}), 201

        # 方式 2：文件上传
        file = request.files.get("file")
        if not file:
            return jsonify({"success": False, "error": "需要提供 JSON body 或上传 .kotosk 文件"}), 400

        filename = file.filename or ""
        if not filename.endswith(".kotosk"):
            return jsonify({"success": False, "error": "仅支持 .kotosk 文件"}), 400

        _, SkillPackager = _auto_builder()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".kotosk")
        file.save(tmp.name)
        tmp.close()

        try:
            manifest, skills = SkillPackager.unpack(tmp.name)
        finally:
            os.unlink(tmp.name)

        installed = []
        errors = []
        for skill in skills:
            try:
                SkillRecorder.save_and_register(skill, overwrite=False)
                installed.append(skill.id)
            except FileExistsError:
                errors.append(f"'{skill.id}' 已存在（跳过）")
            except Exception as e:
                errors.append(f"'{skill.id}' 失败: {e}")

        return jsonify({
            "success": True,
            "manifest": manifest,
            "installed": installed,
            "skipped_errors": errors,
        }), 201

    except Exception as e:
        logger.exception("[skillmarket/install]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/uninstall/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/uninstall/<skill_id>", methods=["POST", "DELETE"])
def uninstall_skill(skill_id: str):
    """卸载自定义 Skill（内置 Skill 不可删除）"""
    sm = _sm()
    sm._ensure_init()

    # 检查是否内置
    skill_def = sm._def_registry.get(skill_id)
    if skill_def and getattr(skill_def, "author", "") == "builtin":
        return jsonify({
            "success": False,
            "error": "内置 Skill 不可卸载，可以禁用它",
        }), 400

    skill_file = _SKILLS_DIR / f"{skill_id}.json"
    if not skill_file.exists():
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    try:
        skill_file.unlink()
        sm._def_registry.pop(skill_id, None)
        sm._registry.pop(skill_id, None)
        return jsonify({"success": True, "uninstalled": skill_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/toggle/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/toggle/<skill_id>", methods=["POST"])
def toggle_skill(skill_id: str):
    """启用或禁用 Skill。请求体: { "enabled": bool }"""
    data = request.json or {}
    enabled = bool(data.get("enabled", True))

    sm = _sm()
    success = sm.set_enabled(skill_id, enabled)

    if not success:
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    # 同步更新自定义 Skill 文件（内置 Skill 状态存 user_settings.json，已由 set_enabled 处理）
    skill_file = _SKILLS_DIR / f"{skill_id}.json"
    if skill_file.exists():
        try:
            d = json.loads(skill_file.read_text(encoding="utf-8"))
            d["enabled"] = enabled
            skill_file.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[toggle] 同步文件失败: {e}")

    return jsonify({"success": True, "skill_id": skill_id, "enabled": enabled})


# ══════════════════════════════════════════════════════════════════════════════
# PUT /api/skillmarket/edit/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/edit/<skill_id>", methods=["PUT"])
def edit_skill(skill_id: str):
    """
    编辑自定义 Skill 的可更新字段。
    可更新: name, description, icon, system_prompt_template, tags, input_variables, output_spec
    内置 Skill 不可修改。
    """
    sm = _sm()
    sm._ensure_init()

    skill_def = sm._def_registry.get(skill_id)
    if skill_def and getattr(skill_def, "author", "") == "builtin":
        return jsonify({
            "success": False,
            "error": "内置 Skill 不可直接修改。请先使用「克隆」功能创建副本",
        }), 400

    skill_file = _SKILLS_DIR / f"{skill_id}.json"
    if not skill_file.exists():
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    try:
        data = request.json or {}
        existing = json.loads(skill_file.read_text(encoding="utf-8"))

        editable = [
            "name", "description", "icon", "system_prompt_template",
            "prompt", "tags", "input_variables", "output_spec",
            "intent_description", "task_types", "bound_tools",
        ]
        changed = False
        for field in editable:
            if field in data:
                existing[field] = data[field]
                changed = True

        if not changed:
            return jsonify({"success": False, "error": "请提供至少一个可更新的字段"}), 400

        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        skill_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

        # 重新注册到 SkillManager
        SkillDefinition, _, _ = _schema()
        updated_def = SkillDefinition.from_dict(existing)
        sm.register_custom(updated_def)

        return jsonify({"success": True, "skill": existing})
    except Exception as e:
        logger.exception("[skillmarket/edit]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/duplicate/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/duplicate/<skill_id>", methods=["POST"])
def duplicate_skill(skill_id: str):
    """
    克隆一个 Skill（内置或自定义），生成新 ID 的副本。
    请求体: { "new_name": str (可选), "author": str (可选) }
    """
    sm = _sm()
    sm._ensure_init()

    skill_def = sm._def_registry.get(skill_id)
    if not skill_def:
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    data = request.json or {}
    new_name = data.get("new_name") or f"{skill_def.name}（副本）"
    new_author = data.get("author", "user")

    try:
        import copy
        new_def = copy.deepcopy(skill_def)
        new_def.name = new_name
        new_def.author = new_author

        # 生成新 ID（防止与原始冲突）
        from app.core.skills.skill_auto_builder import _make_skill_id
        base_id = _make_skill_id(new_name)
        new_id = base_id
        counter = 1
        while (sm._def_registry.get(new_id) or (_SKILLS_DIR / f"{new_id}.json").exists()):
            new_id = f"{base_id}_{counter}"
            counter += 1
        new_def.id = new_id
        new_def.created_at = datetime.now(timezone.utc).isoformat()

        SkillRecorder = _recorder()
        SkillRecorder.save_and_register(new_def, overwrite=False)

        return jsonify({"success": True, "new_skill_id": new_id, "skill": new_def.to_dict()}), 201
    except Exception as e:
        logger.exception("[skillmarket/duplicate]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/export/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/export/<skill_id>", methods=["GET"])
def export_skill(skill_id: str):
    """导出单个 Skill 为 .kotosk 文件（附带 README）"""
    sm = _sm()
    sm._ensure_init()
    skill_def = sm._def_registry.get(skill_id)
    if not skill_def:
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    try:
        _, SkillPackager = _auto_builder()
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".kotosk", prefix=f"koto_{skill_id}_"
        ) as tmp:
            tmp_path = tmp.name

        readme = (
            f"# {skill_def.name}\n\n"
            f"**作者:** {skill_def.author}\n"
            f"**版本:** {skill_def.version}\n\n"
            f"## 描述\n{skill_def.description}\n\n"
            f"## 意图\n{skill_def.intent_description or '未设置'}\n"
        )
        SkillPackager.pack(
            skills=[skill_def],
            output_path=tmp_path,
            pack_name=skill_def.name,
            author=skill_def.author,
            description=skill_def.description,
            readme=readme,
        )

        return send_file(
            tmp_path,
            as_attachment=True,
            download_name=f"{skill_id}.kotosk",
            mimetype="application/zip",
        )
    except Exception as e:
        logger.exception("[skillmarket/export]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/export-pack
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/export-pack", methods=["GET"])
def export_pack():
    """
    批量导出多个 Skill 为 .kotosk 包。
    查询参数: ids=id1,id2,id3 或 ids[]=id1&ids[]=id2
    """
    ids_csv = request.args.get("ids", "")
    ids_list = request.args.getlist("ids[]")
    if ids_csv:
        ids_list = ids_csv.split(",")
    ids_list = [i.strip() for i in ids_list if i.strip()]

    if not ids_list:
        return jsonify({"success": False, "error": "请通过 ids 或 ids[] 指定要导出的 Skill ID"}), 400

    sm = _sm()
    sm._ensure_init()

    skills_to_pack = []
    missing = []
    for sid in ids_list:
        skill_def = sm._def_registry.get(sid)
        if skill_def:
            skills_to_pack.append(skill_def)
        else:
            missing.append(sid)

    if not skills_to_pack:
        return jsonify({"success": False, "error": "未找到任何指定的 Skill", "missing": missing}), 404

    try:
        _, SkillPackager = _auto_builder()
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".kotosk", prefix="koto_pack_"
        ) as tmp:
            tmp_path = tmp.name

        pack_name = request.args.get("pack_name", f"koto-skill-pack-{len(skills_to_pack)}")
        SkillPackager.pack(
            skills=skills_to_pack,
            output_path=tmp_path,
            pack_name=pack_name,
            author="exported",
            description=f"包含 {len(skills_to_pack)} 个 Skill 的导出包",
        )

        return send_file(
            tmp_path,
            as_attachment=True,
            download_name=f"{pack_name}.kotosk",
            mimetype="application/zip",
        )
    except Exception as e:
        logger.exception("[skillmarket/export-pack]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/import
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/import", methods=["POST"])
def import_pack():
    """
    从上传的 .kotosk 文件导入 Skill。
    multipart/form-data: file=<.kotosk 文件>
    """
    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "error": "请上传 .kotosk 文件"}), 400

    filename = file.filename or ""
    if not filename.endswith(".kotosk"):
        return jsonify({"success": False, "error": "仅支持 .kotosk 文件格式"}), 400

    try:
        _, SkillPackager = _auto_builder()
        SkillRecorder = _recorder()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".kotosk") as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        try:
            manifest, skills = SkillPackager.unpack(tmp_path)
        finally:
            os.unlink(tmp_path)

        overwrite = str(request.form.get("overwrite", "false")).lower() == "true"
        installed, skipped, errors_list = [], [], []

        for skill in skills:
            try:
                SkillRecorder.save_and_register(skill, overwrite=overwrite)
                installed.append(skill.id)
            except FileExistsError:
                skipped.append(skill.id)
            except Exception as e:
                errors_list.append({"id": skill.id, "error": str(e)})

        return jsonify({
            "success": True,
            "manifest": manifest,
            "installed": installed,
            "skipped": skipped,
            "errors": errors_list,
            "total": len(skills),
        })
    except Exception as e:
        logger.exception("[skillmarket/import]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/rate/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/rate/<skill_id>", methods=["POST"])
def rate_skill(skill_id: str):
    """
    对 Skill 进行本地评分（1-5 星）。
    请求体: { "score": int (1-5), "comment": str (可选) }
    """
    data = request.json or {}
    score = int(data.get("score", 0))
    if not 1 <= score <= 5:
        return jsonify({"success": False, "error": "score 必须在 1-5 之间"}), 400

    try:
        ratings = _load_ratings()
        entry = ratings.get(skill_id, {"avg": 0.0, "count": 0, "votes": []})
        entry["votes"].append({
            "score": score,
            "comment": data.get("comment", ""),
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        total = sum(v["score"] for v in entry["votes"])
        entry["count"] = len(entry["votes"])
        entry["avg"] = round(total / entry["count"], 2)
        ratings[skill_id] = entry
        _save_ratings(ratings)

        return jsonify({
            "success": True,
            "skill_id": skill_id,
            "avg": entry["avg"],
            "count": entry["count"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/stats
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/stats", methods=["GET"])
def get_stats():
    """全局统计：Skill 数量、分类分布、评分分布"""
    try:
        sm = _sm()
        sm._ensure_init()

        total = len(sm._def_registry)
        by_category: Dict[str, int] = {}
        builtin_count = 0
        custom_count = 0
        enabled_count = 0

        for sid, skill_def in sm._def_registry.items():
            cat = getattr(skill_def, "category", "unknown")
            cat_str = cat.value if hasattr(cat, "value") else str(cat)
            by_category[cat_str] = by_category.get(cat_str, 0) + 1

            if getattr(skill_def, "author", "") == "builtin":
                builtin_count += 1
            else:
                custom_count += 1

            leg = sm._registry.get(sid, {})
            if leg.get("enabled", skill_def.enabled):
                enabled_count += 1

        ratings = _load_ratings()
        avg_rating = 0.0
        if ratings:
            avg_rating = round(
                sum(v.get("avg", 0) for v in ratings.values()) / len(ratings), 2
            )

        return jsonify({
            "success": True,
            "total_skills": total,
            "builtin_skills": builtin_count,
            "custom_skills": custom_count,
            "enabled_skills": enabled_count,
            "by_category": by_category,
            "avg_rating": avg_rating,
            "rated_count": len(ratings),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/suggest   —   智能 Skill 推荐
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/suggest", methods=["GET"])
def suggest_skills():
    """
    根据用户当前输入推荐最相关的未启用 Skill。

    查询参数:
      q          - 用户输入文本（必填）
      task_type  - 任务类型 (CHAT / CODER / RESEARCH …)
      top_k      - 返回数量（默认 3）
      all        - "true" 时也包含已启用的 Skill

    示例:
      GET /api/skillmarket/suggest?q=帮我写一份专业的商务报告&task_type=CHAT
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"success": False, "error": "参数 q 不能为空"}), 400

    task_type = request.args.get("task_type", None)
    top_k = min(int(request.args.get("top_k", 3)), 10)
    include_enabled = request.args.get("all", "false").lower() == "true"

    try:
        sm = _sm()
        suggestions = sm.suggest_skills(
            user_input=q,
            task_type=task_type,
            top_k=top_k,
            exclude_enabled=(not include_enabled),
        )
        return jsonify({
            "success": True,
            "query": q,
            "count": len(suggestions),
            "suggestions": suggestions,
        })
    except Exception as e:
        logger.exception("[skillmarket/suggest]")
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/check-conflicts/<id>   —   冲突检测
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/check-conflicts/<skill_id>", methods=["GET"])
def check_conflicts(skill_id: str):
    """
    检测启用某个 Skill 是否会与当前已启用 Skill 产生冲突。

    响应示例:
    {
      "has_conflict": true,
      "hard_conflicts": [{"id": "concise_mode", "name": "精简模式", "reason": "..."}],
      "soft_conflicts": []
    }
    """
    try:
        sm = _sm()
        result = sm.detect_conflicts(skill_id)
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/validate-response   —   对 AI 回复做 Skill OutputSpec 验收
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/validate-response", methods=["POST"])
def validate_response():
    """
    对一段 AI 生成的回复文本，检验所有当前激活 Skill 的 OutputSpec 约束。

    请求体:
    {
      "text": str,          AI 回复文本（必填）
      "task_type": str      任务类型（可选）
    }

    响应:
    {
      "all_passed": bool,
      "results": [{"skill_id", "skill_name", "passed", "reason"}, ...]
    }
    """
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"success": False, "error": "text 不能为空"}), 400

    try:
        sm = _sm()
        result = sm.validate_response(text, task_type=data.get("task_type"))
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/status   —   Skill 库状态摘要（供 UI 面板使用）
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/status", methods=["GET"])
def skill_status():
    """
    返回 Skill 库运行时状态：总数、启用数、自定义数、当前激活名称列表等。
    """
    try:
        sm = _sm()
        summary = sm.get_status_summary()
        return jsonify({"success": True, **summary})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ── Manifest v2  更新 / 回滚 / 依赖树 / 验证 (Skill 生命周期) ─────────────────
# ══════════════════════════════════════════════════════════════════════════════

_ROLLBACK_DIR = _BASE_DIR / "config" / "skills" / "_rollback"
_ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)


def _compare_versions(v1: str, v2: str) -> int:
    """简单版本比较：v1 > v2 → 1；== → 0；< → -1。"""
    def _parts(v: str):
        try:
            return [int(x) for x in v.strip().lstrip("v").split(".")]
        except Exception:
            return [0]
    for a, b in zip(_parts(v1), _parts(v2)):
        if a > b:
            return 1
        if a < b:
            return -1
    return len(_parts(v1)) - len(_parts(v2))


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/check-updates
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/check-updates", methods=["POST"])
def check_updates():
    """
    检查所有已安装自定义 Skill 的可用更新。
    对每个有 update_url 字段的 Skill，发起 HTTPS GET 获取最新 manifest，
    对比版本号后返回有更新的列表。
    """
    import urllib.request
    import ssl

    sm = _sm()
    sm._ensure_init()

    updates_available = []
    errors = []

    for skill_id, skill_def in sm._def_registry.items():
        update_url = getattr(skill_def, "update_url", "") or ""
        if not update_url:
            continue
        # 安全：仅允许 HTTPS
        if not update_url.startswith("https://"):
            errors.append({"skill_id": skill_id, "error": "update_url 必须使用 HTTPS"})
            continue
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(
                update_url,
                headers={"User-Agent": "koto-skill-updater/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                remote = json.loads(resp.read().decode("utf-8"))
            remote_version = remote.get("version", "0.0.0")
            current_version = getattr(skill_def, "version", "0.0.0") or "0.0.0"
            if _compare_versions(remote_version, current_version) > 0:
                updates_available.append({
                    "skill_id": skill_id,
                    "name": skill_def.name,
                    "current_version": current_version,
                    "latest_version": remote_version,
                    "update_url": update_url,
                    "changelog": remote.get("changelog", ""),
                })
        except Exception as exc:
            errors.append({"skill_id": skill_id, "error": str(exc)})

    return jsonify({
        "success": True,
        "updates_available": len(updates_available),
        "updates": updates_available,
        "errors": errors,
    })


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/update/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/update/<skill_id>", methods=["POST"])
def update_skill(skill_id: str):
    """
    从 update_url 拉取最新版本并安装（先备份当前版本以支持回滚）。
    Body (可选): { "force": true }
    """
    import urllib.request
    import ssl

    sm = _sm()
    sm._ensure_init()

    skill_def = sm._def_registry.get(skill_id)
    if not skill_def:
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404
    if getattr(skill_def, "author", "") == "builtin":
        return jsonify({"success": False, "error": "内置 Skill 不支持自动更新"}), 400

    update_url = getattr(skill_def, "update_url", "") or ""
    if not update_url:
        return jsonify({"success": False, "error": "该 Skill 未设置 update_url"}), 400
    if not update_url.startswith("https://"):
        return jsonify({"success": False, "error": "update_url 必须使用 HTTPS"}), 400

    data_body = request.get_json(silent=True) or {}
    force = bool(data_body.get("force", False))

    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            update_url,
            headers={"User-Agent": "koto-skill-updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            remote_data = json.loads(resp.read().decode("utf-8"))

        remote_version = remote_data.get("version", "0.0.0")
        current_version = getattr(skill_def, "version", "0.0.0") or "0.0.0"

        if not force and _compare_versions(remote_version, current_version) <= 0:
            return jsonify({
                "success": True,
                "updated": False,
                "message": f"当前已是最新版本 ({current_version})",
            })

        # 备份当前版本
        skill_file = _SKILLS_DIR / f"{skill_id}.json"
        if skill_file.exists():
            backup = _ROLLBACK_DIR / f"{skill_id}_v{current_version}.json"
            backup.write_text(skill_file.read_text(encoding="utf-8"), encoding="utf-8")

        remote_data["id"] = skill_id
        skill_file.write_text(
            json.dumps(remote_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        SkillDefinition, _, _ = _schema()
        sm.register_custom(SkillDefinition.from_dict(remote_data))

        return jsonify({
            "success": True,
            "updated": True,
            "skill_id": skill_id,
            "from_version": current_version,
            "to_version": remote_version,
        })
    except Exception as exc:
        logger.exception("[skillmarket/update/%s]", skill_id)
        return jsonify({"success": False, "error": str(exc)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/rollback/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/rollback/<skill_id>", methods=["POST"])
def rollback_skill(skill_id: str):
    """
    回滚 Skill 到上一个备份版本。
    Body (可选): { "version": "1.0.0" }
    """
    sm = _sm()
    sm._ensure_init()

    skill_def = sm._def_registry.get(skill_id)
    if not skill_def:
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    data_body = request.get_json(silent=True) or {}
    target_version = data_body.get("version")

    backups = sorted(_ROLLBACK_DIR.glob(f"{skill_id}_v*.json"), reverse=True)
    if not backups:
        return jsonify({"success": False, "error": "无可用的回滚备份"}), 404

    if target_version:
        match = _ROLLBACK_DIR / f"{skill_id}_v{target_version}.json"
        if not match.exists():
            available = [b.stem.split("_v", 1)[-1] for b in backups]
            return jsonify({
                "success": False,
                "error": f"未找到版本 {target_version} 的备份",
                "available_versions": available,
            }), 404
        backup_file = match
    else:
        backup_file = backups[0]

    try:
        backup_data = json.loads(backup_file.read_text(encoding="utf-8"))
        skill_file = _SKILLS_DIR / f"{skill_id}.json"
        current_version = getattr(skill_def, "version", "unknown") or "unknown"

        if skill_file.exists():
            pre = _ROLLBACK_DIR / f"{skill_id}_v{current_version}_pre_rollback.json"
            pre.write_text(skill_file.read_text(encoding="utf-8"), encoding="utf-8")

        skill_file.write_text(
            json.dumps(backup_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        SkillDefinition, _, _ = _schema()
        sm.register_custom(SkillDefinition.from_dict(backup_data))

        return jsonify({
            "success": True,
            "skill_id": skill_id,
            "rolled_back_to": backup_data.get("version", "unknown"),
            "previous_version": current_version,
        })
    except Exception as exc:
        logger.exception("[skillmarket/rollback/%s]", skill_id)
        return jsonify({"success": False, "error": str(exc)}), 500


@marketplace_bp.route("/rollback/<skill_id>/history", methods=["GET"])
def rollback_history(skill_id: str):
    """列出某个 Skill 的所有可回滚备份版本。"""
    backups = sorted(_ROLLBACK_DIR.glob(f"{skill_id}_v*.json"), reverse=True)
    result = []
    for b in backups:
        try:
            d = json.loads(b.read_text(encoding="utf-8"))
            result.append({
                "version": d.get("version", "unknown"),
                "backup_file": b.name,
                "updated_at": d.get("updated_at", ""),
                "name": d.get("name", skill_id),
            })
        except Exception:
            result.append({"backup_file": b.name, "error": "无法解析"})
    return jsonify({"skill_id": skill_id, "backups": result})


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/dependencies/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/dependencies/<skill_id>", methods=["GET"])
def get_dependencies(skill_id: str):
    """
    返回指定 Skill 的依赖树（manifest v2 `dependencies` 字段），最深 3 层。
    """
    sm = _sm()
    sm._ensure_init()

    def _resolve(sid: str, depth: int = 0) -> Dict:
        if depth > 3:
            return {"id": sid, "error": "超过最大递归深度"}
        skill = sm._def_registry.get(sid)
        if not skill:
            return {"id": sid, "installed": False}
        deps = list(getattr(skill, "dependencies", None) or [])
        return {
            "id": sid,
            "name": skill.name,
            "version": getattr(skill, "version", ""),
            "installed": True,
            "enabled": sm._registry.get(sid, {}).get("enabled", skill.enabled),
            "dependencies": [_resolve(d, depth + 1) for d in deps],
        }

    if not sm._def_registry.get(skill_id):
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    return jsonify({"success": True, "tree": _resolve(skill_id)})


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/skillmarket/verify/<id>
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/verify/<skill_id>", methods=["POST"])
def verify_skill(skill_id: str):
    """
    验证 Skill 的 manifest v2 兼容性与权限声明。

    检查项：min_koto_version 兼容性、依赖安装状态、permission 授权。
    Body (可选): { "allowed_permissions": ["file_read", "network"] }
    """
    sm = _sm()
    sm._ensure_init()

    skill_def = sm._def_registry.get(skill_id)
    if not skill_def:
        return jsonify({"success": False, "error": f"Skill '{skill_id}' 不存在"}), 404

    data_body = request.get_json(silent=True) or {}
    allowed_permissions = set(data_body.get("allowed_permissions", [
        "file_read", "network", "clipboard", "agent_call",
    ]))

    results = []
    passed = True

    # 1. Koto 版本兼容性
    compatibility = getattr(skill_def, "compatibility", None) or {}
    min_ver = compatibility.get("min_koto_version")
    if min_ver:
        koto_ver = "1.0.0"  # 当前 Koto 版本占位
        ok = _compare_versions(koto_ver, min_ver) >= 0
        results.append({
            "check": "koto_version",
            "passed": ok,
            "detail": f"要求 >= {min_ver}，当前 {koto_ver}",
        })
        if not ok:
            passed = False

    # 2. 依赖状态
    for dep_id in (getattr(skill_def, "dependencies", None) or []):
        dep = sm._def_registry.get(dep_id)
        dep_enabled = dep and sm._registry.get(dep_id, {}).get("enabled", dep.enabled)
        ok = bool(dep_enabled)
        results.append({
            "check": f"dependency:{dep_id}",
            "passed": ok,
            "detail": "已安装并启用" if ok else ("未安装" if not dep else "已安装但未启用"),
        })
        if not ok:
            passed = False

    # 3. 权限检查
    for perm in (getattr(skill_def, "permissions", None) or []):
        ok = perm in allowed_permissions
        results.append({
            "check": f"permission:{perm}",
            "passed": ok,
            "detail": "在允许列表内" if ok else f"权限 '{perm}' 未授权",
        })
        if not ok:
            passed = False

    return jsonify({
        "success": True,
        "skill_id": skill_id,
        "verified": passed,
        "checks": results,
    })


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/sessions  —  列出可用的对话会话（供创作工坊使用）
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/sessions", methods=["GET"])
def list_sessions():
    """
    返回 chats/ 目录中所有可用对话会话的摘要列表，供创作工坊「从对话提取」功能使用。
    每条记录包含: session_id（文件名去后缀）、标题、消息数、最后更新时间。
    """
    chats_dir = _BASE_DIR / "chats"
    sessions = []

    if chats_dir.exists():
        for chat_file in sorted(chats_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(chat_file.read_text(encoding="utf-8"))
                session_id = chat_file.stem

                # 支持多种 JSON 结构
                messages = data if isinstance(data, list) else data.get("messages", data.get("history", []))
                msg_count = len(messages) if isinstance(messages, list) else 0

                # 取首条用户消息作为标题预览
                title = session_id
                if isinstance(messages, list):
                    for msg in messages:
                        if isinstance(msg, dict):
                            role = msg.get("role", "")
                            content = msg.get("content", msg.get("text", ""))
                            if role in ("user", "human") and content:
                                title = str(content)[:60]
                                break

                sessions.append({
                    "session_id": session_id,
                    "title": title,
                    "message_count": msg_count,
                    "updated_at": datetime.fromtimestamp(
                        chat_file.stat().st_mtime
                    ).strftime("%Y-%m-%d %H:%M"),
                    "file_name": chat_file.name,
                })
            except Exception as e:
                logger.debug(f"[sessions] 跳过 {chat_file.name}: {e}")

    return jsonify({
        "success": True,
        "total": len(sessions),
        "sessions": sessions,
    })


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/skillmarket/active  —  返回当前已激活的 Skill 列表（供聊天 UI 使用）
# ══════════════════════════════════════════════════════════════════════════════

@marketplace_bp.route("/active", methods=["GET"])
def get_active_skills():
    """
    返回当前所有已启用 Skill 的精简信息（id、name、icon、category）。
    含冲突预警：若多个互冲突的 Skill 同时启用，在列表中标注被抑制的 Skill。
    """
    try:
        sm = _sm()
        sm._ensure_init()

        HIDDEN_FROM_PILL_BAR = {"long_term_memory"}

        # 获取冲突信息
        conflicts = sm.check_conflicts()
        suppressed_ids = {c["loser_id"] for c in conflicts}

        active = []
        for skill_id, s in sm._registry.items():
            if skill_id in HIDDEN_FROM_PILL_BAR:
                continue
            if s.get("enabled", False):
                active.append({
                    "id": skill_id,
                    "name": s.get("name", skill_id),
                    "icon": s.get("icon", "🔧"),
                    "category": s.get("category", "custom"),
                    "description": s.get("description", ""),
                    "has_template": bool(s.get("template_path") or
                        (_BASE_DIR / "config" / "skill_templates" / skill_id / "template.docx").exists()),
                    "suppressed": skill_id in suppressed_ids,
                })

        return jsonify({
            "success": True,
            "count": len(active),
            "skills": active,
            "conflicts": conflicts,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@marketplace_bp.route("/conflicts", methods=["GET"])
def get_skill_conflicts():
    """
    返回当前启用 Skills 之间所有冲突关系。
    可选参数 task_type（如 ?task_type=CHAT）筛选特定任务类型下的冲突。
    """
    try:
        task_type = request.args.get("task_type")
        sm = _sm()
        conflicts = sm.check_conflicts(task_type=task_type)
        return jsonify({
            "success": True,
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "summary": (
                f"检测到 {len(conflicts)} 处冲突：" +
                "；".join(
                    f"「{c['winner_name']}」抑制「{c['loser_name']}」"
                    for c in conflicts
                ) if conflicts else "当前无冲突"
            ),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ▼  Word 模板管理（Template Skills）
# ══════════════════════════════════════════════════════════════════════════════

_TMPL_ROOT = _BASE_DIR / "config" / "skill_templates"
_TMPL_OUT  = _BASE_DIR / "config" / "skill_template_outputs"

ALLOWED_TMPL_EXTENSIONS = {".docx"}
MAX_TMPL_SIZE = 10 * 1024 * 1024  # 10 MB


def _safe_skill_id(skill_id: str) -> bool:
    """验证 skill_id 是合法的标识符，防止路径穿越。"""
    return bool(re.fullmatch(r"[a-z0-9_\-]{1,60}", skill_id))


@marketplace_bp.route("/templates/upload", methods=["POST"])
def upload_skill_template():
    """
    上传 Word 模板文件并绑定到指定 Skill。

    Form 字段：
      - file     : .docx 文件
      - skill_id : 要绑定的 Skill ID

    返回：
      { success, skill_id, fields, field_count, template_preview }
    """
    try:
        skill_id = request.form.get("skill_id", "").strip().lower()
        if not skill_id or not _safe_skill_id(skill_id):
            return jsonify({"success": False, "error": "skill_id 无效"}), 400

        if "file" not in request.files:
            return jsonify({"success": False, "error": "未上传文件"}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"success": False, "error": "文件名为空"}), 400

        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_TMPL_EXTENSIONS:
            return jsonify({"success": False, "error": f"仅支持 .docx 格式，不接受 {ext}"}), 400

        # 读取并校验大小
        data = f.read()
        if len(data) > MAX_TMPL_SIZE:
            return jsonify({"success": False, "error": "文件过大，最大 10 MB"}), 413

        # 保存
        tmpl_dir = _TMPL_ROOT / skill_id
        tmpl_dir.mkdir(parents=True, exist_ok=True)
        tmpl_path = tmpl_dir / "template.docx"
        tmpl_path.write_bytes(data)

        # 解析字段
        from app.core.skills.template_engine import TemplateEngine
        fields = TemplateEngine.parse_fields(tmpl_path)
        preview = TemplateEngine.get_raw_text(tmpl_path)

        # 更新 Skill 注册表中的 template_path 字段
        sm = _sm()
        sm._ensure_init()
        if skill_id in sm._registry:
            sm._registry[skill_id]["template_path"] = str(
                Path("config") / "skill_templates" / skill_id / "template.docx"
            )
            sm._registry[skill_id]["bound_tools"] = list(
                set(sm._registry[skill_id].get("bound_tools", [])) | {"fill_skill_template", "get_template_fields"}
            )
            sm._save_states_to_settings()

            # 同步更新 config/skills/{skill_id}.json（若存在）
            skill_json = _SKILLS_DIR / f"{skill_id}.json"
            if skill_json.exists():
                with open(skill_json, "r", encoding="utf-8") as fp:
                    sdata = json.load(fp)
                sdata["template_path"] = str(
                    Path("config") / "skill_templates" / skill_id / "template.docx"
                )
                sdata["bound_tools"] = list(
                    set(sdata.get("bound_tools", [])) | {"fill_skill_template", "get_template_fields"}
                )
                with open(skill_json, "w", encoding="utf-8") as fp:
                    json.dump(sdata, fp, ensure_ascii=False, indent=2)

        logger.info(f"[templates/upload] skill={skill_id} fields={fields}")
        return jsonify({
            "success": True,
            "skill_id": skill_id,
            "fields": fields,
            "field_count": len(fields),
            "template_preview": preview[:800],
        })
    except Exception as e:
        logger.error(f"[templates/upload] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@marketplace_bp.route("/templates/<skill_id>", methods=["GET"])
def get_skill_template_info(skill_id: str):
    """
    返回 Skill 模板信息：字段列表、预览文本、是否已绑定。
    """
    if not _safe_skill_id(skill_id):
        return jsonify({"success": False, "error": "skill_id 无效"}), 400
    try:
        tmpl_path = _TMPL_ROOT / skill_id / "template.docx"
        if not tmpl_path.exists():
            return jsonify({"success": False, "has_template": False,
                            "message": "该 Skill 尚未绑定 Word 模板"}), 200

        from app.core.skills.template_engine import TemplateEngine
        fields = TemplateEngine.parse_fields(tmpl_path)
        preview = TemplateEngine.get_raw_text(tmpl_path)
        return jsonify({
            "success": True,
            "has_template": True,
            "skill_id": skill_id,
            "fields": fields,
            "field_count": len(fields),
            "template_preview": preview[:800],
        })
    except Exception as e:
        logger.error(f"[templates/info] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@marketplace_bp.route("/templates/<skill_id>", methods=["DELETE"])
def delete_skill_template(skill_id: str):
    """删除 Skill 绑定的模板文件并清除 template_path 字段。"""
    if not _safe_skill_id(skill_id):
        return jsonify({"success": False, "error": "skill_id 无效"}), 400
    try:
        tmpl_path = _TMPL_ROOT / skill_id / "template.docx"
        if tmpl_path.exists():
            tmpl_path.unlink()

        sm = _sm()
        sm._ensure_init()
        if skill_id in sm._registry:
            sm._registry[skill_id].pop("template_path", None)
            bt = sm._registry[skill_id].get("bound_tools", [])
            sm._registry[skill_id]["bound_tools"] = [
                t for t in bt if t not in {"fill_skill_template", "get_template_fields"}
            ]
            sm._save_states_to_settings()

        skill_json = _SKILLS_DIR / f"{skill_id}.json"
        if skill_json.exists():
            with open(skill_json, "r", encoding="utf-8") as fp:
                sdata = json.load(fp)
            sdata.pop("template_path", None)
            sdata["bound_tools"] = [
                t for t in sdata.get("bound_tools", [])
                if t not in {"fill_skill_template", "get_template_fields"}
            ]
            with open(skill_json, "w", encoding="utf-8") as fp:
                json.dump(sdata, fp, ensure_ascii=False, indent=2)

        return jsonify({"success": True, "message": f"Skill '{skill_id}' 的模板已删除"})
    except Exception as e:
        logger.error(f"[templates/delete] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@marketplace_bp.route("/templates/<skill_id>/output/<filename>", methods=["GET"])
def download_template_output(skill_id: str, filename: str):
    """
    下载已填充的 .docx 输出文件。
    文件名格式：{skill_id}_{timestamp}.docx（由 fill_skill_template 工具生成）
    """
    if not _safe_skill_id(skill_id):
        return jsonify({"success": False, "error": "skill_id 无效"}), 400

    # 严格验证文件名，防止路径穿越
    if not re.fullmatch(r"[a-z0-9_\-]{1,60}_\d{8}_\d{6}\.docx", filename):
        return jsonify({"success": False, "error": "文件名无效"}), 400

    out_path = _TMPL_OUT / skill_id / filename
    if not out_path.exists():
        return jsonify({"success": False, "error": "文件不存在"}), 404

    return send_file(
        str(out_path),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

