"""
skill_recorder.py — 从对话自动提取 SkillDefinition
======================================================
将一段用户-AI 对话转化为标准化的 SkillDefinition，
方便后续复用、导出 MCP 工具、或触发 LoRA 微调。

主要入口:
  SkillRecorder.from_conversation(session_id, skill_name, description)
  SkillRecorder.from_text(user_input, ai_response, skill_name, description)
  SkillRecorder.save_and_register(skill_def)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys as _sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 路径辅助 ──────────────────────────────────────────────────────────────────


def _get_base_dir() -> Path:
    if getattr(_sys, 'frozen', False):
        return Path(_sys.executable).parent
    return Path(__file__).resolve().parents[3]


_BASE_DIR = _get_base_dir()
_CHATS_DIR = str(_BASE_DIR / "chats")
_SKILLS_DIR = str(_BASE_DIR / "config" / "skills")


# ── 懒加载 SkillDefinition ────────────────────────────────────────────────────

def _get_skill_schema():
    from app.core.skills.skill_schema import SkillDefinition, InputVariable, OutputSpec
    return SkillDefinition, InputVariable, OutputSpec


def _get_skill_manager():
    from app.core.skills.skill_manager import SkillManager
    return SkillManager


# ── 对话加载 ─────────────────────────────────────────────────────────────────

def _load_chat_history(session_id: str) -> List[Dict[str, str]]:
    """
    从 chats/{session_id}.json 读取对话记录。
    支持多种格式:
      - [{"role": "user"|"model"|"assistant", "content": "...", "parts": [...]}]
      - {"history": [...]}
    """
    path = os.path.join(_CHATS_DIR, f"{session_id}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict) and "history" in raw:
            return raw["history"]
    except Exception as e:
        logger.warning(f"[skill_recorder] 无法读取 chats/{session_id}.json: {e}")
    return []


def _extract_turns(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    将原始历史记录规范化为 [{"role": "user"|"assistant", "text": str}, ...]
    """
    turns = []
    for entry in history:
        role_raw = entry.get("role", "")
        role = "assistant" if role_raw in ("model", "assistant", "ai") else "user"

        # content 可能是字符串或 parts 列表
        text = entry.get("content") or ""
        if not text and "parts" in entry:
            parts = entry["parts"]
            if isinstance(parts, list):
                text = " ".join(
                    p.get("text", p) if isinstance(p, dict) else str(p)
                    for p in parts
                )
        if text:
            turns.append({"role": role, "text": str(text)})
    return turns


# ── 技能元信息推断 ────────────────────────────────────────────────────────────

_TAG_PATTERNS = [
    (r"帮我写|请写|生成.*文章|写一篇", "writing"),
    (r"翻译|translate|译文", "translation"),
    (r"分析|总结|摘要|summary", "analysis"),
    (r"代码|python|javascript|程序|function|def |class ", "coding"),
    (r"搜索|查找|查询|search|find", "search"),
    (r"计算|算出|数学|math", "math"),
    (r"邮件|email|mail", "email"),
    (r"会议|日程|提醒|schedule", "productivity"),
]


def _infer_tags(text: str) -> List[str]:
    tags = []
    text_lower = text.lower()
    for pattern, tag in _TAG_PATTERNS:
        if re.search(pattern, text_lower):
            tags.append(tag)
    return list(set(tags)) or ["general"]


def _infer_input_variables(user_text: str):
    """
    简单启发式：识别 {占位符} 或常见参数模式。
    """
    SkillDefinition, InputVariable, OutputSpec = _get_skill_schema()
    placeholders = re.findall(r"\{(\w+)\}", user_text)
    if placeholders:
        return [
            InputVariable(
                name=p,
                description=f"输入参数: {p}",
                required=True,
                example=p,
            )
            for p in dict.fromkeys(placeholders)  # 去重保序
        ]
    # 默认单输入
    return [
        InputVariable(
            name="input",
            description="用户输入",
            required=True,
            example=user_text[:80] if user_text else "...",
        )
    ]


def _make_skill_id(name: str) -> str:
    """生成 URL 安全、小写、无空格的 skill id"""
    slug = re.sub(r"[^\w\u4e00-\u9fff]", "_", name.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or f"skill_{hashlib.md5(name.encode()).hexdigest()[:8]}"


# ── 核心类 ────────────────────────────────────────────────────────────────────

class SkillRecorder:
    """
    从对话 / 文本片段提取标准化 SkillDefinition。

    用法示例:
        sd = SkillRecorder.from_conversation("session_abc", "邮件起草助手")
        sd = SkillRecorder.from_text(user_input, ai_response, "代码审查", "帮助审查代码质量")
        SkillRecorder.save_and_register(sd)
    """

    # ── 从 session 对话文件提取 ──────────────────────────────────────────────

    @classmethod
    def from_conversation(
        cls,
        session_id: str,
        skill_name: str,
        description: str = "",
        max_turns: int = 5,
    ):
        """
        从 chats/{session_id}.json 读取最近 max_turns 轮对话，
        自动提取 SkillDefinition。

        返回 SkillDefinition 对象，如果对话不存在则抛出 ValueError。
        """
        history = _load_chat_history(session_id)
        if not history:
            raise ValueError(
                f"[SkillRecorder] 找不到 session '{session_id}' 的对话记录，"
                f"路径: {_CHATS_DIR}/{session_id}.json"
            )

        turns = _extract_turns(history)
        # 取最近的 max_turns 轮（成对）
        recent = turns[-max_turns * 2:] if len(turns) > max_turns * 2 else turns

        # 找最后一对 user-assistant
        user_text = ""
        ai_text = ""
        for i in range(len(recent) - 1, -1, -1):
            if recent[i]["role"] == "assistant" and not ai_text:
                ai_text = recent[i]["text"]
            elif recent[i]["role"] == "user" and not user_text:
                user_text = recent[i]["text"]
            if user_text and ai_text:
                break

        examples = [
            {
                "user_input": t["text"]
                for t in recent
                if t["role"] == "user"
            }
        ]
        # 改用列表推导更清晰
        example_pairs = cls._build_example_pairs(recent, max_turns)

        return cls._build_skill_def(
            skill_name=skill_name,
            description=description,
            prototype_user_input=user_text,
            prototype_ai_response=ai_text,
            example_pairs=example_pairs,
            session_id=session_id,
        )

    # ── 从文本片段提取 ───────────────────────────────────────────────────────

    @classmethod
    def from_text(
        cls,
        user_input: str,
        ai_response: str,
        skill_name: str,
        description: str = "",
    ):
        """
        从单对用户输入/AI 响应直接构建 SkillDefinition。
        """
        return cls._build_skill_def(
            skill_name=skill_name,
            description=description,
            prototype_user_input=user_input,
            prototype_ai_response=ai_response,
            example_pairs=[{"input": user_input, "output": ai_response}],
            session_id=None,
        )

    # ── 持久化并注册 ─────────────────────────────────────────────────────────

    @classmethod
    def save_and_register(cls, skill_def, overwrite: bool = False) -> str:
        """
        将 SkillDefinition 保存到 config/skills/{id}.json 并在 SkillManager 中注册。
        返回 skill_id。
        """
        os.makedirs(_SKILLS_DIR, exist_ok=True)
        target = os.path.join(_SKILLS_DIR, f"{skill_def.id}.json")
        if os.path.exists(target) and not overwrite:
            raise FileExistsError(
                f"Skill '{skill_def.id}' 已存在。传入 overwrite=True 强制覆盖。"
            )

        # 保存 JSON
        with open(target, "w", encoding="utf-8") as f:
            json.dump(skill_def.to_dict(), f, ensure_ascii=False, indent=2)

        # 注册到 SkillManager
        try:
            SkillManager = _get_skill_manager()
            SkillManager.register_custom(skill_def)
            logger.info(f"[skill_recorder] 已注册 Skill: {skill_def.id}")
        except Exception as e:
            logger.warning(f"[skill_recorder] SkillManager 注册失败（已保存到磁盘）: {e}")

        return skill_def.id

    # ── 私有辅助 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_example_pairs(
        turns: List[Dict[str, str]], max_pairs: int
    ) -> List[Dict[str, str]]:
        """从规范化的 turns 列表提取用户-assistant 配对示例"""
        pairs = []
        i = 0
        while i < len(turns) - 1 and len(pairs) < max_pairs:
            if turns[i]["role"] == "user" and turns[i + 1]["role"] == "assistant":
                pairs.append({"input": turns[i]["text"], "output": turns[i + 1]["text"]})
                i += 2
            else:
                i += 1
        return pairs

    @staticmethod
    def _build_skill_def(
        skill_name: str,
        description: str,
        prototype_user_input: str,
        prototype_ai_response: str,
        example_pairs: List[Dict[str, str]],
        session_id: Optional[str] = None,
    ):
        """内部工厂：构建 SkillDefinition 实例"""
        SkillDefinition, InputVariable, OutputSpec = _get_skill_schema()

        skill_id = _make_skill_id(skill_name)
        tags = _infer_tags(prototype_user_input + " " + description)
        input_vars = _infer_input_variables(prototype_user_input)

        system_prompt = (
            f"你是一个专注于「{skill_name}」任务的 AI 助手。\n"
            + (f"{description}\n" if description else "")
            + "请根据用户的具体输入，给出高质量、简洁明了的回答。"
        )

        # 输出规格
        max_ch = max(len(prototype_ai_response), 200) if prototype_ai_response else 2000
        output_spec = OutputSpec(
            format="text",
            max_chars=min(max_ch * 3, 8000),
            must_contain=[],
            must_not_contain=[],
        )

        sd = SkillDefinition(
            id=skill_id,
            name=skill_name,
            icon="🤖",
            category="custom",
            description=description or f"自动从对话提取的技能：{skill_name}",
            version="1.0.0",
            author="skill_recorder",
            tags=tags,
            input_variables=input_vars,
            system_prompt_template=system_prompt,
            output_spec=output_spec,
            # 用 tags 字段存储 session 来源元数据（简化）
        )
        return sd
