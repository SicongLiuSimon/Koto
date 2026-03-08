# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   Koto  ─  Template Fill Plugin（Word 模板填充 Agent 工具）      ║
╚══════════════════════════════════════════════════════════════════╝

提供两个 Agent 工具：

  get_template_fields(skill_id)
    → 返回某个 Skill 的模板字段列表，Agent 用来了解需要收集哪些信息

  fill_skill_template(skill_id, values)
    → 把 Agent 生成的内容填入 Word 模板，返回下载 URL
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin

logger = logging.getLogger(__name__)

import sys as _sys
_BASE_DIR = (
    Path(_sys.executable).parent if getattr(_sys, "frozen", False)
    else Path(__file__).resolve().parents[4]   # project root
)
_TMPL_DIR   = _BASE_DIR / "config" / "skill_templates"
_OUTPUT_DIR = _BASE_DIR / "config" / "skill_template_outputs"


class TemplateFillPlugin(AgentPlugin):
    """Agent 插件：读取 / 填充 Skill 绑定的 Word 模板。"""

    @property
    def name(self) -> str:
        return "TemplateFill"

    @property
    def description(self) -> str:
        return "Tools for reading and filling Word document templates bound to Skills."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_template_fields",
                "func": self.get_template_fields,
                "description": (
                    "获取某个 Skill 的 Word 模板中所有需要填写的字段名称列表。"
                    "在调用 fill_skill_template 前先调用此工具了解需要哪些字段。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "skill_id": {
                            "type": "STRING",
                            "description": "Skill 的唯一标识符（如 meeting_minutes）"
                        }
                    },
                    "required": ["skill_id"]
                }
            },
            {
                "name": "fill_skill_template",
                "func": self.fill_skill_template,
                "description": (
                    "将内容填入 Skill 绑定的 Word 文档模板，生成已填写的 .docx 文件。"
                    "返回下载链接，告知用户文件已生成。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "skill_id": {
                            "type": "STRING",
                            "description": "Skill 的唯一标识符"
                        },
                        "values": {
                            "type": "OBJECT",
                            "description": (
                                "字段名 → 填写内容 的键值对。"
                                "键必须与 get_template_fields 返回的字段名匹配。"
                                "示例：{\"日期\": \"2026-03-08\", \"参会人员\": \"张三、李四\"}"
                            )
                        }
                    },
                    "required": ["skill_id", "values"]
                }
            }
        ]

    # ──────────────────────────────────────────────────────────────────────────
    # 工具实现
    # ──────────────────────────────────────────────────────────────────────────

    def get_template_fields(self, skill_id: str) -> str:
        """返回模板中所有 {{field}} 占位符名称的 JSON 列表。"""
        try:
            tmpl_path = self._get_template_path(skill_id)
            if not tmpl_path:
                return json.dumps({
                    "success": False,
                    "error": f"Skill '{skill_id}' 没有绑定 Word 模板，或模板文件不存在。"
                }, ensure_ascii=False)

            from app.core.skills.template_engine import TemplateEngine
            fields = TemplateEngine.parse_fields(tmpl_path)
            return json.dumps({
                "success": True,
                "skill_id": skill_id,
                "fields": fields,
                "field_count": len(fields),
                "message": f"模板包含 {len(fields)} 个待填写字段：{', '.join(fields)}"
            }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[TemplateFillPlugin] get_template_fields error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    def fill_skill_template(self, skill_id: str, values: Dict[str, Any]) -> str:
        """填充模板并返回下载 URL。"""
        try:
            tmpl_path = self._get_template_path(skill_id)
            if not tmpl_path:
                return json.dumps({
                    "success": False,
                    "error": f"Skill '{skill_id}' 没有绑定 Word 模板，或模板文件不存在。"
                }, ensure_ascii=False)

            # 若 values 是 str（LLM 有时传 JSON 字符串），先尝试解析
            if isinstance(values, str):
                try:
                    values = json.loads(values)
                except json.JSONDecodeError:
                    return json.dumps({
                        "success": False,
                        "error": "values 参数格式错误，必须是 JSON 对象"
                    }, ensure_ascii=False)

            from app.core.skills.template_engine import TemplateEngine

            # 检查是否有缺失字段（仅提示，不阻止生成）
            fields = TemplateEngine.parse_fields(tmpl_path)
            _, missing = TemplateEngine.validate_fields(fields, values)

            # 构建输出路径
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = _OUTPUT_DIR / skill_id
            out_path = out_dir / f"{skill_id}_{ts}.docx"

            result_path = TemplateEngine.fill(tmpl_path, values, out_path)

            # 构建前端可访问的下载 URL
            download_url = f"/api/skillmarket/templates/{skill_id}/output/{result_path.name}"

            warning = ""
            if missing:
                warning = f"  注意：以下字段未填写，保留了占位符：{', '.join(missing)}"

            return json.dumps({
                "success": True,
                "skill_id": skill_id,
                "file_name": result_path.name,
                "download_url": download_url,
                "filled_fields": list(values.keys()),
                "missing_fields": missing,
                "message": (
                    f"Word 文档已生成！[点击下载]({download_url})\n{warning}"
                ).strip()
            }, ensure_ascii=False)

        except Exception as e:
            logger.error(f"[TemplateFillPlugin] fill_skill_template error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    # ──────────────────────────────────────────────────────────────────────────
    # 内部辅助
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _get_template_path(skill_id: str) -> Path | None:
        """
        查找 Skill 绑定的模板文件路径。
        优先读取技能 JSON 中的 template_path 字段；
        如果没有，查找 config/skill_templates/{skill_id}/template.docx。
        """
        # 从 SkillManager registry 读取 template_path
        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
            skill = SkillManager._registry.get(skill_id)
            if skill and skill.get("template_path"):
                p = Path(skill["template_path"])
                if not p.is_absolute():
                    p = _BASE_DIR / p
                if p.exists():
                    return p
        except Exception:
            pass

        # 回退：约定目录
        fallback = _TMPL_DIR / skill_id / "template.docx"
        if fallback.exists():
            return fallback

        return None
